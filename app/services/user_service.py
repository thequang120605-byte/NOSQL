from typing import List, Optional, Dict, Any
from app.database import (
    db_manager, get_db,
    user_key, idx_user_email_key, get_prefix_range, unpack_key,
    encode_value, decode_value
)
from app.models.user import User

class UserService:
    """Implements Mục 9.1: Quản trị người dùng.
    Uses Member B's exact tuple schema: ('user', user_id).
    """

    @staticmethod
    def create_user(user: User) -> User:
        user.validate()
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            u_key = user_key(user.user_id)
            if tr.get(u_key) is not None:
                raise ValueError(f"Mã người dùng '{user.user_id}' đã tồn tại!")

            # Check email uniqueness via secondary index
            email_key = idx_user_email_key(user.email)
            if tr.get(email_key) is not None:
                raise ValueError(f"Email '{user.email}' đã được đăng ký!")

            tr.set(u_key, encode_value(user.to_dict()))
            tr.set(email_key, user.user_id.encode('utf-8'))
            return user

        return _txn(db)

    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[User]:
        db = get_db()
        raw = db.get(user_key(user_id))
        if raw is None:
            return None
        data = decode_value(raw)
        data['user_id'] = user_id
        return User.from_dict(data)

    @staticmethod
    def get_user_by_email(email: str) -> Optional[User]:
        db = get_db()
        user_id_raw = db.get(idx_user_email_key(email))
        if user_id_raw is None:
            return None
        return UserService.get_user_by_id(user_id_raw.decode('utf-8'))

    @staticmethod
    def get_all_users() -> List[User]:
        db = get_db()
        start, stop = get_prefix_range(('user',))
        items = db.get_range(start, stop)
        users = []
        for k_bytes, raw_val in items:
            tup = unpack_key(k_bytes)
            data = decode_value(raw_val)
            data['user_id'] = tup[1]
            users.append(User.from_dict(data))
        return users

    @staticmethod
    def update_user(user_id: str, updates: Dict[str, Any]) -> User:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            u_key = user_key(user_id)
            raw = tr.get(u_key)
            if raw is None:
                raise ValueError(f"Không tìm thấy người dùng '{user_id}'")

            data = decode_value(raw)
            old_email = data.get('email', '')
            new_email = updates.get('email', old_email).strip().lower()

            if new_email and new_email != old_email:
                new_idx = idx_user_email_key(new_email)
                if tr.get(new_idx) is not None:
                    raise ValueError(f"Email '{new_email}' đã được đăng ký!")
                tr.clear(idx_user_email_key(old_email))
                tr.set(new_idx, user_id.encode('utf-8'))
                data['email'] = new_email

            if 'name' in updates:
                data['name'] = updates['name'].strip()
            if 'phone' in updates:
                data['phone'] = updates['phone'].strip()
            if 'role' in updates:
                data['role'] = updates['role']
            if 'status' in updates:
                data['status'] = updates['status']
            if 'balance' in updates:
                data['balance'] = float(updates['balance'])
            if 'address' in updates:
                data['address'] = updates['address'].strip()

            data["user_id"] = user_id
            user = User.from_dict(data)
            user.validate()
            tr.set(u_key, encode_value(user.to_dict()))
            return user

        return _txn(db)

    @staticmethod
    def delete_user(user_id: str) -> bool:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            u_key = user_key(user_id)
            raw = tr.get(u_key)
            if raw is None:
                return False
            data = decode_value(raw)
            if tr.get_range(*get_prefix_range(('idx_user_orders', user_id)), limit=1):
                raise ValueError("Khách hàng đã có đơn: hãy khóa thay vì xóa")
            if 'email' in data:
                tr.clear(idx_user_email_key(data['email']))
            tr.clear(u_key)
            return True

        return _txn(db)
