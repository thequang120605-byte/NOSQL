# Rà soát Đề tài 19 — FoundationDB

Ngày lập: 13/11/2026. Học phần NoSQL; GV TS. Nguyễn Thị Định.

## Phạm vi và kết luận

Đã đối chiếu nội dung 36 file của mỗi ZIP, toàn bộ cấu trúc, các module Python, template/JS/CSS, README, dependency và snapshot JSON. Hai ZIP có cùng nội dung từng byte theo đường dẫn tương đối; khác tên thư mục NoSQL/NoSQL2. Không có hai nhánh chức năng cần merge.
Rubric lấy trực tiếp từ `Phan_cong_NoSQL_De_tai_19_FoundationDB.docx`, trang 1–5. Đây là file phân công dẫn nguồn rubric PDF; bản PDF gốc không nằm trong hai ZIP nên không tự suy diễn yêu cầu chi tiết ngoài file phân công.

Ứng dụng có khung demo tương đối đầy đủ, nhưng bản gốc chưa sẵn sàng để khẳng định đạt trọn Mục 5/7/8/9: nhiều lỗi dữ liệu và transaction được tái hiện. Phần lý thuyết/vận hành A không thể kết luận hoàn thành chỉ từ source. Kế hoạch dưới đây dành cho cả nhóm vì ô chọn vai trò và chức năng trong yêu cầu chưa được điền. Bản sửa ưu tiên truy vấn nâng cao B + nghiệp vụ transaction C, kèm sửa các lỗi nền trực tiếp ảnh hưởng hai phần này.

## 1. Chức năng đã hiện diện trong source gốc

Số dòng dưới đây tham chiếu **bản gốc**, không phải số dòng sau sửa. “Có triển khai” không đồng nghĩa đã kiểm chứng đầy đủ trên FoundationDB thật.

| Module | File/hàm bản gốc | Khả năng đã có |
|---|---|---|
| Cấu hình và kết nối | `app/config.py`; `app/database/connection.py:34` | API version, cluster file, mở FDB hoặc fallback JSON, trạng thái backend |
| KV giả lập | `app/database/fdb_engine.py:25,170` | Byte keys, SortedDict, pending writes/clear/range, lưu JSON; transaction còn lỗi |
| Schema | `app/database/schema.py:14–61` | Tuple pack/unpack, prefix ranges, 6 nhóm entity + 3 index, JSON UTF-8 |
| Người dùng | `app/services/user_service.py:15,37,47,55,68,111` | Thêm/xem/sửa/xóa, index email duy nhất, role/status/balance trong hồ sơ |
| Sản phẩm | `app/services/product_service.py:16,31,41,54,59,91` | CRUD, lọc danh mục, kiểm tra giá/tồn kho không âm |
| Tạo đơn | `app/services/order_service.py:37` | Kiểm tra user, đọc/trừ kho, ghi order/items/index/history trong callback transaction |
| Truy vấn đơn | cùng file, `:150,183,197,212,227` | Tìm mã; tất cả đơn; theo user/status bằng index; timeline theo prefix |
| Thanh toán | cùng file, `:250,377,382` | Ghi receipt giả lập, cập nhật trạng thái khi thành công; đọc giao dịch đơn/toàn bộ |
| Chuyển trạng thái | cùng file, `:321` | Ghi order/index/history, hoàn kho khi CANCELLED, nhưng chưa kiểm tra state machine |
| Thống kê/lọc | `analytics_service.py:17,67` | Số đơn/user/product, doanh số, phân bố trạng thái; lọc status/date/user/text trong Python |
| Demo lỗi giao dịch | `analytics_service.py:112,164` | Kịch bản thiếu tồn kho và nhiều luồng mua cùng sản phẩm; chưa đủ để chứng minh FDB phân tán |
| Backup JSON | `backup_service.py:11,43` | Xuất key/value hex và nhập lại; bản gốc chưa atomic và chưa giới hạn phạm vi |
| Dữ liệu mẫu | `seed_service.py:11`, `init_data.py`, `data/foundationdb_storage.json` | 3 user, 4 product, 4 order, 4 item, 3 receipt, 7 history; SUCCESS/FAILED/CANCELLED |
| Script truy vấn | `query_data.py:21` | 5 ví dụ: user, order, index user, history, index status |
| Web/API | `app/main.py:16` | 7 trang: dashboard/users/products/orders/detail/KV/reports; các route thao tác dữ liệu |
| Frontend | `app/templates/*.html`, `app/static/*` | Bootstrap, biểu đồ Chart.js, modal, SweetAlert; chưa có trang đăng nhập |
| Khởi chạy | `run.py`, `start.bat`, `requirements.txt`, `README.md` | Flask port 5001, script cài dependency/chạy; README có mô tả vượt quá code thực tế |

