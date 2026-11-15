# Nguyên văn code thay đổi

## app/config.py

```python
import os

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'nosql-foundationdb-ecommerce-secret-key-2026')
    FDB_CLUSTER_FILE = os.environ.get('FDB_CLUSTER_FILE', os.path.join(DATA_DIR, 'fdb.cluster'))
    FDB_API_VERSION = int(os.environ.get('FDB_API_VERSION', '630'))
    
    # Storage persistence path for emulated FDB engine (sorted KV snapshot)
    FDB_LOCAL_STORAGE_FILE = os.environ.get('FDB_LOCAL_STORAGE_FILE', os.path.join(DATA_DIR, 'foundationdb_storage.json'))
    
    # Auto-seed mock data on startup if database is empty
    AUTO_SEED = os.environ.get('AUTO_SEED', '0') == '1'
    FDB_MODE = os.environ.get('FDB_MODE', 'auto').lower()
    
    # Server configuration
    HOST = '127.0.0.1'
    PORT = int(os.environ.get('PORT', 5001))
    DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1'

```

## app/database/connection.py

```python
import os
import time
from typing import Optional, Any
from app.config import Config
from app.database.fdb_engine import FDBLocalEngine

class FoundationDBConnection:
    """Manages FoundationDB connection and abstraction layer.
    Fulfills Mục 8: Kết nối CSDL với ứng dụng (0.5 điểm).
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(FoundationDBConnection, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, cluster_file: Optional[str] = None):
        if self._initialized:
            return
        
        self.cluster_file = cluster_file or Config.FDB_CLUSTER_FILE
        self.mode = "LOCAL_EMULATED"  # "REAL_FDB" or "LOCAL_EMULATED"
        self.is_connected = False
        self.error_message = None
        self.connected_at = None
        self.real_db = None
        self.local_engine = None

        self._initialize_connection()
        self._initialized = True

    def _initialize_connection(self):
        """Attempts to connect to FoundationDB cluster if available, otherwise initializes local engine."""
        print("[FDB Connection] Initializing FoundationDB connection...")
        
        # Step 1: Check if real fdb driver with C API is available
        try:
            if Config.FDB_MODE == "local":
                raise RuntimeError("Explicit local demonstration mode")
            if Config.FDB_MODE not in {"auto", "real", "local"}:
                raise ValueError("FDB_MODE must be auto, real or local")
            import fdb
            # Try to select api version
            fdb.api_version(Config.FDB_API_VERSION)
            if os.path.exists(self.cluster_file):
                print(f"[FDB Connection] Found cluster file at {self.cluster_file}. Connecting...")
                from app.database.real_adapter import RealDatabaseAdapter
                raw_db = fdb.open(self.cluster_file)
                raw_db.options.set_transaction_timeout(5000)
                raw_db.options.set_transaction_retry_limit(10)
                self.real_db = RealDatabaseAdapter(raw_db)
                self.real_db.get(b'__connection_probe__')  # Force a real round-trip.
                self.mode = "REAL_FDB"
                self.is_connected = True
                self.connected_at = time.time()
                print("[FDB Connection] Connected successfully to FoundationDB Cluster!")
                return
            else:
                print(f"[FDB Connection] Cluster file not found at {self.cluster_file}.")
        except Exception as e:
            self.error_message = str(e)
            print(f"[FDB Connection] Real FoundationDB driver unavailable: {e}")

        if Config.FDB_MODE == "real":
            raise RuntimeError(f"FoundationDB connection failed: {self.error_message or self.cluster_file}")
        # Step 2: Fallback to single-process demo Local FDB Key-Value Engine
        print("[FDB Connection] Falling back to single-process demo FoundationDB Local Key-Value Engine.")
        self.local_engine = FDBLocalEngine(Config.FDB_LOCAL_STORAGE_FILE)
        self.mode = "LOCAL_EMULATED"
        self.is_connected = True
        self.connected_at = time.time()
        print(f"[FDB Connection] Local FDB Engine active. Storage file: {Config.FDB_LOCAL_STORAGE_FILE}")

    def get_db(self):
        """Returns the active database engine."""
        if self.mode == "REAL_FDB" and self.real_db:
            return self.real_db
        return self.local_engine

    def transactional(self, func):
        return self.get_db().transactional(func)

    def get_status_info(self) -> dict:
        """Returns detailed connection diagnostic information for the Dashboard."""
        status = {
            "mode": self.mode,
            "is_connected": self.is_connected,
            "driver_name": "apple/foundationdb (C-API)" if self.mode == "REAL_FDB" else "Local emulator (single process; not FoundationDB)",
            "cluster_file": self.cluster_file,
            "connected_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.connected_at)) if self.connected_at else None,
            "error_detail": self.error_message,
            "total_keys": self.local_engine.count_keys() if self.local_engine else None,
            "api_version": Config.FDB_API_VERSION
        }
        return status

# Global singleton
db_manager = FoundationDBConnection()
get_db = db_manager.get_db
transactional = db_manager.transactional

```

## app/database/fdb_engine.py

```python
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

```

## app/database/real_adapter.py

```python
"""Synchronous service facade; official FDB decorator owns commit and retry."""
from functools import wraps

class RealTransactionAdapter:
    def __init__(self, tr):
        self.tr = tr

    def get(self, key):
        return self.tr.get(key).wait()

    def set(self, key, value):
        self.tr.set(key, value)

    def clear(self, key):
        self.tr.clear(key)

    def clear_range(self, begin, end):
        self.tr.clear_range(begin, end)

    def get_range(self, begin, end, limit=None, reverse=False):
        return [(kv.key, kv.value) for kv in self.tr.get_range(
            begin, end, limit=0 if limit is None else limit, reverse=reverse)]

    def __getitem__(self, key):
        return self.get_range(key.start, key.stop) if isinstance(key, slice) else self.get(key)

    def __setitem__(self, key, value):
        self.set(key, value)

class RealDatabaseAdapter:
    def __init__(self, db):
        self.db = db

    def transactional(self, func):
        import fdb
        @fdb.transactional
        def execute(tr, *args, **kwargs):
            return func(RealTransactionAdapter(tr), *args, **kwargs)
        @wraps(func)
        def wrapper(db_or_tr, *args, **kwargs):
            if isinstance(db_or_tr, RealTransactionAdapter):
                return func(db_or_tr, *args, **kwargs)
            return execute(self.db, *args, **kwargs)
        return wrapper

    def get(self, key):
        return self.transactional(lambda tr: tr.get(key))(self)

    def set(self, key, value):
        return self.transactional(lambda tr: tr.set(key, value))(self)

    def get_range(self, begin, end, limit=None, reverse=False):
        return self.transactional(lambda tr: tr.get_range(begin, end, limit, reverse))(self)

```

## app/database/schema.py

```python
import json
from fdb import tuple as fdb_tuple

# Exact Tuple Schema from Member B's Design (BÁO CÁO PHẦN 2)
# 1. User: ('user', user_id)
# 2. Order: ('order', order_id)
# 3. Order Item: ('order_item', order_id, item_id)
# 4. Transaction: ('transaction', txn_id)
# 5. Order History: ('order_history', order_id, timestamp)
# 6. Secondary Index: ('idx_user_orders', user_id, order_id)
# 7. Secondary Index: ('idx_status_orders', status, created_at, order_id)
# 8. Product: ('product', product_id)

def pack_key(tup: tuple) -> bytes:
    """Pack a python tuple into FoundationDB byte string."""
    return fdb_tuple.pack(tup)

def unpack_key(key_bytes: bytes) -> tuple:
    """Unpack FoundationDB byte string into python tuple."""
    return fdb_tuple.unpack(key_bytes)

def get_prefix_range(prefix_tuple: tuple) -> tuple:
    """Returns (start_key, end_key) for range scanning by prefix."""
    r = fdb_tuple.range(prefix_tuple)
    return r.start, r.stop

# Entity Key Packers
def user_key(user_id: str) -> bytes:
    return pack_key(('user', str(user_id).strip()))

def order_key(order_id: str) -> bytes:
    return pack_key(('order', str(order_id).strip()))

def order_item_key(order_id: str, item_id: str) -> bytes:
    return pack_key(('order_item', str(order_id).strip(), str(item_id).strip()))

def transaction_key(txn_id: str) -> bytes:
    return pack_key(('transaction', str(txn_id).strip()))

def order_history_key(order_id: str, timestamp: str) -> bytes:
    return pack_key(('order_history', str(order_id).strip(), str(timestamp).strip()))

def product_key(product_id: str) -> bytes:
    return pack_key(('product', str(product_id).strip()))

# Secondary Index Packers
def idx_user_orders_key(user_id: str, order_id: str) -> bytes:
    return pack_key(('idx_user_orders', str(user_id).strip(), str(order_id).strip()))

def idx_status_orders_key(status: str, created_at: str, order_id: str) -> bytes:
    return pack_key(('idx_status_orders', str(status).strip(), str(created_at).strip(), str(order_id).strip()))

def idx_user_email_key(email: str) -> bytes:
    return pack_key(('idx_users_email', str(email).strip().lower()))

# JSON Value Serialization
def encode_value(data: dict) -> bytes:
    """Encode dictionary into JSON UTF-8 bytes."""
    return json.dumps(data, ensure_ascii=False).encode('utf-8')

def decode_value(raw: bytes) -> dict:
    """Decode raw bytes into python dictionary."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return json.loads(raw)
    return json.loads(raw.decode('utf-8'))

# Logical subspaces retain the original packed tuple bytes; no migration required.
APP_PREFIXES = ('user', 'product', 'order', 'order_item', 'transaction',
                'order_history', 'idx_user_orders', 'idx_status_orders', 'idx_users_email')

def clear_application_data(tr):
    for prefix in APP_PREFIXES:
        tr.clear_range(*get_prefix_range((prefix,)))

```

## app/main.py

```python
import os
import json
import tempfile
from io import BytesIO
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from app.config import Config, BASE_DIR
from app.database import db_manager
from app.models.user import User
from app.models.product import Product
from app.models.order import OrderStatus
from app.services.user_service import UserService
from app.services.product_service import ProductService
from app.services.order_service import OrderService
from app.services.analytics_service import AnalyticsService
from app.services.seed_service import SeedService
from app.services.backup_service import BackupService

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024

    # Auto-seed if database is empty
    with app.app_context():
        status = db_manager.get_status_info()
        if Config.AUTO_SEED:
            print("[Startup] Database is empty. Running initial auto-seed...")
            try:
                SeedService.populate_seed_data(clear_existing=False)
            except Exception as e:
                print(f"[Startup] Warning during auto-seed: {e}")

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify({'status': 'error', 'message': str(error)}), 400

    # Context processor to inject database status into all templates
    @app.context_processor
    def inject_global_data():
        return {
            "db_info": db_manager.get_status_info(),
            "all_statuses": sorted(list(OrderStatus.ALL))
        }

    # ==========================
    # WEB UI ROUTES
    # ==========================

    @app.route('/')
    def dashboard():
        summary = AnalyticsService.get_dashboard_summary()
        orders = OrderService.get_all_orders()
        recent_orders = orders[:6]
        return render_template('dashboard.html', active_page='dashboard', summary=summary, recent_orders=recent_orders)

    @app.route('/users')
    def users_page():
        users = UserService.get_all_users()
        return render_template('users.html', active_page='users', users=users)

    @app.route('/products')
    def products_page():
        category = request.args.get('category')
        all_products = ProductService.get_all_products()
        categories = sorted(list(set(p.category for p in all_products if p.category)))

        if category:
            products = ProductService.get_products_by_category(category)
        else:
            products = all_products

        return render_template('products.html', active_page='products', products=products, categories=categories, selected_cat=category)

    @app.route('/orders')
    def orders_page():
        status = request.args.get('status')
        from_date = request.args.get('from_date')
        to_date = request.args.get('to_date')
        search_q = request.args.get('q')

        orders = AnalyticsService.filter_orders(
            status=status,
            from_date=from_date,
            to_date=to_date,
            search_query=search_q
        )

        users = UserService.get_all_users()
        products = ProductService.get_all_products()
        products_json = [p.to_dict() for p in products]

        return render_template(
            'orders.html',
            active_page='orders',
            orders=orders,
            users=users,
            products=products,
            products_json=products_json,
            filter_status=status or 'ALL',
            filter_from_date=from_date or '',
            filter_to_date=to_date or '',
            filter_q=search_q or ''
        )

    @app.route('/orders/<order_id>')
    def order_detail_page(order_id):
        order = OrderService.get_order_by_id(order_id)
        if not order:
            return "Không tìm thấy đơn hàng", 404

        user = UserService.get_user_by_id(order.user_id)
        history = OrderService.get_order_history(order_id)
        transactions = OrderService.get_order_transactions(order_id)
        order_json = json.dumps(order.to_dict(), indent=2, ensure_ascii=False)

        return render_template(
            'order_detail.html',
            active_page='orders',
            order=order,
            user=user,
            history=history,
            transactions=transactions,
            order_json=order_json
        )

    @app.route('/kv-explorer')
    def kv_explorer_page():
        kv_items = ProductService.get_raw_key_values(limit=150)
        return render_template('kv_explorer.html', active_page='kv_explorer', kv_items=kv_items)

    @app.route('/reports')
    def reports_page():
        summary = AnalyticsService.get_dashboard_summary()
        return render_template('reports.html', active_page='reports', summary=summary)

    # ==========================
    # REST API ENDPOINTS
    # ==========================

    @app.route('/api/users', methods=['POST'])
    def api_create_user():
        try:
            data = request.get_json() or {}
            user = User.from_dict(data)
            created = UserService.create_user(user)
            return jsonify({"status": "success", "user": created.to_dict()}), 201
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/users/<user_id>', methods=['PUT'])
    def api_update_user(user_id):
        try:
            updates = request.get_json() or {}
            updated = UserService.update_user(user_id, updates)
            return jsonify({"status": "success", "user": updated.to_dict()})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/users/<user_id>', methods=['DELETE'])
    def api_delete_user(user_id):
        try:
            res = UserService.delete_user(user_id)
            if res:
                return jsonify({"status": "success"})
            return jsonify({"status": "error", "message": "Không tìm thấy người dùng"}), 404
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/products', methods=['POST'])
    def api_create_product():
        try:
            data = request.get_json() or {}
            product = Product.from_dict(data)
            created = ProductService.create_product(product)
            return jsonify({"status": "success", "product": created.to_dict()}), 201
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/products/<product_id>', methods=['PUT'])
    def api_update_product(product_id):
        try:
            updates = request.get_json() or {}
            updated = ProductService.update_product(product_id, updates)
            return jsonify({"status": "success", "product": updated.to_dict()})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/products/<product_id>', methods=['DELETE'])
    def api_delete_product(product_id):
        try:
            res = ProductService.delete_product(product_id)
            if res:
                return jsonify({"status": "success"})
            return jsonify({"status": "error", "message": "Không tìm thấy sản phẩm"}), 404
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/orders', methods=['POST'])
    def api_create_order():
        try:
            data = request.get_json() or {}
            user_id = data.get('user_id')
            shipping_address = data.get('shipping_address')
            items_data = data.get('items', [])
            custom_id = data.get('custom_order_id')

            order = OrderService.create_order(
                user_id=user_id,
                items_data=items_data,
                shipping_address=shipping_address,
                custom_order_id=custom_id
            )
            return jsonify({"status": "success", "order": order.to_dict()}), 201
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/orders/<order_id>/pay', methods=['POST'])
    def api_pay_order(order_id):
        try:
            data = request.get_json() or {}
            method = data.get('payment_method', 'E_WALLET')
            is_success = data.get('is_success', True)
            note = data.get('note', '')

            result = OrderService.process_payment(order_id, method, is_success=is_success, note=note, request_id=data.get("request_id"))
            return jsonify({"status": "success", "result": result})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/orders/<order_id>/status', methods=['POST'])
    def api_update_order_status(order_id):
        try:
            data = request.get_json() or {}
            new_status = data.get('new_status')
            operator = data.get('operator', 'ADMIN')
            note = data.get('note', '')

            order = OrderService.update_order_status(order_id, new_status, operator=operator, note=note)
            return jsonify({"status": "success", "order": order.to_dict()})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    @app.route('/api/acid-test', methods=['POST'])
    def api_acid_test():
        try:
            result = AnalyticsService.simulate_acid_transaction_test()
            return jsonify(result)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/api/concurrent-test', methods=['POST'])
    def api_concurrent_test():
        try:
            data = request.get_json() or {}
            threads = int(data.get('threads', 10))
            stock = int(data.get('stock', 1))
            result = AnalyticsService.simulate_concurrent_stress_test(num_threads=threads, target_stock=stock)
            return jsonify(result)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/api/seed', methods=['POST'])
    def api_seed():
        try:
            res = SeedService.populate_seed_data(clear_existing=True)
            return jsonify({"status": "success", "result": res})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/api/backup', methods=['GET'])
    def api_backup():
        try:
            with tempfile.TemporaryDirectory() as directory:
                backup_file = os.path.join(directory, 'snapshot.json')
                BackupService.export_backup(backup_file)
                with open(backup_file, 'rb') as stream:
                    payload = BytesIO(stream.read())
            return send_file(payload, as_attachment=True, download_name='foundationdb_ecommerce_backup.json', mimetype='application/json')
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/api/restore', methods=['POST'])
    def api_restore():
        try:
            if 'backup_file' not in request.files:
                return jsonify({"status": "error", "message": "Không tìm thấy file tải lên"}), 400
            file = request.files['backup_file']
            with tempfile.TemporaryDirectory() as directory:
                save_path = os.path.join(directory, 'restore.json')
                file.save(save_path)
                res = BackupService.restore_backup(save_path)
            return jsonify({"status": "success", "restored_records": res['restored_records']})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    from app.services.query_service import QueryService

    @app.route('/queries')
    def queries_page():
        return render_template('queries.html', active_page='queries')

    @app.route('/api/queries/orders')
    def query_orders():
        try:
            return jsonify(QueryService.orders_by_status_time(
                request.args.get('status', 'SUCCESS'), request.args.get('from_date'),
                request.args.get('to_date'), int(request.args.get('limit', 50)), request.args.get('cursor')))
        except (ValueError, TypeError) as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/queries/transactions/summary')
    def query_transactions():
        try:
            return jsonify(QueryService.transaction_summary(request.args.get('from_date'), request.args.get('to_date')))
        except (ValueError, TypeError) as e:
            return jsonify({'error': str(e)}), 400

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)

```

## app/models/order.py

```python
from datetime import datetime
from typing import List, Dict, Any, Optional

class OrderStatus:
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CREATED = "PENDING"  # Map CREATED to PENDING for Member B compatibility
    PAID = "SUCCESS"     # Map PAID to SUCCESS for Member B compatibility
    PROCESSING = "PROCESSING"
    SHIPPING = "SHIPPING"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    RETURNED = "RETURNED"

    ALL = {PENDING, SUCCESS, FAILED, CANCELLED, PROCESSING, SHIPPING, DELIVERED, COMPLETED, RETURNED}

    # Allowed state transitions
    TRANSITIONS = {
        PENDING: [SUCCESS, FAILED, CANCELLED],
        FAILED: [PENDING, CANCELLED],
        SUCCESS: [PROCESSING],
        PROCESSING: [SHIPPING],
        SHIPPING: [DELIVERED],
        DELIVERED: [COMPLETED],
        COMPLETED: [], CANCELLED: [], RETURNED: []
    }

    @classmethod
    def can_transition(cls, from_status: str, to_status: str) -> bool:
        # Permissive transition check
        return to_status in cls.TRANSITIONS.get(from_status, []) or from_status == to_status


class OrderItem:
    def __init__(self, product_id: str, product_name: str, quantity: int, unit_price: float):
        self.product_id = product_id
        self.product_name = product_name
        self.quantity = int(quantity)
        self.unit_price = float(unit_price)
        self.subtotal = self.quantity * self.unit_price

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "subtotal": self.subtotal
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrderItem':
        return cls(
            product_id=data.get("product_id", ""),
            product_name=data.get("product_name", ""),
            quantity=data.get("quantity", 1),
            unit_price=data.get("unit_price", 0.0)
        )


class OrderStatusHistory:
    def __init__(
        self,
        history_id: str,
        order_id: str,
        previous_status: Optional[str],
        new_status: str,
        action: str,
        operator: str = "SYSTEM",
        note: str = "",
        timestamp: Optional[str] = None
    ):
        self.history_id = history_id
        self.order_id = order_id
        self.previous_status = previous_status
        self.new_status = new_status
        self.action = action
        self.operator = operator
        self.note = note
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "history_id": self.history_id,
            "order_id": self.order_id,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "action": self.action,
            "operator": self.operator,
            "note": self.note,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrderStatusHistory':
        return cls(
            history_id=data.get("history_id", ""),
            order_id=data.get("order_id", ""),
            previous_status=data.get("previous_status"),
            new_status=data.get("new_status", ""),
            action=data.get("action", ""),
            operator=data.get("operator", "SYSTEM"),
            note=data.get("note", ""),
            timestamp=data.get("timestamp")
        )


class Order:
    def __init__(
        self,
        order_id: str,
        user_id: str,
        items: List[OrderItem],
        shipping_address: str,
        status: str = OrderStatus.CREATED,
        total_amount: Optional[float] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None
    ):
        self.order_id = order_id.strip()
        self.user_id = user_id.strip()
        self.items = items
        self.shipping_address = shipping_address.strip()
        self.status = status if status in OrderStatus.ALL else OrderStatus.CREATED
        self.total_amount = total_amount if total_amount is not None else sum(item.subtotal for item in self.items)
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.updated_at = updated_at or self.created_at

    def validate(self):
        if not self.order_id:
            raise ValueError("Mã đơn hàng (Order ID) không được để trống")
        if not self.user_id:
            raise ValueError("Mã khách hàng (User ID) không được để trống")
        if not self.items or len(self.items) == 0:
            raise ValueError("Đơn hàng phải có ít nhất một sản phẩm")
        for item in self.items:
            if item.quantity <= 0:
                raise ValueError(f"Số lượng sản phẩm '{item.product_name}' phải lớn hơn 0")
        if not self.shipping_address:
            raise ValueError("Địa chỉ giao hàng không được để trống")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "user_id": self.user_id,
            "items": [item.to_dict() for item in self.items],
            "shipping_address": self.shipping_address,
            "status": self.status,
            "total_amount": self.total_amount,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Order':
        items = [OrderItem.from_dict(it) for it in data.get("items", [])]
        return cls(
            order_id=data.get("order_id", ""),
            user_id=data.get("user_id", ""),
            items=items,
            shipping_address=data.get("shipping_address", ""),
            status=data.get("status", OrderStatus.CREATED),
            total_amount=data.get("total_amount"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at")
        )

```

