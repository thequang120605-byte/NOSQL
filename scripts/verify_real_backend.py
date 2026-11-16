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
            tx_raw = tr.get(transaction_key('REQ-'+receipt_id))
            if tx_raw:
                try:
                    import json
                    from decimal import Decimal
                    tx_data = json.loads(tx_raw.decode('utf-8'))
                    day = tx_data.get('timestamp', '')[:10]
                    st = tx_data.get('status', 'SUCCESS')
                    amt = int(Decimal(str(int(float(tx_data.get('amount', 0))))))
                    from app.database.schema import agg_transactions_day_key, decode_value, encode_value
                    agg_k = agg_transactions_day_key(day, st)
                    agg_raw = tr.get(agg_k)
                    if agg_raw:
                        agg_summary = decode_value(agg_raw)
                        agg_summary['count'] = max(0, agg_summary.get('count', 1) - 1)
                        agg_summary['amount_vnd'] = max(0, agg_summary.get('amount_vnd', amt) - amt)
                        if agg_summary['count'] == 0 and agg_summary['amount_vnd'] == 0:
                            tr.clear(agg_k)
                        else:
                            tr.set(agg_k, encode_value(agg_summary))
                except Exception:
                    pass
            for k in (user_key(uid),idx_user_email_key(email),product_key(pid),transaction_key('REQ-'+receipt_id)):
                tr.clear(k)
        cleanup(get_db())
