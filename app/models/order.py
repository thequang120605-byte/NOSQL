from datetime import datetime
from typing import List, Dict, Any, Optional

class OrderStatus:
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CREATED = "PENDING"  # Map CREATED to PENDING for Member B compatibility
    PAID = "SUCCESS"     # Map PAID to SUCCESS for Member B compatibility
    PROCESSING = "PROCESSING"
    SHIPPING = "SHIPPING"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    RETURNED = "RETURNED"

    ALL = {PENDING, SUCCESS, FAILED, CANCELLED, PROCESSING, SHIPPING, DELIVERED, COMPLETED, RETURNED}

    # Allowed state transitions
    TRANSITIONS = {
        PENDING: [SUCCESS, FAILED, CANCELLED],
        FAILED: [PENDING, CANCELLED],
        SUCCESS: [PROCESSING],
        PROCESSING: [SHIPPING],
        SHIPPING: [DELIVERED],
        DELIVERED: [COMPLETED],
        COMPLETED: [], CANCELLED: [], RETURNED: []
    }

    @classmethod
    def can_transition(cls, from_status: str, to_status: str) -> bool:
        # Permissive transition check
        return to_status in cls.TRANSITIONS.get(from_status, []) or from_status == to_status


class OrderItem:
    def __init__(self, product_id: str, product_name: str, quantity: int, unit_price: float):
        self.product_id = product_id
        self.product_name = product_name
        self.quantity = int(quantity)
        self.unit_price = float(unit_price)
        self.subtotal = self.quantity * self.unit_price

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "subtotal": self.subtotal
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrderItem':
        return cls(
            product_id=data.get("product_id", ""),
            product_name=data.get("product_name", ""),
            quantity=data.get("quantity", 1),
            unit_price=data.get("unit_price", 0.0)
        )


class OrderStatusHistory:
    def __init__(
        self,
        history_id: str,
        order_id: str,
        previous_status: Optional[str],
        new_status: str,
        action: str,
        operator: str = "SYSTEM",
        note: str = "",
        timestamp: Optional[str] = None
    ):
        self.history_id = history_id
        self.order_id = order_id
        self.previous_status = previous_status
        self.new_status = new_status
        self.action = action
        self.operator = operator
        self.note = note
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "history_id": self.history_id,
            "order_id": self.order_id,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "action": self.action,
            "operator": self.operator,
            "note": self.note,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrderStatusHistory':
        return cls(
            history_id=data.get("history_id", ""),
            order_id=data.get("order_id", ""),
            previous_status=data.get("previous_status"),
            new_status=data.get("new_status", ""),
            action=data.get("action", ""),
            operator=data.get("operator", "SYSTEM"),
            note=data.get("note", ""),
            timestamp=data.get("timestamp")
        )


class Order:
    def __init__(
        self,
        order_id: str,
        user_id: str,
        items: List[OrderItem],
        shipping_address: str,
        status: str = OrderStatus.CREATED,
        total_amount: Optional[float] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None
    ):
        self.order_id = order_id.strip()
        self.user_id = user_id.strip()
        self.items = items
        self.shipping_address = shipping_address.strip()
        self.status = status if status in OrderStatus.ALL else OrderStatus.CREATED
        self.total_amount = total_amount if total_amount is not None else sum(item.subtotal for item in self.items)
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.updated_at = updated_at or self.created_at

    def validate(self):
        if not self.order_id:
            raise ValueError("Mã đơn hàng (Order ID) không được để trống")
        if not self.user_id:
            raise ValueError("Mã khách hàng (User ID) không được để trống")
        if not self.items or len(self.items) == 0:
            raise ValueError("Đơn hàng phải có ít nhất một sản phẩm")
        for item in self.items:
            if item.quantity <= 0:
                raise ValueError(f"Số lượng sản phẩm '{item.product_name}' phải lớn hơn 0")
        if not self.shipping_address:
            raise ValueError("Địa chỉ giao hàng không được để trống")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "user_id": self.user_id,
            "items": [item.to_dict() for item in self.items],
            "shipping_address": self.shipping_address,
            "status": self.status,
            "total_amount": self.total_amount,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Order':
        items = [OrderItem.from_dict(it) for it in data.get("items", [])]
        return cls(
            order_id=data.get("order_id", ""),
            user_id=data.get("user_id", ""),
            items=items,
            shipping_address=data.get("shipping_address", ""),
            status=data.get("status", OrderStatus.CREATED),
            total_amount=data.get("total_amount"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at")
        )