## app/models/product.py

```python
import math
from datetime import datetime
from typing import Optional, Dict, Any

class Product:
    VALID_STATUSES = {'available', 'out_of_stock', 'discontinued'}

    def __init__(
        self,
        product_id: str,
        name: str,
        category: str,
        price: float,
        stock: int,
        description: str = "",
        status: Optional[str] = None,
        updated_at: Optional[str] = None
    ):
        self.product_id = product_id.strip()
        self.name = name.strip()
        self.category = category.strip()
        self.price = float(price)
        self.stock = int(stock)
        self.description = description.strip()
        if status:
            self.status = status.lower()
        else:
            self.status = "available" if self.stock > 0 else "out_of_stock"
        self.updated_at = updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def validate(self):
        """Validate product data."""
        if not self.product_id:
            raise ValueError("Mã sản phẩm (Product ID) không được để trống")
        if not self.name:
            raise ValueError("Tên sản phẩm không được để trống")
        if not math.isfinite(self.price) or self.price < 0:
            raise ValueError("Giá sản phẩm không được là số âm")
        if self.stock < 0:
            raise ValueError("Số lượng tồn kho không được là số âm")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "category": self.category,
            "price": self.price,
            "stock": self.stock,
            "description": self.description,
            "status": self.status,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Product':
        return cls(
            product_id=data.get("product_id", ""),
            name=data.get("name", ""),
            category=data.get("category", "Chung"),
            price=data.get("price", 0.0),
            stock=data.get("stock", 0),
            description=data.get("description", ""),
            status=data.get("status"),
            updated_at=data.get("updated_at")
        )

```

## app/models/transaction.py

```python
from datetime import datetime
from typing import Optional, Dict, Any

class PaymentTransaction:
    VALID_STATUSES = {'PENDING', 'SUCCESS', 'FAILED', 'REFUNDED'}
    VALID_METHODS = {'COD', 'CREDIT_CARD', 'E_WALLET', 'BANK_TRANSFER', 'MOMO', 'VNPAY'}

    def __init__(
        self,
        txn_id: str,
        order_id: str,
        user_id: str,
        amount: float,
        payment_method: str = "E_WALLET",
        status: str = "PENDING",
        gateway_code: str = "",
        note: str = "",
        created_at: Optional[str] = None
    ):
        self.txn_id = txn_id.strip()
        self.order_id = order_id.strip()
        self.user_id = user_id.strip()
        self.amount = float(amount)
        self.payment_method = payment_method.upper() if payment_method.upper() in self.VALID_METHODS else "E_WALLET"
        self.status = status.upper() if status.upper() in self.VALID_STATUSES else "PENDING"
        self.gateway_code = gateway_code.strip()
        self.note = note.strip()
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def validate(self):
        if not self.txn_id:
            raise ValueError("Mã giao dịch (Transaction ID) không được để trống")
        if not self.order_id:
            raise ValueError("Mã đơn hàng (Order ID) không được để trống")
        if self.amount <= 0:
            raise ValueError("Số tiền giao dịch phải lớn hơn 0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "order_id": self.order_id,
            "user_id": self.user_id,
            "amount": self.amount,
            "payment_method": self.payment_method,
            "status": self.status,
            "gateway_code": self.gateway_code,
            "note": self.note,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PaymentTransaction':
        return cls(
            txn_id=data.get("txn_id", ""),
            order_id=data.get("order_id", ""),
            user_id=data.get("user_id", ""),
            amount=data.get("amount", 0.0),
            payment_method=data.get("payment_method", "E_WALLET"),
            status=data.get("status", "PENDING"),
            gateway_code=data.get("gateway_code", ""),
            note=data.get("note", ""),
            created_at=data.get("created_at")
        )

```

## app/models/user.py

```python
import math
import re
from datetime import datetime
from typing import Optional, Dict, Any

class User:
    VALID_ROLES = {'customer', 'admin', 'merchant'}
    VALID_STATUSES = {'active', 'locked'}

    def __init__(
        self,
        user_id: str,
        name: str,
        email: str,
        phone: str = "",
        role: str = "customer",
        status: str = "active",
        balance: float = 0.0,
        address: str = "",
        created_at: Optional[str] = None
    ):
        self.user_id = user_id.strip()
        self.name = name.strip()
        self.email = email.strip().lower()
        self.phone = phone.strip()
        self.role = role.lower() if role.lower() in self.VALID_ROLES else "customer"
        self.status = status.lower() if status.lower() in self.VALID_STATUSES else "active"
        self.balance = float(balance)
        self.address = address.strip()
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def validate(self):
        """Validate user data. Raises ValueError if invalid."""
        if not self.user_id:
            raise ValueError("Mã người dùng (User ID) không được để trống")
        if not self.name:
            raise ValueError("Họ và tên người dùng không được để trống")
        if not self.email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", self.email):
            raise ValueError(f"Email không hợp lệ: '{self.email}'")
        if not math.isfinite(self.balance) or self.balance < 0:
            raise ValueError("Số dư tài khoản không được là số âm")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "role": self.role,
            "status": self.status,
            "balance": self.balance,
            "address": self.address,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        return cls(
            user_id=data.get("user_id", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            role=data.get("role", "customer"),
            status=data.get("status", "active"),
            balance=data.get("balance", 0.0),
            address=data.get("address", ""),
            created_at=data.get("created_at")
        )

```

## app/services/analytics_service.py

```python
from datetime import datetime
from typing import Dict, Any, List, Optional
from app.services.user_service import UserService
from app.services.product_service import ProductService
from app.services.order_service import OrderService
from app.models.product import Product
from app.models.order import OrderStatus, Order
from app.database import db_manager, get_db

class AnalyticsService:
    """Implements Mục 9.4: Chức năng hỗ trợ (0.5 điểm).
    Provides multi-criteria search, date/status filtering, aggregated metrics,
    and ACID transaction integrity diagnostics.
    """

    @staticmethod
    def get_dashboard_summary() -> Dict[str, Any]:
        """Calculates global dashboard statistics."""
        users = UserService.get_all_users()
        products = ProductService.get_all_products()
        orders = OrderService.get_all_orders()
        transactions = OrderService.get_all_transactions()

        # Order status distribution
        status_counts = {s: 0 for s in OrderStatus.ALL}
        total_revenue = 0.0

        for ord in orders:
            status_counts[ord.status] = status_counts.get(ord.status, 0) + 1
            if ord.status in [OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.SHIPPING, OrderStatus.DELIVERED, OrderStatus.COMPLETED]:
                total_revenue += ord.total_amount

        # Transaction success vs failed
        txn_stats = {"SUCCESS": 0, "FAILED": 0, "PENDING": 0, "REFUNDED": 0}
        total_txn_amount = 0.0
        for t in transactions:
            txn_stats[t.status] = txn_stats.get(t.status, 0) + 1
            if t.status == "SUCCESS":
                total_txn_amount += t.amount

        total_txns = len(transactions)
        success_rate = (txn_stats["SUCCESS"] / total_txns * 100) if total_txns > 0 else 0.0

        # Total products and inventory
        total_stock = sum(p.stock for p in products)
        out_of_stock_count = sum(1 for p in products if p.stock <= 0)

        # Connection status
        db_status = db_manager.get_status_info()

        return {
            "total_users": len(users),
            "total_products": len(products),
            "total_stock": total_stock,
            "out_of_stock_count": out_of_stock_count,
            "total_orders": len(orders),
            "total_revenue": total_revenue,
            "status_counts": status_counts,
            "total_transactions": total_txns,
            "txn_stats": txn_stats,
            "success_rate": round(success_rate, 1),
            "total_txn_amount": total_txn_amount,
            "db_status": db_status
        }

    @staticmethod
    def filter_orders(
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
        search_query: Optional[str] = None
    ) -> List[Order]:
        """Advanced multi-criteria filtering for orders."""
        from datetime import date
        first = date.fromisoformat(from_date) if from_date else None
        last = date.fromisoformat(to_date) if to_date else None
        if first and last and first > last:
            raise ValueError("Ngày bắt đầu phải <= ngày kết thúc")
        if status and status.upper() != 'ALL':
            from app.services.query_service import QueryService
            from app.database import get_prefix_range, unpack_key
            start, stop = QueryService.bounds(status, from_date, to_date)
            @db_manager.transactional
            def read_candidates(tr):
                return [OrderService.get_order_by_id(unpack_key(k)[3], tr)
                        for k, _ in tr.get_range(start, stop)]
            orders = [o for o in read_candidates(get_db()) if o is not None]
        elif user_id:
            orders = OrderService.get_orders_by_user(user_id)
        else:
            orders = OrderService.get_all_orders()
        filtered = []

        q = search_query.strip().lower() if search_query else None
        u_id = user_id.strip() if user_id else None
        st = status.strip().upper() if status else None

        for o in orders:
            # Filter by status
            if st and st != "ALL" and o.status != st:
                continue

            # Filter by user_id
            if u_id and o.user_id != u_id:
                continue

            # Filter by date range (format: YYYY-MM-DD)
            ord_date = o.created_at[:10]
            if from_date and ord_date < from_date:
                continue
            if to_date and ord_date > to_date:
                continue

            # Search query matching order_id, user_id, shipping_address, or items
            if q:
                match_id = q in o.order_id.lower()
                match_user = q in o.user_id.lower()
                match_addr = q in o.shipping_address.lower()
                match_item = any(q in item.product_name.lower() or q in item.product_id.lower() for item in o.items)
                if not (match_id or match_user or match_addr or match_item):
                    continue

            filtered.append(o)

        return filtered

    @staticmethod
    def simulate_acid_transaction_test() -> Dict[str, Any]:
        """Educational demonstration of FoundationDB ACID Atomic Rollback.
        Attempts to create an order with 1 available product and 1 out-of-stock product.
        Verifies that NO partial writes occur and stock remains unchanged!
        """
        products = ProductService.get_all_products()
        users = UserService.get_all_users()
        if not products or not users:
            return {"status": "error", "message": "Cần nạp dữ liệu mẫu trước khi chạy kiểm thử ACID"}

        valid_user = next((u.user_id for u in users if u.status == 'active'), None)
        if not valid_user:
            return {"status": "error", "message": "Cần khách hàng đang hoạt động"}
        from uuid import uuid4
        test_order_id = 'TEST-ACID-' + uuid4().hex
        valid_prod = next((p for p in products if p.stock > 0 and p.status != 'discontinued'), None)
        if valid_prod is None:
            return {"status": "error", "message": "Cần sản phẩm còn hàng để kiểm tra rollback sau ghi"}
        initial_stock = valid_prod.stock

        # Create simulated order with 1 valid item and 1 invalid item (huge quantity exceeding stock)
        invalid_qty = initial_stock + 999
        test_items = [
            {"product_id": valid_prod.product_id, "quantity": 1},
            {"product_id": valid_prod.product_id, "quantity": invalid_qty}
        ]

        error_caught = None
        try:
            OrderService.create_order(
                user_id=valid_user,
                items_data=test_items,
                shipping_address="ACID Rollback Test Address",
                custom_order_id=test_order_id
            )
        except Exception as e:
            error_caught = str(e)

        # Check stock afterwards
        refreshed_prod = ProductService.get_product_by_id(valid_prod.product_id)
        final_stock = refreshed_prod.stock
        order_created = OrderService.get_order_by_id(test_order_id)

        success = (error_caught is not None) and (final_stock == initial_stock) and (order_created is None)

        return {
            "test_name": "Kiểm thử Tính toàn vẹn ACID & Tự động Rollback khi thiếu tồn kho",
            "tested_product": valid_prod.product_id,
            "initial_stock": initial_stock,
            "attempted_deduction": 1 + invalid_qty,
            "error_message": error_caught,
            "final_stock": final_stock,
            "order_created": order_created is not None,
            "acid_integrity_maintained": success,
            "conclusion": "Thành công! Giao dịch trên backend hiện tại đã rollback toàn bộ các thao tác ghi dở dang, đảm bảo tính nguyên tử (Atomicity) 100%." if success else "Lỗi toàn vẹn!"
        }

    @staticmethod
    def simulate_concurrent_stress_test(num_threads: int = 10, target_stock: int = 1) -> Dict[str, Any]:
        """Educational demonstration of FoundationDB Concurrency & Race Condition Prevention.
        Simulates a Flash Sale scenario:
        - 1 limited product has ONLY `target_stock` items left in inventory.
        - `num_threads` users concurrently attempt to purchase that item at the exact same millisecond.
        - FoundationDB Strict Serializable ACID ensures NO OVERSELLING:
          Exactly `target_stock` transactions succeed, and `num_threads - target_stock` are safely rejected!
        """
        import time
        from concurrent.futures import ThreadPoolExecutor

        users = UserService.get_all_users()
        if not users:
            return {"status": "error", "message": "Cần nạp dữ liệu khách hàng trước khi chạy kiểm thử"}

        # 1. Create or reset a dedicated Flash Sale test product
        from uuid import uuid4
        import threading
        if not 1 <= num_threads <= 50 or not 1 <= target_stock <= num_threads:
            raise ValueError("1 <= stock <= threads <= 50")
        run_id = uuid4().hex
        barrier = threading.Barrier(num_threads)
        flash_pid = "P-FLASH-" + run_id
        existing = ProductService.get_product_by_id(flash_pid)
        if existing:
            flash_prod = ProductService.update_product(flash_pid, {
                "name": "Tai nghe Gaming Pro (Flash Sale 1K)",
                "category": "FlashSale",
                "price": 1000.0,
                "stock": target_stock,
                "description": "Sản phẩm giới hạn dùng để kiểm thử tranh chấp đồng thời (Concurrency Control)"
            })
        else:
            flash_prod = Product(
                product_id=flash_pid,
                name="Tai nghe Gaming Pro (Flash Sale 1K)",
                category="FlashSale",
                price=1000.0,
                stock=target_stock,
                description="Sản phẩm giới hạn dùng để kiểm thử tranh chấp đồng thời (Concurrency Control)",
                status="available"
            )
            ProductService.create_product(flash_prod)

        # 2. Concurrently fire purchase orders from multiple threads
        logs = []
        start_time = time.time()

        def _worker(thread_idx: int):
            user_id = users[thread_idx % len(users)].user_id
            order_id = f"FLASH-{run_id}-{thread_idx+1:02d}"
            barrier.wait(timeout=10)
            try:
                OrderService.create_order(
                    user_id=user_id,
                    items_data=[{"product_id": flash_pid, "quantity": 1, "price": 1000.0}],
                    shipping_address="Hệ thống Kiểm thử Tranh chấp Đồng thời",
                    custom_order_id=order_id
                )
                return {"thread": f"Thread #{thread_idx+1:02d}", "user": user_id, "status": "SUCCESS", "message": f"Đặt mua thành công ({order_id})"}
            except Exception as e:
                return {"thread": f"Thread #{thread_idx+1:02d}", "user": user_id, "status": "REJECTED", "message": str(e)}

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_worker, i) for i in range(num_threads)]
            for f in futures:
                logs.append(f.result())

        elapsed_ms = round((time.time() - start_time) * 1000, 2)

        # 3. Verify final inventory and results
        refreshed_prod = ProductService.get_product_by_id(flash_pid)
        final_stock = refreshed_prod.stock if refreshed_prod else -999
        successful_orders = sum(1 for log in logs if log['status'] == 'SUCCESS')
        rejected_orders = sum(1 for log in logs if log['status'] == 'REJECTED')

        no_overselling = (successful_orders == target_stock) and (final_stock == 0)

        return {
            "test_name": f"Mô phỏng Flash Sale tranh chấp đồng thời ({num_threads} Luồng song song)",
            "product_id": flash_pid,
            "product_name": flash_prod.name,
            "initial_stock": target_stock,
            "concurrent_requests": num_threads,
            "successful_orders": successful_orders,
            "rejected_orders": rejected_orders,
            "final_stock": final_stock,
            "elapsed_ms": elapsed_ms,
            "no_overselling": no_overselling,
            "thread_logs": logs,
            "conclusion": (
                f"Hoàn hảo! Backend hiện tại đã ngăn chặn hiện tượng bán vượt tồn kho (Overselling). "
                f"Đúng {successful_orders} khách hàng đầu tiên mua được hàng, {rejected_orders} giao dịch đồng thời còn lại "
                f"bị từ chối an toàn khi phát hiện hết kho (tồn kho cuối = 0 cái)."
                if no_overselling else "Cảnh báo: Phát hiện lỗi toàn vẹn hoặc bán vượt tồn kho!"
            )
        }

```

## app/services/backup_service.py

```python
"""Small logical JSON snapshots of this application's prefixes, not native fdbbackup."""
import os
import json
import time
from app.database import get_db, db_manager
from app.database.schema import APP_PREFIXES, get_prefix_range, unpack_key, clear_application_data

class BackupService:
    MAX_BYTES = 1_000_000  # conservative demo transaction bound

    @staticmethod
    def export_backup(target_path):
        @db_manager.transactional
        def read(tr):
            rows = []
            size = 0
            for prefix in APP_PREFIXES:
                for k, v in tr.get_range(*get_prefix_range((prefix,)), limit=10001):
                    size += len(k) + len(v)
                    rows.append({'key_hex': k.hex(), 'val_hex': v.hex()})
                    if size > BackupService.MAX_BYTES or len(rows) > 10000:
                        raise ValueError('Snapshot demo quá lớn; dùng fdbbackup cho dữ liệu lớn')
            return rows
        rows = read(get_db())
        payload = {'metadata': {'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'export_version': '2.1', 'total_records': len(rows)}, 'records': rows}
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return {'file_path': target_path, 'total_records': len(rows), 'size_bytes': os.path.getsize(target_path)}

    @staticmethod
    def restore_backup(source_path):
        with open(source_path, encoding='utf-8') as f:
            payload = json.load(f)
        records = payload.get('records')
        if not isinstance(records, list) or len(records) > 10000:
            raise ValueError('records phải là danh sách tối đa 10000 bản ghi')
        rows, seen, size = [], set(), 0
        for rec in records:
            k, v = bytes.fromhex(rec['key_hex']), bytes.fromhex(rec['val_hex'])
            parts = unpack_key(k)
            if not parts or parts[0] not in APP_PREFIXES or k in seen:
                raise ValueError('Khóa trùng hoặc ngoài phạm vi ứng dụng')
            if len(k) > 10000 or len(v) > 100000:
                raise ValueError('Key/value vượt giới hạn')
            seen.add(k)
            size += len(k) + len(v)
            rows.append((k, v))
        if size > BackupService.MAX_BYTES:
            raise ValueError('Snapshot demo quá lớn')
        @db_manager.transactional
        def restore(tr):
            # Check existing dataset before clearing: also bounds mutation/conflict ranges.
            existing_size = 0
            for prefix in APP_PREFIXES:
                for k, v in tr.get_range(*get_prefix_range((prefix,)), limit=10001):
                    existing_size += len(k) + len(v)
                    if existing_size > BackupService.MAX_BYTES:
                        raise ValueError('Dữ liệu hiện tại quá lớn để restore bằng demo')
            clear_application_data(tr)
            for k, v in rows:
                tr.set(k, v)
        restore(get_db())
        return {'restored_records': len(rows), 'source_metadata': payload.get('metadata', {})}

```

## app/services/order_service.py

