"""Read-only reproducible query examples; initialize seed explicitly beforehand."""
import json
from app.services.user_service import UserService
from app.services.order_service import OrderService
from app.services.query_service import QueryService

def show(name, value):
    print(name)
    print(json.dumps(value, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    user = UserService.get_user_by_id('U101')
    order = OrderService.get_order_by_id('ORD2001')
    show('Q1 User U101', user.to_dict() if user else None)
    show('Q2 Order ORD2001', order.to_dict() if order else None)
    show('Q3 Orders U101', [o.to_dict() for o in OrderService.get_orders_by_user('U101')])
    show('Q4 History ORD2001', [h.to_dict() for h in OrderService.get_order_history('ORD2001')])
    show('Q5 SUCCESS 20–23/10/2026', QueryService.orders_by_status_time('SUCCESS', '2026-10-20', '2026-10-23'))
    show('Q6 Transactions ORD2001', [t.to_dict() for t in OrderService.get_order_transactions('ORD2001')])
    show('Q7 Summary', QueryService.transaction_summary())
