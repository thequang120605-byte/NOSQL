from datetime import datetime, timedelta
import threading
from uuid import uuid4
import math
import hashlib
import json
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

    _last_ts = ""
    _ts_lock = threading.Lock()

    @classmethod
    def _generate_monotonic_iso(cls) -> str:
        """Generates strictly increasing ISO timestamps to prevent key collisions in FoundationDB order_history."""
        with cls._ts_lock:
            ts = datetime.now().isoformat(timespec="microseconds")
            if ts <= cls._last_ts:
                try:
                    dt = datetime.fromisoformat(cls._last_ts) + timedelta(microseconds=1)
                    ts = dt.isoformat(timespec="microseconds")
                except Exception:
                    pass
            cls._last_ts = ts
            return ts

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
        shipping_address = shipping_address.strip()
        if not items_data:
            raise ValueError("Đơn hàng phải có ít nhất 1 sản phẩm")

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "user_id": user_id,
                    "items": items_data,
                    "shipping_address": shipping_address,
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

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

            # Check if order_id already exists (Idempotent replay check)
            o_key = order_key(order_id)
            existing = decode_value(tr.get(o_key))
            if existing is not None:
                if existing.get("request_fingerprint") != fingerprint:
                    raise ValueError(
                        f"Mã yêu cầu '{order_id}' đã dùng cho nội dung khác hoặc đơn cũ chưa có fingerprint"
                    )
                return OrderService.get_order_by_id(order_id, tr)

            # 2. Check and deduct inventory
            order_items = []
            total_amount = 0.0
            now_iso = OrderService._generate_monotonic_iso()

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
                "shipping_address": shipping_address or "Địa chỉ khách hàng",
                "request_fingerprint": fingerprint
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
        timestamp = OrderService._generate_monotonic_iso()

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
            from app.services.query_service import add_receipt_summary
            add_receipt_summary(tr, receipt)
            OrderService._change_status(tr, order_id, data, target,
                                        payment_method + " Demo", receipt['note'], timestamp)
            return {'transaction': receipt, 'order_status': target, 'replayed': False}
        return _txn(get_db())

    @staticmethod
    def update_order_status(order_id: str, new_status: str, operator: str = "Admin", note: str = "") -> Order:
        if not isinstance(new_status, str) or new_status.upper().strip() not in OrderStatus.ALL:
            raise ValueError("Trạng thái không hợp lệ")
        order_id, new_status = order_id.strip(), new_status.strip().upper()
        timestamp = OrderService._generate_monotonic_iso()

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