```python
from datetime import datetime
from uuid import uuid4
import math
from typing import List, Optional, Dict, Any
from app.database import (
    db_manager, get_db,
    order_key, order_item_key, transaction_key, order_history_key,
    idx_user_orders_key, idx_status_orders_key, user_key, product_key,
    get_prefix_range, unpack_key,
    encode_value, decode_value
)
from app.models.order import Order, OrderItem, OrderStatus, OrderStatusHistory
from app.models.transaction import PaymentTransaction

class OrderService:
    """Implements Mục 9.3: Quản lý nghiệp vụ.
    Synchronized 100% with Member B's Tuple Design (BÁO CÁO PHẦN 2):
    - ('order', order_id)
    - ('order_item', order_id, item_id)
    - ('transaction', txn_id)
    - ('order_history', order_id, timestamp)
    - ('idx_user_orders', user_id, order_id)
    - ('idx_status_orders', status, created_at, order_id)
    """

    @staticmethod
    def _generate_order_id() -> str:
        return "ORD" + uuid4().hex

    @staticmethod
    def _generate_txn_id() -> str:
        return "TXN" + uuid4().hex

    @staticmethod
    def create_order(user_id: str, items_data: List[Dict[str, Any]], shipping_address: str, custom_order_id: Optional[str] = None) -> Order:
        """Atomic Multi-Key Transaction:
        1. Check User
        2. Check & Deduct Stock
        3. Store Order record: ('order', order_id)
        4. Store Order Items: ('order_item', order_id, item_id)
        5. Store Indexes: ('idx_user_orders', user_id, order_id), ('idx_status_orders', status, created_at, order_id)
        6. Store History: ('order_history', order_id, timestamp)
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("Mã khách hàng không được trống")
        user_id = user_id.strip()
        if not isinstance(shipping_address, str) or not shipping_address.strip():
            raise ValueError("Địa chỉ giao hàng không được trống")
        if not items_data:
            raise ValueError("Đơn hàng phải có ít nhất 1 sản phẩm")

        db = get_db()
        order_id = custom_order_id.strip() if custom_order_id else OrderService._generate_order_id()

        @db_manager.transactional
        def _txn(tr):
            # 1. Validate User
            u_raw = tr.get(user_key(user_id))
            if u_raw is None:
                raise ValueError(f"Khách hàng '{user_id}' không tồn tại trong hệ thống")
            u_dict = decode_value(u_raw)
            if u_dict.get('status') == 'locked':
                raise ValueError(f"Tài khoản '{user_id}' đang bị khóa")

            # Check if order_id already exists
            o_key = order_key(order_id)
            if tr.get(o_key) is not None:
                raise ValueError(f"Mã đơn hàng '{order_id}' đã tồn tại")

            # 2. Check and deduct inventory
            order_items = []
            total_amount = 0.0
            now_iso = datetime.now().isoformat(timespec="microseconds")

            for idx, item_input in enumerate(items_data, start=1):
                prod_id = item_input['product_id']
                quantity = item_input['quantity']
                if isinstance(quantity, bool) or str(quantity).strip() != str(int(quantity)):
                    raise ValueError("Số lượng phải là số nguyên")
                qty = int(quantity)
                if qty <= 0:
                    raise ValueError(f"Số lượng '{prod_id}' phải lớn hơn 0")

                p_key = product_key(prod_id)
                p_raw = tr.get(p_key)
                
                if p_raw is None:
                    raise ValueError(f"Sản phẩm '{prod_id}' không tồn tại")
                # Always use the catalog price.
                unit_price = float(item_input.get('price', 0.0))
                prod_name = prod_id
                
                if p_raw is not None:
                    p_data = decode_value(p_raw)
                    if p_data.get('status') == 'discontinued':
                        raise ValueError("Sản phẩm đã ngừng kinh doanh")
                    curr_stock = p_data.get('stock', 0)
                    prod_name = p_data.get('name', prod_id)
                    unit_price = float(p_data.get('price', unit_price))
                    
                    if not math.isfinite(unit_price) or unit_price < 0:
                        raise ValueError("Giá sản phẩm không hợp lệ")
                    if curr_stock < qty:
                        raise ValueError(f"Không đủ tồn kho cho '{prod_name}'! Còn {curr_stock}, yêu cầu {qty}")
                    
                    p_data['stock'] = curr_stock - qty
                    if p_data['stock'] == 0:
                        p_data['status'] = 'out_of_stock'
                    tr.set(p_key, encode_value(p_data))

                item_id = f"IT{idx:02d}"
                order_item = OrderItem(prod_id, prod_name, qty, unit_price)
                order_items.append(order_item)
                total_amount += order_item.subtotal

                # 4. Store Order Item tuple: ('order_item', order_id, item_id)
                item_key = order_item_key(order_id, item_id)
                tr.set(item_key, encode_value({
                    "product_id": prod_id,
                    "product_name": prod_name,
                    "quantity": qty,
                    "price": unit_price
                }))

            # 3. Store Order tuple: ('order', order_id)
            order_payload = {
                "user_id": user_id,
                "total_amount": total_amount,
                "status": OrderStatus.PENDING,
                "created_at": now_iso,
                "shipping_address": shipping_address or "Địa chỉ khách hàng"
            }
            tr.set(o_key, encode_value(order_payload))

            # 5. Store Secondary Indexes
            tr.set(idx_user_orders_key(user_id, order_id), b'')
            tr.set(idx_status_orders_key(OrderStatus.PENDING, now_iso, order_id), b'')

            # 6. Store Order History tuple: ('order_history', order_id, timestamp)
            hist_key = order_history_key(order_id, now_iso)
            tr.set(hist_key, encode_value({
                "status": OrderStatus.PENDING,
                "note": "Khởi tạo đơn hàng",
                "updated_by": "System"
            }))

            order = Order(
                order_id=order_id,
                user_id=user_id,
                items=order_items,
                shipping_address=shipping_address,
                status=OrderStatus.PENDING,
                total_amount=total_amount,
                created_at=now_iso,
                updated_at=now_iso
            )
            return order

        return _txn(db)

    @staticmethod
    def get_order_by_id(order_id: str, tr=None) -> Optional[Order]:
        """Fetch order record and its order_items."""
        if tr is None:
            @db_manager.transactional
            def read_order(tr):
                return OrderService.get_order_by_id(order_id, tr)
            return read_order(get_db())
        db = tr
        raw = db.get(order_key(order_id))
        if raw is None:
            return None
        data = decode_value(raw)

        # Fetch items from ('order_item', order_id) prefix
        start, stop = get_prefix_range(('order_item', order_id))
        raw_items = db.get_range(start, stop)
        items = []
        for _, val_bytes in raw_items:
            it_data = decode_value(val_bytes)
            items.append(OrderItem(
                product_id=it_data.get("product_id", ""),
                product_name=it_data.get("product_name", it_data.get("product_id")),
                quantity=it_data.get("quantity", 1),
                unit_price=it_data.get("price", 0.0)
            ))

        return Order(
            order_id=order_id,
            user_id=data.get("user_id", ""),
            items=items,
            shipping_address=data.get("shipping_address", "Địa chỉ giao hàng"),
            status=data.get("status", OrderStatus.PENDING),
            total_amount=data.get("total_amount", 0.0),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at", data.get("created_at"))
        )

    @staticmethod
    def get_all_orders() -> List[Order]:
        db = get_db()
        start, stop = get_prefix_range(('order',))
        items = db.get_range(start, stop)
        orders = []
        for k_bytes, _ in items:
            tup = unpack_key(k_bytes)
            order_id = tup[1]
            ord_obj = OrderService.get_order_by_id(order_id)
            if ord_obj:
                orders.append(ord_obj)
        return sorted(orders, key=lambda x: x.created_at, reverse=True)

    @staticmethod
    def get_orders_by_user(user_id: str) -> List[Order]:
        """Range scan on ('idx_user_orders', user_id) index."""
        db = get_db()
        start, stop = get_prefix_range(('idx_user_orders', user_id.strip()))
        items = db.get_range(start, stop)
        orders = []
        for k_bytes, _ in items:
            tup = unpack_key(k_bytes)
            order_id = tup[2]
            ord_obj = OrderService.get_order_by_id(order_id)
            if ord_obj:
                orders.append(ord_obj)
        return sorted(orders, key=lambda x: x.created_at, reverse=True)

    @staticmethod
    def get_orders_by_status(status: str) -> List[Order]:
        """Range scan on ('idx_status_orders', status) index."""
        db = get_db()
        start, stop = get_prefix_range(('idx_status_orders', status.strip().upper()))
        items = db.get_range(start, stop)
        orders = []
        for k_bytes, _ in items:
            tup = unpack_key(k_bytes)
            order_id = tup[3]
            ord_obj = OrderService.get_order_by_id(order_id)
            if ord_obj:
                orders.append(ord_obj)
        return sorted(orders, key=lambda x: x.created_at, reverse=True)

    @staticmethod
    def get_order_history(order_id: str) -> List[OrderStatusHistory]:
        """Range scan on ('order_history', order_id) prefix."""
        db = get_db()
        start, stop = get_prefix_range(('order_history', order_id.strip()))
        items = db.get_range(start, stop)
        history = []
        for idx, (k_bytes, val_bytes) in enumerate(items, start=1):
            tup = unpack_key(k_bytes)
            timestamp = tup[2]
            h_data = decode_value(val_bytes)
            history.append(OrderStatusHistory(
                history_id=str(idx),
                order_id=order_id,
                previous_status=h_data.get("previous_status"),
                new_status=h_data.get("status", ""),
                action=f"CẬP NHẬT {h_data.get('status')}",
                operator=h_data.get("updated_by", "System"),
                note=h_data.get("note", ""),
                timestamp=timestamp
            ))
        return sorted(history, key=lambda x: x.timestamp)

    @staticmethod
    def _change_status(tr, order_id, data, new_status, operator, note, timestamp):
        previous = data['status']
        data['status'] = new_status
        data['updated_at'] = timestamp
        tr.set(order_key(order_id), encode_value(data))
        tr.clear(idx_status_orders_key(previous, data['created_at'], order_id))
        tr.set(idx_status_orders_key(new_status, data['created_at'], order_id), b'')
        tr.set(order_history_key(order_id, timestamp), encode_value({
            'previous_status': previous, 'status': new_status,
            'note': note, 'updated_by': operator
        }))

    @staticmethod
    def process_payment(order_id: str, payment_method: str, is_success: bool = True,
                        note: str = "", request_id: Optional[str] = None) -> Dict[str, Any]:
        """Demo payment receipt; no external charge or wallet debit is performed.
        Retry the same request_id to retrieve its committed result without duplicate writes.
        """
        if type(is_success) is not bool:
            raise ValueError("is_success phải là JSON boolean")
        payment_method = payment_method.strip().upper()
        if payment_method not in PaymentTransaction.VALID_METHODS:
            raise ValueError("Phương thức thanh toán không hợp lệ")
        order_id = order_id.strip()
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100):
            raise ValueError("request_id phải có 1–100 ký tự")
        txn_id = "REQ-" + request_id.strip() if request_id is not None else OrderService._generate_txn_id()
        timestamp = datetime.now().isoformat(timespec="microseconds")

        @db_manager.transactional
        def _txn(tr):
            existing = decode_value(tr.get(transaction_key(txn_id)))
            target = "SUCCESS" if is_success else "FAILED"
            if existing is not None:
                if (existing['order_id'], existing['payment_method'], existing['status']) != (order_id, payment_method, target):
                    raise ValueError("request_id đã dùng cho giao dịch khác")
                return {'transaction': existing, 'order_status': target, 'replayed': True}
            data = decode_value(tr.get(order_key(order_id)))
            if data is None:
                raise ValueError("Không tìm thấy đơn hàng")
            if data['status'] != OrderStatus.PENDING:
                raise ValueError("Chỉ thanh toán đơn PENDING; đơn FAILED cần chuyển lại PENDING")
            amount = float(data['total_amount'])
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("Số tiền thanh toán phải dương và hữu hạn")
            receipt = {
                'txn_id': txn_id, 'order_id': order_id, 'user_id': data['user_id'],
                'amount': amount, 'payment_method': payment_method, 'status': target,
                'timestamp': timestamp, 'note': note or f"Thanh toán {target} qua {payment_method}"
            }
            tr.set(transaction_key(txn_id), encode_value(receipt))
            OrderService._change_status(tr, order_id, data, target,
                                        payment_method + " Demo", receipt['note'], timestamp)
            return {'transaction': receipt, 'order_status': target, 'replayed': False}
        return _txn(get_db())

    @staticmethod
    def update_order_status(order_id: str, new_status: str, operator: str = "Admin", note: str = "") -> Order:
        if not isinstance(new_status, str) or new_status.upper().strip() not in OrderStatus.ALL:
            raise ValueError("Trạng thái không hợp lệ")
        order_id, new_status = order_id.strip(), new_status.strip().upper()
        timestamp = datetime.now().isoformat(timespec="microseconds")

        @db_manager.transactional
        def _txn(tr):
            data = decode_value(tr.get(order_key(order_id)))
            if data is None:
                raise ValueError("Không tìm thấy đơn hàng")
            previous = data['status']
            if previous == new_status:
                return OrderService.get_order_by_id(order_id, tr)
            if new_status in {OrderStatus.SUCCESS, OrderStatus.FAILED}:
                raise ValueError("Dùng chức năng thanh toán để ghi trạng thái và giao dịch đồng bộ")
            if not OrderStatus.can_transition(previous, new_status):
                raise ValueError(f"Không cho phép chuyển {previous} → {new_status}")
            if new_status == OrderStatus.CANCELLED:
                for _, raw in tr.get_range(*get_prefix_range(('order_item', order_id))):
                    item = decode_value(raw)
                    key = product_key(item['product_id'])
                    product = decode_value(tr.get(key))
                    if product is None:
                        raise ValueError("Không thể hoàn kho: sản phẩm đã bị xóa")
                    product['stock'] += int(item['quantity'])
                    if product['stock'] > 0 and product.get('status') == 'out_of_stock':
                        product['status'] = 'available'
                    tr.set(key, encode_value(product))
            OrderService._change_status(tr, order_id, data, new_status, operator, note, timestamp)
            return OrderService.get_order_by_id(order_id, tr)
        return _txn(get_db())

    @staticmethod
    def get_order_transactions(order_id: str) -> List[PaymentTransaction]:
        all_txns = OrderService.get_all_transactions()
        return [t for t in all_txns if t.order_id == order_id]

    @staticmethod
    def get_all_transactions() -> List[PaymentTransaction]:
        db = get_db()
        start, stop = get_prefix_range(('transaction',))
        items = db.get_range(start, stop)
        txns = []
        for k_bytes, val_bytes in items:
            tup = unpack_key(k_bytes)
            txn_id = tup[1]
            t_data = decode_value(val_bytes)
            txns.append(PaymentTransaction(
                txn_id=txn_id,
                order_id=t_data.get("order_id", ""),
                user_id=t_data.get("user_id", ""),
                amount=float(t_data.get("amount", 0.0)),
                payment_method=t_data.get("payment_method", "MOMO"),
                status=t_data.get("status", "SUCCESS"),
                note=t_data.get("note", ""),
                created_at=t_data.get("timestamp", "")
            ))
        return sorted(txns, key=lambda x: x.created_at, reverse=True)

```

## app/services/product_service.py

```python
import json
from typing import List, Optional, Dict, Any
from app.database import (
    db_manager, get_db,
    product_key, get_prefix_range, unpack_key,
    encode_value, decode_value
)
from app.models.product import Product

class ProductService:
    """Implements Mục 9.2: Thao tác dữ liệu hàng hóa & kho.
    Tuple schema: ('product', product_id).
    """

    @staticmethod
    def create_product(product: Product) -> Product:
        product.validate()
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            k = product_key(product.product_id)
            if tr.get(k) is not None:
                raise ValueError(f"Mã sản phẩm '{product.product_id}' đã tồn tại!")
            tr.set(k, encode_value(product.to_dict()))
            return product

        return _txn(db)

    @staticmethod
    def get_product_by_id(product_id: str) -> Optional[Product]:
        db = get_db()
        raw = db.get(product_key(product_id))
        if raw is None:
            return None
        data = decode_value(raw)
        data['product_id'] = product_id
        return Product.from_dict(data)

    @staticmethod
    def get_all_products() -> List[Product]:
        db = get_db()
        start, stop = get_prefix_range(('product',))
        items = db.get_range(start, stop)
        products = []
        for k_bytes, raw_val in items:
            tup = unpack_key(k_bytes)
            data = decode_value(raw_val)
            data['product_id'] = tup[1]
            products.append(Product.from_dict(data))
        return products

    @staticmethod
    def get_products_by_category(category: str) -> List[Product]:
        all_prods = ProductService.get_all_products()
        return [p for p in all_prods if p.category.lower() == category.lower()]

    @staticmethod
    def update_product(product_id: str, updates: Dict[str, Any]) -> Product:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            k = product_key(product_id)
            raw = tr.get(k)
            if raw is None:
                raise ValueError(f"Không tìm thấy sản phẩm '{product_id}'")

            data = decode_value(raw)
            if 'name' in updates:
                data['name'] = updates['name'].strip()
            if 'category' in updates:
                data['category'] = updates['category'].strip()
            if 'price' in updates:
                data['price'] = float(updates['price'])
            if 'stock' in updates:
                stk = int(updates['stock'])
                data['stock'] = stk
                data['status'] = "available" if stk > 0 else "out_of_stock"
            if 'description' in updates:
                data['description'] = updates['description'].strip()

            data["product_id"] = product_id
            prod = Product.from_dict(data)
            prod.validate()
            tr.set(k, encode_value(prod.to_dict()))
            return prod

        return _txn(db)

    @staticmethod
    def delete_product(product_id: str) -> bool:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            k = product_key(product_id)
            if tr.get(k) is None:
                return False
            for _, raw in tr.get_range(*get_prefix_range(('order_item',))):
                if decode_value(raw).get('product_id') == product_id:
                    raise ValueError("Sản phẩm đã có trong đơn hàng; không được xóa")
            tr.clear(k)
            return True

        return _txn(db)

    @staticmethod
    def get_raw_key_values(limit: int = 200) -> List[Dict[str, Any]]:
        """Displays all raw FoundationDB keys with tuple unpack representation."""
        db = get_db()
        # Scan from empty byte to highest byte
        items = db.get_range(b'', b'\xff', limit=limit)
        results = []
        for k_bytes, v_bytes in items:
            try:
                unpacked = str(unpack_key(k_bytes))
            except Exception:
                unpacked = repr(k_bytes)

            val_repr = v_bytes.decode('utf-8', errors='replace')
            try:
                val_json = json.loads(val_repr)
                val_display = json.dumps(val_json, indent=2, ensure_ascii=False)
            except Exception:
                val_display = val_repr

            results.append({
                "hex_key": k_bytes.hex(),
                "raw_key": unpacked,
                "length": len(v_bytes),
                "value_display": val_display
            })
        return results

```

## app/services/query_service.py

```python
"""Member B queries using the existing ordered tuple indexes (no schema migration)."""
from datetime import date, timedelta
from app.database import db_manager, get_db, get_prefix_range, unpack_key, decode_value
from app.database.schema import pack_key
from app.models.order import OrderStatus
from app.services.order_service import OrderService


class QueryService:
    @staticmethod
    def bounds(status, from_date=None, to_date=None):
        status = (status or '').strip().upper()
        if status not in OrderStatus.ALL:
            raise ValueError('Chọn một trạng thái hợp lệ')
        first = date.fromisoformat(from_date) if from_date else None
        last = date.fromisoformat(to_date) if to_date else None
        if first and last and first > last:
            raise ValueError('Ngày bắt đầu phải <= ngày kết thúc')
        start, stop = get_prefix_range(('idx_status_orders', status))
        if first:
            start = pack_key(('idx_status_orders', status, first.isoformat() + 'T00:00:00'))
        if last:
            stop = pack_key(('idx_status_orders', status, (last + timedelta(days=1)).isoformat() + 'T00:00:00'))
        return start, stop

    @staticmethod
    def orders_by_status_time(status, from_date=None, to_date=None, limit=50, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('limit phải từ 1 đến 200')
        start, stop = QueryService.bounds(status, from_date, to_date)
        if cursor:
            key = bytes.fromhex(cursor)
            parts = unpack_key(key)
            if len(parts) != 4 or parts[:2] != ('idx_status_orders', status.strip().upper()) or not start <= key < stop:
                raise ValueError('Cursor không thuộc bộ lọc hiện tại')
            start = key + b'\x00'  # exclusive continuation after exact index key

        @db_manager.transactional
        def query(tr):
            rows = tr.get_range(start, stop, limit=limit + 1)
            orders = []
            for key, _ in rows[:limit]:
                order = OrderService.get_order_by_id(unpack_key(key)[3], tr)
                if order is None:
                    raise ValueError('Chỉ mục mồ côi; cần kiểm tra/khôi phục dữ liệu')
                orders.append(order.to_dict())
            return {'orders': orders, 'next_cursor': rows[limit-1][0].hex() if len(rows) > limit else None,
                    'index': 'idx_status_orders', 'order': 'created_at ASC, order_id ASC',
                    'consistency': 'one transaction per page; pages may change under concurrent writes'}
        return query(get_db())

    @staticmethod
    def transaction_summary(from_date=None, to_date=None):
        # Aggregation is an application-side range scan, NOT an indexed SQL GROUP BY.
        first = date.fromisoformat(from_date) if from_date else None
        last = date.fromisoformat(to_date) if to_date else None
        if first and last and first > last:
            raise ValueError('Khoảng ngày không hợp lệ')
        @db_manager.transactional
        def query(tr):
            groups = {}
            rows = tr.get_range(*get_prefix_range(('transaction',)), limit=10001)
            if len(rows) > 10000:
                raise ValueError('Demo giới hạn 10000 giao dịch; cần tổng hợp theo lô cho dữ liệu lớn')
            for _, raw in rows:
                data = decode_value(raw)
                day = date.fromisoformat(data['timestamp'][:10])
                if (first and day < first) or (last and day > last):
                    continue
                status = data['status']
                group = groups.setdefault(status, {'count': 0, 'amount': 0.0})
                group['count'] += 1
                group['amount'] += float(data['amount'])
            return {'groups': groups, 'count': sum(x['count'] for x in groups.values()),
                    'successful_amount': groups.get('SUCCESS', {}).get('amount', 0),
                    'method': 'transaction prefix scan + Python aggregation; demo receipts only'}
        return query(get_db())

```

## app/services/seed_service.py

