# FoundationDB — Đề tài 19: bản rà soát và bổ sung

Học phần NoSQL — GV: TS. Nguyễn Thị Định.
Đọc `AUDIT_VA_KE_HOACH.md` để xem đối chiếu rubric, lỗi bản gốc, phần đã sửa và phần còn thiếu.

## Cấu trúc bàn giao

Đây là toàn bộ dự án đã sửa, giải nén và chạy độc lập. Không cần ghép hai ZIP cũ vì 36 file trong hai ZIP có nội dung giống hệt nhau (bỏ tên thư mục gốc).
Giữ nguyên Flask, model/service, FoundationDB Python binding, tuple keys và JSON values. Không đổi prefix hiện có, không cần migration đổi khóa.
`CHANGED_FILES.md` liệt kê chính xác đường dẫn file thay đổi. `changes.patch` là diff so với NoSQL.zip; `FULL_CHANGED_CODE.md` chứa nguyên văn các file code đã sửa/tạo.
Sao lưu dữ liệu hiện tại trước khi thay source. Không chép đè `data/foundationdb_storage.json` của bạn nếu muốn giữ dữ liệu cũ; tốt nhất chạy bản này trong thư mục riêng.

## Chạy demo local (PowerShell trên Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:FDB_MODE="local"
$env:AUTO_SEED="0"
python init_data.py
python run.py
```

Truy cập http://127.0.0.1:5001 và http://127.0.0.1:5001/queries.
`init_data.py` mặc định bỏ qua nếu đã có dữ liệu ứng dụng. `python init_data.py --reset` xóa các prefix của ứng dụng rồi nạp lại mẫu; chỉ dùng khi chủ động reset dữ liệu demo. Nút “Nạp lại dữ liệu” cũng reset các prefix này.
Local dùng JSON và xử lý xung đột trong một tiến trình; không có replication, failover, độ bền phân tán hay đồng bộ nhiều worker. Không dùng kết quả local để khẳng định đã kiểm chứng FoundationDB phân tán.
Không có đăng nhập/phân quyền bảo vệ route; đây là demo cục bộ, chưa phải ứng dụng triển khai công khai.

## Chạy FoundationDB thật (WSL Ubuntu)

Cần cài server và native client FoundationDB 7.3.77 đang hoạt động; chỉ pip install không cung cấp `libfdb_c`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export FDB_MODE=real
export FDB_CLUSTER_FILE=/etc/foundationdb/fdb.cluster
export AUTO_SEED=0
fdbcli -C "$FDB_CLUSTER_FILE" --exec 'status'
python scripts/verify_real_backend.py
python init_data.py
python query_data.py
python run.py
```

`FDB_MODE=real` dừng nếu thiếu client/cluster hoặc probe thất bại; không tự chuyển sang giả lập. `FDB_MODE=auto` giữ cơ chế fallback và luôn hiển thị backend. API version mặc định giữ 630 theo source; đó là API compatibility version, không phải phiên bản server/tuple protocol. Binding được pin 7.3.77. Nếu máy bạn đang dùng bộ server/client khác, cần kiểm tra và đồng bộ phiên bản.
`verify_real_backend.py` chỉ tạo các mã CHECK-* ngẫu nhiên và dọn các bản ghi đó sau chạy. Script chưa được chạy trên cụm thật trong phiên rà soát này. Nó không chứng minh failover hoặc kiểm thử đa node; nhóm vẫn cần chạy kịch bản tranh chấp trên cụm thật và lưu log.

## Kiểm thử đã bàn giao

```bash
python -m pip install pytest
python -m pytest -q tests
```

Chạy từ thư mục gốc dự án. Tests dùng database local trong bộ nhớ riêng, không reset file dữ liệu đang làm.
21 ca đã PASS; xem `TEST_RESULTS.txt`. Đã kiểm tra trả HTTP 200/render Jinja cho 8 trang; chưa kiểm tra thị giác trong trình duyệt hoặc tài nguyên CDN offline.

## Truy vấn B và kết quả kỳ vọng

Chạy trên dữ liệu mẫu chưa chỉnh sửa:

