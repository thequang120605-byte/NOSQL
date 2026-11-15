import json
from fdb import tuple as fdb_tuple
from app.database import get_db, db_manager
from app.database.schema import APP_PREFIXES, get_prefix_range, clear_application_data

class SeedService:
    """Populates EXACT seed data matching Member B's init_data.py (BÁO CÁO PHẦN 2).
    Synchronized 100% with Member B (Nguyễn Ngọc Hoàng).
    """

    @staticmethod
    def populate_seed_data(clear_existing: bool = True):
        @db_manager.transactional
        def populate(db):
            if clear_existing:
                clear_application_data(db)
            elif any(db.get_range(*get_prefix_range((p,)), limit=1) for p in APP_PREFIXES):
                return {"skipped": True, "reason": "Application data already exists"}
            print("[SeedService] Inserting Member B's official dataset into FoundationDB...")

            # 1. Products (P001 - P008) rich catalog supporting e-commerce visuals & inventory
            products = [
                ("P001", {
                    "name": "Bàn phím cơ Gaming P001",
                    "category": "Bàn phím & Chuột",
                    "price": 1500000.0,
                    "stock": 25,
                    "status": "available",
                    "description": "Bàn phím cơ Optical Blue Switch, đèn nền RGB Per-Key 16.8 triệu màu",
                    "brand": "CyberCore Gear",
                    "rating": 4.9,
                    "reviews_count": 248,
                    "tag": "Bán chạy nhất",
                    "features": ["Hot-swappable 5-pin", "Núm xoay âm lượng đa năng CNC", "Foam tiêu âm Poron 3 lớp", "Tri-mode Type-C / 2.4G / Bluetooth"],
                    "specs": {
                        "Switch": "Optical Gateron Blue Switch",
                        "Keycaps": "PBT Double-shot xuyên LED",
                        "Pin": "4,000 mAh (200 giờ sử dụng)",
                        "Trọng lượng": "980 gram đầm tay"
                    },
                    "image_url": "/static/img/P001.jpg"
                }),
                ("P002", {
                    "name": "Chuột quang không dây P002",
                    "category": "Bàn phím & Chuột",
                    "price": 250000.0,
                    "stock": 50,
                    "status": "available",
                    "description": "Chuột không dây 2.4G Silent, cảm biến quang học PixArt PAW3395 Flagship",
                    "brand": "NeonTech Precision",
                    "rating": 4.8,
                    "reviews_count": 312,
                    "tag": "Siêu nhẹ 49g",
                    "features": ["Tốc độ phản hồi 0.25ms không trễ", "Feet chuột 100% Virgin PTFE", "DPI tùy chỉnh từ 100 đến 26,000", "Switch Huano 80 triệu lượt bấm"],
                    "specs": {
                        "Cảm biến": "PixArt PAW3395 (26,000 DPI)",
                        "Polling Rate": "4,000 Hz HyperPolling",
                        "Thời lượng pin": "90 giờ hoạt động liên tục",
                        "Trọng lượng": "49g siêu nhẹ"
                    },
                    "image_url": "/static/img/P002.jpg"
                }),
                ("P003", {
                    "name": "Màn hình IPS 24 inch P003",
                    "category": "Màn hình",
                    "price": 2300000.0,
                    "stock": 15,
                    "status": "available",
                    "description": "Màn hình cong viền siêu mỏng 165Hz Fast-Crystal IPS, hỗ trợ HDR và G-Sync",
                    "brand": "StellarVision",
                    "rating": 5.0,
                    "reviews_count": 189,
                    "tag": "Đồ họa & Gaming",
                    "features": ["Tần số quét 165Hz siêu mượt", "Tấm nền Fast-Crystal IPS 0.5ms GtG", "Độ phủ màu 100% sRGB chuẩn đồ họa", "Đèn viền RGB Ambient Glow sau lưng"],
                    "specs": {
                        "Kích thước": "24 inch tràn viền 3 cạnh",
                        "Tấm nền": "Super IPS 165Hz",
                        "Độ phân giải": "Full HD 1920 x 1080",
                        "Cổng kết nối": "DisplayPort 1.4, HDMI 2.0, Audio Out"
                    },
                    "image_url": "/static/img/P003.jpg"
                }),
                ("P004", {
                    "name": "Tai nghe Gaming P004",
                    "category": "Âm thanh",
                    "price": 400000.0,
                    "stock": 35,
                    "status": "available",
                    "description": "Tai nghe không dây Cyber Earbuds ANC, Driver 12mm mạ Titan, chống ồn Hybrid -45dB",
                    "brand": "AcousticVibe Audio",
                    "rating": 4.9,
                    "reviews_count": 420,
                    "tag": "Chống ồn chủ động",
                    "features": ["Chống ồn chủ động Hybrid ANC -45dB", "Thời lượng pin 40 giờ kèm hộp sạc", "Chế độ Xuyên âm Transparency Mode", "Chuẩn kháng nước & mồ hôi IPX5"],
                    "specs": {
                        "Driver": "Dynamic Driver 12mm mạ Titan",
                        "Bluetooth": "Chuẩn 5.4 Low-Latency 35ms",
                        "Microphone": "4 Mic AI chống nhiễu đàm thoại",
                        "Sạc nhanh": "10 phút sạc dùng 3 giờ"
                    },
                    "image_url": "/static/img/P004.jpg"
                }),
                ("P005", {
                    "name": "Tai nghe Over-Ear Cyber X1 Hi-Res",
                    "category": "Âm thanh",
                    "price": 1850000.0,
                    "stock": 20,
                    "status": "available",
                    "description": "Tai nghe trùm đầu không dây Hi-Res Audio, màng loa Neodymium 50mm, âm thanh vòm 7.1 Spatial",
                    "brand": "CyberCore Audio",
                    "rating": 4.9,
                    "reviews_count": 156,
                    "tag": "Âm thanh vòm 7.1",
                    "features": ["Âm thanh vòm đa hướng Spatial 7.1", "Đệm tai Memory Foam da Protein thoáng khí", "Kết nối Lossless 2.4GHz không trễ", "Vòng tròn LED cyan phát sáng độc bản"],
                    "specs": {
                        "Driver": "50mm Titanium Neodymium",
                        "Pin": "55 giờ chơi game liên tục",
                        "Dải tần": "15Hz - 40,000Hz Hi-Res",
                        "Khối lượng": "270 gram siêu nhẹ"
                    },
                    "image_url": "/static/img/P005.jpg"
                }),
                ("P006", {
                    "name": "Loa Soundbar Cyber RGB Desktop Wave",
                    "category": "Âm thanh",
                    "price": 890000.0,
                    "stock": 28,
                    "status": "available",
                    "description": "Loa thanh Soundbar Gaming để bàn, dải đèn LED sóng nhạc quang học, âm thanh vòm Stereo 40W",
                    "brand": "AcousticVibe Audio",
                    "rating": 4.8,
                    "reviews_count": 98,
                    "tag": "Đèn LED theo nhạc",
                    "features": ["Dải LED spectrum nhảy theo nhịp nhạc", "Cụm 4 loa kép âm bass siêu trầm", "Cảm ứng lực điều chỉnh chế độ sáng", "Đa kết nối AUX 3.5mm, USB, Bluetooth 5.3"],
                    "specs": {
                        "Công suất": "RMS 20W Peak 40W Stereo",
                        "Cổng kết nối": "USB-C, AUX 3.5mm, BT 5.3",
                        "Kích thước": "420 x 70 x 70 mm",
                        "Dải tần": "60Hz - 20,000Hz"
                    },
                    "image_url": "/static/img/P006.jpg"
                }),
                ("P007", {
                    "name": "Kính Thực Tế Ảo Cyber VR HoloLens Pro",
                    "category": "Phụ kiện VR",
                    "price": 4950000.0,
                    "stock": 10,
                    "status": "available",
                    "description": "Kính VR cao cấp thế hệ mới, Dual 4K OLED 120Hz, định vị không gian 6DoF Inside-Out",
                    "brand": "QuantumVision VR",
                    "rating": 5.0,
                    "reviews_count": 64,
                    "tag": "Công nghệ Metaverse",
                    "features": ["Thấu kính Pancake quang học sắc nét", "Màn hình Dual OLED 4K (2160x2160 mỗi mắt)", "Định vị 6DoF bằng 4 camera góc rộng", "Truyền phát PC VR không dây Wi-Fi 6E"],
                    "specs": {
                        "Góc nhìn": "FOV 115 độ siêu rộng",
                        "Tần số quét": "120Hz Fast-Switch",
                        "Trọng lượng": "385 gram cân bằng trọng tâm",
                        "Dung lượng pin": "5,000 mAh dùng 3 giờ"
                    },
                    "image_url": "/static/img/P007.jpg"
                }),
                ("P008", {
                    "name": "Bàn Di Chuột Cyber RGB Fast-Glide XL",
                    "category": "Bàn phím & Chuột",
                    "price": 320000.0,
                    "stock": 60,
                    "status": "available",
                    "description": "Lót chuột gaming cỡ lớn 900x400mm, viền LED quang học Neon 14 chế độ sáng, vải sợi Cordura siêu mịn",
                    "brand": "NeonTech Gear",
                    "rating": 4.8,
                    "reviews_count": 175,
                    "tag": "Chống nước Nano",
                    "features": ["Lớp phủ Nano kháng nước tràn", "Viền LED sợi quang RGB 14 chế độ sáng", "Đế cao su tự nhiên chống trượt 100%", "Cáp Type-C bọc dù tháo rời tiện lợi"],
                    "specs": {
                        "Kích thước": "900 x 400 x 4 mm (Size XL)",
                        "Bề mặt": "Vải sợi Cordura dệt mịn",
                        "Cấp nguồn": "5V Type-C rời",
                        "Trọng lượng": "750 gram"
                    },
                    "image_url": "/static/img/P008.jpg"
                })
            ]
            for pid, pdata in products:
                k = fdb_tuple.pack(('product', pid))
                db.set(k, json.dumps(pdata, ensure_ascii=False).encode('utf-8'))

            # 2. Danh sách Users (Từ file init_data.py của Thành viên B)
            users = [
                ("U101", {"name": "Nguyen Van A", "email": "a@gmail.com", "phone": "0901111111", "role": "customer", "balance": 25000000.0, "status": "active"}),
                ("U102", {"name": "Tran Thi B", "email": "b@gmail.com", "phone": "0902222222", "role": "customer", "balance": 18000000.0, "status": "active"}),
                ("U103", {"name": "Le Van C", "email": "c@gmail.com", "phone": "0903333333", "role": "customer", "balance": 5000000.0, "status": "active"})
            ]
            for uid, udata in users:
                k = fdb_tuple.pack(('user', uid))
                db.set(k, json.dumps(udata, ensure_ascii=False).encode('utf-8'))
                db.set(fdb_tuple.pack(('idx_users_email', udata['email'])), uid.encode('utf-8'))

            # 3. Danh sách Orders & Indexes (Từ file init_data.py của Thành viên B)
            orders = [
                ("ORD2001", "U101", 1500000, "SUCCESS", "2026-10-20T10:00:00"),
                ("ORD2002", "U101", 500000, "FAILED", "2026-10-21T11:30:00"),
                ("ORD2003", "U102", 2300000, "CANCELLED", "2026-10-22T14:15:00"),
                ("ORD2004", "U103", 1200000, "SUCCESS", "2026-10-23T09:00:00")
            ]
            for oid, uid, amount, status, created_at in orders:
                order_data = {"user_id": uid, "total_amount": amount, "status": status, "created_at": created_at, "shipping_address": "Việt Nam"}
                db.set(fdb_tuple.pack(('order', oid)), json.dumps(order_data, ensure_ascii=False).encode('utf-8'))
                db.set(fdb_tuple.pack(('idx_user_orders', uid, oid)), b'')
                db.set(fdb_tuple.pack(('idx_status_orders', status, created_at, oid)), b'')

            # 4. Order Items (Từ file init_data.py của Thành viên B)
            order_items = [
                ("ORD2001", "IT01", {"product_id": "P001", "quantity": 1, "price": 1500000, "product_name": "Bàn phím cơ Gaming P001"}),
                ("ORD2002", "IT02", {"product_id": "P002", "quantity": 2, "price": 250000, "product_name": "Chuột quang không dây P002"}),
                ("ORD2003", "IT03", {"product_id": "P003", "quantity": 1, "price": 2300000, "product_name": "Màn hình IPS 24 inch P003"}),
                ("ORD2004", "IT04", {"product_id": "P004", "quantity": 3, "price": 400000, "product_name": "Tai nghe Gaming P004"})
            ]
            for oid, iid, idata in order_items:
                db.set(fdb_tuple.pack(('order_item', oid, iid)), json.dumps(idata, ensure_ascii=False).encode('utf-8'))

            # 5. Transactions (Từ file init_data.py của Thành viên B)
            transactions = [
                ("TXN3001", "ORD2001", 1500000, "MOMO", "SUCCESS", "2026-10-20T10:05:00"),
                ("TXN3002", "ORD2002", 500000, "VNPAY", "FAILED", "2026-10-21T11:35:00"),
                ("TXN3003", "ORD2004", 1200000, "BANK_TRANSFER", "SUCCESS", "2026-10-23T09:05:00")
            ]
            for tid, oid, amount, method, tstatus, ttime in transactions:
                t_data = {"order_id": oid, "amount": amount, "payment_method": method, "status": tstatus, "timestamp": ttime}
                db.set(fdb_tuple.pack(('transaction', tid)), json.dumps(t_data, ensure_ascii=False).encode('utf-8'))
                
                # Pre-aggregated rollup for ('agg_transactions_day', day, status)
                day = ttime[:10]
                agg_k = fdb_tuple.pack(('agg_transactions_day', day, tstatus))
                raw_agg = db.get(agg_k)
                curr_agg = json.loads(raw_agg.decode('utf-8')) if raw_agg else {"count": 0, "amount_vnd": 0}
                curr_agg["count"] += 1
                curr_agg["amount_vnd"] += int(amount)
                db.set(agg_k, json.dumps(curr_agg, ensure_ascii=False).encode('utf-8'))

            # 6. Order History (Từ file init_data.py của Thành viên B)
            histories = [
                ("ORD2001", "2026-10-20T10:00:00", {"status": "PENDING", "note": "Khởi tạo đơn hàng", "updated_by": "System"}),
                ("ORD2001", "2026-10-20T10:05:00", {"status": "SUCCESS", "note": "Thanh toán thành công", "updated_by": "Momo Gateway"}),
                ("ORD2002", "2026-10-21T11:30:00", {"status": "PENDING", "note": "Khởi tạo đơn hàng", "updated_by": "System"}),
                ("ORD2002", "2026-10-21T11:35:00", {"status": "FAILED", "note": "Ngân hàng từ chối giao dịch", "updated_by": "VNPAY"}),
                ("ORD2003", "2026-10-22T14:15:00", {"status": "CANCELLED", "note": "Khách hàng chủ động hủy", "updated_by": "User U102"}),
                ("ORD2004", "2026-10-23T09:00:00", {"status": "PENDING", "note": "Khởi tạo đơn hàng", "updated_by": "System"}),
                ("ORD2004", "2026-10-23T09:05:00", {"status": "SUCCESS", "note": "Thanh toán thành công", "updated_by": "Bank Gateway"})
            ]
            for oid, htime, hdata in histories:
                db.set(fdb_tuple.pack(('order_history', oid, htime)), json.dumps(hdata, ensure_ascii=False).encode('utf-8'))

            print("[SeedService] Successfully populated Member B's dataset: 3 users, 4 products, 4 orders, 3 txns, 7 histories!")
            return {
                "users_seeded": len(users),
                "products_seeded": len(products),
                "orders_seeded": len(orders)
            }
        return populate(get_db())