## 2. Đối chiếu Mục 1–9 của file phân công

| Mục / Điểm / Người | Trạng thái source gốc | Thiếu hoặc chưa đạt; việc bản sửa xử lý |
|---|---|---|
| 1 / 0,5 / A — lý thuyết và cài Tool | Có README khởi chạy Python | Chưa có hướng dẫn server/client/cluster đầy đủ và minh chứng cài FDB. Bổ sung hướng dẫn kết nối thật, chưa thay cho ảnh/log cài đặt của A |
| 2 / 0,5 / A — chi tiết chức năng Tool | Có KV Explorer, dashboard, snapshot | Chưa có bộ minh chứng quản trị cluster/coordinator/process/status, phân biệt chức năng DB và app. Cần tài liệu A |
| 3 / 0,5 / A — so sánh GUI Tool | Không thấy trong ZIP | Cần bảng so sánh có cùng tiêu chí và nguồn; nhóm chốt công cụ được giảng viên chấp nhận |
| 4 / 0,5 / A — ứng dụng phù hợp, ưu điểm | README nói về TMĐT/ACID | Chưa phân tích giới hạn, trade-off, mô hình triển khai phân tán; cần Phần I và nguồn |
| 5 / 2,0 / B — tạo DB, insert đủ dữ liệu | Schema và seed đã có | Chỉ 4 đơn, chưa bao phủ các trạng thái mở rộng; seed có nguy cơ ghi đè/không atomic, quy ước ID/time thiếu nhất quán. Đã sửa seed atomic, cập nhật ID, chặn dữ liệu sai. Cần bộ dataset mở rộng và minh chứng |
| 6 / 0,5 / A — import/export, backup/restore | JSON snapshot đã có | Bản gốc local xóa rồi ghi từng record; real không xóa dữ liệu cũ; range vượt vùng user keys. Đã sửa logical snapshot bounded/atomic/scoped. Vẫn thiếu native backup/restore và log kiểm chứng |
| 7 / 1,0 / B — truy vấn cơ bản/nâng cao | Có 5 query script; GUI lọc Python | Thiếu status+time range scan thật, script tổng hợp giao dịch, bộ input/expected output. Đã thêm query service, GUI, API, cursor, summary và bảng kết quả. Vẫn cần ảnh chạy trên tool đã chốt |
| 8 / 0,5 / C — kết nối ứng dụng | Có real/fallback | Nhánh real chưa chuẩn hóa Future; fdb.open chưa chứng minh kết nối; startup dùng số khóa real=0 nên seed lại. Đã thêm adapter .wait, probe timeout, fail-fast real, seed không đè; chưa chạy trên server thật trong môi trường này |
| 9 / 2,0 / C — demo 4 nhóm | Đã có màn hình cho 4 nhóm | Cần các sửa nghiệp vụ và kiểm thử chi tiết bên dưới; chưa có xác thực/phân quyền thực thi, chưa có hoàn tiền thật |

Mục 9 gồm **9.1 quản trị người dùng; 9.2 thao tác dữ liệu; 9.3 nghiệp vụ; 9.4 hỗ trợ — mỗi nhóm 0,5 điểm**. Không quy đổi mức hoàn thiện code thành điểm chắc chắn. Theo file phân công, Mục 1–9 tổng 8 điểm; Mục 10 báo cáo/slide/vấn đáp/nộp bài còn 2 điểm chung.

### Mục 9 chi tiết

- 9.1: Có hồ sơ người dùng, role, khóa tài khoản, email unique. Sửa user mẫu lỗi do ID chỉ nằm ở key; xóa user có đơn làm mồ côi quan hệ. Đã sửa hai lỗi này. Chưa có login/session/middleware kiểm quyền; trường role không phải cơ chế phân quyền. File phân công chỉ nói “quản trị người dùng”, nên không khẳng định login là tiêu chí bắt buộc chưa được ghi, nhưng README gốc tuyên bố phân quyền là chưa đúng.
- 9.2: Có CRUD sản phẩm. Sửa product mẫu lỗi thiếu ID; cho xóa product đã có trong order_item; NaN có thể vượt kiểm tra chỉ `price < 0`. Đã bổ sung ID, chặn xóa có tham chiếu và kiểm tra hữu hạn.
- 9.3: Có create/pay/status/history nhưng không chặn state transition; hủy lặp hoàn kho nhiều lần; thanh toán đơn đã hủy/đã thành công; đọc kết quả ngoài transaction trả trạng thái cũ; history chính xác đến giây bị ghi đè. Đã sửa các luồng, thêm request_id chống ghi receipt lặp.
- 9.4: Có dashboard/lọc/backup/rollback/concurrency. Đã bổ sung query GUI và sửa demo concurrency dùng ID riêng mỗi lần, barrier, giới hạn số luồng. Dashboard và tìm kiếm chung vẫn có full scan/N+1, chưa phục vụ dữ liệu lớn.

