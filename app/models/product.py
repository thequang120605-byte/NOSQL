import math
from datetime import datetime
from typing import Optional, Dict, Any, List

class Product:
    VALID_STATUSES = {'available', 'out_of_stock', 'discontinued'}

    def __init__(
        self,
        product_id: str,
        name: str,
        category: str,
        price: float,
        stock: int,
        description: str = "",
        status: Optional[str] = None,
        updated_at: Optional[str] = None,
        brand: str = "",
        specs: Optional[Dict[str, str]] = None,
        rating: float = 4.9,
        reviews_count: int = 120,
        tag: str = "",
        features: Optional[List[str]] = None,
        image_url: str = ""
    ):
        self.product_id = product_id.strip()
        self.name = name.strip()
        self.category = category.strip()
        self.price = float(price)
        self.stock = int(stock)
        self.description = description.strip()
        if status:
            self.status = status.lower()
        else:
            self.status = "available" if self.stock > 0 else "out_of_stock"
        self.updated_at = updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.brand = (brand or "CyberTech Prime").strip()
        self.specs = specs or {}
        self.rating = float(rating) if rating is not None else 4.9
        self.reviews_count = int(reviews_count) if reviews_count is not None else 120
        self.tag = (tag or "").strip()
        self.features = features or []
        self.image_url = image_url or f"/static/img/{self.product_id}.jpg"

    def validate(self):
        """Validate product data."""
        if not self.product_id:
            raise ValueError("Mã sản phẩm (Product ID) không được để trống")
        if not self.name:
            raise ValueError("Tên sản phẩm không được để trống")
        if not math.isfinite(self.price) or self.price < 0:
            raise ValueError("Giá sản phẩm không được là số âm")
        if self.stock < 0:
            raise ValueError("Số lượng tồn kho không được là số âm")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "category": self.category,
            "price": self.price,
            "stock": self.stock,
            "description": self.description,
            "status": self.status,
            "updated_at": self.updated_at,
            "brand": self.brand,
            "specs": self.specs,
            "rating": self.rating,
            "reviews_count": self.reviews_count,
            "tag": self.tag,
            "features": self.features,
            "image_url": self.image_url
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Product':
        return cls(
            product_id=data.get("product_id", ""),
            name=data.get("name", ""),
            category=data.get("category", "Chung"),
            price=data.get("price", 0.0),
            stock=data.get("stock", 0),
            description=data.get("description", ""),
            status=data.get("status"),
            updated_at=data.get("updated_at"),
            brand=data.get("brand", "CyberTech Prime"),
            specs=data.get("specs", {}),
            rating=data.get("rating", 4.9),
            reviews_count=data.get("reviews_count", 120),
            tag=data.get("tag", ""),
            features=data.get("features", []),
            image_url=data.get("image_url", "")
        )
