import os
import json
import threading
import time
from typing import List, Tuple, Optional, Any, Callable
from sortedcontainers import SortedDict

class FDBConflictError(Exception):
    """Raised when an optimistic concurrency conflict occurs."""
    pass

class FDBTransactionError(Exception):
    """Raised when a transaction error occurs."""
    pass

def _to_bytes(k: Any) -> bytes:
    if hasattr(k, 'key'):
        return k.key()
    if isinstance(k, (bytes, bytearray)):
        return bytes(k)
    if isinstance(k, str):
        return k.encode('utf-8')
    raise TypeError(f"Key must be bytes or Subspace with .key(), got {type(k)}")

class LocalTransaction:
    """Single-process demo transaction with snapshot and coarse version checking."""
    def __init__(self, engine: 'FDBLocalEngine'):
        self.engine = engine
        with engine.lock:
            self.snapshot = engine.data.copy()
            self.read_version = engine.version
        self.read_keys = set()
        self.writes = {}
        self.clears = set()
        self.clear_ranges = []
        self.committed = False
        self.aborted = False

    def get(self, key: Any) -> Optional[bytes]:
        if self.aborted or self.committed:
            raise FDBTransactionError("Transaction already closed")
        key = _to_bytes(key)
        self.read_keys.add(key)
        
        # Check writes in this transaction
        if key in self.writes:
            return self.writes[key]
        if key in self.clears:
            return None
        for begin, end in self.clear_ranges:
            if begin <= key < end:
                return None
        
        # Fall back to committed engine state
        with self.engine.lock:
            return self.snapshot.get(key, None)

    def set(self, key: Any, value: bytes):
        if self.aborted or self.committed:
            raise FDBTransactionError("Transaction already closed")
        key = _to_bytes(key)
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError(f"Value must be bytes, got {type(value)}")
        
        value = bytes(value)
        self.writes[key] = value
        self.clears.discard(key)

    def clear(self, key: Any):
        if self.aborted or self.committed:
            raise FDBTransactionError("Transaction already closed")
        key = _to_bytes(key)
        self.clears.add(key)
        self.writes.pop(key, None)

    def clear_range(self, begin: Any, end: Any):
        if self.aborted or self.committed:
            raise FDBTransactionError("Transaction already closed")
        begin = _to_bytes(begin)
        end = _to_bytes(end)
        self.clear_ranges.append((begin, end))
        # Remove any pending writes in range
        to_del = [k for k in self.writes if begin <= k < end]
        for k in to_del:
            del self.writes[k]

    def get_range(self, begin: Any, end: Any, limit: Optional[int] = None, reverse: bool = False) -> List[Tuple[bytes, bytes]]:
        if self.aborted or self.committed:
            raise FDBTransactionError("Transaction already closed")
        
        begin = _to_bytes(begin)
        end = _to_bytes(end)
        
        with self.engine.lock:
            # Combine engine data with uncommitted transaction modifications
            merged = {}
            # Base data from engine in range
            for k in self.snapshot.irange(begin, end, inclusive=(True, False)):
                merged[k] = self.snapshot[k]
            
            # Apply clear ranges
            for c_begin, c_end in self.clear_ranges:
                for k in list(merged.keys()):
                    if c_begin <= k < c_end:
                        del merged[k]
            
            # Apply individual clears
            for k in self.clears:
                merged.pop(k, None)
            
            # Apply transaction writes within range
            for k, v in self.writes.items():
                if begin <= k < end:
                    merged[k] = v
            
            # Sort items by byte key
            sorted_items = sorted(merged.items(), key=lambda x: x[0], reverse=reverse)
            if limit is not None:
                sorted_items = sorted_items[:limit]
            
            # Record keys read for conflict detection
            for k, _ in sorted_items:
                self.read_keys.add(k)
                
            return sorted_items

    def __getitem__(self, key: Any):
        if isinstance(key, slice):
            return self.get_range(key.start, key.stop)
        return self.get(key)

    def __setitem__(self, key: Any, value: bytes):
        self.set(key, value)

    def __delitem__(self, key: Any):
        if isinstance(key, slice):
            self.clear_range(key.start, key.stop)
        else:
            self.clear(key)

    def get_range_startswith(self, prefix: bytes, limit: Optional[int] = None) -> List[Tuple[bytes, bytes]]:
        prefix = bytes(prefix)
        # Prefix range: from prefix to prefix + \xff
        end = prefix + b'\xff'
        return self.get_range(prefix, end, limit=limit)

    def commit(self):
        if self.aborted or self.committed:
            raise FDBTransactionError("Transaction already closed")
        with self.engine.lock:
            if self.engine.version != self.read_version:
                raise FDBConflictError("Local snapshot changed; retry required")
            if not (self.writes or self.clears or self.clear_ranges):
                self.committed = True
                return
            candidate = self.snapshot.copy()
            for begin, end in self.clear_ranges:
                for key in list(candidate.irange(begin, end, inclusive=(True, False))):
                    candidate.pop(key, None)
            for key in self.clears:
                candidate.pop(key, None)
            candidate.update(self.writes)
            previous = self.engine.data
            self.engine.data = candidate
            try:
                self.engine.save_to_disk()
            except Exception:
                self.engine.data = previous
                raise
            self.engine.version += 1
            self.committed = True

    def rollback(self):
        self.aborted = True
        self.writes.clear()
        self.clears.clear()
        self.clear_ranges.clear()


