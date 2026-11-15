from datetime import datetime
from typing import Optional, Dict, Any

class PaymentTransaction:
    VALID_STATUSES = {'PENDING', 'SUCCESS', 'FAILED', 'REFUNDED'}
    VALID_METHODS = {'COD', 'CREDIT_CARD', 'E_WALLET', 'BANK_TRANSFER', 'MOMO', 'VNPAY'}

    def __init__(
        self,
        txn_id: str,
        order_id: str,
        user_id: str,
        amount: float,
        payment_method: str = "E_WALLET",
        status: str = "PENDING",
        gateway_code: str = "",
        note: str = "",
        created_at: Optional[str] = None
    ):
        self.txn_id = txn_id.strip()
        self.order_id = order_id.strip()
        self.user_id = user_id.strip()
        self.amount = float(amount)
        self.payment_method = payment_method.upper() if payment_method.upper() in self.VALID_METHODS else "E_WALLET"
        self.status = status.upper() if status.upper() in self.VALID_STATUSES else "PENDING"
        self.gateway_code = gateway_code.strip()
        self.note = note.strip()
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def validate(self):
        if not self.txn_id:
            raise ValueError("Mã giao dịch (Transaction ID) không được để trống")
        if not self.order_id:
            raise ValueError("Mã đơn hàng (Order ID) không được để trống")
        if self.amount <= 0:
            raise ValueError("Số tiền giao dịch phải lớn hơn 0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "order_id": self.order_id,
            "user_id": self.user_id,
            "amount": self.amount,
            "payment_method": self.payment_method,
            "status": self.status,
            "gateway_code": self.gateway_code,
            "note": self.note,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PaymentTransaction':
        return cls(
            txn_id=data.get("txn_id", ""),
            order_id=data.get("order_id", ""),
            user_id=data.get("user_id", ""),
            amount=data.get("amount", 0.0),
            payment_method=data.get("payment_method", "E_WALLET"),
            status=data.get("status", "PENDING"),
            gateway_code=data.get("gateway_code", ""),
            note=data.get("note", ""),
            created_at=data.get("created_at")
        )