| Truy vấn | Cách chạy | Kết quả mong đợi |
|---|---|---|
| Người dùng | `UserService.get_user_by_id('U101')` | Nguyen Van A |
| Đơn theo mã | `OrderService.get_order_by_id('ORD2001')` | 1.500.000, SUCCESS, 1 mặt hàng |
| Đơn theo khách | `OrderService.get_orders_by_user('U101')` | ORD2001 và ORD2002 |
| Lịch sử | `OrderService.get_order_history('ORD2001')` | PENDING rồi SUCCESS |
| Giao dịch đơn | `OrderService.get_order_transactions('ORD2001')` | TXN3001, MOMO, SUCCESS |
| Trạng thái + thời gian | `/api/queries/orders?status=SUCCESS&from_date=2026-10-20&to_date=2026-10-23` | ORD2001 và ORD2004, tăng dần ngày tạo |
| Phân trang | thêm `&limit=1`, dùng `next_cursor` cho lần sau | Trang 1 ORD2001, trang 2 ORD2004 |
| Tổng hợp | `/api/queries/transactions/summary` | 3 giao dịch: SUCCESS 2/2.700.000; FAILED 1/500.000 |

Trang `/queries` cho thao tác lọc và phân trang bằng GUI tự xây. Việc giảng viên chấp nhận công cụ này thay GUI Tool trong rubric cần nhóm xác nhận.
Lọc thời gian dùng `idx_status_orders` với khoảng [đầu ngày, đầu ngày kế tiếp ngày cuối); không full-scan mọi đơn. Định dạng thời gian theo source: ISO local `YYYY-MM-DDTHH:MM:SS[.ffffff]`, không timezone; dữ liệu ngoài ứng dụng cần chuẩn hóa trước nhập. Cursor dùng cho cùng bộ lọc. Mỗi trang nhất quán trong một transaction; nhiều trang không giữ cùng snapshot khi có ghi đồng thời.
Tổng hợp giao dịch vẫn quét prefix `transaction`, tính ở Python; giới hạn 10.000 bản ghi để phù hợp demo, không tuyên bố là native aggregation/indexed GROUP BY.

## Luồng C đã chuẩn hóa

`PENDING → SUCCESS → PROCESSING → SHIPPING → DELIVERED → COMPLETED`.
Thanh toán thất bại: `PENDING → FAILED → PENDING` để thử lại.
Chỉ `PENDING` hoặc `FAILED` được hủy và hoàn kho. Gửi lại cùng trạng thái là no-op.
`SUCCESS/FAILED` phải đi qua API thanh toán, không được set trực tiếp để bỏ qua ghi receipt.
Các trạng thái RETURNED/REFUNDED chưa có workflow hoàn chỉnh. Đơn đã thanh toán không được hủy cho tới khi có quy trình hoàn tiền; đây là giới hạn chủ động của bản sửa.
Thanh toán là thao tác ghi nhận giả lập, không gọi MoMo/VNPAY, không trừ ví thật. Số dư là dữ liệu hồ sơ, chưa phải sổ cái.

```http
POST /api/orders/MA_DON/pay
Content-Type: application/json

{"payment_method":"MOMO","is_success":true,"request_id":"payment-attempt-001"}
```

Gửi lại cùng request_id/order/method/result trả receipt đã ghi, không tạo giao dịch mới. Dùng request_id khác cho một lần thử thanh toán mới. Ghi order, status index, history và receipt trong cùng transaction. UI tạo UUID cho mỗi lần mở modal, giữ UUID khi gửi lại.

## Sao lưu

Backup JSON chỉ là logical export/import các prefix ứng dụng, giới hạn 1 MB tổng key/value và 10.000 bản ghi, không thay cho native `fdbbackup/fdbrestore`.
Restore kiểm tra hex, prefix, trùng key và kích thước trước khi thay toàn bộ prefix ứng dụng trong một transaction; giữ prefix ứng dụng khác. Chưa thẩm định đầy đủ quan hệ nghiệp vụ trong file JSON bất kỳ: chỉ dùng snapshot do chính ứng dụng xuất và kiểm tra đối chiếu sau phục hồi.
Nhóm A còn cần quy trình native backup/restore và minh chứng trên FoundationDB thật.
