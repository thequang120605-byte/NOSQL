import json
from typing import List, Optional, Dict, Any
from app.database import (
    db_manager, get_db,
    product_key, get_prefix_range, unpack_key,
    encode_value, decode_value
)
from app.models.product import Product

class ProductService:
    """Implements Mục 9.2: Thao tác dữ liệu hàng hóa & kho.
    Tuple schema: ('product', product_id).
    """

    @staticmethod
    def create_product(product: Product) -> Product:
        product.validate()
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            k = product_key(product.product_id)
            if tr.get(k) is not None:
                raise ValueError(f"Mã sản phẩm '{product.product_id}' đã tồn tại!")
            tr.set(k, encode_value(product.to_dict()))
            return product

        return _txn(db)

    @staticmethod
    def get_product_by_id(product_id: str) -> Optional[Product]:
        db = get_db()
        raw = db.get(product_key(product_id))
        if raw is None:
            return None
        data = decode_value(raw)
        data['product_id'] = product_id
        return Product.from_dict(data)

    @staticmethod
    def get_all_products() -> List[Product]:
        db = get_db()
        start, stop = get_prefix_range(('product',))
        items = db.get_range(start, stop)
        products = []
        for k_bytes, raw_val in items:
            tup = unpack_key(k_bytes)
            data = decode_value(raw_val)
            data['product_id'] = tup[1]
            products.append(Product.from_dict(data))
        return products

    @staticmethod
    def get_products_by_category(category: str) -> List[Product]:
        all_prods = ProductService.get_all_products()
        return [p for p in all_prods if p.category.lower() == category.lower()]

    @staticmethod
    def update_product(product_id: str, updates: Dict[str, Any]) -> Product:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            k = product_key(product_id)
            raw = tr.get(k)
            if raw is None:
                raise ValueError(f"Không tìm thấy sản phẩm '{product_id}'")

            data = decode_value(raw)
            if 'name' in updates:
                data['name'] = updates['name'].strip()
            if 'category' in updates:
                data['category'] = updates['category'].strip()
            if 'price' in updates:
                data['price'] = float(updates['price'])
            if 'stock' in updates:
                stk = int(updates['stock'])
                data['stock'] = stk
                data['status'] = "available" if stk > 0 else "out_of_stock"
            if 'description' in updates:
                data['description'] = updates['description'].strip()

            data["product_id"] = product_id
            prod = Product.from_dict(data)
            prod.validate()
            tr.set(k, encode_value(prod.to_dict()))
            return prod

        return _txn(db)

    @staticmethod
    def delete_product(product_id: str) -> bool:
        db = get_db()

        @db_manager.transactional
        def _txn(tr):
            k = product_key(product_id)
            if tr.get(k) is None:
                return False
            for _, raw in tr.get_range(*get_prefix_range(('order_item',))):
                if decode_value(raw).get('product_id') == product_id:
                    raise ValueError("Sản phẩm đã có trong đơn hàng; không được xóa")
            tr.clear(k)
            return True

        return _txn(db)

    @staticmethod
    def get_raw_key_values(limit: int = 200) -> List[Dict[str, Any]]:
        """Displays all raw FoundationDB keys with tuple unpack representation."""
        db = get_db()
        # Scan from empty byte to highest byte
        items = db.get_range(b'', b'\xff', limit=limit)
        results = []
        for k_bytes, v_bytes in items:
            try:
                unpacked = str(unpack_key(k_bytes))
            except Exception:
                unpacked = repr(k_bytes)

            val_repr = v_bytes.decode('utf-8', errors='replace')
            try:
                val_json = json.loads(val_repr)
                val_display = json.dumps(val_json, indent=2, ensure_ascii=False)
            except Exception:
                val_display = val_repr

            results.append({
                "hex_key": k_bytes.hex(),
                "raw_key": unpacked,
                "length": len(v_bytes),
                "value_display": val_display
            })
        return results