class FDBLocalEngine:
    """Local teaching emulator. Not a distributed FoundationDB implementation.
    
    Adheres to FoundationDB concepts:
    - Byte-ordered Key-Value storage
    - Subspace prefixing
    - ACID multi-key atomic transactions
    - Range scans and limits
    """
    def __init__(self, storage_file: Optional[str] = None):
        self.storage_file = storage_file
        self.lock = threading.RLock()
        self.data: SortedDict = SortedDict()
        self.version = 0
        self.load_from_disk()

    def load_from_disk(self):
        if not self.storage_file or not os.path.exists(self.storage_file):
            return
        try:
            with open(self.storage_file, 'r', encoding='utf-8') as f:
                raw_dict = json.load(f)
                with self.lock:
                    self.data.clear()
                    for hex_k, hex_v in raw_dict.items():
                        self.data[bytes.fromhex(hex_k)] = bytes.fromhex(hex_v)
        except Exception as e:
            print(f"[FDBLocalEngine] Warning loading storage file: {e}")

    def save_to_disk(self):
        if not self.storage_file:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.storage_file)), exist_ok=True)
            export_dict = {}
            with self.lock:
                for k, v in self.data.items():
                    export_dict[k.hex()] = v.hex()
            
            tmp_file = self.storage_file + '.tmp'
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(export_dict, f, indent=2)
            if os.path.exists(self.storage_file):
                os.replace(tmp_file, self.storage_file)
            else:
                os.rename(tmp_file, self.storage_file)
        except Exception as e:
            raise FDBTransactionError(f"Cannot persist local snapshot: {e}") from e

    def create_transaction(self) -> LocalTransaction:
        return LocalTransaction(self)

    def transactional(self, func: Callable):
        """Decorator to execute a function in an atomic ACID transaction.
        Supports the service call convention of fdb.transactional:
        If invoked as func(db, *args), it begins a transaction `tr` and calls func(tr, *args).
        If invoked as func(tr, *args), it directly calls func(tr, *args).
        """
        def wrapper(*args, **kwargs):
            if len(args) > 0 and isinstance(args[0], LocalTransaction):
                return func(*args, **kwargs)
            
            for attempt in range(20):
                tr = self.create_transaction()
                call_args = (tr,) + args[1:] if args and isinstance(args[0], FDBLocalEngine) else (tr,) + args
                try:
                    result = func(*call_args, **kwargs)
                    tr.commit()
                    return result
                except FDBConflictError:
                    tr.rollback()
                    if attempt == 19:
                        raise
                    time.sleep(min(0.001 * (attempt + 1), 0.02))
                except Exception:
                    tr.rollback()
                    raise
        return wrapper

    # Non-transactional helper shortcuts
    def get(self, key: Any) -> Optional[bytes]:
        with self.lock:
            return self.data.get(_to_bytes(key), None)

    def set(self, key: Any, value: bytes):
        tr = self.create_transaction()
        tr.set(key, value)
        tr.commit()

    def clear(self, key: Any):
        tr = self.create_transaction()
        tr.clear(key)
        tr.commit()

    def clear_all(self):
        with self.lock:
            self.data.clear()
            self.version += 1
            self.save_to_disk()

    def get_range(self, begin: Any, end: Any, limit: Optional[int] = None) -> List[Tuple[bytes, bytes]]:
        tr = self.create_transaction()
        return tr.get_range(begin, end, limit=limit)

    def count_keys(self) -> int:
        with self.lock:
            return len(self.data)
