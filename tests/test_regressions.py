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