## 3. Key/Value/Subspace và truy vấn B

| Nhóm | Key tuple | Value | Đánh giá |
|---|---|---|---|
| User | `('user', user_id)` | JSON hồ sơ | ID nằm ở key, không bắt buộc lặp trong value; cần bơm ID trước model.validate |
| Product | `('product', product_id)` | JSON giá/kho | Cùng lỗi ID với user; tiền còn float |
| Order | `('order', order_id)` | user_id, total_amount, status, created_at, address | Không nhúng items; phù hợp tách entity |
| Item | `('order_item', order_id, item_id)` | product_id/name, quantity, price | Quét prefix lấy toàn bộ item; giữ giá lúc đặt |
| Payment | `('transaction', txn_id)` | order_id, amount, method, status, timestamp | Thiếu index theo order; bản gốc quét tất cả receipt để lọc |
| History | `('order_history', order_id, timestamp)` | status, note, updated_by | Giây không đủ phân biệt sự kiện; bản sửa dùng microseconds và previous_status |
| User index | `('idx_user_orders', user_id, order_id)` | bytes rỗng | Hiệu quả tìm đơn user; không có thứ tự thời gian tự nhiên |
| Status index | `('idx_status_orders', status, created_at, order_id)` | bytes rỗng | Đã đủ cấu trúc lọc status+time; bản gốc chưa tận dụng bound theo time |
| Email index | `('idx_users_email', normalized_email)` | user_id UTF-8 | Giá trị index không phải JSON; cần giải thích rõ trong báo cáo |

Đây là **phân vùng logic bằng tuple prefix**, chưa dùng đối tượng `fdb.Subspace` hoặc Directory Layer. Không được ghi trong báo cáo là đã dùng Directory Layer. Rubric nêu “prefix/subspace”; không tự thêm yêu cầu bắt buộc đổi sang class Subspace. Bản sửa giữ nguyên bytes của key, tránh phá tương thích.
Chưa có namespace chung theo project/tenant. Maintenance đã được giới hạn vào 9 prefix hiện có, nhưng vẫn nên dùng cluster/database demo riêng; nếu thêm namespace phải có migration thống nhất với B/C.
Dữ liệu mẫu đủ cho truy vấn cơ bản và SUCCESS/FAILED/CANCELLED nhưng chưa có mẫu PENDING đang mở, PROCESSING/SHIPPING/DELIVERED/COMPLETED hoặc user bị khóa/hết kho. Không được coi việc có enum là đã có dữ liệu bao phủ.
Timestamp naive local theo source cần thống nhất; phân tán nhiều múi giờ nên chuyển UTC có kế hoạch migration. Microseconds giảm nguy cơ trùng nhưng chưa thay thế versionstamp/sequence bảo đảm thứ tự tuyệt đối đa máy. Nếu nhóm cần minh chứng timeline phân tán nâng cao, bổ sung versionstamp hoặc sequence transaction-safe theo đơn.

## 4. Lỗi gốc đã chứng minh và bản sửa

`ORIGINAL_BUG_EVIDENCE.json` chứa kết quả thực chạy bản gốc trên bộ local trong bộ nhớ riêng:

| Lỗi | Bằng chứng | Sửa ở |
|---|---|---|
| Sửa user/product mẫu | Báo “Mã người dùng/Mã sản phẩm không được để trống” | user_service/product_service đưa ID từ key vào model |
| Hủy lặp tăng kho | Tồn đầu 25 → đặt 24 → hủy 25 → hủy lại 26 | update_order_status kiểm state và no-op |
| Trả kết quả cũ | API/service trả PENDING nhưng DB đã CANCELLED | Đọc order/items qua cùng tr trước commit, chỉ trả sau commit thành công |
| Thanh toán sau hủy/lặp | Đơn CANCELLED thành SUCCESS, 2 receipt thành công | Chỉ PENDING được pay; request_id idempotent |
| Bán sản phẩm không tồn tại | Product NONE vẫn tạo đơn 100 đồng | Bắt buộc product tồn tại, giá từ catalog |
| Mất lịch sử | Tạo/hủy/pay nhanh nhưng còn 1 key history | Timestamp microseconds; previous_status |

