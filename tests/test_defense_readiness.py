import os
os.environ['FDB_MODE'] = 'local'
os.environ['AUTO_SEED'] = '0'
import pytest
from app.config import Config
from app.database import db_manager, get_db, order_key, decode_value
from app.database.fdb_engine import FDBLocalEngine
from app.services.seed_service import SeedService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.query_service import QueryService
from app.database.connection import FoundationDBConnection

@pytest.fixture(autouse=True)
def isolated_db():
    old = db_manager.local_engine
    db_manager.local_engine = FDBLocalEngine()
    SeedService.populate_seed_data()
    yield
    db_manager.local_engine = old

def test_order_idempotency_exact_replay():
    """Verify that retrying create_order with the same custom_order_id and identical payload
    returns the already-committed order without creating duplicate orders or double-deducting stock.
    """
    initial_stock = ProductService.get_product_by_id('P001').stock
    
    # 1. First order creation
    first_order = OrderService.create_order(
        user_id='U101',
        items_data=[{'product_id': 'P001', 'quantity': 2}],
        shipping_address='123 Nguyen Hue, Q1, HCM',
        custom_order_id='IDEMPOTENT-ORD-001'
    )
    assert first_order.order_id == 'IDEMPOTENT-ORD-001'
    assert ProductService.get_product_by_id('P001').stock == initial_stock - 2

    # 2. Retry with exact same payload and custom_order_id
    replayed_order = OrderService.create_order(
        user_id='U101',
        items_data=[{'product_id': 'P001', 'quantity': 2}],
        shipping_address='123 Nguyen Hue, Q1, HCM',
        custom_order_id='IDEMPOTENT-ORD-001'
    )
    assert replayed_order.order_id == first_order.order_id
    assert replayed_order.total_amount == first_order.total_amount
    # Stock must NOT be deducted a second time
    assert ProductService.get_product_by_id('P001').stock == initial_stock - 2

def test_order_idempotency_payload_mismatch_rejected():
    """Verify that sending the same custom_order_id with different payload (tampered quantity or address)
    is rejected with ValueError to prevent replay poisoning.
    """
    OrderService.create_order(
        user_id='U101',
        items_data=[{'product_id': 'P001', 'quantity': 1}],
        shipping_address='123 Nguyen Hue, Q1, HCM',
        custom_order_id='IDEMPOTENT-ORD-002'
    )

    # Attempt to reuse the same order_id with different quantity
    with pytest.raises(ValueError, match="Mã yêu cầu .* đã dùng cho nội dung khác"):
        OrderService.create_order(
            user_id='U101',
            items_data=[{'product_id': 'P001', 'quantity': 5}],
            shipping_address='123 Nguyen Hue, Q1, HCM',
            custom_order_id='IDEMPOTENT-ORD-002'
        )

def test_preaggregated_transaction_summary_updates():
    """Verify that processing payment atomically updates ('agg_transactions_day', day, status)
    and transaction_summary reflects the accurate aggregation without full table scans.
    """
    # Create and pay a new order
    order = OrderService.create_order(
        user_id='U101',
        items_data=[{'product_id': 'P002', 'quantity': 2}],  # 250,000 * 2 = 500,000
        shipping_address='HCM',
        custom_order_id='PAY-TEST-001'
    )

    before_summary = QueryService.transaction_summary()
    before_success_count = before_summary['groups'].get('SUCCESS', {}).get('count', 0)
    before_success_rev = before_summary.get('successful_amount_vnd', 0)

    # Process successful payment
    res = OrderService.process_payment('PAY-TEST-001', 'MOMO', is_success=True, request_id='REQ-PAY-TEST-1')
    assert res['order_status'] == 'SUCCESS'

    after_summary = QueryService.transaction_summary()
    after_success_count = after_summary['groups'].get('SUCCESS', {}).get('count', 0)
    after_success_rev = after_summary.get('successful_amount_vnd', 0)

    assert after_success_count == before_success_count + 1
    assert after_success_rev == before_success_rev + 500000

    # Test payment replay doesn't inflate summary
    OrderService.process_payment('PAY-TEST-001', 'MOMO', is_success=True, request_id='REQ-PAY-TEST-1')
    replayed_summary = QueryService.transaction_summary()
    assert replayed_summary.get('successful_amount_vnd', 0) == after_success_rev

def test_fdb_mode_strict_validation():
    """Verify that FDB_MODE accepts only 'real' or 'local' and rejects 'auto' or invalid strings."""
    old_mode = Config.FDB_MODE
    try:
        Config.FDB_MODE = 'auto'
        conn = FoundationDBConnection.__new__(FoundationDBConnection)
        with pytest.raises(ValueError, match="FDB_MODE chỉ nhận real hoặc local; không tự fallback"):
            conn._initialize_connection()

        Config.FDB_MODE = 'unknown_engine'
        with pytest.raises(ValueError, match="FDB_MODE chỉ nhận real hoặc local; không tự fallback"):
            conn._initialize_connection()
    finally:
        Config.FDB_MODE = old_mode
