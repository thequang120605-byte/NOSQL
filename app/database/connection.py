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
        
        # Validation strictly before try block: no auto fallback
        if Config.FDB_MODE not in {"real", "local"}:
            raise ValueError("FDB_MODE chỉ nhận real hoặc local; không tự fallback")

        if Config.FDB_MODE == "local":
            print("[FDB Connection] Explicit local demonstration mode selected.")
            self.local_engine = FDBLocalEngine(Config.FDB_LOCAL_STORAGE_FILE)
            self.mode = "LOCAL_EMULATED"
            self.is_connected = True
            self.connected_at = time.time()
            print(f"[FDB Connection] Local FDB Engine active. Storage file: {Config.FDB_LOCAL_STORAGE_FILE}")
            return

        # FDB_MODE == "real": Attempt real FoundationDB connection
        try:
            import fdb
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
                raise FileNotFoundError(f"Cluster file not found at {self.cluster_file}")
        except Exception as e:
            self.error_message = str(e)
            print(f"[FDB Connection] Real FoundationDB driver unavailable: {e}")

        # Real connection failed -> Fail fast and do not silently mask with emulator
        raise RuntimeError(f"FoundationDB connection failed: {self.error_message or self.cluster_file}")

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