Các lỗi tìm bằng đọc code và kiểm thử hồi quy:

- `fdb_engine.py:144`: read_keys được ghi nhưng không dùng kiểm tra conflict; đọc committed state thay snapshot; commit không ngăn lost update, range phantom. Bản sửa dùng snapshot/version conflict toàn DB và retry hữu hạn, chỉ trong một tiến trình.
- Commit clear_range sau set làm mất giá trị ghi lại; đã sửa thứ tự áp dụng clear rồi write.
- save_to_disk nuốt lỗi ghi đĩa; bản sửa truyền lỗi và khôi phục in-memory state. Chưa tuyên bố durability khi mất điện/fsync hoặc multi-process.
- `connection.py` trả raw FDB Transaction.get Future; `is None` trên Future sai. Adapter mới .wait() trả bytes/None, decorator chính thức quản lý commit/retry. Nhánh adapter có unit test giả lập Future; chưa có integration server thật.
- requirements thiếu sortedcontainers dù import trực tiếp; đã bổ sung và pin binding.
- `init_data.py`/`query_data.py` tự mở DB bằng đường dẫn mặc định khác connection manager; đã thống nhất về manager. Bản gốc init có commit.wait, vấn đề chính là đường kết nối/retry và seed không thống nhất, không phải thiếu wait commit.
- seed ghi từng key và startup coi real total_keys=0; đã chuyển seed một transaction, tránh tự ghi đè dữ liệu có sẵn; AUTO_SEED mặc định tắt.
- FAILED payment chỉ thêm history FAILED, order vẫn PENDING; bản sửa đồng bộ order/index/history/receipt.
- Model PaymentTransaction đổi MOMO/VNPAY thành E_WALLET; đã giữ đúng hai phương thức này. Receipt mới lưu user_id.
- ID ngẫu nhiên chỉ 90 lựa chọn trong mỗi phút/giây theo chuỗi cắt ngắn; chuyển UUID.
- Xóa user/product gây mất liên kết; đã chặn khi có tham chiếu.
- Restore bản gốc có thể xóa dữ liệu trước khi phát hiện hex lỗi; bản sửa kiểm tra trước ghi và khôi phục atomic trong phạm vi nhỏ.
- Query/backup dùng upper bound `b'\xff\xff\xff\xff'`, lấn system key range FDB; KV explorer dùng `b'\xff'`, backup dùng prefix ứng dụng.
- HTML/JS có các điểm nội suy chưa an toàn: đã đổi products_json sang tojson, serialize onclick/ID bằng tojson, escape dữ liệu trong HTML động ở điểm đã rà soát. Đây không phải audit bảo mật đầy đủ.
- `is_success='false'` bị bool(string)=True: đã yêu cầu boolean JSON.

## 5. Kế hoạch làm tiếp theo vai trò

Thứ tự ưu tiên phục vụ tiêu chí, không bảo đảm điểm tuyệt đối.

### A — Mục 1,2,3,4,6 (2,5 điểm)

1. **P0:** chạy bản sửa với FDB_MODE=real trên WSL, xác minh native client/server và cluster file. Lưu `fdbcli status`, log script verify_real_backend và ảnh header REAL_FDB. Không chỉ chụp UI giả lập.
2. **P0:** chuẩn bị native backup/restore bằng công cụ FoundationDB trên database demo; xác nhận dữ liệu/key count/query trước–sau cùng B. Nộp script, đường dẫn backup, log trạng thái và kết quả phục hồi. JSON snapshot hiện có chỉ hỗ trợ import/export ứng dụng nhỏ.
3. **P1:** hoàn thiện Phần I/III: cài đặt, phiên bản, chức năng quản trị, phạm vi áp dụng, giới hạn của môi trường single node; tránh gọi single node là đã chứng minh chịu lỗi phân tán.
4. **P1:** chốt GUI Tool với giảng viên theo ghi chú rubric; lập bảng so sánh cùng tiêu chí, có nguồn và ảnh chức năng. Công cụ tự xây có GUI nhưng chưa tự động đáp ứng mọi yêu cầu về Tool.
5. **Nghiệm thu:** thành viên khác làm lại theo hướng dẫn từ máy sạch và kiểm tra được dữ liệu sau restore.

### B — Mục 5,7 (3,0 điểm)

