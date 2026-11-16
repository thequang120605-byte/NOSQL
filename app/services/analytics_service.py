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
            "status": "success" if success else "error",
            "message": "Kiểm thử ACID thành công" if success else (error_caught or "Kiểm thử ACID chưa đạt điều kiện rollback an toàn"),
            "test_name": "Kiểm thử Tính toàn vẹn ACID & Tự động Rollback khi thiếu tồn kho",
            "tested_product": valid_prod.product_id,
            "initial_stock": initial_stock,
            "attempted_deduction": 1 + invalid_qty,
            "error_message": error_caught,
            "final_stock": final_stock,
            "order_created": order_created is not None,
            "order_cancelled": order_created is None,
            "stock_remained_consistent": final_stock == initial_stock,
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
