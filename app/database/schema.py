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

def agg_transactions_day_key(day: str, status: str) -> bytes:
    return pack_key(('agg_transactions_day', str(day).strip(), str(status).strip()))

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
                'order_history', 'idx_user_orders', 'idx_status_orders', 'idx_users_email',
                'agg_transactions_day')

def clear_application_data(tr):
    for prefix in APP_PREFIXES:
        tr.clear_range(*get_prefix_range((prefix,)))
