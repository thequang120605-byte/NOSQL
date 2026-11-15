import math
import re
from datetime import datetime
from typing import Optional, Dict, Any

class User:
    VALID_ROLES = {'customer', 'admin', 'merchant'}
    VALID_STATUSES = {'active', 'locked'}

    def __init__(
        self,
        user_id: str,
        name: str,
        email: str,
        phone: str = "",
        role: str = "customer",
        status: str = "active",
        balance: float = 0.0,
        address: str = "",
        created_at: Optional[str] = None
    ):
        self.user_id = user_id.strip()
        self.name = name.strip()
        self.email = email.strip().lower()
        self.phone = phone.strip()
        self.role = role.lower() if role.lower() in self.VALID_ROLES else "customer"
        self.status = status.lower() if status.lower() in self.VALID_STATUSES else "active"
        self.balance = float(balance)
        self.address = address.strip()
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def validate(self):
        """Validate user data. Raises ValueError if invalid."""
        if not self.user_id:
            raise ValueError("Mã người dùng (User ID) không được để trống")
        if not self.name:
            raise ValueError("Họ và tên người dùng không được để trống")
        if not self.email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", self.email):
            raise ValueError(f"Email không hợp lệ: '{self.email}'")
        if not math.isfinite(self.balance) or self.balance < 0:
            raise ValueError("Số dư tài khoản không được là số âm")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "role": self.role,
            "status": self.status,
            "balance": self.balance,
            "address": self.address,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        return cls(
            user_id=data.get("user_id", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            role=data.get("role", "customer"),
            status=data.get("status", "active"),
            balance=data.get("balance", 0.0),
            address=data.get("address", ""),
            created_at=data.get("created_at")
        )