```python
import json
from fdb import tuple as fdb_tuple
from app.database import get_db, db_manager
from app.database.schema import APP_PREFIXES, get_prefix_range, clear_application_data

class SeedService:
    """Populates EXACT seed data matching Member B's init_data.py (BÁO CÁO PHẦN 2).
    Synchronized 100% with Member B (Nguyễn Ngọc Hoàng).
    """

    @staticmethod
    def populate_seed_data(clear_existing: bool = True):
        @db_manager.transactional
        def populate(db):
            if clear_existing:
                clear_application_data(db)
            elif any(db.get_range(*get_prefix_range((p,)), limit=1) for p in APP_PREFIXES):
                return {"skipped": True, "reason": "Application data already exists"}
            print("[SeedService] Inserting Member B's official dataset into FoundationDB...")

            # 1. Products (P001 - P004) to support catalog and inventory
            products = [
                ("P001", {"name": "Bàn phím cơ Gaming P001", "category": "Phụ kiện", "price": 1500000.0, "stock": 25, "status": "available", "description": "Bàn phím cơ Blue Switch RGB"}),
                ("P002", {"name": "Chuột quang không dây P002", "category": "Phụ kiện", "price": 250000.0, "stock": 50, "status": "available", "description": "Chuột không dây 2.4G Silent"}),
                ("P003", {"name": "Màn hình IPS 24 inch P003", "category": "Màn hình", "price": 2300000.0, "stock": 15, "status": "available", "description": "Màn hình Full HD 100Hz"}),
                ("P004", {"name": "Tai nghe Gaming P004", "category": "Âm thanh", "price": 400000.0, "stock": 35, "status": "available", "description": "Tai nghe chống ồn Bass Boost"})
            ]
            for pid, pdata in products:
                k = fdb_tuple.pack(('product', pid))
                db.set(k, json.dumps(pdata, ensure_ascii=False).encode('utf-8'))

            # 2. Danh sách Users (Từ file init_data.py của Thành viên B)
            users = [
                ("U101", {"name": "Nguyen Van A", "email": "a@gmail.com", "phone": "0901111111", "role": "customer", "balance": 25000000.0, "status": "active"}),
                ("U102", {"name": "Tran Thi B", "email": "b@gmail.com", "phone": "0902222222", "role": "customer", "balance": 18000000.0, "status": "active"}),
                ("U103", {"name": "Le Van C", "email": "c@gmail.com", "phone": "0903333333", "role": "customer", "balance": 5000000.0, "status": "active"})
            ]
            for uid, udata in users:
                k = fdb_tuple.pack(('user', uid))
                db.set(k, json.dumps(udata, ensure_ascii=False).encode('utf-8'))
                db.set(fdb_tuple.pack(('idx_users_email', udata['email'])), uid.encode('utf-8'))

            # 3. Danh sách Orders & Indexes (Từ file init_data.py của Thành viên B)
            orders = [
                ("ORD2001", "U101", 1500000, "SUCCESS", "2026-10-20T10:00:00"),
                ("ORD2002", "U101", 500000, "FAILED", "2026-10-21T11:30:00"),
                ("ORD2003", "U102", 2300000, "CANCELLED", "2026-10-22T14:15:00"),
                ("ORD2004", "U103", 1200000, "SUCCESS", "2026-10-23T09:00:00")
            ]
            for oid, uid, amount, status, created_at in orders:
                order_data = {"user_id": uid, "total_amount": amount, "status": status, "created_at": created_at, "shipping_address": "Việt Nam"}
                db.set(fdb_tuple.pack(('order', oid)), json.dumps(order_data, ensure_ascii=False).encode('utf-8'))
                db.set(fdb_tuple.pack(('idx_user_orders', uid, oid)), b'')
                db.set(fdb_tuple.pack(('idx_status_orders', status, created_at, oid)), b'')

            # 4. Order Items (Từ file init_data.py của Thành viên B)
            order_items = [
                ("ORD2001", "IT01", {"product_id": "P001", "quantity": 1, "price": 1500000, "product_name": "Bàn phím cơ Gaming P001"}),
                ("ORD2002", "IT02", {"product_id": "P002", "quantity": 2, "price": 250000, "product_name": "Chuột quang không dây P002"}),
                ("ORD2003", "IT03", {"product_id": "P003", "quantity": 1, "price": 2300000, "product_name": "Màn hình IPS 24 inch P003"}),
                ("ORD2004", "IT04", {"product_id": "P004", "quantity": 3, "price": 400000, "product_name": "Tai nghe Gaming P004"})
            ]
            for oid, iid, idata in order_items:
                db.set(fdb_tuple.pack(('order_item', oid, iid)), json.dumps(idata, ensure_ascii=False).encode('utf-8'))

            # 5. Transactions (Từ file init_data.py của Thành viên B)
            transactions = [
                ("TXN3001", "ORD2001", 1500000, "MOMO", "SUCCESS", "2026-10-20T10:05:00"),
                ("TXN3002", "ORD2002", 500000, "VNPAY", "FAILED", "2026-10-21T11:35:00"),
                ("TXN3003", "ORD2004", 1200000, "BANK_TRANSFER", "SUCCESS", "2026-10-23T09:05:00")
            ]
            for tid, oid, amount, method, tstatus, ttime in transactions:
                t_data = {"order_id": oid, "amount": amount, "payment_method": method, "status": tstatus, "timestamp": ttime}
                db.set(fdb_tuple.pack(('transaction', tid)), json.dumps(t_data, ensure_ascii=False).encode('utf-8'))

            # 6. Order History (Từ file init_data.py của Thành viên B)
            histories = [
                ("ORD2001", "2026-10-20T10:00:00", {"status": "PENDING", "note": "Khởi tạo đơn hàng", "updated_by": "System"}),
                ("ORD2001", "2026-10-20T10:05:00", {"status": "SUCCESS", "note": "Thanh toán thành công", "updated_by": "Momo Gateway"}),
                ("ORD2002", "2026-10-21T11:30:00", {"status": "PENDING", "note": "Khởi tạo đơn hàng", "updated_by": "System"}),
                ("ORD2002", "2026-10-21T11:35:00", {"status": "FAILED", "note": "Ngân hàng từ chối giao dịch", "updated_by": "VNPAY"}),
                ("ORD2003", "2026-10-22T14:15:00", {"status": "CANCELLED", "note": "Khách hàng chủ động hủy", "updated_by": "User U102"}),
                ("ORD2004", "2026-10-23T09:00:00", {"status": "PENDING", "note": "Khởi tạo đơn hàng", "updated_by": "System"}),
                ("ORD2004", "2026-10-23T09:05:00", {"status": "SUCCESS", "note": "Thanh toán thành công", "updated_by": "Bank Gateway"})
            ]
            for oid, htime, hdata in histories:
                db.set(fdb_tuple.pack(('order_history', oid, htime)), json.dumps(hdata, ensure_ascii=False).encode('utf-8'))

            print("[SeedService] Successfully populated Member B's dataset: 3 users, 4 products, 4 orders, 3 txns, 7 histories!")
            return {
                "users_seeded": len(users),
                "products_seeded": len(products),
                "orders_seeded": len(orders)
            }
        return populate(get_db())

```

## app/services/user_service.py

```python
from typing import List, Optional, Dict, Any
from app.database import (
    db_manager, get_db,
    user_key, idx_user_email_key, get_prefix_range, unpack_key,
    encode_value, decode_value
)
from app.models.user import User

class UserService:
    """Implements Mục 9.1: Quản trị người dùng.
    Uses Member B's exact tuple schema: ('user', user_id).
    """

    @staticmethod
    def create_user(user: User) -> User:
        user.validate()
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            u_key = user_key(user.user_id)
            if tr.get(u_key) is not None:
                raise ValueError(f"Mã người dùng '{user.user_id}' đã tồn tại!")

            # Check email uniqueness via secondary index
            email_key = idx_user_email_key(user.email)
            if tr.get(email_key) is not None:
                raise ValueError(f"Email '{user.email}' đã được đăng ký!")

            tr.set(u_key, encode_value(user.to_dict()))
            tr.set(email_key, user.user_id.encode('utf-8'))
            return user

        return _txn(db)

    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[User]:
        db = get_db()
        raw = db.get(user_key(user_id))
        if raw is None:
            return None
        data = decode_value(raw)
        data['user_id'] = user_id
        return User.from_dict(data)

    @staticmethod
    def get_user_by_email(email: str) -> Optional[User]:
        db = get_db()
        user_id_raw = db.get(idx_user_email_key(email))
        if user_id_raw is None:
            return None
        return UserService.get_user_by_id(user_id_raw.decode('utf-8'))

    @staticmethod
    def get_all_users() -> List[User]:
        db = get_db()
        start, stop = get_prefix_range(('user',))
        items = db.get_range(start, stop)
        users = []
        for k_bytes, raw_val in items:
            tup = unpack_key(k_bytes)
            data = decode_value(raw_val)
            data['user_id'] = tup[1]
            users.append(User.from_dict(data))
        return users

    @staticmethod
    def update_user(user_id: str, updates: Dict[str, Any]) -> User:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            u_key = user_key(user_id)
            raw = tr.get(u_key)
            if raw is None:
                raise ValueError(f"Không tìm thấy người dùng '{user_id}'")

            data = decode_value(raw)
            old_email = data.get('email', '')
            new_email = updates.get('email', old_email).strip().lower()

            if new_email and new_email != old_email:
                new_idx = idx_user_email_key(new_email)
                if tr.get(new_idx) is not None:
                    raise ValueError(f"Email '{new_email}' đã được đăng ký!")
                tr.clear(idx_user_email_key(old_email))
                tr.set(new_idx, user_id.encode('utf-8'))
                data['email'] = new_email

            if 'name' in updates:
                data['name'] = updates['name'].strip()
            if 'phone' in updates:
                data['phone'] = updates['phone'].strip()
            if 'role' in updates:
                data['role'] = updates['role']
            if 'status' in updates:
                data['status'] = updates['status']
            if 'balance' in updates:
                data['balance'] = float(updates['balance'])
            if 'address' in updates:
                data['address'] = updates['address'].strip()

            data["user_id"] = user_id
            user = User.from_dict(data)
            user.validate()
            tr.set(u_key, encode_value(user.to_dict()))
            return user

        return _txn(db)

    @staticmethod
    def delete_user(user_id: str) -> bool:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            u_key = user_key(user_id)
            raw = tr.get(u_key)
            if raw is None:
                return False
            data = decode_value(raw)
            if tr.get_range(*get_prefix_range(('idx_user_orders', user_id)), limit=1):
                raise ValueError("Khách hàng đã có đơn: hãy khóa thay vì xóa")
            if 'email' in data:
                tr.clear(idx_user_email_key(data['email']))
            tr.clear(u_key)
            return True

        return _txn(db)

```

## app/static/js/app.js

```
function escapeHtml(value) {
    const span = document.createElement('span');
    span.textContent = String(value);
    return span.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Common JavaScript Helpers for FoundationDB E-Commerce Demo

function formatCurrencyVND(amount) {
    return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(amount);
}

function showSuccessAlert(message, callback) {
    Swal.fire({
        icon: 'success',
        title: 'Thành công',
        text: message,
        timer: 1800,
        showConfirmButton: false
    }).then(() => {
        if (callback) callback();
    });
}

function showErrorAlert(message) {
    Swal.fire({
        icon: 'error',
        title: 'Có lỗi xảy ra',
        text: message,
        confirmButtonColor: '#0d6efd'
    });
}

// Function to trigger Seed Sample Data
async function seedSampleData() {
    const confirm = await Swal.fire({
        title: 'Nạp lại dữ liệu mẫu?',
        text: 'Thao tác này sẽ làm mới toàn bộ database FoundationDB với các bộ dữ liệu người dùng, sản phẩm và kịch bản đơn hàng mẫu!',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#0d6efd',
        cancelButtonColor: '#6c757d',
        confirmButtonText: 'Đồng ý nạp',
        cancelButtonText: 'Hủy'
    });

    if (confirm.isConfirmed) {
        Swal.fire({
            title: 'Đang xử lý...',
            text: 'Đang khởi tạo các Subspace và ghi bản ghi vào FoundationDB',
            allowOutsideClick: false,
            didOpen: () => Swal.showLoading()
        });

        try {
            const resp = await fetch('/api/seed', { method: 'POST' });
            const data = await resp.json();
            if (data.status === 'success') {
                showSuccessAlert('Đã nạp thành công bộ dữ liệu mẫu!', () => {
                    window.location.reload();
                });
            } else {
                showErrorAlert(data.message || 'Lỗi nạp dữ liệu');
            }
        } catch (e) {
            showErrorAlert('Lỗi mạng hoặc server không phản hồi: ' + e.message);
        }
    }
}

```

## app/templates/base.html

```
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}FDB Commerce Operations{% endblock %}</title>
    <!-- Bootstrap 5 CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- Font Awesome 6 Icons -->
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css" rel="stylesheet">
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <!-- SweetAlert2 -->
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <!-- Modern Custom CSS -->
    <link href="{{ url_for('static', filename='css/custom.css') }}" rel="stylesheet">
</head>
<body>

    <!-- Main Enterprise Navbar -->
    <nav class="navbar navbar-expand-lg navbar-dark navbar-custom sticky-top">
        <div class="container-fluid px-4 py-1">
            <a class="navbar-brand d-flex align-items-center gap-2" href="/">
                <div class="navbar-brand-logo text-white">
                    <i class="fa-solid fa-cube fs-5"></i>
                </div>
                <div class="d-flex flex-column">
                    <span class="fw-bold tracking-tight text-white fs-5 lh-1">FDB <span class="text-indigo-400" style="color: #818cf8;">Commerce</span></span>
                    <span style="font-size: 0.68rem; color: #94a3b8; letter-spacing: 0.04em;">DISTRIBUTED OPERATIONS PLATFORM</span>
                </div>
            </a>

            <button class="navbar-toggler border-0" type="button" data-bs-toggle="collapse" data-bs-target="#mainNav">
                <span class="navbar-toggler-icon"></span>
            </button>

            <div class="collapse navbar-collapse" id="mainNav">
                <ul class="navbar-nav mx-auto mb-2 mb-lg-0 gap-1">
                    <li class="nav-item">
                        <a class="nav-link nav-link-custom {% if active_page == 'dashboard' %}active{% endif %}" href="/">
                            <i class="fa-solid fa-chart-pie me-1 text-primary-soft"></i> Tổng quan
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link nav-link-custom {% if active_page == 'orders' %}active{% endif %}" href="/orders">
                            <i class="fa-solid fa-bag-shopping me-1 text-primary-soft"></i> Quản lý đơn hàng
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link nav-link-custom {% if active_page == 'products' %}active{% endif %}" href="/products">
                            <i class="fa-solid fa-cubes-stacked me-1 text-primary-soft"></i> Sản phẩm & Kho
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link nav-link-custom {% if active_page == 'users' %}active{% endif %}" href="/users">
                            <i class="fa-solid fa-user-group me-1 text-primary-soft"></i> Khách hàng
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link nav-link-custom {% if active_page == 'kv_explorer' %}active{% endif %}" href="/kv-explorer">
                            <i class="fa-solid fa-terminal me-1 text-warning"></i> Key-Value Explorer
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link nav-link-custom {% if active_page == 'reports' %}active{% endif %}" href="/reports">
                            <i class="fa-solid fa-chart-line me-1 text-primary-soft"></i> Báo cáo & Kiểm thử
                        </a>
                    </li>
                    <li class="nav-item"><a class="nav-link" href="/queries">Truy vấn B</a></li>
                </ul>

                <div class="d-flex align-items-center gap-3">
                    <div class="d-none d-md-flex align-items-center gap-2 bg-dark bg-opacity-50 px-3 py-1 rounded-pill border border-white border-opacity-10 text-white small" style="font-size: 0.8rem;">
                        <span class="spinner-grow spinner-grow-sm text-success" style="width: 7px; height: 7px;" role="status"></span>
                        <span class="text-white-50">Storage:</span>
                        <span class="fw-semibold text-white">{{ db_info.mode }}</span>
                    </div>

                    <button class="btn btn-sm btn-outline-light px-3 py-1 rounded-pill" onclick="seedSampleData()" title="Khôi phục lại dữ liệu mẫu gốc">
                        <i class="fa-solid fa-rotate me-1"></i> Nạp lại dữ liệu
                    </button>
                </div>
            </div>
        </div>
    </nav>

    <!-- Sub-header Breadcrumb / System Diagnostic Banner -->
    <div class="sub-header py-2 px-4">
        <div class="container-fluid d-flex justify-content-between align-items-center flex-wrap gap-2 text-muted" style="font-size: 0.82rem;">
            <div class="d-flex align-items-center gap-2">
                <span class="badge bg-light text-dark border"><i class="fa-solid fa-network-wired text-primary me-1"></i> {{ db_info.mode }}</span>
                <span class="text-muted d-none d-sm-inline">API version: <strong>{{ db_info.api_version }}</strong></span>
                <span class="text-muted d-none d-md-inline">• Cách ly: <strong>{{ "FoundationDB transactions" if db_info.mode == "REAL_FDB" else "Giả lập một tiến trình; không phải cụm FoundationDB" }}</strong></span>
            </div>
            <div class="d-flex align-items-center gap-3">
                <span>Số khóa hiện tại: <strong class="text-dark">{{ db_info.total_keys }} keys</strong></span>
                <span>Trạng thái: <span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill">Active</span></span>
            </div>
        </div>
    </div>

    <!-- Main Content Area -->
    <main class="container-fluid px-4 py-4">
        {% block content %}{% endblock %}
    </main>

    <!-- Clean Modern Footer -->
    <footer class="bg-white border-top py-3 text-muted small mt-5">
        <div class="container-fluid px-4 d-flex justify-content-between align-items-center flex-wrap gap-2">
            <span>© 2026 <strong>FDB Commerce Operations Platform</strong>. High-Throughput Key-Value Engine.</span>
            <span class="text-secondary" style="font-size: 0.75rem;">FoundationDB Multi-key ACID Transaction Core</span>
        </div>
    </footer>

    <!-- Bootstrap 5 JS -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    <!-- Custom JS -->
    <script src="{{ url_for('static', filename='js/app.js') }}"></script>
    {% block scripts %}{% endblock %}
</body>
</html>

```

## app/templates/dashboard.html

