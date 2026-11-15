"""Member B queries using the existing ordered tuple indexes (no schema migration)."""
import math
from datetime import date, timedelta
from decimal import Decimal
from app.database import db_manager, get_db, get_prefix_range, unpack_key, decode_value
from app.database.schema import pack_key, encode_value
from app.models.order import OrderStatus
from app.services.order_service import OrderService


def add_receipt_summary(tr, receipt: dict):
    """Atomically roll up transaction receipts into ('agg_transactions_day', day, status).
    Ensures O(days) pre-aggregated queries without full transaction scans.
    """
    amount_val = float(receipt.get("amount", 0))
    if not math.isfinite(amount_val) or amount_val < 0 or int(amount_val) != amount_val:
        raise ValueError("Số tiền phải là số nguyên VND hợp lệ")
    amount = Decimal(str(int(amount_val)))
    day = date.fromisoformat(receipt["timestamp"][:10]).isoformat()
    key = pack_key(("agg_transactions_day", day, receipt["status"]))
    summary = decode_value(tr.get(key)) or {
        "count": 0,
        "amount_vnd": 0,
    }
    summary["count"] += 1
    summary["amount_vnd"] += int(amount)
    tr.set(key, encode_value(summary))


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
            return {
                'orders': orders,
                'next_cursor': rows[limit-1][0].hex() if len(rows) > limit else None,
                'index': 'idx_status_orders',
                'order': 'created_at ASC, order_id ASC',
                'consistency': 'one transaction per page; pages may change under concurrent writes'
            }
        return query(get_db())

    @staticmethod
    def transaction_summary(from_date=None, to_date=None):
        """Query aggregated financial statistics using pre-aggregated ('agg_transactions_day', day, status) range.
        Replaces O(N) full transaction scans with O(days) range scan.
        """
        if from_date and to_date:
            first = date.fromisoformat(from_date)
            last = date.fromisoformat(to_date)
            if first > last or (last - first).days > 366:
                raise ValueError('Khoảng ngày hợp lệ tối đa 366 ngày và từ ngày phải <= đến ngày')
        else:
            first = date.fromisoformat(from_date) if from_date else date(2020, 1, 1)
            last = date.fromisoformat(to_date) if to_date else date(2035, 12, 31)
            if first > last:
                raise ValueError('Ngày bắt đầu phải <= ngày kết thúc')

        start = pack_key(('agg_transactions_day', first.isoformat()))
        stop = pack_key(('agg_transactions_day', (last + timedelta(days=1)).isoformat()))

        @db_manager.transactional
        def query(tr):
            groups = {}
            for key, raw in tr.get_range(start, stop):
                parts = unpack_key(key)
                status = parts[2]
                val = decode_value(raw)
                group = groups.setdefault(
                    status, {"count": 0, "amount_vnd": 0, "amount": 0.0}
                )
                group["count"] += val.get("count", 0)
                group["amount_vnd"] += int(val.get("amount_vnd", 0))
                group["amount"] += float(val.get("amount_vnd", 0))

            return {
                'groups': groups,
                'count': sum(x['count'] for x in groups.values()),
                'successful_amount_vnd': groups.get('SUCCESS', {}).get('amount_vnd', 0),
                'successful_amount': groups.get('SUCCESS', {}).get('amount_vnd', 0),
                'method': 'pre-aggregated agg_transactions_day range scan (O(days) bounded scan; zero full transaction scan)'
            }
        return query(get_db())
