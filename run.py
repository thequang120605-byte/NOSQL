import sys
import os

# Ensure current directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.main import create_app
from app.config import Config

app = create_app()

if __name__ == '__main__':
    print("=" * 70)
    print("  FDB COMMERCE OPERATIONS PLATFORM")
    print("  Distributed Transaction & Order Management Core")
    print("  Key-Value Storage Engine: Apple FoundationDB (API 630)")
    print("=" * 70)
    print(f"  Trang chủ Dashboard: http://{Config.HOST}:{Config.PORT}")
    print(f"  Quản lý Đơn hàng:    http://{Config.HOST}:{Config.PORT}/orders")
    print(f"  Quản lý Sản phẩm:    http://{Config.HOST}:{Config.PORT}/products")
    print(f"  Quản lý Khách hàng:  http://{Config.HOST}:{Config.PORT}/users")
    print(f"  Key-Value Explorer:  http://{Config.HOST}:{Config.PORT}/kv-explorer")
    print(f"  Báo cáo & Kiểm thử:  http://{Config.HOST}:{Config.PORT}/reports")
    print(f"  Truy vấn B (NoSQL):  http://{Config.HOST}:{Config.PORT}/queries")
    print("=" * 70)
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
