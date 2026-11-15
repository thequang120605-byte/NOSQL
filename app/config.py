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
    FDB_MODE = os.environ.get('FDB_MODE', 'real').strip().lower()
    
    # Server configuration
    HOST = '127.0.0.1'
    PORT = int(os.environ.get('PORT', 5001))
    DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1'