```
{% extends "base.html" %}

{% block title %}Dashboard Tổng Quan - FDB Commerce{% endblock %}

{% block content %}
<!-- Top Header Area -->
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
    <div>
        <h4 class="fw-bold mb-1 text-dark">Bảng Điều Khiển Vận Hành</h4>
        <p class="text-muted small mb-0">Theo dõi thời gian thực các giao dịch phân tán, trạng thái đơn hàng & chỉ mục FoundationDB</p>
    </div>
    <div class="d-flex gap-2">
        <a href="/orders" class="btn btn-primary px-3 py-2 rounded-3 shadow-sm d-flex align-items-center gap-2">
            <i class="fa-solid fa-plus"></i>
            <span>Tạo Đơn Hàng Mới</span>
        </a>
        <button class="btn btn-outline-danger px-3 py-2 rounded-3 d-flex align-items-center gap-2" onclick="runAcidTest()">
            <i class="fa-solid fa-bolt-lightning text-danger"></i>
            <span>Kiểm Thử ACID Rollback</span>
        </button>
    </div>
</div>

<!-- 4 Modern Stat Cards -->
<div class="row g-3 mb-4">
    <div class="col-12 col-sm-6 col-xl-3">
        <div class="card stat-card-modern revenue h-100">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <span class="text-muted small fw-semibold text-uppercase tracking-wider">Doanh Thu Demo</span>
                    <h3 class="fw-bold mt-2 text-dark mb-1">{{ "{:,.0f}".format(summary.total_revenue) }} <span class="fs-6 fw-normal text-muted">đ</span></h3>
                    <div class="d-flex align-items-center gap-1 text-success small">
                        <i class="fa-solid fa-circle-check"></i>
                        <span>Đã ghi nhận thanh toán</span>
                    </div>
                </div>
                <div class="stat-icon-badge" style="background: #ecfdf5; color: #10b981;">
                    <i class="fa-solid fa-wallet"></i>
                </div>
            </div>
        </div>
    </div>

    <div class="col-12 col-sm-6 col-xl-3">
        <div class="card stat-card-modern orders h-100">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <span class="text-muted small fw-semibold text-uppercase tracking-wider">Tổng Đơn Hàng</span>
                    <h3 class="fw-bold mt-2 text-dark mb-1">{{ summary.total_orders }}</h3>
                    <div class="d-flex align-items-center gap-1 text-primary small">
                        <i class="fa-solid fa-truck-fast"></i>
                        <span>{{ summary.status_counts.SUCCESS }} thành công, {{ summary.status_counts.CANCELLED }} đã hủy</span>
                    </div>
                </div>
                <div class="stat-icon-badge" style="background: #eff6ff; color: #3b82f6;">
                    <i class="fa-solid fa-box"></i>
                </div>
            </div>
        </div>
    </div>

    <div class="col-12 col-sm-6 col-xl-3">
        <div class="card stat-card-modern stock h-100">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <span class="text-muted small fw-semibold text-uppercase tracking-wider">Tồn Kho Sản Phẩm</span>
                    <h3 class="fw-bold mt-2 text-dark mb-1">{{ summary.total_stock }} <span class="fs-6 fw-normal text-muted">cái</span></h3>
                    <div class="d-flex align-items-center gap-1 text-warning small">
                        <i class="fa-solid fa-warehouse"></i>
                        <span>{{ summary.total_products }} danh mục mặt hàng</span>
                    </div>
                </div>
                <div class="stat-icon-badge" style="background: #fffbeb; color: #f59e0b;">
                    <i class="fa-solid fa-cubes"></i>
                </div>
            </div>
        </div>
    </div>

    <div class="col-12 col-sm-6 col-xl-3">
        <div class="card stat-card-modern h-100">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <span class="text-muted small fw-semibold text-uppercase tracking-wider">Tổng Khách Hàng</span>
                    <h3 class="fw-bold mt-2 text-dark mb-1">{{ summary.total_users }}</h3>
                    <div class="d-flex align-items-center gap-1 text-secondary small">
                        <i class="fa-solid fa-users"></i>
                        <span>Đã định danh trên Tuple</span>
                    </div>
                </div>
                <div class="stat-icon-badge" style="background: #eef2ff; color: #6366f1;">
                    <i class="fa-solid fa-user-check"></i>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Database Diagnostic Engine Card -->
<div class="card mb-4 border-0 shadow-sm" style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);">
    <div class="card-body p-4 text-white">
        <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
            <div class="d-flex align-items-center gap-2">
                <i class="fa-solid fa-server text-indigo-400" style="color: #818cf8;"></i>
                <h6 class="fw-bold mb-0 text-white">Kiến Trúc Lưu Trữ & Trạng Thái Engine FoundationDB</h6>
            </div>
            <span class="badge bg-success bg-opacity-25 text-success border border-success border-opacity-25 px-3 py-1 rounded-pill">
                ● CSDL Hoạt động ổn định
            </span>
        </div>
        <div class="row g-3">
            <div class="col-md-3">
                <div class="p-3 rounded-3" style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1);">
                    <div class="text-white-50 small">Chế độ vận hành</div>
                    <div class="fw-bold text-white mt-1">{{ summary.db_status.mode }}</div>
                    <div class="small text-white-50" style="font-size: 0.75rem;">Sorted Tuple Byte Order</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="p-3 rounded-3" style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1);">
                    <div class="text-white-50 small">Phiên bản giao tiếp API</div>
                    <div class="fw-bold text-white mt-1">FoundationDB {{ summary.db_status.api_version }}</div>
                    <div class="small text-white-50" style="font-size: 0.75rem;">Đồng bộ thiết kế Tuple Layer</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="p-3 rounded-3" style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1);">
                    <div class="text-white-50 small">Tổng số Key phân vùng</div>
                    <div class="fw-bold text-white mt-1">{{ summary.db_status.total_keys }} Khóa nhị phân</div>
                    <div class="small text-white-50" style="font-size: 0.75rem;">user, order, transaction, idx</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="p-3 rounded-3" style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1);">
                    <div class="text-white-50 small">Cấp độ cô lập giao dịch</div>
                    <div class="fw-bold text-success mt-1">{{ "Strict Serializable" if db_info.mode == "REAL_FDB" else "Giả lập một tiến trình" }}</div>
                    <div class="small text-white-50" style="font-size: 0.75rem;">Multi-key Atomic Commit</div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Charts Section -->
<div class="row g-4 mb-4">
    <div class="col-lg-6">
        <div class="card h-100">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0">Phân Bổ Trạng Thái Đơn Hàng</h6>
                <span class="text-muted small">Tổng hợp từ các đơn hàng</span>
            </div>
            <div class="card-body d-flex justify-content-center align-items-center py-4">
                <div style="max-width: 320px; width: 100%;">
                    <canvas id="orderStatusChart"></canvas>
                </div>
            </div>
        </div>
    </div>

    <div class="col-lg-6">
        <div class="card h-100">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0">Tỷ Lệ Giao Dịch Thanh Toán</h6>
                <span class="badge bg-indigo-subtle text-indigo rounded-pill px-3 py-1" style="background: #e0e7ff; color: #4338ca;">
                    Thành công: {{ summary.success_rate }}%
                </span>
            </div>
            <div class="card-body d-flex justify-content-center align-items-center py-4">
                <div style="max-width: 320px; width: 100%;">
                    <canvas id="transactionChart"></canvas>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Recent Orders Table -->
<div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
        <div>
            <h6 class="fw-bold mb-0">Đơn Hàng Gần Đây</h6>
            <span class="text-muted small" style="font-size: 0.8rem;">Quét dữ liệu phạm vi trên Tuple `('order', order_id)`</span>
        </div>
        <a href="/orders" class="btn btn-sm btn-outline-secondary px-3 rounded-pill">
            Xem tất cả <i class="fa-solid fa-arrow-right ms-1"></i>
        </a>
    </div>
    <div class="card-body p-0">
        <div class="table-responsive">
            <table class="table table-custom align-middle">
                <thead>
                    <tr>
                        <th class="ps-4">Mã Đơn Hàng</th>
                        <th>Khách Hàng</th>
                        <th>Chi Tiết Mặt Hàng</th>
                        <th>Tổng Giá Trị</th>
                        <th>Trạng Thái</th>
                        <th>Thời Gian Tạo</th>
                        <th class="text-end pe-4">Thao Tác</th>
                    </tr>
                </thead>
                <tbody>
                    {% for ord in recent_orders %}
                    <tr>
                        <td class="ps-4">
                            <a href="/orders/{{ ord.order_id }}" class="fw-bold text-decoration-none text-primary">
                                {{ ord.order_id }}
                            </a>
                        </td>
                        <td>
                            <div class="d-flex align-items-center">
                                <div class="user-avatar-circle">{{ ord.user_id[:2] }}</div>
                                <span class="fw-semibold">{{ ord.user_id }}</span>
                            </div>
                        </td>
                        <td>
                            <span class="badge bg-light text-dark border">{{ ord.items|length }} mặt hàng</span>
                        </td>
                        <td class="fw-bold text-dark">{{ "{:,.0f}".format(ord.total_amount) }} đ</td>
                        <td>
                            <span class="status-pill status-{{ ord.status }}">
                                <span class="status-dot"></span>
                                {{ ord.status }}
                            </span>
                        </td>
                        <td class="text-muted small">{{ ord.created_at }}</td>
                        <td class="text-end pe-4">
                            <a href="/orders/{{ ord.order_id }}" class="btn btn-sm btn-light border px-3 rounded-pill">
                                Chi tiết
                            </a>
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="7" class="text-center py-4 text-muted">Chưa có đơn hàng nào trong hệ thống</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
// Order Status Doughnut Chart
const statusData = {{ summary.status_counts | tojson }};
const ctxOrder = document.getElementById('orderStatusChart').getContext('2d');
new Chart(ctxOrder, {
    type: 'doughnut',
    data: {
        labels: Object.keys(statusData),
        datasets: [{
            data: Object.values(statusData),
            backgroundColor: [
                '#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#ef4444', '#64748b'
            ],
            borderWidth: 2,
            borderColor: '#ffffff'
        }]
    },
    options: {
        responsive: true,
        cutout: '70%',
        plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 12, padding: 15, font: { family: "'Plus Jakarta Sans', sans-serif" } } }
        }
    }
});

// Transaction Chart
const txnStats = {{ summary.txn_stats | tojson }};
const ctxTxn = document.getElementById('transactionChart').getContext('2d');
new Chart(ctxTxn, {
    type: 'pie',
    data: {
        labels: ['Thành công (SUCCESS)', 'Thất bại (FAILED)', 'Đang chờ (PENDING)', 'Hoàn tiền (REFUNDED)'],
        datasets: [{
            data: [txnStats.SUCCESS, txnStats.FAILED, txnStats.PENDING, txnStats.REFUNDED],
            backgroundColor: ['#10b981', '#ef4444', '#f59e0b', '#8b5cf6'],
            borderWidth: 2,
            borderColor: '#ffffff'
        }]
    },
    options: {
        responsive: true,
        plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 12, padding: 15, font: { family: "'Plus Jakarta Sans', sans-serif" } } }
        }
    }
});

async function runAcidTest() {
    Swal.fire({
        title: 'Đang kiểm tra giao dịch ACID...',
        text: 'Thực thi giao dịch phân tán mô phỏng trường hợp thiếu tồn kho trên FoundationDB',
        allowOutsideClick: false,
        didOpen: () => Swal.showLoading()
    });

    try {
        const resp = await fetch('/api/acid-test', { method: 'POST' });
        const data = await resp.json();
        
        Swal.fire({
            icon: data.acid_integrity_maintained ? 'success' : 'error',
            title: data.test_name,
            html: `
                <div class="text-start small p-3 bg-light border rounded-3 mt-2">
                    <p class="mb-1"><strong>Sản phẩm kiểm tra:</strong> <code>${escapeHtml(data.tested_product)}</code></p>
                    <p class="mb-1"><strong>Tồn kho ban đầu:</strong> ${data.initial_stock} cái</p>
                    <p class="mb-1"><strong>Cố tình đặt mua vượt mức:</strong> <span class="text-danger fw-bold">${data.attempted_deduction} cái</span></p>
                    <p class="mb-2"><strong>Ngoại lệ phát hiện:</strong> <span class="text-danger">${escapeHtml(data.error_message)}</span></p>
                    <p class="mb-1"><strong>Tồn kho sau Rollback:</strong> <span class="text-success fw-bold">${data.final_stock} cái</span> (Không hao hụt!)</p>
                    <p class="mb-0"><strong>Đơn hàng dở dang:</strong> ${data.order_created ? 'Bị lỗi' : '<span class="text-success">Không có (Đã hủy)</span>'}</p>
                    <hr>
                    <div class="alert alert-success mb-0 py-2 small">
                        <i class="fa-solid fa-shield-check me-1"></i> ${escapeHtml(data.conclusion)}
                    </div>
                </div>
            `,
            confirmButtonColor: '#4f46e5',
            confirmButtonText: 'Đóng'
        });
    } catch (e) {
        showErrorAlert('Lỗi kiểm thử: ' + e.message);
    }
}
</script>
{% endblock %}

```

## app/templates/order_detail.html

```
{% extends "base.html" %}

{% block title %}Đơn Hàng {{ order.order_id }} - FDB Commerce{% endblock %}

{% block content %}
<!-- Detail Header -->
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
    <div>
        <div class="d-flex align-items-center gap-2">
            <a href="/orders" class="btn btn-sm btn-light border px-2 py-1 rounded-2">
                <i class="fa-solid fa-arrow-left text-muted"></i>
            </a>
            <h4 class="fw-bold mb-0 text-dark">Chi Tiết Đơn Hàng: <span class="text-primary">{{ order.order_id }}</span></h4>
            <span class="status-pill status-{{ order.status }} ms-2">
                <span class="status-dot"></span> {{ order.status }}
            </span>
        </div>
        <p class="text-muted small mb-0 mt-1">Khởi tạo lúc: {{ order.created_at }} | Cập nhật lúc: {{ order.updated_at }}</p>
    </div>

    <!-- Quick State Transition Buttons -->
    <div class="d-flex gap-2 flex-wrap">
        {% if order.status in ['CREATED', 'PENDING'] %}
            <button class="btn btn-success px-3 py-2 rounded-3 shadow-sm d-flex align-items-center gap-2" onclick="openPaymentModal(true)">
                <i class="fa-solid fa-credit-card"></i>
                <span>Thanh Toán Thành Công</span>
            </button>
            <button class="btn btn-outline-danger px-3 py-2 rounded-3" onclick="openPaymentModal(false)">
                <i class="fa-solid fa-ban me-1"></i> Báo Thanh Toán Thất Bại
            </button>
        {% elif order.status == 'FAILED' %}
            <button class="btn btn-primary" onclick="transitionStatus('PENDING', 'Admin', 'Thử thanh toán lại')">Thử thanh toán lại</button>
        {% elif order.status == 'DELIVERED' %}
            <button class="btn btn-success" onclick="transitionStatus('COMPLETED', 'Admin', 'Hoàn tất đơn hàng')">Hoàn tất đơn hàng</button>
        {% elif order.status in ['PAID', 'SUCCESS'] %}
            <button class="btn btn-warning px-3 py-2 rounded-3 text-dark fw-semibold" onclick="transitionStatus('PROCESSING', 'Kho Tổng', 'Bắt đầu đóng gói kiện hàng')">
                <i class="fa-solid fa-box-archive me-1"></i> Chuẩn Bị Hàng (PROCESSING)
            </button>
        {% elif order.status == 'PROCESSING' %}
            <button class="btn btn-primary px-3 py-2 rounded-3" onclick="transitionStatus('SHIPPING', 'Bưu tá GHN', 'Bàn giao bưu tá bưu cục')">
                <i class="fa-solid fa-truck-fast me-1"></i> Giao Vận Chuyển (SHIPPING)
            </button>
        {% elif order.status == 'SHIPPING' %}
            <button class="btn btn-info text-white px-3 py-2 rounded-3" onclick="transitionStatus('DELIVERED', 'Shipper', 'Khách hàng nhận hàng và ký nhận')">
                <i class="fa-solid fa-house-chimney-check me-1"></i> Giao Hàng Thành Công
            </button>
        {% elif order.status in ['DELIVERED', 'COMPLETED'] %}
            <span class="badge bg-success-subtle text-success border border-success-subtle px-3 py-2 rounded-pill">
                <i class="fa-solid fa-check-double me-1"></i> Đơn Hàng Hoàn Tất
            </span>
        {% endif %}

        {% if order.status in ['PENDING', 'FAILED'] %}
            <button class="btn btn-outline-danger px-3 py-2 rounded-3" onclick="cancelOrderWithRollback()">
                <i class="fa-solid fa-xmark me-1"></i> Hủy Đơn & Hoàn Tồn Kho
            </button>
        {% endif %}
    </div>
</div>

<div class="row g-4">
    <!-- Left Column: Customer, Items, Transactions & Raw KV Inspector -->
    <div class="col-lg-7">
        <!-- Customer Info Card -->
        <div class="card mb-4">
            <div class="card-header">
                <h6 class="fw-bold mb-0 text-dark">
                    <i class="fa-solid fa-user-circle me-2 text-primary"></i> Thông Tin Khách Hàng & Vận Chuyển
                </h6>
            </div>
            <div class="card-body">
                <div class="row g-3">
                    <div class="col-sm-6">
                        <span class="text-muted small">Mã Khách Hàng:</span>
                        <div class="fw-bold text-dark mt-1">
                            {{ user.name if user else order.user_id }} <span class="badge bg-light text-secondary border font-monospace">{{ order.user_id }}</span>
                        </div>
                    </div>
                    <div class="col-sm-6">
                        <span class="text-muted small">Email Liên Hệ:</span>
                        <div class="fw-semibold text-dark mt-1">{{ user.email if user else '---' }}</div>
                    </div>
                    <div class="col-12">
                        <span class="text-muted small">Địa Chỉ Giao Nhận:</span>
                        <div class="fw-semibold text-dark mt-1">
                            <i class="fa-solid fa-location-dot text-danger me-1"></i> {{ order.shipping_address }}
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Order Items Card -->
        <div class="card mb-4">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0 text-dark">
                    <i class="fa-solid fa-box-open me-2 text-primary"></i> Chi Tiết Mặt Hàng Đặt Mua
                </h6>
                <span class="badge bg-light text-secondary border">{{ order.items|length }} sản phẩm</span>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive">
                    <table class="table table-custom align-middle">
                        <thead>
                            <tr>
                                <th class="ps-4">Sản Phẩm</th>
                                <th class="text-center">Số Lượng</th>
                                <th class="text-end">Đơn Giá</th>
                                <th class="text-end pe-4">Thành Tiền</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in order.items %}
                            <tr>
                                <td class="ps-4">
                                    <div class="fw-bold text-dark">{{ item.product_name }}</div>
                                    <span class="badge bg-light text-secondary border font-monospace" style="font-size: 0.72rem;">{{ item.product_id }}</span>
                                </td>
                                <td class="text-center fw-semibold">{{ item.quantity }}</td>
                                <td class="text-end">{{ "{:,.0f}".format(item.unit_price) }} đ</td>
                                <td class="text-end pe-4 fw-bold text-dark">{{ "{:,.0f}".format(item.subtotal) }} đ</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                        <tfoot>
                            <tr class="bg-light">
                                <td colspan="3" class="text-end fw-bold ps-4">Tổng Giá Trị Đơn Hàng:</td>
                                <td class="text-end pe-4 fw-bold text-dark fs-5">{{ "{:,.0f}".format(order.total_amount) }} đ</td>
                            </tr>
                        </tfoot>
                    </table>
                </div>
            </div>
        </div>

        <!-- Transactions Subspace Card -->
        <div class="card mb-4">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0 text-dark">
                    <i class="fa-solid fa-credit-card me-2 text-primary"></i> Lịch Sử Giao Dịch Thanh Toán
                </h6>
                <span class="badge bg-light text-secondary border">Tuple: ('transaction', txn_id)</span>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive">
                    <table class="table table-custom align-middle small">
                        <thead>
                            <tr>
                                <th class="ps-4">Mã Giao Dịch</th>
                                <th>Phương Thức</th>
                                <th>Số Tiền</th>
                                <th>Trạng Thái</th>
                                <th>Thời Gian</th>
                                <th class="pe-4">Ghi Chú</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for t in transactions %}
                            <tr>
                                <td class="ps-4 font-monospace fw-semibold text-primary">{{ t.txn_id }}</td>
                                <td><span class="badge bg-light text-dark border">{{ t.payment_method }}</span></td>
                                <td class="fw-bold text-dark">{{ "{:,.0f}".format(t.amount) }} đ</td>
                                <td>
                                    <span class="status-pill status-{{ t.status }}">
                                        <span class="status-dot"></span> {{ t.status }}
                                    </span>
                                </td>
                                <td class="text-muted">{{ t.created_at }}</td>
                                <td class="pe-4 text-muted">{{ t.note }}</td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="6" class="text-center py-3 text-muted">Chưa ghi nhận giao dịch thanh toán nào</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- High-Tech Developer Terminal KV Inspector -->
        <div class="card border-0">
            <div class="terminal-box">
                <div class="terminal-header">
                    <div class="terminal-dots">
                        <span class="terminal-dot dot-red"></span>
                        <span class="terminal-dot dot-yellow"></span>
                        <span class="terminal-dot dot-green"></span>
                    </div>
                    <span>foundationdb://kv-explorer/order/{{ order.order_id }}</span>
                    <span>SERIALIZED</span>
                </div>
<span class="text-secondary">// 1. Khóa chính Đơn hàng (Primary Key Tuple):</span>
<span class="text-warning">Key:</span> ('order', '{{ order.order_id }}')
<span class="text-warning">Value:</span> {{ order_json }}

<span class="text-secondary">// 2. Chỉ mục phụ theo Người dùng (Secondary Index):</span>
<span class="text-warning">Key:</span> ('idx_user_orders', '{{ order.user_id }}', '{{ order.order_id }}') => Value: b''

<span class="text-secondary">// 3. Chỉ mục phụ theo Trạng thái & Ngày tạo:</span>
<span class="text-warning">Key:</span> ('idx_status_orders', '{{ order.status }}', '{{ order.created_at }}', '{{ order.order_id }}') => Value: b''

<span class="text-secondary">// 4. Chi tiết các mặt hàng (Order Items Tuple Prefix):</span>
<span class="text-warning">Range:</span> ('order_item', '{{ order.order_id }}', ...) [{{ order.items|length }} mặt hàng]

<span class="text-secondary">// 5. Dòng thời gian lịch sử trạng thái:</span>
<span class="text-warning">Range:</span> ('order_history', '{{ order.order_id }}', ...) [{{ history|length }} bản ghi audit]
            </div>
        </div>
    </div>

    <!-- Right Column: Modern Timeline Stepper -->
    <div class="col-lg-5">
        <div class="card sticky-top" style="top: 90px;">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0 text-dark">
                    <i class="fa-solid fa-timeline me-2 text-primary"></i> Dòng Thời Gian Trạng Thái
                </h6>
                <span class="badge bg-indigo-subtle text-indigo rounded-pill px-3" style="background: #e0e7ff; color: #4338ca;">
                    {{ history|length }} sự kiện
                </span>
            </div>
            <div class="card-body p-4">
                <ul class="timeline-modern mb-0">
                    {% for h in history %}
                    <li class="timeline-step {% if h.new_status == 'COMPLETED' or h.new_status == 'SUCCESS' %}success{% elif h.new_status == 'CANCELLED' or h.new_status == 'FAILED' %}danger{% endif %}">
                        <div class="timeline-icon">
                            {% if h.new_status in ['COMPLETED', 'SUCCESS'] %}
                                <i class="fa-solid fa-check"></i>
                            {% elif h.new_status in ['CANCELLED', 'FAILED'] %}
                                <i class="fa-solid fa-xmark"></i>
                            {% else %}
                                <i class="fa-solid fa-circle-dot"></i>
                            {% endif %}
                        </div>
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span class="fw-bold text-dark">{{ h.action }}</span>
                            <span class="status-pill status-{{ h.new_status }}">{{ h.new_status }}</span>
                        </div>
                        <div class="text-muted small mb-1" style="font-size: 0.78rem;">
                            <i class="fa-regular fa-clock me-1"></i> {{ h.timestamp }}
                        </div>
                        <div class="small mb-2">
                            <span class="badge bg-light text-secondary border">Thao tác bởi: {{ h.operator }}</span>
                        </div>
                        {% if h.note %}
                        <div class="p-2 bg-light rounded-3 small text-secondary border-start border-3 border-primary">
                            {{ h.note }}
                        </div>
                        {% endif %}
                    </li>
                    {% endfor %}
                </ul>
            </div>
        </div>
    </div>
</div>

<!-- Modal: Thanh Toán Đơn Hàng -->
<div class="modal fade" id="paymentModal" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content border-0 shadow">
            <div class="modal-header border-bottom">
                <h5 class="modal-title fw-bold" id="paymentModalTitle">Ghi Nhận Giao Dịch</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <form id="paymentForm" onsubmit="submitPayment(event)">
                <div class="modal-body">
                    <input type="hidden" id="payment_is_success" value="true">
                    
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Số Tiền Thanh Toán</label>
                        <input type="text" class="form-control bg-light fw-bold text-dark fs-5" value="{{ '{:,.0f}'.format(order.total_amount) }} đ" disabled>
                    </div>

                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Phương Thức Thanh Toán</label>
                        <select class="form-select" id="payment_method">
                            <option value="MOMO" selected>Ví MoMo (Momo Gateway)</option>
                            <option value="VNPAY">Cổng VNPAY / QR Pay</option>
                            <option value="BANK_TRANSFER">Chuyển Khoản Trực Tiếp</option>
                            <option value="COD">Thanh Toán Khi Giao Hàng (COD)</option>
                        </select>
                    </div>

                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Ghi Chú Giao Dịch</label>
                        <textarea class="form-control" id="payment_note" rows="2" placeholder="Ghi chú nghiệp vụ..."></textarea>
                    </div>
                </div>
                <div class="modal-footer border-top">
                    <button type="button" class="btn btn-light" data-bs-dismiss="modal">Đóng</button>
                    <button type="submit" class="btn btn-success px-4" id="btnSubmitPayment">
                        Xác Nhận Thanh Toán
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
const currentOrderId = {{ order.order_id | tojson }};
let paymentRequestId = null;

function openPaymentModal(isSuccess) {
    paymentRequestId = crypto.randomUUID();
    document.getElementById('payment_is_success').value = isSuccess ? "true" : "false";
    const title = document.getElementById('paymentModalTitle');
    const btn = document.getElementById('btnSubmitPayment');
    const note = document.getElementById('payment_note');

    if (isSuccess) {
        title.innerHTML = '<i class="fa-solid fa-credit-card text-success me-2"></i> Xác Nhận Thanh Toán Thành Công';
        btn.className = 'btn btn-success px-4';
        btn.innerText = 'Xác Nhận Thành Công';
        note.value = 'Khách hàng hoàn tất thanh toán trực tuyến';
    } else {
        title.innerHTML = '<i class="fa-solid fa-ban text-danger me-2"></i> Báo Thanh Toán Thất Bại';
        btn.className = 'btn btn-danger px-4';
        btn.innerText = 'Ghi Nhận Thất Bại';
        note.value = 'Ngân hàng từ chối giao dịch: Số dư không đủ hoặc thẻ bị khóa';
    }

    new bootstrap.Modal(document.getElementById('paymentModal')).show();
}

async function submitPayment(e) {
    e.preventDefault();
    const isSuccess = document.getElementById('payment_is_success').value === "true";
    const method = document.getElementById('payment_method').value;
    const note = document.getElementById('payment_note').value;

    try {
        const resp = await fetch(`/api/orders/${currentOrderId}/pay`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                request_id: paymentRequestId,
                payment_method: method,
                is_success: isSuccess,
                note: note
            })
        });
        const res = await resp.json();
        if (res.status === 'success') {
            showSuccessAlert(
                isSuccess ? 'Thanh toán thành công! Trạng thái chuyển sang SUCCESS.' : 'Đã ghi nhận giao dịch thất bại!',
                () => location.reload()
            );
        } else {
            showErrorAlert(res.message);
        }
    } catch (err) {
        showErrorAlert(err.message);
    }
}

async function transitionStatus(newStatus, operator, defaultNote) {
    const { value: note } = await Swal.fire({
        title: `Chuyển trạng thái: ${newStatus}?`,
        input: 'text',
        inputLabel: 'Ghi chú cập nhật:',
        inputValue: defaultNote,
        showCancelButton: true,
        confirmButtonColor: '#4f46e5',
        confirmButtonText: 'Xác nhận chuyển',
        cancelButtonText: 'Hủy'
    });

    if (note !== undefined) {
        try {
            const resp = await fetch(`/api/orders/${currentOrderId}/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    new_status: newStatus,
                    operator: operator,
                    note: note
                })
            });
            const res = await resp.json();
            if (res.status === 'success') {
                showSuccessAlert(`Đơn hàng đã cập nhật sang ${newStatus}!`, () => location.reload());
            } else {
                showErrorAlert(res.message);
            }
        } catch (err) {
            showErrorAlert(err.message);
        }
    }
}

