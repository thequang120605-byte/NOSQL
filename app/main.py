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
        products = ProductService.get_all_products()
        return render_template('dashboard.html', active_page='dashboard', summary=summary, recent_orders=recent_orders, products=products)

    @app.route('/users')
    def users_page():
        users = UserService.get_all_users()
        return render_template('users.html', active_page='users', users=users)

    @app.route('/products')
    def products_page():
        category = request.args.get('category')
        search = request.args.get('q')
        all_products = ProductService.get_all_products()
        categories = sorted(list(set(p.category for p in all_products if p.category)))

        products = ProductService.get_all_products(category=category, search=search)

        return render_template(
            'products.html',
            active_page='products',
            products=products,
            categories=categories,
            selected_cat=category,
            search_query=search
        )

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
        prefix = request.args.get('prefix')
        kv_items = ProductService.get_raw_key_values(limit=150, prefix_filter=prefix)
        from app.database.schema import APP_PREFIXES
        return render_template(
            'kv_explorer.html',
            active_page='kv_explorer',
            kv_items=kv_items,
            selected_prefix=prefix,
            app_prefixes=APP_PREFIXES
        )

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
            
            # Idempotency support: Idempotency-Key header or custom_order_id
            idempotency_header = request.headers.get("Idempotency-Key", "").strip()
            custom_id = idempotency_header or (data.get('custom_order_id') or '').strip()
            if idempotency_header and (not idempotency_header or len(idempotency_header) > 100):
                raise ValueError("Idempotency-Key phải có 1–100 ký tự")

            order = OrderService.create_order(
                user_id=user_id,
                items_data=items_data,
                shipping_address=shipping_address,
                custom_order_id=custom_id or None
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
        all_statuses = ['SUCCESS', 'PENDING', 'PROCESSING', 'SHIPPING', 'DELIVERED', 'COMPLETED', 'CANCELLED', 'FAILED']
        return render_template('queries.html', active_page='queries', all_statuses=all_statuses)

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