1. **P0:** xác nhận bảng schema trên, ý nghĩa SUCCESS = thanh toán thành công, timestamp ISO, index và loại value. Ghi rõ prefix logic, không mô tả nhầm Directory Layer.
2. **P0:** chạy `query_data.py` và `/queries` trên FDB thật; chụp input/output cho tìm đơn, receipt, đơn theo user, status+time, timeline, summary. Đối chiếu expected trong README.
3. **P1:** bổ sung dataset bằng service nghiệp vụ: khách active/locked, product còn/hết hàng, các trạng thái mở rộng; mã cố định để kết quả query lặp lại. Kèm bảng tổng mong đợi và trường hợp không tìm thấy/không đủ kho/email trùng.
4. **P1:** viết kiểm tra consistency: order↔items↔status/user indexes, receipt↔order, lịch sử; bổ sung rebuild index với dry-run trước khi sửa. Dùng khi import/restore dữ liệu ngoài ứng dụng.
5. **P2:** thêm index transaction theo order/time nếu tăng dữ liệu; backfill index cũ và cập nhật atomic mọi nơi ghi, không thêm index nửa chừng. Chuẩn hóa tiền integer VND/Decimal và timestamp UTC nếu mở rộng.
6. **Nghiệm thu:** mỗi query có key range/input/expected output và ảnh kết quả; tài liệu Phần II khớp code.

### C — Mục 8,9 (2,5 điểm)

1. **P0:** chạy 21 regression tests, rồi script kiểm tra real và demo concurrency trên FDB thật. Lưu backend, log, tồn kho trước–sau, receipt/index/history.
2. **P0:** demo đủ bốn nhóm: thêm/sửa/khóa user; CRUD product chưa tham chiếu; tạo đơn–pay–giao hàng/hủy đơn chưa trả tiền; query/thống kê/backup. Thử thao tác sai và xem thông báo.
3. **P1:** nếu trình bày “phân quyền”, phải code login/session/password hash và kiểm quyền trên route, không chỉ có trường role. Chuẩn hóa status/role không hợp lệ thành lỗi thay vì âm thầm đổi mặc định.
4. **P1:** quyết định phạm vi tiền: hiện chỉ receipt demo. Nếu cần hủy sau thanh toán/trả hàng, code refund/idempotency và đối soát receipt–stock–revenue trong transaction; hiện bị chặn để tránh sai sổ.
5. **P1:** bổ sung xử lý request lỗi nhất quán, log server, giới hạn transaction size/items; bỏ debug và bí mật mặc định khi triển khai thật.
6. **P2:** giảm full scan/N+1, snapshot nhất quán toàn dashboard, pagination các danh sách; đóng gói CDN để thuyết trình offline.
7. **Nghiệm thu:** Phần IV có sơ đồ luồng, ảnh 4 nhóm chức năng, test PASS/FAIL và giới hạn trung thực.

Cả nhóm còn Mục 10: ghép báo cáo/slide, ghi đóng góp, kiểm tra biểu mẫu và tập vấn đáp. File phân công yêu cầu báo cáo 50–60 trang, TNR 13, giãn dòng 1,5; source ZIP không thay thế các tài liệu này.

## 6. Bản sửa đã kiểm tra đến đâu

21 tests PASS trên Python 3.12, backend local trong bộ nhớ, gồm rollback toàn state, thiếu product, hủy lặp, state machine, thanh toán/idempotency/concurrent receipt, index/history, tranh chấp hàng cuối, cập nhật seed ID, xóa tham chiếu, date/cursor/summary, phantom conflict, clear-then-set, lỗi ghi đĩa, backup/restore, prefix ngoài ứng dụng, HTTP/render 8 trang và Future adapter.
`TEST_RESULTS.txt` có log. Có một warning từ plugin pytest của môi trường, không phải test failure.
Đã compile Python. Không có native FoundationDB C library/server trong môi trường rà soát, nên chưa chạy integration FDB, multi-node, failover, native backup/restore. Chưa kiểm tra UI bằng thao tác trình duyệt hoặc chụp ảnh trực quan.
Không tự khẳng định toàn bộ lỗi đã hết. Các hạn chế còn lại gồm auth, refund, quy mô lớn, timestamp phân tán, kiểm định dữ liệu import, tiền float và minh chứng học thuật/vận hành.

Tài liệu kỹ thuật đối chiếu: https://apple.github.io/foundationdb/api-python.html (Future, transactional, retry); https://apple.github.io/foundationdb/developer-guide.html (transaction và tác dụng phụ ngoài DB). Không dùng nội dung mô tả README gốc làm bằng chứng code đã chạy.