async function cancelOrderWithRollback() {
    const confirm = await Swal.fire({
        title: 'Hủy đơn hàng này?',
        text: 'Hệ thống sẽ thực thi giao dịch ACID hoàn trả lại toàn bộ số lượng tồn kho của các mặt hàng vào kho sản phẩm!',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#ef4444',
        confirmButtonText: 'Đồng ý hủy đơn & Hoàn kho',
        cancelButtonText: 'Đóng'
    });

    if (confirm.isConfirmed) {
        transitionStatus('CANCELLED', 'Khách hàng', 'Khách hàng chủ động hủy đơn - Đã hoàn trả tồn kho');
    }
}
</script>
{% endblock %}

```

## app/templates/orders.html

```
{% extends "base.html" %}

{% block title %}Quản Lý Đơn Hàng - FDB Commerce{% endblock %}

{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
    <div>
        <h4 class="fw-bold mb-1 text-dark">Quản Lý Đơn Hàng & Giao Dịch</h4>
        <p class="text-muted small mb-0">Theo dõi trạng thái xử lý, vận chuyển & đối soát thanh toán trực tuyến</p>
    </div>
    <button class="btn btn-primary px-3 py-2 rounded-3 shadow-sm d-flex align-items-center gap-2" data-bs-toggle="modal" data-bs-target="#createOrderModal">
        <i class="fa-solid fa-cart-plus"></i>
        <span>Tạo Đơn Hàng Mới</span>
    </button>
</div>

<!-- Modern Search & Filter Console -->
<div class="card mb-4">
    <div class="card-body p-3">
        <form method="GET" action="/orders" class="row g-2 align-items-end">
            <div class="col-md-3">
                <label class="form-label small fw-semibold text-muted mb-1">Trạng Thái Đơn Hàng</label>
                <select name="status" class="form-select form-select-sm">
                    <option value="ALL" {% if filter_status == 'ALL' %}selected{% endif %}>Tất cả trạng thái</option>
                    {% for st in all_statuses %}
                    <option value="{{ st }}" {% if filter_status == st %}selected{% endif %}>{{ st }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-2">
                <label class="form-label small fw-semibold text-muted mb-1">Từ Ngày</label>
                <input type="date" name="from_date" class="form-control form-control-sm" value="{{ filter_from_date }}">
            </div>
            <div class="col-md-2">
                <label class="form-label small fw-semibold text-muted mb-1">Đến Ngày</label>
                <input type="date" name="to_date" class="form-control form-control-sm" value="{{ filter_to_date }}">
            </div>
            <div class="col-md-3">
                <label class="form-label small fw-semibold text-muted mb-1">Tìm Kiếm Nhanh</label>
                <input type="text" name="q" class="form-control form-control-sm" placeholder="Mã đơn, mã khách, địa chỉ..." value="{{ filter_q }}">
            </div>
            <div class="col-md-2 d-flex gap-1">
                <button type="submit" class="btn btn-sm btn-primary flex-grow-1">
                    <i class="fa-solid fa-magnifying-glass me-1"></i> Lọc
                </button>
                <a href="/orders" class="btn btn-sm btn-light border" title="Xóa bộ lọc">
                    <i class="fa-solid fa-rotate-left"></i>
                </a>
            </div>
        </form>
    </div>
</div>

<!-- Orders Table -->
<div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
        <div>
            <h6 class="fw-bold mb-0">Danh Sách Đơn Hàng ({{ orders|length }})</h6>
            <span class="text-muted small" style="font-size: 0.8rem;">Cập nhật đồng thời trên <code>order</code>, <code>idx_user_orders</code>, <code>idx_status_orders</code></span>
        </div>
        <span class="badge bg-light text-secondary border">ACID Multi-key Commit</span>
    </div>
    <div class="card-body p-0">
        <div class="table-responsive">
            <table class="table table-custom align-middle">
                <thead>
                    <tr>
                        <th class="ps-4">Mã Đơn Hàng</th>
                        <th>Khách Hàng</th>
                        <th>Mặt Hàng Đặt Mua</th>
                        <th>Địa Chỉ Giao Hàng</th>
                        <th>Tổng Giá Trị</th>
                        <th>Trạng Thái</th>
                        <th>Thời Gian Khởi Tạo</th>
                        <th class="text-end pe-4">Chi Tiết</th>
                    </tr>
                </thead>
                <tbody>
                    {% for o in orders %}
                    <tr>
                        <td class="ps-4">
                            <a href="/orders/{{ o.order_id }}" class="fw-bold text-decoration-none text-primary">
                                {{ o.order_id }}
                            </a>
                        </td>
                        <td>
                            <div class="d-flex align-items-center">
                                <div class="user-avatar-circle">{{ o.user_id[:2] }}</div>
                                <span class="fw-semibold text-dark">{{ o.user_id }}</span>
                            </div>
                        </td>
                        <td>
                            <span class="badge bg-light text-dark border">{{ o.items|length }} sản phẩm</span>
                        </td>
                        <td class="small text-muted text-truncate" style="max-width: 200px;" title="{{ o.shipping_address }}">
                            {{ o.shipping_address }}
                        </td>
                        <td class="fw-bold text-dark">{{ "{:,.0f}".format(o.total_amount) }} đ</td>
                        <td>
                            <span class="status-pill status-{{ o.status }}">
                                <span class="status-dot"></span>
                                {{ o.status }}
                            </span>
                        </td>
                        <td class="small text-muted">{{ o.created_at }}</td>
                        <td class="text-end pe-4">
                            <a href="/orders/{{ o.order_id }}" class="btn btn-sm btn-light border px-3 rounded-pill">
                                Chi tiết & Timeline
                            </a>
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="8" class="text-center py-4 text-muted">Không tìm thấy đơn hàng nào phù hợp với điều kiện tìm kiếm</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>

<!-- Modal: Tạo Đơn Hàng Mới -->
<div class="modal fade" id="createOrderModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content border-0 shadow">
            <div class="modal-header border-bottom">
                <h5 class="modal-title fw-bold">
                    <i class="fa-solid fa-cart-arrow-down text-primary me-2"></i> Tạo Đơn Hàng Mới
                </h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <form id="createOrderForm" onsubmit="submitCreateOrder(event)">
                <div class="modal-body">
                    <div class="p-3 bg-light rounded-3 mb-3 small border">
                        <div class="d-flex align-items-center gap-2 text-primary fw-semibold mb-1">
                            <i class="fa-solid fa-shield-halved"></i>
                            <span>Cam Kết Giao Dịch ACID (Atomic Inventory Reservation)</span>
                        </div>
                        <p class="mb-0 text-muted" style="font-size: 0.8rem;">
                            Hệ thống kiểm tra tồn kho đồng thời cho toàn bộ mặt hàng. Nếu bất kỳ mặt hàng nào thiếu kho, toàn bộ giao dịch sẽ tự động Rollback, đảm bảo không xảy ra sai lệch số lượng tồn.
                        </p>
                    </div>

                    <div class="row g-2 mb-3">
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Chọn Khách Hàng <span class="text-danger">*</span></label>
                            <select class="form-select" name="user_id" id="order_user_select" required onchange="onUserSelectChange()">
                                <option value="" disabled selected>-- Chọn khách hàng --</option>
                                {% for u in users %}
                                <option value="{{ u.user_id }}" data-address="{{ u.address }}" data-balance="{{ u.balance }}" data-status="{{ u.status }}">
                                    {{ u.name }} ({{ u.user_id }}) - Ví: {{ "{:,.0f}".format(u.balance) }} đ {% if u.status == 'locked' %}[BỊ KHÓA]{% endif %}
                                </option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Mã Đơn Hàng (Tự chọn hoặc để trống)</label>
                            <input type="text" class="form-control" name="custom_order_id" placeholder="Tự sinh nếu để trống">
                        </div>
                    </div>

                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Địa Chỉ Nhận Hàng <span class="text-danger">*</span></label>
                        <input type="text" class="form-control" name="shipping_address" id="order_shipping_address" required placeholder="Địa chỉ giao nhận...">
                    </div>

                    <hr class="my-3">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <h6 class="fw-bold mb-0">Danh Sách Mặt Hàng Đặt Mua</h6>
                        <button type="button" class="btn btn-sm btn-outline-primary" onclick="addItemRow()">
                            <i class="fa-solid fa-plus me-1"></i> Thêm Mặt Hàng
                        </button>
                    </div>

                    <div id="itemsContainer">
                        <div class="row g-2 mb-2 item-row align-items-center">
                            <div class="col-md-7">
                                <select class="form-select item-product-select" required onchange="calculateOrderTotal()">
                                    <option value="" disabled selected>-- Chọn sản phẩm --</option>
                                    {% for p in products %}
                                    <option value="{{ p.product_id }}" data-price="{{ p.price }}" data-stock="{{ p.stock }}">
                                        {{ p.name }} - {{ "{:,.0f}".format(p.price) }} đ (Kho: {{ p.stock }})
                                    </option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="col-md-3">
                                <div class="input-group">
                                    <span class="input-group-text small">SL</span>
                                    <input type="number" class="form-control item-qty" value="1" min="1" required onchange="calculateOrderTotal()">
                                </div>
                            </div>
                            <div class="col-md-2 text-end">
                                <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeItemRow(this)">
                                    <i class="fa-solid fa-trash-can"></i>
                                </button>
                            </div>
                        </div>
                    </div>

                    <div class="p-3 bg-light rounded-3 mt-3 d-flex justify-content-between align-items-center border">
                        <span class="fw-semibold text-muted">Dự Tính Tổng Tiền:</span>
                        <h4 class="fw-bold text-dark mb-0" id="orderTotalPreview">0 đ</h4>
                    </div>
                </div>
                <div class="modal-footer border-top">
                    <button type="button" class="btn btn-light" data-bs-dismiss="modal">Hủy</button>
                    <button type="submit" class="btn btn-primary px-4">
                        <i class="fa-solid fa-check me-1"></i> Xác Nhận Tạo Đơn
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
const productsList = {{ products_json | tojson }};

function onUserSelectChange() {
    const sel = document.getElementById('order_user_select');
    const opt = sel.options[sel.selectedIndex];
    const addr = opt.getAttribute('data-address');
    if (addr) {
        document.getElementById('order_shipping_address').value = addr;
    }
}

function addItemRow() {
    const container = document.getElementById('itemsContainer');
    const row = document.createElement('div');
    row.className = 'row g-2 mb-2 item-row align-items-center';

    let optionsHtml = '<option value="" disabled selected>-- Chọn sản phẩm --</option>';
    productsList.forEach(p => {
        optionsHtml += `<option value="${escapeHtml(p.product_id)}" data-price="${p.price}" data-stock="${p.stock}">
            ${escapeHtml(p.name)} - ${formatCurrencyVND(p.price)} (Kho: ${p.stock})
        </option>`;
    });

    row.innerHTML = `
        <div class="col-md-7">
            <select class="form-select item-product-select" required onchange="calculateOrderTotal()">
                ${optionsHtml}
            </select>
        </div>
        <div class="col-md-3">
            <div class="input-group">
                <span class="input-group-text small">SL</span>
                <input type="number" class="form-control item-qty" value="1" min="1" required onchange="calculateOrderTotal()">
            </div>
        </div>
        <div class="col-md-2 text-end">
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeItemRow(this)">
                <i class="fa-solid fa-trash-can"></i>
            </button>
        </div>
    `;
    container.appendChild(row);
}

function removeItemRow(btn) {
    const container = document.getElementById('itemsContainer');
    if (container.querySelectorAll('.item-row').length > 1) {
        btn.closest('.item-row').remove();
        calculateOrderTotal();
    } else {
        showErrorAlert('Đơn hàng phải có ít nhất 1 sản phẩm!');
    }
}

function calculateOrderTotal() {
    let total = 0;
    const rows = document.querySelectorAll('.item-row');
    rows.forEach(r => {
        const sel = r.querySelector('.item-product-select');
        const qty = parseInt(r.querySelector('.item-qty').value) || 0;
        if (sel.selectedIndex > 0) {
            const price = parseFloat(sel.options[sel.selectedIndex].getAttribute('data-price')) || 0;
            total += price * qty;
        }
    });
    document.getElementById('orderTotalPreview').innerText = formatCurrencyVND(total);
}

async function submitCreateOrder(e) {
    e.preventDefault();
    const form = e.target;
    const userId = form.user_id.value;
    const shippingAddress = form.shipping_address.value;
    const customOrderId = form.custom_order_id.value;

    const items = [];
    const rows = document.querySelectorAll('.item-row');
    rows.forEach(r => {
        const sel = r.querySelector('.item-product-select');
        const qty = parseInt(r.querySelector('.item-qty').value) || 0;
        if (sel.value) {
            items.push({
                product_id: sel.value,
                quantity: qty
            });
        }
    });

    if (items.length === 0) {
        showErrorAlert('Vui lòng chọn ít nhất 1 mặt hàng');
        return;
    }

    const payload = {
        user_id: userId,
        shipping_address: shippingAddress,
        custom_order_id: customOrderId,
        items: items
    };

    try {
        const resp = await fetch('/api/orders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (res.status === 'success') {
            showSuccessAlert('Tạo đơn hàng thành công!', () => {
                window.location.href = `/orders/${res.order.order_id}`;
            });
        } else {
            showErrorAlert(res.message);
        }
    } catch (err) {
        showErrorAlert(err.message);
    }
}
</script>
{% endblock %}

```

## app/templates/products.html

```
{% extends "base.html" %}

{% block title %}Danh Mục Sản Phẩm & Kho - FDB Commerce{% endblock %}

{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
    <div>
        <h4 class="fw-bold mb-1 text-dark">Quản Lý Danh Mục Hàng Hóa & Tồn Kho</h4>
        <p class="text-muted small mb-0">Theo dõi định giá, tồn kho thực tế & phân loại danh mục mặt hàng</p>
    </div>
    <button class="btn btn-primary px-3 py-2 rounded-3 shadow-sm d-flex align-items-center gap-2" data-bs-toggle="modal" data-bs-target="#addProductModal">
        <i class="fa-solid fa-plus"></i>
        <span>Thêm Sản Phẩm Mới</span>
    </button>
</div>

<!-- Category Filter Pills -->
<div class="card mb-4">
    <div class="card-body py-2">
        <div class="d-flex align-items-center gap-2 flex-wrap">
            <span class="small fw-semibold text-muted me-2"><i class="fa-solid fa-filter me-1"></i> Phân loại:</span>
            <a href="/products" class="btn btn-sm {% if not selected_cat %}btn-primary text-white{% else %}btn-light border{% endif %} rounded-pill px-3">
                Tất cả ({{ products|length }})
            </a>
            {% for cat in categories %}
            <a href="/products?category={{ cat }}" class="btn btn-sm {% if selected_cat == cat %}btn-primary text-white{% else %}btn-light border{% endif %} rounded-pill px-3">
                {{ cat }}
            </a>
            {% endfor %}
        </div>
    </div>
</div>

<!-- Products Table -->
<div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
        <div>
            <h6 class="fw-bold mb-0">Danh Sách Mặt Hàng ({{ products|length }})</h6>
            <span class="text-muted small" style="font-size: 0.8rem;">Cấu trúc Tuple: <code>('product', product_id)</code></span>
        </div>
        <span class="badge bg-light text-secondary border">Real-time Stock Tracking</span>
    </div>
    <div class="card-body p-0">
        <div class="table-responsive">
            <table class="table table-custom align-middle">
                <thead>
                    <tr>
                        <th class="ps-4">Mã Mặt Hàng</th>
                        <th>Tên Sản Phẩm</th>
                        <th>Danh Mục</th>
                        <th>Đơn Giá Bán</th>
                        <th>Số Lượng Tồn</th>
                        <th>Trạng Thái Kho</th>
                        <th>Cập Nhật Cuối</th>
                        <th class="text-end pe-4">Thao Tác</th>
                    </tr>
                </thead>
                <tbody>
                    {% for p in products %}
                    <tr>
                        <td class="ps-4">
                            <span class="badge bg-light text-dark border font-monospace">{{ p.product_id }}</span>
                        </td>
                        <td>
                            <div class="fw-bold text-dark">{{ p.name }}</div>
                            <div class="text-muted small" style="font-size: 0.78rem;">{{ p.description }}</div>
                        </td>
                        <td>
                            <span class="badge bg-indigo-subtle text-indigo border rounded-pill px-3" style="background: #e0e7ff; color: #4338ca;">
                                {{ p.category }}
                            </span>
                        </td>
                        <td class="fw-bold text-dark">{{ "{:,.0f}".format(p.price) }} đ</td>
                        <td>
                            <span class="fw-bold {% if p.stock <= 5 %}text-danger{% elif p.stock <= 15 %}text-warning{% else %}text-dark{% endif %}">
                                {{ p.stock }} cái
                            </span>
                        </td>
                        <td>
                            {% if p.stock > 0 %}
                                <span class="status-pill status-SUCCESS">
                                    <span class="status-dot"></span> Sẵn sàng giao
                                </span>
                            {% else %}
                                <span class="status-pill status-FAILED">
                                    <span class="status-dot"></span> Hết hàng
                                </span>
                            {% endif %}
                        </td>
                        <td class="text-muted small">{{ p.updated_at or '---' }}</td>
                        <td class="text-end pe-4">
                            <button class="btn btn-sm btn-light border px-2 py-1 rounded-2 me-1" onclick='openEditProductModal({{ p.to_dict() | tojson }})' title="Chỉnh sửa">
                                <i class="fa-solid fa-pen-to-square text-secondary"></i>
                            </button>
                            <button class="btn btn-sm btn-light border px-2 py-1 rounded-2 text-danger" onclick='deleteProduct({{ p.product_id | tojson }}, {{ p.name | tojson }})' title="Xóa">
                                <i class="fa-solid fa-trash-can"></i>
                            </button>
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="8" class="text-center py-4 text-muted">Không tìm thấy sản phẩm nào trong kho</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>

<!-- Modal: Thêm Sản Phẩm -->
<div class="modal fade" id="addProductModal" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content border-0 shadow">
            <div class="modal-header border-bottom">
                <h5 class="modal-title fw-bold">Thêm Sản Phẩm Vào Kho</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <form id="addProductForm" onsubmit="submitAddProduct(event)">
                <div class="modal-body">
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Mã Sản Phẩm <span class="text-danger">*</span></label>
                        <input type="text" class="form-control" name="product_id" required placeholder="Ví dụ: P005">
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Tên Sản Phẩm <span class="text-danger">*</span></label>
                        <input type="text" class="form-control" name="name" required placeholder="Ví dụ: Ổ Cứng SSD NVMe 1TB">
                    </div>
                    <div class="row g-2 mb-3">
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Danh Mục <span class="text-danger">*</span></label>
                            <input type="text" class="form-control" name="category" required placeholder="Linh kiện / Phụ kiện">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Đơn Giá (VNĐ) <span class="text-danger">*</span></label>
                            <input type="number" class="form-control" name="price" required min="0" value="1500000">
                        </div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Số Lượng Nhập Kho <span class="text-danger">*</span></label>
                        <input type="number" class="form-control" name="stock" required min="0" value="20">
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Mô Tả Kỹ Thuật</label>
                        <textarea class="form-control" name="description" rows="2" placeholder="Thông số..."></textarea>
                    </div>
                </div>
                <div class="modal-footer border-top">
                    <button type="button" class="btn btn-light" data-bs-dismiss="modal">Hủy</button>
                    <button type="submit" class="btn btn-primary px-4">Lưu Sản Phẩm</button>
                </div>
            </form>
        </div>
    </div>
</div>

<!-- Modal: Sửa Sản Phẩm -->
<div class="modal fade" id="editProductModal" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content border-0 shadow">
            <div class="modal-header border-bottom">
                <h5 class="modal-title fw-bold">Cập Nhật Thông Tin Mặt Hàng</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <form id="editProductForm" onsubmit="submitEditProduct(event)">
                <div class="modal-body">
                    <input type="hidden" name="product_id" id="edit_prod_id">
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Mã Sản Phẩm</label>
                        <input type="text" class="form-control bg-light" id="display_prod_id" disabled>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Tên Sản Phẩm</label>
                        <input type="text" class="form-control" name="name" id="edit_prod_name" required>
                    </div>
                    <div class="row g-2 mb-3">
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Danh Mục</label>
                            <input type="text" class="form-control" name="category" id="edit_prod_category" required>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Đơn Giá (VNĐ)</label>
                            <input type="number" class="form-control" name="price" id="edit_prod_price" required min="0">
                        </div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Số Lượng Tồn Kho</label>
                        <input type="number" class="form-control" name="stock" id="edit_prod_stock" required min="0">
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Mô Tả</label>
                        <textarea class="form-control" name="description" id="edit_prod_desc" rows="2"></textarea>
                    </div>
                </div>
                <div class="modal-footer border-top">
                    <button type="button" class="btn btn-light" data-bs-dismiss="modal">Hủy</button>
                    <button type="submit" class="btn btn-primary px-4">Lưu Thay Đổi</button>
                </div>
            </form>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
async function submitAddProduct(e) {
    e.preventDefault();
    const form = e.target;
    const payload = {
        product_id: form.product_id.value,
        name: form.name.value,
        category: form.category.value,
        price: parseFloat(form.price.value) || 0,
        stock: parseInt(form.stock.value) || 0,
        description: form.description.value
    };

    try {
        const resp = await fetch('/api/products', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (res.status === 'success') {
            showSuccessAlert('Đã thêm sản phẩm thành công!', () => location.reload());
        } else {
            showErrorAlert(res.message);
        }
    } catch (err) {
        showErrorAlert(err.message);
    }
}

function openEditProductModal(p) {
    document.getElementById('edit_prod_id').value = p.product_id;
    document.getElementById('display_prod_id').value = p.product_id;
    document.getElementById('edit_prod_name').value = p.name;
    document.getElementById('edit_prod_category').value = p.category;
    document.getElementById('edit_prod_price').value = p.price;
    document.getElementById('edit_prod_stock').value = p.stock;
    document.getElementById('edit_prod_desc').value = p.description || '';
    new bootstrap.Modal(document.getElementById('editProductModal')).show();
}

async function submitEditProduct(e) {
    e.preventDefault();
    const form = e.target;
    const prodId = form.product_id.value;
    const payload = {
        name: form.name.value,
        category: form.category.value,
        price: parseFloat(form.price.value) || 0,
        stock: parseInt(form.stock.value) || 0,
        description: form.description.value
    };

    try {
        const resp = await fetch(`/api/products/${prodId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (res.status === 'success') {
            showSuccessAlert('Cập nhật sản phẩm thành công!', () => location.reload());
        } else {
            showErrorAlert(res.message);
        }
    } catch (err) {
        showErrorAlert(err.message);
    }
}

async function deleteProduct(prodId, name) {
    const confirm = await Swal.fire({
        title: 'Xóa sản phẩm này?',
        text: `Bạn có chắc muốn xóa '${name}' (${prodId})?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#ef4444',
        confirmButtonText: 'Đồng ý xóa',
        cancelButtonText: 'Hủy'
    });

    if (confirm.isConfirmed) {
        try {
            const resp = await fetch(`/api/products/${prodId}`, { method: 'DELETE' });
            const res = await resp.json();
            if (res.status === 'success') {
                showSuccessAlert('Đã xóa sản phẩm khỏi kho!', () => location.reload());
            } else {
                showErrorAlert(res.message);
            }
        } catch (err) {
            showErrorAlert(err.message);
        }
    }
}
</script>
{% endblock %}

```

## app/templates/queries.html

```
{% extends 'base.html' %}
{% block content %}
<h3>Truy vấn nâng cao — Thành viên B</h3>
<p>Lọc bằng chỉ mục trạng thái + thời gian; ngày kết thúc được tính trọn ngày. Mỗi trang đọc trong một transaction.</p>
<form id="query" class="d-flex gap-3 flex-wrap mb-3">
<select name="status" class="form-select w-auto">{% for st in all_statuses %}<option {% if st == 'SUCCESS' %}selected{% endif %}>{{ st }}</option>{% endfor %}</select>
<label>Từ ngày <input name="from_date" type="date" value="2026-10-20"></label>
<label>Đến ngày <input name="to_date" type="date" value="2026-10-23"></label>
<label>Số dòng <input name="limit" type="number" value="50" min="1" max="200"></label>
<button class="btn btn-primary">Tìm đơn</button>
<button type="button" id="summary" class="btn btn-outline-primary">Tổng hợp giao dịch</button>
<button type="button" id="next" class="btn btn-outline-secondary" disabled>Trang tiếp</button>
</form>
<p>Tổng hợp giao dịch dùng quét prefix và tính bằng Python, giới hạn demo 10.000 giao dịch.</p>
<pre id="result" class="bg-white border p-3" style="white-space:pre-wrap" aria-live="polite"></pre>
{% endblock %}
{% block scripts %}
<script>
let cursor = null, savedQuery = '';
const form = document.getElementById('query'), output = document.getElementById('result'), next = document.getElementById('next');
async function run(summary = false, more = false) {
    const params = more ? new URLSearchParams(savedQuery) : new URLSearchParams(new FormData(form));
    if (!more) savedQuery = params.toString();
    if (more && cursor) params.set('cursor', cursor);
    next.disabled = true;
    try {
        const response = await fetch((summary ? '/api/queries/transactions/summary?' : '/api/queries/orders?') + params);
        const data = await response.json();
        output.textContent = JSON.stringify(data, null, 2);
        cursor = response.ok && !summary ? data.next_cursor : null;
        next.disabled = !cursor;
    } catch (e) { output.textContent = e.message; }
}
form.onsubmit = e => { e.preventDefault(); run(); };
document.getElementById('summary').onclick = () => run(true);
next.onclick = () => run(false, true);
</script>
{% endblock %}

```

## app/templates/reports.html

```
{% extends "base.html" %}

{% block title %}Báo Cáo & Kiểm Thử - FDB Commerce{% endblock %}

{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
    <div>
        <h4 class="fw-bold mb-1 text-dark">Báo Cáo Doanh Thu & Kiểm Thử ACID</h4>
        <p class="text-muted small mb-0">Thống kê hiệu suất tài chính, bài test giao dịch phân tán và sao lưu dữ liệu</p>
    </div>
</div>

<div class="row g-4 mb-4">
    <!-- ACID Testing Card -->
    <div class="col-lg-6">
        <div class="card h-100">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0 text-dark">
                    <i class="fa-solid fa-vial-circle-check me-2 text-indigo" style="color: #4f46e5;"></i> Kiểm Thử Tính Toàn Vẹn ACID
                </h6>
                <span class="badge bg-indigo-subtle text-indigo rounded-pill px-3" style="background: #e0e7ff; color: #4338ca;">
                    {{ "Strict Serializable" if db_info.mode == "REAL_FDB" else "Giả lập một tiến trình" }}
                </span>
            </div>
            <div class="card-body">
                <p class="text-muted small" style="line-height: 1.6;">
                    <strong>Kịch bản kiểm thử:</strong> Khởi tạo giao dịch đặt hàng gồm 2 sản phẩm (1 sản phẩm hợp lệ và 1 sản phẩm có số lượng vượt quá tồn kho).
                    FoundationDB cần phát hiện ngoại lệ và <strong>rollback 100%</strong>:
                    tồn kho của sản phẩm đầu tiên không được bị trừ dở dang và không có đơn hàng ma nào được lưu vào CSDL.
                </p>
                <div class="text-center my-3">
                    <button class="btn btn-outline-primary px-4 py-2 rounded-pill" onclick="runAcidTestDetailed()">
                        <i class="fa-solid fa-play me-1"></i> Thực Thi Kiểm Thử Giao Dịch
                    </button>
                </div>
                <div id="acidTestResultBox" style="display: none;" class="mt-3 p-3 bg-light border rounded-3 small">
                    <!-- Results injected here -->
                </div>
            </div>
        </div>
    </div>

    <!-- Backup & Restore Card -->
    <div class="col-lg-6">
        <div class="card h-100">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0 text-dark">
                    <i class="fa-solid fa-database me-2 text-success"></i> Sao Lưu & Phục Hồi Dữ Liệu (Snapshot)
                </h6>
                <span class="badge bg-success-subtle text-success rounded-pill px-3 border border-success-subtle">
                    Logical JSON Snapshot
                </span>
            </div>
            <div class="card-body">
                <p class="text-muted small" style="line-height: 1.6;">
                    Cung cấp cơ chế Snapshot kết xuất toàn bộ các Tuple Key-Value trong FoundationDB ra file JSON và phục hồi nguyên trạng khi có sự cố.
                </p>

                <div class="row g-3 mt-2">
                    <div class="col-sm-6">
                        <div class="p-3 border rounded-3 text-center bg-light h-100 d-flex flex-column justify-content-between">
                            <div>
                                <i class="fa-solid fa-file-export fs-3 text-success mb-2"></i>
                                <div class="fw-bold text-dark">Xuất Bản Sao Lưu</div>
                                <div class="small text-muted mb-3" style="font-size: 0.75rem;">Tải snapshot JSON toàn bộ dữ liệu</div>
                            </div>
                            <a href="/api/backup" class="btn btn-sm btn-success w-100 rounded-pill">
                                <i class="fa-solid fa-download me-1"></i> Tải Snapshot File
                            </a>
                        </div>
                    </div>
                    <div class="col-sm-6">
                        <div class="p-3 border rounded-3 text-center bg-light h-100 d-flex flex-column justify-content-between">
                            <div>
                                <i class="fa-solid fa-file-import fs-3 text-primary mb-2"></i>
                                <div class="fw-bold text-dark">Phục Hồi Dữ Liệu</div>
                                <div class="small text-muted mb-3" style="font-size: 0.75rem;">Nạp file snapshot vào FoundationDB</div>
                            </div>
                            <div>
                                <button class="btn btn-sm btn-outline-primary w-100 rounded-pill" onclick="triggerRestoreInput()">
                                    <i class="fa-solid fa-upload me-1"></i> Chọn File Restore
                                </button>
                                <input type="file" id="restoreFileInput" style="display: none;" accept=".json" onchange="submitRestore(this)">
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Concurrent Race Condition Testing Card -->
<div class="card mb-4 border-primary-subtle">
    <div class="card-header bg-primary-subtle bg-opacity-25 d-flex justify-content-between align-items-center flex-wrap gap-2">
        <h6 class="fw-bold mb-0 text-dark">
            <i class="fa-solid fa-bolt-lightning me-2 text-warning"></i> Mô Phỏng Tranh Chấp Đồng Thời (Flash Sale Race Condition Test)
        </h6>
        <span class="badge bg-primary text-white rounded-pill px-3">
            Serializable OCC (10 Threads Song Song)
        </span>
    </div>
    <div class="card-body">
        <div class="row align-items-center">
            <div class="col-lg-8">
                <p class="text-muted small mb-2" style="line-height: 1.6;">
                    <strong>Kịch bản thực tế:</strong> Tạo một sản phẩm Flash Sale cực hiếm chỉ còn đúng <strong>1 cái tồn kho</strong>.
                    Kích hoạt đồng thời <strong>10 luồng người dùng (Threads)</strong> bấm mua tại sau cùng một hàng rào đồng bộ.
                </p>
                <p class="text-muted small mb-0" style="line-height: 1.6;">
                    <strong>Mục tiêu kiểm chứng:</strong> Cấp độ cô lập {{ "Strict Serializable" if db_info.mode == "REAL_FDB" else "Giả lập một tiến trình" }} của FoundationDB phải ngăn chặn tuyệt đối lỗi <em>bán vượt tồn kho (Overselling)</em>:
                    <strong>Chính xác 1 đơn hàng thành công</strong> và <strong>9 đơn hàng còn lại bị từ chối/rollback an toàn</strong>, tồn kho cuối cùng về đúng 0 cái.
                </p>
            </div>
            <div class="col-lg-4 text-lg-end text-center mt-3 mt-lg-0">
                <button class="btn btn-primary px-4 py-2 rounded-pill shadow-sm" onclick="runConcurrentStressTest()">
                    <i class="fa-solid fa-play me-2"></i> Chạy Stress Test Tranh Chấp
                </button>
            </div>
        </div>

        <div id="concurrentResultBox" style="display: none;" class="mt-4 p-3 bg-light border rounded-3">
            <!-- Results injected here -->
        </div>
    </div>
</div>

<!-- Revenue & Performance Metrics Table -->
<div class="card">
    <div class="card-header">
        <h6 class="fw-bold mb-0 text-dark">
            <i class="fa-solid fa-chart-simple me-2 text-primary"></i> Tổng Hợp Hiệu Suất Tài Chính
        </h6>
    </div>
    <div class="card-body">
        <div class="row g-3">
            <div class="col-md-4">
                <div class="p-3 bg-light border rounded-3">
                    <span class="text-muted small">Tổng Doanh Số Đã Thanh Toán:</span>
                    <h4 class="fw-bold text-dark mt-1 mb-0">{{ "{:,.0f}".format(summary.total_revenue) }} đ</h4>
                </div>
            </div>
            <div class="col-md-4">
                <div class="p-3 bg-light border rounded-3">
                    <span class="text-muted small">Số Tiền Qua Cổng Giao Dịch:</span>
                    <h4 class="fw-bold text-dark mt-1 mb-0">{{ "{:,.0f}".format(summary.total_txn_amount) }} đ</h4>
                </div>
            </div>
            <div class="col-md-4">
                <div class="p-3 bg-light border rounded-3">
                    <span class="text-muted small">Tỷ Lệ Thanh Toán Thành Công:</span>
                    <h4 class="fw-bold text-success mt-1 mb-0">{{ summary.success_rate }}%</h4>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
async function runAcidTestDetailed() {
    const box = document.getElementById('acidTestResultBox');
    box.style.display = 'block';
    box.innerHTML = '<div class="text-center text-muted py-2"><span class="spinner-border spinner-border-sm me-2"></span> Đang thực thi giao dịch ACID...</div>';

    try {
        const resp = await fetch('/api/acid-test', { method: 'POST' });
        const data = await resp.json();

        box.innerHTML = `
            <div class="alert alert-success py-2 mb-2">
                <strong><i class="fa-solid fa-circle-check me-1"></i> ${data.test_name}</strong>
            </div>
            <table class="table table-sm table-borderless mb-0">
                <tr><td class="text-muted">Sản phẩm thử nghiệm:</td><td class="fw-bold"><code>${escapeHtml(data.tested_product)}</code></td></tr>
                <tr><td class="text-muted">Tồn kho ban đầu:</td><td class="fw-bold">${data.initial_stock} cái</td></tr>
                <tr><td class="text-muted">Số lượng cố tình trừ:</td><td class="fw-bold text-danger">${data.attempted_deduction} cái</td></tr>
                <tr><td class="text-muted">Ngoại lệ phát hiện:</td><td class="text-danger small">${escapeHtml(data.error_message)}</td></tr>
                <tr><td class="text-muted">Tồn kho sau Rollback:</td><td class="fw-bold text-success">${data.final_stock} cái (Không bị hao hụt)</td></tr>
                <tr><td class="text-muted">Trạng thái đơn hàng:</td><td class="fw-bold">${data.order_created ? 'Bị lỗi' : '<span class="text-success">An toàn (Rollback hoàn toàn)</span>'}</td></tr>
            </table>
            <div class="p-2 bg-white border rounded mt-2 text-success fw-semibold">
                ${escapeHtml(data.conclusion)}
            </div>
        `;
    } catch (e) {
        box.innerHTML = `<div class="alert alert-danger mb-0">Lỗi kiểm thử: ${escapeHtml(e.message)}</div>`;
    }
}

async function runConcurrentStressTest() {
    const box = document.getElementById('concurrentResultBox');
    box.style.display = 'block';
    box.innerHTML = '<div class="text-center text-primary py-3"><span class="spinner-border spinner-border-sm me-2"></span> Đang khởi chạy 10 luồng song song bắn request cùng 1 mili-giây...</div>';

    try {
        const resp = await fetch('/api/concurrent-test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ threads: 10, stock: 1 })
        });
        const data = await resp.json();

        let logsHtml = '';
        data.thread_logs.forEach(log => {
            const isSuccess = log.status === 'SUCCESS';
            const badgeClass = isSuccess ? 'bg-success text-white' : 'bg-danger-subtle text-danger border border-danger-subtle';
            logsHtml += `
                <div class="d-flex justify-content-between align-items-center py-1 px-2 border-bottom small">
                    <span class="fw-semibold text-dark"><i class="fa-solid fa-microchip me-1 text-muted"></i> ${log.thread} (${escapeHtml(log.user)})</span>
                    <span class="badge ${badgeClass} rounded-pill">${log.status}</span>
                    <span class="text-muted small">${escapeHtml(log.message)}</span>
                </div>
            `;
        });

        box.innerHTML = `
            <div class="alert ${data.no_overselling ? 'alert-success' : 'alert-danger'} py-2 mb-3 d-flex justify-content-between align-items-center flex-wrap gap-2">
                <span class="fw-bold"><i class="fa-solid fa-shield-halved me-1"></i> ${data.test_name}</span>
                <span class="badge bg-dark rounded-pill">${data.elapsed_ms} ms</span>
            </div>

            <div class="row g-2 mb-3 text-center">
                <div class="col-6 col-md-3">
                    <div class="p-2 bg-white border rounded-3">
                        <div class="text-muted small">Luồng Song Song</div>
                        <div class="fs-5 fw-bold text-dark">${data.concurrent_requests}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="p-2 bg-white border rounded-3">
                        <div class="text-muted small">Tồn Kho Ban Đầu</div>
                        <div class="fs-5 fw-bold text-warning">${data.initial_stock} cái</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="p-2 bg-white border rounded-3">
                        <div class="text-muted small">Đơn Thành Công</div>
                        <div class="fs-5 fw-bold text-success">${data.successful_orders}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="p-2 bg-white border rounded-3">
                        <div class="text-muted small">Bị Chặn / Từ Chối</div>
                        <div class="fs-5 fw-bold text-danger">${data.rejected_orders}</div>
                    </div>
                </div>
            </div>

            <div class="card border mb-3">
                <div class="card-header bg-white py-2 small fw-bold text-dark">
                    <i class="fa-solid fa-list-check me-1 text-primary"></i> Nhật Ký Xử Lý Chi Tiết Của 10 Luồng:
                </div>
                <div class="card-body p-0" style="max-height: 220px; overflow-y: auto;">
                    ${logsHtml}
                </div>
            </div>

            <div class="p-3 bg-white border rounded-3 text-success fw-semibold small">
                <i class="fa-solid fa-check-double me-1 text-success fs-6"></i> ${escapeHtml(data.conclusion)}
            </div>
        `;
    } catch (e) {
        box.innerHTML = `<div class="alert alert-danger mb-0">Lỗi kiểm thử tranh chấp: ${escapeHtml(e.message)}</div>`;
    }
}

function triggerRestoreInput() {
    document.getElementById('restoreFileInput').click();
}

async function submitRestore(input) {
    if (!input.files || input.files.length === 0) return;
    const file = input.files[0];
    const formData = new FormData();
    formData.append('backup_file', file);

    Swal.fire({
        title: 'Đang phục hồi dữ liệu...',
        text: 'Đang nạp snapshot vào FoundationDB',
        allowOutsideClick: false,
        didOpen: () => Swal.showLoading()
    });

    try {
        const resp = await fetch('/api/restore', {
            method: 'POST',
            body: formData
        });
        const res = await resp.json();
        if (res.status === 'success') {
            showSuccessAlert(`Phục hồi thành công ${res.restored_records} bản ghi!`, () => location.reload());
        } else {
            showErrorAlert(res.message);
        }
    } catch (err) {
        showErrorAlert(err.message);
    }
}
</script>
{% endblock %}

```

## app/templates/users.html

```
{% extends "base.html" %}

{% block title %}Quản Lý Khách Hàng - FDB Commerce{% endblock %}

{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
    <div>
        <h4 class="fw-bold mb-1 text-dark">Quản Lý Tài Khoản Khách Hàng</h4>
        <p class="text-muted small mb-0">Quản lý hồ sơ định danh, số dư tài khoản & chỉ mục tra cứu email</p>
    </div>
    <button class="btn btn-primary px-3 py-2 rounded-3 shadow-sm d-flex align-items-center gap-2" data-bs-toggle="modal" data-bs-target="#addUserModal">
        <i class="fa-solid fa-user-plus"></i>
        <span>Thêm Khách Hàng</span>
    </button>
</div>

<!-- Users List Table -->
<div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
        <div>
            <h6 class="fw-bold mb-0">Danh Sách Người Dùng ({{ users|length }})</h6>
            <span class="text-muted small" style="font-size: 0.8rem;">Tra cứu phạm vi Tuple: <code>('user', user_id)</code></span>
        </div>
        <span class="badge bg-light text-secondary border">Index: idx_users_email</span>
    </div>
    <div class="card-body p-0">
        <div class="table-responsive">
            <table class="table table-custom align-middle">
                <thead>
                    <tr>
                        <th class="ps-4">Mã Người Dùng</th>
                        <th>Họ và Tên</th>
                        <th>Email Định Danh</th>
                        <th>Số Điện Thoại</th>
                        <th>Vai Trò</th>
                        <th>Số Dư Ví</th>
                        <th>Trạng Thái</th>
                        <th class="text-end pe-4">Thao Tác</th>
                    </tr>
                </thead>
                <tbody>
                    {% for u in users %}
                    <tr>
                        <td class="ps-4">
                            <span class="badge bg-light text-dark border font-monospace">{{ u.user_id }}</span>
                        </td>
                        <td>
                            <div class="d-flex align-items-center">
                                <div class="user-avatar-circle">{{ u.name[:2].upper() }}</div>
                                <div>
                                    <div class="fw-bold text-dark">{{ u.name }}</div>
                                    <div class="text-muted small" style="font-size: 0.75rem;">{{ u.address or 'Việt Nam' }}</div>
                                </div>
                            </div>
                        </td>
                        <td>
                            <code class="text-primary">{{ u.email }}</code>
                        </td>
                        <td>{{ u.phone or '---' }}</td>
                        <td>
                            {% if u.role == 'admin' %}
                                <span class="badge bg-danger-subtle text-danger border border-danger-subtle rounded-pill px-3">Quản trị viên</span>
                            {% elif u.role == 'merchant' %}
                                <span class="badge bg-warning-subtle text-warning border border-warning-subtle rounded-pill px-3">Đối tác bán hàng</span>
                            {% else %}
                                <span class="badge bg-secondary-subtle text-secondary border border-secondary-subtle rounded-pill px-3">Khách hàng</span>
                            {% endif %}
                        </td>
                        <td class="fw-bold text-dark">{{ "{:,.0f}".format(u.balance) }} đ</td>
                        <td>
                            {% if u.status == 'active' %}
                                <span class="status-pill status-SUCCESS">
                                    <span class="status-dot"></span> Đang hoạt động
                                </span>
                            {% else %}
                                <span class="status-pill status-FAILED">
                                    <span class="status-dot"></span> Bị tạm khóa
                                </span>
                            {% endif %}
                        </td>
                        <td class="text-end pe-4">
                            <button class="btn btn-sm btn-light border px-2 py-1 rounded-2 me-1" onclick='openEditModal({{ u.to_dict() | tojson }})' title="Chỉnh sửa">
                                <i class="fa-solid fa-pen-to-square text-secondary"></i>
                            </button>
                            <button class="btn btn-sm btn-light border px-2 py-1 rounded-2 text-danger" onclick='deleteUser({{ u.user_id | tojson }}, {{ u.name | tojson }})' title="Xóa">
                                <i class="fa-solid fa-trash-can"></i>
                            </button>
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="8" class="text-center py-4 text-muted">Chưa có người dùng nào được khởi tạo</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>

<!-- Modal: Thêm Người Dùng -->
<div class="modal fade" id="addUserModal" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content border-0 shadow">
            <div class="modal-header border-bottom">
                <h5 class="modal-title fw-bold">Thêm Khách Hàng Mới</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <form id="addUserForm" onsubmit="submitAddUser(event)">
                <div class="modal-body">
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Mã Định Danh (User ID) <span class="text-danger">*</span></label>
                        <input type="text" class="form-control" name="user_id" required placeholder="Ví dụ: U104">
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Họ và Tên <span class="text-danger">*</span></label>
                        <input type="text" class="form-control" name="name" required placeholder="Ví dụ: Đỗ Gia Bảo">
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Email <span class="text-danger">*</span></label>
                        <input type="email" class="form-control" name="email" required placeholder="bao.do@gmail.com">
                        <div class="form-text" style="font-size: 0.78rem;">Được lập chỉ mục phụ để tra cứu nhanh</div>
                    </div>
                    <div class="row g-2 mb-3">
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Số Điện Thoại</label>
                            <input type="text" class="form-control" name="phone" placeholder="09xxxxxxxx">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Số Dư Ban Đầu (VNĐ)</label>
                            <input type="number" class="form-control" name="balance" value="10000000" min="0">
                        </div>
                    </div>
                    <div class="row g-2 mb-3">
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Phân Loại Vai Trò</label>
                            <select class="form-select" name="role">
                                <option value="customer" selected>Khách hàng (Customer)</option>
                                <option value="merchant">Đối tác (Merchant)</option>
                                <option value="admin">Quản trị viên (Admin)</option>
                            </select>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Trạng Thái Hoạt Động</label>
                            <select class="form-select" name="status">
                                <option value="active" selected>Hoạt động (Active)</option>
                                <option value="locked">Tạm khóa (Locked)</option>
                            </select>
                        </div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Địa Chỉ Nhận Hàng</label>
                        <textarea class="form-control" name="address" rows="2" placeholder="Số nhà, đường, quận/huyện..."></textarea>
                    </div>
                </div>
                <div class="modal-footer border-top">
                    <button type="button" class="btn btn-light" data-bs-dismiss="modal">Hủy</button>
                    <button type="submit" class="btn btn-primary px-4">Lưu Bản Ghi</button>
                </div>
            </form>
        </div>
    </div>
</div>

<!-- Modal: Sửa Người Dùng -->
<div class="modal fade" id="editUserModal" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content border-0 shadow">
            <div class="modal-header border-bottom">
                <h5 class="modal-title fw-bold">Cập Nhật Thông Tin</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <form id="editUserForm" onsubmit="submitEditUser(event)">
                <div class="modal-body">
                    <input type="hidden" name="user_id" id="edit_user_id">
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Mã Định Danh</label>
                        <input type="text" class="form-control bg-light" id="display_user_id" disabled>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Họ và Tên</label>
                        <input type="text" class="form-control" name="name" id="edit_name" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Email</label>
                        <input type="email" class="form-control" name="email" id="edit_email" required>
                    </div>
                    <div class="row g-2 mb-3">
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Số Điện Thoại</label>
                            <input type="text" class="form-control" name="phone" id="edit_phone">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Số Dư Tài Khoản (VNĐ)</label>
                            <input type="number" class="form-control" name="balance" id="edit_balance" min="0">
                        </div>
                    </div>
                    <div class="row g-2 mb-3">
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Vai Trò</label>
                            <select class="form-select" name="role" id="edit_role">
                                <option value="customer">Khách hàng</option>
                                <option value="merchant">Đối tác</option>
                                <option value="admin">Quản trị viên</option>
                            </select>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small fw-semibold">Trạng Thái</label>
                            <select class="form-select" name="status" id="edit_status">
                                <option value="active">Hoạt động</option>
                                <option value="locked">Tạm khóa</option>
                            </select>
                        </div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-semibold">Địa Chỉ</label>
                        <textarea class="form-control" name="address" id="edit_address" rows="2"></textarea>
                    </div>
                </div>
                <div class="modal-footer border-top">
                    <button type="button" class="btn btn-light" data-bs-dismiss="modal">Hủy</button>
                    <button type="submit" class="btn btn-primary px-4">Cập Nhật</button>
                </div>
            </form>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
async function submitAddUser(e) {
    e.preventDefault();
    const form = e.target;
    const payload = {
        user_id: form.user_id.value,
        name: form.name.value,
        email: form.email.value,
        phone: form.phone.value,
        balance: parseFloat(form.balance.value) || 0,
        role: form.role.value,
        status: form.status.value,
        address: form.address.value
    };

    try {
        const resp = await fetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (res.status === 'success') {
            showSuccessAlert('Đã thêm khách hàng thành công!', () => location.reload());
        } else {
            showErrorAlert(res.message);
        }
    } catch (err) {
        showErrorAlert(err.message);
    }
}

function openEditModal(userData) {
    document.getElementById('edit_user_id').value = userData.user_id;
    document.getElementById('display_user_id').value = userData.user_id;
    document.getElementById('edit_name').value = userData.name;
    document.getElementById('edit_email').value = userData.email;
    document.getElementById('edit_phone').value = userData.phone || '';
    document.getElementById('edit_balance').value = userData.balance || 0;
    document.getElementById('edit_role').value = userData.role || 'customer';
    document.getElementById('edit_status').value = userData.status || 'active';
    document.getElementById('edit_address').value = userData.address || '';
    
    new bootstrap.Modal(document.getElementById('editUserModal')).show();
}

async function submitEditUser(e) {
    e.preventDefault();
    const form = e.target;
    const userId = form.user_id.value;
    const payload = {
        name: form.name.value,
        email: form.email.value,
        phone: form.phone.value,
        balance: parseFloat(form.balance.value) || 0,
        role: form.role.value,
        status: form.status.value,
        address: form.address.value
    };

    try {
        const resp = await fetch(`/api/users/${userId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (res.status === 'success') {
            showSuccessAlert('Cập nhật hồ sơ thành công!', () => location.reload());
        } else {
            showErrorAlert(res.message);
        }
    } catch (err) {
        showErrorAlert(err.message);
    }
}

async function deleteUser(userId, name) {
    const confirm = await Swal.fire({
        title: 'Xóa tài khoản này?',
        text: `Bạn có chắc muốn xóa khách hàng '${name}' (${userId})?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#ef4444',
        confirmButtonText: 'Đồng ý xóa',
        cancelButtonText: 'Hủy'
    });

    if (confirm.isConfirmed) {
        try {
            const resp = await fetch(`/api/users/${userId}`, { method: 'DELETE' });
            const res = await resp.json();
            if (res.status === 'success') {
                showSuccessAlert('Đã xóa tài khoản!', () => location.reload());
            } else {
                showErrorAlert(res.message);
            }
        } catch (err) {
            showErrorAlert(err.message);
        }
    }
}
</script>
{% endblock %}

```

## init_data.py

```python
"""Explicit dataset reset: python init_data.py --reset (demo database only)."""
import argparse
from app.services.seed_service import SeedService
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true')
    args = parser.parse_args()
    print(SeedService.populate_seed_data(clear_existing=args.reset))

```

## query_data.py

```python
"""Read-only reproducible query examples; initialize seed explicitly beforehand."""
import json
from app.services.user_service import UserService
from app.services.order_service import OrderService
from app.services.query_service import QueryService

def show(name, value):
    print(name)
    print(json.dumps(value, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    user = UserService.get_user_by_id('U101')
    order = OrderService.get_order_by_id('ORD2001')
    show('Q1 User U101', user.to_dict() if user else None)
    show('Q2 Order ORD2001', order.to_dict() if order else None)
    show('Q3 Orders U101', [o.to_dict() for o in OrderService.get_orders_by_user('U101')])
    show('Q4 History ORD2001', [h.to_dict() for h in OrderService.get_order_history('ORD2001')])
    show('Q5 SUCCESS 20–23/10/2026', QueryService.orders_by_status_time('SUCCESS', '2026-10-20', '2026-10-23'))
    show('Q6 Transactions ORD2001', [t.to_dict() for t in OrderService.get_order_transactions('ORD2001')])
    show('Q7 Summary', QueryService.transaction_summary())

```

## requirements.txt

```
Flask>=3.0.0
Flask-Cors>=4.0.0
python-docx>=1.1.0
foundationdb==7.3.77
sortedcontainers>=2.4.0,<3

```

## scripts/verify_real_backend.py

```python
"""Run from project root: python scripts/verify_real_backend.py.
Requires FDB_MODE=real. Creates unique test records and removes only those records.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uuid import uuid4
from app.database import db_manager, get_db
from app.database.schema import (user_key, product_key, order_key, transaction_key,
    idx_user_email_key, idx_user_orders_key, idx_status_orders_key, get_prefix_range)
from app.models.user import User
from app.models.product import Product
from app.services.user_service import UserService
from app.services.product_service import ProductService
from app.services.order_service import OrderService

if __name__ == '__main__':
    if db_manager.mode != 'REAL_FDB':
        raise SystemExit('STOP: cần FDB_MODE=real và kết nối FoundationDB thật')
    token = uuid4().hex
    uid, pid, oid, bad_oid = ('CHECK-U-'+token, 'CHECK-P-'+token, 'CHECK-O-'+token, 'CHECK-BAD-'+token)
    email = token+'@example.test'
    receipt_id='CHECK-T-'+token
    try:
        UserService.create_user(User(uid, 'Integration test', email))
        ProductService.create_product(Product(pid, 'Integration test', 'test', 1000, 2))
        OrderService.create_order(uid,[{'product_id':pid,'quantity':1}], 'Integration',oid)
        try:
            OrderService.create_order(uid,[{'product_id':pid,'quantity':1},{'product_id':pid,'quantity':99}], 'Integration',bad_oid)
            raise AssertionError('Expected insufficient-stock rejection')
        except ValueError:
            assert OrderService.get_order_by_id(bad_oid) is None
        assert ProductService.get_product_by_id(pid).stock == 1
        first=OrderService.process_payment(oid,'MOMO',request_id=receipt_id)
        second=OrderService.process_payment(oid,'MOMO',request_id=receipt_id)
        assert first['transaction'] == second['transaction'] and second['replayed']
        assert OrderService.update_order_status(oid,'PROCESSING').status == 'PROCESSING'
        assert len(OrderService.get_order_history(oid)) == 3
        print('PASS REAL_FDB: read/write, rollback, payment idempotency, status/index/history transaction')
    finally:
        @db_manager.transactional
        def cleanup(tr):
            for order_id in (oid,bad_oid):
                tr.clear(order_key(order_id))
                tr.clear(idx_user_orders_key(uid,order_id))
                for prefix in ('order_item','order_history'):
                    tr.clear_range(*get_prefix_range((prefix,order_id)))
                from app.database.schema import APP_PREFIXES, unpack_key
                for k,_ in tr.get_range(*get_prefix_range(('idx_status_orders',))):
                    if unpack_key(k)[3] == order_id: tr.clear(k)
            for k in (user_key(uid),idx_user_email_key(email),product_key(pid),transaction_key('REQ-'+receipt_id)):
                tr.clear(k)
        cleanup(get_db())

```

## tests/test_regressions.py

```python
import os
os.environ['FDB_MODE'] = 'local'
os.environ['AUTO_SEED'] = '0'
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app.database import db_manager, encode_value, product_key, get_prefix_range, order_key
from app.database.fdb_engine import FDBLocalEngine, FDBConflictError
from app.services.seed_service import SeedService
from app.services.order_service import OrderService as Orders
from app.services.product_service import ProductService as Products
from app.services.user_service import UserService as Users
from app.services.query_service import QueryService as Queries
from app.services.backup_service import BackupService

@pytest.fixture(autouse=True)
def isolated_db():
    old = db_manager.local_engine
    db_manager.local_engine = FDBLocalEngine()
    SeedService.populate_seed_data()
    yield
    db_manager.local_engine = old

def create(oid='TEST', qty=1):
    return Orders.create_order('U101', [{'product_id':'P001','quantity':qty}], 'HCM', oid)

def test_rollback_after_partial_writes():
    before = dict(db_manager.local_engine.data)
    with pytest.raises(ValueError):
        Orders.create_order('U101', [{'product_id':'P001','quantity':1}, {'product_id':'P001','quantity':999}], 'HCM', 'BAD')
    assert dict(db_manager.local_engine.data) == before

def test_missing_product_rejected():
    with pytest.raises(ValueError):
        Orders.create_order('U101', [{'product_id':'NONE','quantity':1,'price':1}], 'HCM')

def test_cancel_once_and_return_current_order():
    create()
    first = Orders.update_order_status('TEST', 'CANCELLED')
    assert first.status == 'CANCELLED'
    assert Products.get_product_by_id('P001').stock == 25
    count = len(Orders.get_order_history('TEST'))
    Orders.update_order_status('TEST', 'CANCELLED')
    assert Products.get_product_by_id('P001').stock == 25
    assert len(Orders.get_order_history('TEST')) == count
    with pytest.raises(ValueError): Orders.process_payment('TEST', 'MOMO')

@pytest.mark.parametrize('status',['GARBAGE','SHIPPING','SUCCESS','FAILED'])
def test_invalid_transition(status):
    create()
    with pytest.raises(ValueError): Orders.update_order_status('TEST', status)

def test_payment_retry_and_index_consistency():
    create()
    first = Orders.process_payment('TEST','MOMO', request_id='receipt1')
    again = Orders.process_payment('TEST','MOMO', request_id='receipt1')
    assert first['transaction'] == again['transaction']
    assert len(Orders.get_order_transactions('TEST')) == 1
    assert Orders.get_order_transactions('TEST')[0].payment_method == 'MOMO'
    assert Orders.get_order_transactions('TEST')[0].user_id == 'U101'
    assert 'TEST' not in [o.order_id for o in Orders.get_orders_by_status('PENDING')]
    assert 'TEST' in [o.order_id for o in Orders.get_orders_by_status('SUCCESS')]
    assert len(Orders.get_order_history('TEST')) == 2
    with pytest.raises(ValueError): Orders.process_payment('TEST','MOMO')
    with pytest.raises(ValueError): Orders.update_order_status('TEST','CANCELLED')

def test_failure_and_retry_flow():
    create()
    Orders.process_payment('TEST', 'VNPAY', False)
    assert Orders.get_order_by_id('TEST').status == 'FAILED'
    Orders.update_order_status('TEST','PENDING')
    Orders.process_payment('TEST','VNPAY',True)
    for state in ['PROCESSING','SHIPPING','DELIVERED','COMPLETED']:
        assert Orders.update_order_status('TEST',state).status == state
    assert len(Orders.get_order_history('TEST')) == 8

def test_concurrent_buy_last_item():
    Products.update_product('P001',{'stock':1})
    barrier = Barrier(10)
    def buy(i):
        barrier.wait()
        try: create('RACE'+str(i)); return True
        except ValueError: return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(buy,range(10))) == 1
    assert Products.get_product_by_id('P001').stock == 0

def test_seed_update_ids_and_referential_delete():
    assert Users.update_user('U101',{'name':'Updated'}).user_id == 'U101'
    assert Products.update_product('P001',{'name':'Updated'}).product_id == 'P001'
    with pytest.raises(ValueError): Users.delete_user('U101')
    with pytest.raises(ValueError): Products.delete_product('P001')
    assert SeedService.populate_seed_data(False)['skipped']
    assert Users.get_user_by_id('U101').name == 'Updated'

def test_query_date_cursor_and_summary():
    page = Queries.orders_by_status_time('SUCCESS','2026-10-20','2026-10-23',1)
    assert [o['order_id'] for o in page['orders']] == ['ORD2001']
    second = Queries.orders_by_status_time('SUCCESS','2026-10-20','2026-10-23',1,page['next_cursor'])
    assert [o['order_id'] for o in second['orders']] == ['ORD2004']
    assert second['next_cursor'] is None
    assert len(Queries.orders_by_status_time('SUCCESS','2026-10-23','2026-10-23')['orders']) == 1
    assert Queries.transaction_summary()['successful_amount'] == 2700000
    assert Queries.transaction_summary()['count'] == 3
    with pytest.raises(ValueError): Queries.orders_by_status_time('SUCCESS','2026-10-23','2026-10-20')
    with pytest.raises(ValueError): Queries.orders_by_status_time('FAILED',cursor=page['next_cursor'])

def test_snapshot_phantom_conflict_and_clear_then_set():
    engine = FDBLocalEngine()
    one,two = engine.create_transaction(),engine.create_transaction()
    assert one.get_range(b'a',b'z') == []
    two.set(b'b',b'2');two.commit()
    one.set(b'c',b'3')
    with pytest.raises(FDBConflictError): one.commit()
    tr=engine.create_transaction();tr.clear_range(b'a',b'z');tr.set(b'b',b'new');tr.commit()
    assert engine.get(b'b') == b'new'

def test_disk_error_does_not_commit(monkeypatch):
    engine=FDBLocalEngine()
    def fail(): raise OSError('disk full')
    monkeypatch.setattr(engine,'save_to_disk',fail)
    with pytest.raises(OSError): engine.set(b'a',b'1')
    assert engine.get(b'a') is None

def test_backup_restore_and_invalid_input(tmp_path):
    file=tmp_path/'backup.json';BackupService.export_backup(str(file))
    before=dict(db_manager.local_engine.data)
    create()
    BackupService.restore_backup(str(file))
    assert dict(db_manager.local_engine.data) == before
    file.write_text(json.dumps({'records':[{'key_hex':'invalid','val_hex':''}]}))
    with pytest.raises(ValueError): BackupService.restore_backup(str(file))
    assert dict(db_manager.local_engine.data) == before

def test_api_and_templates():
    from app.main import create_app
    client=create_app().test_client()
    for path in ['/','/users','/products','/orders','/orders/ORD2001','/kv-explorer','/reports','/queries']:
        assert client.get(path).status_code == 200, path
    create()
    assert client.post('/api/orders/TEST/pay',json={'is_success':'false'}).status_code == 400
    assert client.get('/api/queries/orders?status=SUCCESS&limit=1').status_code == 200
    assert client.get('/api/queries/transactions/summary').json['count'] == 3
    assert client.get('/api/queries/orders?status=INVALID').status_code == 400

def test_real_transaction_adapter_waits_for_missing_value():
    from app.database.real_adapter import RealTransactionAdapter
    class Future:
        def wait(self): return None
    class Transaction:
        def get(self,key): return Future()
    assert RealTransactionAdapter(Transaction()).get(b'missing') is None

def test_concurrency_demo_repeatable():
    from app.services.analytics_service import AnalyticsService
    for _ in range(2):
        result = AnalyticsService.simulate_concurrent_stress_test(10,1)
        assert result['no_overselling']
        assert result['successful_orders'] == 1

def test_concurrent_payment_idempotent():
    create()
    barrier=Barrier(10)
    def pay(i):
        barrier.wait()
        return Orders.process_payment('TEST','MOMO',request_id='same-request')
    with ThreadPoolExecutor(max_workers=10) as pool:
        results=list(pool.map(pay,range(10)))
    assert len(Orders.get_order_transactions('TEST')) == 1
    assert sum(not x['replayed'] for x in results) == 1

def test_restore_preserves_unrelated_prefix(tmp_path):
    from app.database.schema import pack_key
    db=db_manager.local_engine
    key=pack_key(('another_app','keep'))
    db.set(key,b'untouched')
    path=tmp_path/'snapshot.json'
    BackupService.export_backup(str(path))
    BackupService.restore_backup(str(path))
    assert db.get(key) == b'untouched'

def test_api_backup_roundtrip_and_date_error():
    from app.main import create_app
    from io import BytesIO
    client=create_app().test_client()
    exported=client.get('/api/backup')
    assert exported.status_code == 200
    response=client.post('/api/restore',data={'backup_file':(BytesIO(exported.data),'snapshot.json')})
    assert response.status_code == 200
    assert client.get('/orders?status=ALL&from_date=bad').status_code == 400

```
