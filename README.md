# TradingAgents VN trên Botkeep

Hướng dẫn chạy TradingAgents VN liên tục trên Botkeep (hoặc bất kỳ máy chủ Python nào luôn bật). Một tiến trình `main_botkeep.py` gồm:

- **Trang web**: nhập mã cổ phiếu, xem hàng đợi, đọc báo cáo HTML của các agent, sửa danh sách theo dõi.
- **Bot Telegram**: `/phantich`, `/phanbien`, `/nhanh`, `/theodoi`, `/trangthai`, `/id`. Zoom Team Chat ngang hàng (tuỳ chọn).
- **Cảnh báo trong phiên**: stop, mục tiêu, vùng mua, VNINDEX giảm mạnh, không gọi AI.
- **Báo cáo tự động** cho danh sách theo dõi sau giờ đóng cửa mỗi ngày giao dịch (ngày lễ tự bỏ qua).
- **Bot giao dịch** chạy ba chiến lược song song (Phòng thủ - mặc định, Quant Engine và Động lượng VN30), mỗi chiến lược một tài khoản ảo, kèm (tuỳ chọn) lệnh đề xuất cho tài khoản thật. **Mỗi đêm bot tự hiệu chỉnh ngưỡng** của Quant Engine và Động lượng (Phòng thủ giữ bộ đã kiểm định). Không bao giờ gửi lệnh tới CTCK.

Hành động (BUY / HOLD / WATCH / SELL / NO_TRADE) và mọi mức giá do **Quant Engine** chốt bằng code. Các agent AI (4 analyst, Bull/Bear, Trader, 3 risk, Portfolio Manager) diễn giải quanh quyết định đó, rồi **Output Guard** kiểm tra lại bằng code. Mỗi lần chỉ phân tích một mã để vừa gói free (2 GB RAM, 2 GB ổ đĩa).

---

## 1. Chuẩn bị

| Cần có | Lấy ở đâu |
|---|---|
| Ít nhất một nhà cung cấp AI | 9Router (endpoint kiểu OpenAI), hoặc key Groq / OpenRouter / xAI |
| Bot Telegram (tuỳ chọn) | Chat với [@BotFather](https://t.me/BotFather) → `/newbot` → lấy token |
| Mật khẩu trang web | Tự đặt, **bắt buộc** nếu muốn dùng trang web |

Dữ liệu thị trường (DNSE, VCI, CafeF, Google News) không cần key.

## 2. Tạo gói upload

Trên máy có mã nguồn (Python 3.11+):

```bash
git clone https://github.com/traique/trading_vn.git
cd trading_vn
python scripts/build_botkeep_bundle.py
```

Lệnh tạo `dist/botkeep/` và `dist/tradingagents-vn-botkeep.zip` (~100 file, ~230 KB). Script báo lỗi nếu vượt giới hạn upload của Botkeep (250 file, 4 MB). Gói chỉ gồm `tradingagents/`, `vnbot/`, `main_botkeep.py`, `requirements.txt`, `.env.example`, README này và LICENSE; test, ảnh, CLI được bỏ ra.

## 3. Tạo server trên Botkeep

1. Tạo workload **Python 3.11 trở lên**. Nếu gói cho phép chia tài nguyên, cấp khoảng 1,5 GB RAM.
2. Upload file zip rồi giải nén vào **thư mục gốc** của server (hoặc upload nội dung `dist/botkeep/`).
3. File khởi động: `main_botkeep.py`. File requirements: `requirements.txt`.
4. Copy `.env.example` thành `.env` (cùng thư mục với `main_botkeep.py`) và điền tối thiểu:

```env
# AI - ít nhất một dòng có key
ROUTER9_BASE_URL=https://<9router-cua-ban>/v1
ROUTER9_API_KEY=...
ROUTER9_MODEL=gpt-4o-mini
GROQ_API_KEY=...            # dự phòng, nên có
OPENROUTER_API_KEY=...      # dự phòng, nên có

# Web
WEB_PASSWORD=mat-khau-dai-kho-doan
PUBLIC_URL=https://<ten-server>.botkeep...   # địa chỉ Botkeep cấp

# Telegram
TELEGRAM_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_IDS=        # để trống lúc đầu, xem bước 4

# Tuỳ chọn: Claude chọn bóng (cần thêm SIEUTINHIEU=1), xem mục "Claude chọn bóng"
# ANTHROPIC_API_KEY=sk-ant-...
```

5. Bấm **Start**. Log khởi động cần có:
   - `AI provider chain: router9 -> groq -> ...` (nếu thấy `No AI provider configured` là thiếu key).
   - Nếu thấy `WEB_PASSWORD is not set` thì trang web đang khoá: thêm mật khẩu rồi khởi động lại.

Botkeep tự đặt `SERVER_PORT`; chạy nơi khác thì dùng `PORT` hoặc `APP_PORT` (mặc định 8080). Cổng web mở ngay trong 1–2 giây đầu, trước khi bot nạp thư viện: trong lúc đó `/health` trả `{"ok":true,"booting":true}` và các trang khác hiện "Đang khởi động", nên Botkeep không báo "not responding". Log khởi động có dòng `Web: listening on 0.0.0.0:<cổng>`.

## 4. Lấy Telegram id và mở quyền

1. Mở bot trên Telegram, gửi `/id`. Bot trả về id của bạn (với tài khoản chưa được phép, bot chỉ trả lời lệnh này).
2. Điền id vào `.env`: `TELEGRAM_ALLOWED_IDS=123456789` (nhiều người thì cách nhau dấu phẩy).
3. Muốn báo cáo hằng ngày gửi vào một nhóm: thêm bot vào nhóm, lấy id nhóm (số âm) và đặt `TELEGRAM_REPORT_CHAT_IDS`. Bỏ trống thì gửi cho các id trong `TELEGRAM_ALLOWED_IDS`.
4. Khởi động lại server.

## 5. Kiểm tra sau khi chạy

1. Mở `PUBLIC_URL`. Trình duyệt hỏi đăng nhập: **tên gì cũng được**, mật khẩu là `WEB_PASSWORD`.
2. Nhập `FPT`, bấm **Chỉ quyết định hệ thống**. Ra hành động và mức giá trong vài giây là server đã gọi được DNSE/VCI.
   - Báo lỗi kết nối: có thể DNSE/VCI chặn IP máy chủ Botkeep (ở nước ngoài). Khi đó không chạy được trên host này.
3. Gửi `/phantich FPT` cho bot: quyết định hệ thống tới trước, báo cáo đầy đủ (kèm file HTML và link web) tới sau vài phút.
4. `/trangthai`: xem hàng đợi và nhà cung cấp AI nào đang sẵn sàng hay đang nghỉ.
5. Góc trên trang web (cạnh logo) và lệnh `/phienban` hiện số phiên bản; mã bản dựng phải báo **đúng bản phát hành** (xem mục 11).

## 6. Cách dùng

### Bot Telegram

| Lệnh | Việc |
|---|---|
| `/phantich FPT` | Phân tích đầy đủ bằng các agent (vài phút) |
| `/phantich FPT 2026-10-01` | Phân tích tại một ngày trong quá khứ (chỉ dùng dữ liệu có đến ngày đó) |
| `/nhanh FPT` | Chỉ quyết định của Quant Engine, không gọi AI (vài giây) |
| `/phanbien FPT [YYYY-MM-DD]` | AI phản biện quyết định của Quant Engine: **1 lượt gọi AI**, trả lời đồng ý/phản đối kèm lý do |
| `/phanbien` | AI phản biện đúng bao nhiêu (kết quả bóng của các lần phản biện) |
| `/theodoi FPT,HPG,VCB` | Đặt danh sách báo cáo tự động (tối đa 30 mã) |
| `/theodoi` | Xem danh sách đang theo dõi |
| `/trangthai` | Hàng đợi và trạng thái các nhà cung cấp AI (dòng đầu là phiên bản đang chạy) |
| `/phienban` | Phiên bản đang chạy, mã bản dựng (đúng bản phát hành hay không), thời gian chạy, có gì mới |
| `/danhmuc` | Danh mục cả ba tài khoản ảo (và tài khoản thật ở chế độ `signal`) |
| `/lenh` | Lệnh bot đặt cho phiên tới |
| `/hieuchinh` | Ngưỡng Quant Engine đang dùng và kết quả tự hiệu chỉnh 5 đêm gần nhất |
| `/chay` | Chạy ngay (kèm chạy bù) các phiên đã đóng mà bot chưa xử lý; phiên đã chạy thì bỏ qua. Trước 15:05 chỉ tính tới phiên hôm trước |
| `/von 500000000` | Đặt tiền mặt tài khoản thật (chế độ `signal`) |
| `/khop mua FPT 100 56300 [YYYY-MM-DD]` | Báo đã khớp lệnh thật (ngày mặc định hôm nay); bán: `/khop ban FPT 100 58000` (chế độ `signal`; bán được cả lô lẻ, ví dụ 1150 cp). Bot từ chối lệnh không thể xảy ra: không đủ tiền, bán cổ phiếu chưa về (mua phiên T bán được từ chiều T+2, đếm theo lịch giao dịch có ngày lễ), ngày tương lai/cuối tuần/ngày lễ, hoặc ngày trước lệnh đã ghi gần nhất |
| `/thuong FPT 150 [YYYY-MM-DD]` | Báo số cổ phiếu thưởng/cổ tức bằng cổ phiếu **đã về** tài khoản thật (số được nhận, không phải tổng đang giữ): cộng vào vị thế, giữ nguyên tổng giá vốn (chế độ `signal`) |
| `/stv` · `/stv FPT` | Siêu tín hiệu: tổng quan so với các sổ của bot · tín hiệu và lịch sử tính lại của một mã (cần `SIEUTINHIEU=1`) |
| `/stvdanhmuc` | Sổ ảo so sánh của Siêu tín hiệu (danh mục tiền thật của anh nằm ở `/ts`) |
| `/stvtheodoi` | Watchlist riêng của Siêu tín hiệu (tối đa 60 mã, mặc định đủ 60): xem · `bo NVL` · `them HAG` (khi còn chỗ) · `dat MÃ1,MÃ2,...` (đặt cả danh sách) · `macdinh` · `hoantac` (hoàn tác thay đổi vừa rồi) |
| `/stvquet` · `/stvsang` | Quét ngay danh mục Tài sản + 60 mã watchlist (mã trùng hỏi 1 lần, khoảng 2 phút) · bản tin sáng ngay bây giờ |
| `/stvthitruong` | Bot quét 300 mã thanh khoản cao nhất thị trường: mã đã qua mức đổi chiều (trong phiên: theo giá khớp; ngoài giờ: theo giá đóng cửa), so với phần trang cho xem miễn phí, và kết quả đối chiếu các phiên trước |
| `/stvdoichieu` | Đổi chiều trong phiên do bot ước tính cho danh mục + watchlist: mã đã qua mức đổi chiều, rồi mã sát mức (cách ≤ 2%). Ngoài giờ: mức giá đóng cửa sẽ làm từng mã đổi chiều ở phiên tới |
| `/claude` · `/claude lichsu` · `/claude tuan` | Claude chọn bóng: phiên gần nhất (mã chọn, mã nên tránh, ý kiến về danh mục) và thành tích · các phiên gần đây · tổng kết tuần (cần `ANTHROPIC_API_KEY` và `SIEUTINHIEU=1`) |
| `/claude chay` | Chạy ngay cho phiên gần nhất (tốn phí API; mỗi phiên chỉ một lần) |
| `/ts` ... | Danh mục tiền thật của anh (Tài sản): xem mục **Tài sản** bên dưới |
| `/id` | Telegram id của bạn |

Mã nhận được các dạng `FPT`, `fpt`, `FPT.VN`, `HOSE:FPT`. Chỉ số (`VNINDEX`, `VN30`...) không có quyết định giao dịch nên bị từ chối.

### Tài sản (danh mục tiền thật của anh)

Sổ riêng cho tiền thật của anh, **không lẫn với các tài khoản ảo của bot hay sổ ảo so sánh Siêu tín hiệu**. Bot không bao giờ đặt lệnh hay đề xuất mua cho sổ này: anh tự mua bán trên app công ty chứng khoán rồi ghi lại. Dữ liệu nằm trong `data/taisan/`:

- `journal.jsonl`: nhật ký chỉ ghi thêm (mua, bán, cổ phiếu thưởng, nạp, rút, cổ tức tiền, điều chỉnh tiền). Ghi sai thì huỷ: bot ghi thêm một dòng huỷ, không sửa dòng cũ. Danh mục luôn được tính lại từ nhật ký (giá vốn bình quân, T+2 theo lịch giao dịch có ngày lễ: mua phiên T bán được từ chiều T+2; cổ phiếu thưởng bán được ngay). Bot từ chối dòng không thể xảy ra (bán quá số đang có hoặc cổ phiếu chưa về, ngày tương lai, mua/bán ngày nghỉ). Tiền mặt âm chỉ cảnh báo, không chặn.
- `state.json`: cắt lỗ/mục tiêu, ngưỡng tỷ trọng, các cảnh báo đã gửi và kết quả của chúng. `prices.json`: giá đóng cửa gốc (chưa điều chỉnh quyền) cho biểu đồ. `pending.json`: quyền chờ về.
- **Giá theo đúng giá anh nhập:** phí mua anh tự cộng vào giá mua; giá bán ghi đúng giá khớp; bot không tự trừ phí hay thuế ở đâu cả. Phí, thuế, lãi ký quỹ chưa ghi thì khớp một lần bằng `/tstien SỐ_DƯ_THẬT` (một dòng điều chỉnh, tính vào lãi/lỗ, không vào vốn gốc).

| Lệnh (Telegram và Zoom) | Việc |
|---|---|
| `/ts` | Tổng quan: tổng tài sản, tiền mặt, giá trị cổ phiếu, quyền chờ về, lãi/lỗ, từng mã |
| `/tsmua FPT 20 73891 [YYYY-MM-DD] [ghi chú]` | Đã mua (giá đã gồm phí). Bot báo ngày bán được (T+2) và lấy cắt lỗ/mục tiêu của Siêu tín hiệu nếu anh chưa đặt |
| `/tsban FPT 20 75000 [YYYY-MM-DD]` | Đã bán: lãi/lỗ theo giá vốn bình quân |
| `/tsthuong FPT 15` · `/tscotuc FPT 2000 [số cp]` | Cổ phiếu thưởng đã về · cổ tức tiền đã về (đồng/cp; số cp mặc định lấy từ quyền chờ về hoặc số đang giữ) |
| `/tsnap 50000000` · `/tsrut 10000000` | Nạp, rút tiền (thay đổi vốn gốc) |
| `/tstien 1268895` | Đặt tiền mặt bằng số dư thật trên app CTCK (một dòng điều chỉnh) |
| `/tscatlo FPT 52000 [60000]` · `/tscatlo FPT xoa` | Mức cắt lỗ [và mục tiêu] của anh · xoá. Hạ mức cắt lỗ thì bot ghi lại và chấm sau 5/10/20 phiên (không bao giờ hạ im lặng) |
| `/tskiemtra FPT [GIÁ] [KL] [CẮTLỖ]` | **Kiểm tra trước khi mua**: lệnh có nằm trong ngân sách rủi ro không (2% tài sản một lệnh, mọi cắt lỗ cộng lại ≤ 6%, một mã ≤ 30%); ⛔ bình quân giá xuống, mua khi trang BÁN, chưa có cắt lỗ; ⚠️ mua đuổi > 5% giá tín hiệu, cắt lỗ xa hơn 16,4% (lệnh lỗ nặng nhất của quy tắc). Bot không khuyên mua, chỉ tính |
| `/tsgiu FPT 52000 lý do ít nhất ba từ` | Giữ một mã dù bot báo ⛔/🔴: bắt buộc có cắt lỗ dưới giá hiện tại và lý do; bot ghi lại, báo ⛔ khi thủng mức đó, chấm sau 5/10/20 phiên |
| `/tssan 60000000 [lý do]` · `/tssan` | Sàn tài sản để bot nhắc khi tài sản 15:30 xuống dưới (mặc định 60% vốn gốc); chỉ nhắc một lần mỗi đợt, không khoá lệnh · xem |
| `/tsmuctieu 2026-12-24` · `/tsmuctieu xoa` · `/tsmuctieu` | Mốc hoàn vốn anh đặt; bot đặt số đo cạnh mốc (không dự báo) ở báo cáo thứ Sáu, bản tin sáng thứ Hai, trang web · về mặc định · xem |
| `/tsnhatky [n]` · `/tshuy [số]` | Nhật ký (20 dòng) · huỷ dòng cuối hoặc dòng số đó |
| `/tsbaocao` | Báo cáo ngay (như bản 15:30) |
| `/tsnguong 25 40` | Ngưỡng tỷ trọng một mã: theo dõi, nguy hiểm (%) |
| `/tsxuat` | File Excel (Telegram); Zoom trả về đường link tải trên web (cần mật khẩu web) |
| `/tsnhap` | Gửi file `.xlsx` vào bot Telegram kèm chú thích `/tsnhap`: bot cho xem trước và đối chiếu; ghi bằng `/tsnhap xacnhan` (sổ trống) hoặc `/tsnhap thaythe` (thay toàn bộ, sổ cũ lưu bản sao); bỏ: `/tsnhap huy` |

**Tab DANH MỤC (`/taisan`, cần mật khẩu web)**, bố cục như trang Danh mục của app cũ, từ trên xuống:
- **Lời chào** và biến động phiên gần nhất (không tính nạp/rút), số phiên tăng/giảm liên tiếp, số việc cần làm.
- **Cảnh báo tỷ trọng**: mã vượt ngưỡng, bấm × để ẩn trong phiên; nút xuất **XLSX** và **CSV**.
- **Tổng tài sản**: lãi/lỗ so với vốn gốc, các ô tiền mặt, giá trị thị trường, lãi/lỗ đã chốt và đang mở, cổ tức, quyền chờ về, **Rủi ro đang mở** (mọi cắt lỗ chạm thì mất bao nhiêu, % tài sản so với trần 6%, mã chưa có cắt lỗ), VN-Index.
- **Mục tiêu hoàn vốn**: số học trên số đo (cần bao nhiêu %, VNINDEX phải lên đâu, quy tắc đo được cho bao nhiêu mỗi lệnh), đặt mốc ngay trên thẻ. Không dự báo.
- **Việc cần làm phiên tới** (chỉ hiện khi có): các việc treo ⏳ (⛔/🔴 chưa xử lý, số phiên treo, cái giá của chần chừ), ❗ mã thiếu cắt lỗ, 🎯, ⚠️.
- **Thước kỷ luật**: tài sản thực và nếu bán đúng ⛔/🔴, cảnh báo xử lý trong 1 phiên, lệnh mua theo/ngoài quy tắc, bảng kết quả theo nhóm (giá 10 phiên sau). Tính ở báo cáo 15:30, đo bằng code.
- **Tỷ trọng vị thế** và **Tối ưu danh mục**: điểm đa dạng hoá, biến động năm, các thẻ Tỷ trọng (vạch cân bằng rủi ro), Ngành, Tương quan.
- **Hiệu suất danh mục**: 7N/1T/3T/6T/1N/Tất cả; bật **So sánh VN-Index** để xem % theo đơn vị quỹ, tắt để xem tổng tài sản và vốn gốc theo đồng; chạm, di chuột hoặc dùng phím mũi tên để xem từng ngày.
- **Mã đang nắm giữ**, mỗi mã một thẻ: tên công ty, số lô và số lượng, biểu đồ giá 3 tháng, giá hiện tại và thay đổi so với phiên trước, PnL, vốn trung bình, cắt lỗ/mục tiêu, **nếu chạm cắt lỗ mất bao nhiêu** (hoặc «chưa có cắt lỗ: không giới hạn»), việc treo ⛔/🔴 hay «giữ có lý do», 🟠 trong ngày, cổ phiếu chờ T+2, tín hiệu Siêu tín hiệu, tổng mua và giá trị hiện tại, nút **Đọc tin tức** và **Xem lệnh** (nhật ký lọc theo mã). Nút **Làm mới giá** lấy lại giá khớp và biểu đồ nhỏ.
- **Quyền chờ về** (nút ghi nhận khi về) và quyền sắp tới.
- Các mục thu gọn: Chi tiết danh mục, Thêm giao dịch (nút **Kiểm tra lệnh mua** = `/tskiemtra`, không ghi gì vào sổ), Nhật ký giao dịch (lọc theo loại và mã, có nút huỷ), Nạp/rút/cổ tức/điều chỉnh, Cắt lỗ & mục tiêu, Nhận xét & chiến lược, Báo cáo cuối ngày, Nhập file Excel, Ngưỡng cảnh báo tỷ trọng.
  - **Nhận xét & chiến lược**: tín hiệu Siêu tín hiệu của từng mã, nút Quant Engine và AI phản biện, kết quả cảnh báo bán.
  - **Báo cáo cuối ngày**: bản gửi lúc 15:30, xem bản mới nhất, nút gửi ngay qua Telegram và Zoom.
  - **Nhập file Excel**: xem trước rồi xác nhận, tối đa 1 MB.
- **Reset danh mục**: gõ `XOA` để xoá, sổ cũ được lưu bản sao.

**Biểu đồ:** một điểm mỗi phiên từ dòng đầu tiên của nhật ký, theo giá đóng cửa gốc (chưa điều chỉnh quyền, đúng với số cổ phiếu thật đang giữ). Phần trăm tính theo **đơn vị quỹ** nên nạp/rút tiền không bị tính là lãi/lỗ. Sau khi nhập Excel, bot dựng lại toàn bộ lịch sử (vài phút); sau đó mỗi phiên thêm một điểm lúc 15:30.

**Quyền chờ về:** bot đọc lịch sự kiện (VCI) của các mã đã giữ trong 120 ngày. Cổ tức tiền và cổ phiếu thưởng đã qua ngày GDKHQ mà anh chưa ghi được tính vào tổng tài sản thành dòng riêng (cổ tức ước tính sau thuế 5%); ghi `/tsthuong` hoặc `/tscotuc` khi về thì dòng chờ tự chuyển thành tài sản thật. Quyền mua phát hành thêm không tính.

**Báo cáo 15:30** (Telegram và Zoom, các ngày giao dịch; nguồn giá chưa có phiên thì chờ tới 18:00): tổng tài sản, hôm nay và từ đầu so với VNINDEX, dòng **rủi ro đang mở**; 📌 mức cắt lỗ vừa lấy theo kế hoạch của trang (mã chưa có); **việc cần làm phiên tới**: mỗi ⛔ (đóng cửa dưới cắt lỗ) và 🔴 (trang đã chốt BÁN) là một **việc treo có hạn** (bán phiên sau, ưu tiên ATO; số phiên đã treo; từ phiên thứ hai: cái giá của chần chừ bằng đồng so với giá phiên sau cảnh báo; hai lối ra: `/tsban` hoặc `/tsgiu`), ❗ mã nặng chưa có cắt lỗ, 🟠 dấu hiệu sớm trong phiên mà trang không chốt (chỉ theo dõi), 🎯, ⚠️; từng mã; phân bổ; 🛡️ **rủi ro kế hoạch** (từng cắt lỗ chạm thì mất bao nhiêu, tổng, tài sản còn lại và % cần hoàn vốn khi đó, sàn); quyền chờ về và sắp tới (14 ngày). **Thứ Sáu** thêm kết quả cảnh báo, 📏 **thước kỷ luật** và 🎯 **mục tiêu hoàn vốn**. Trước khi gửi bot hỏi lại tín hiệu ngày của các mã đang giữ. Báo cáo không bao giờ nói đến phí hay thuế: giá đúng như anh nhập. Chỉ bản chạy đúng lịch 15:30 mới ghi trạng thái (mức cắt lỗ lấy theo trang, việc treo, sàn, thước kỷ luật); `/tsbaocao` và bản trên web trong phiên chỉ đọc lại, không mở việc treo bằng giá đang khớp.

**Cảnh báo bán trong phiên** (lượt quét Siêu tín hiệu và cảnh báo trong phiên, ghi rõ "Tài sản"): ⛔ Khẩn giá thủng cắt lỗ · 🔴 Mạnh trang đã chốt BÁN · 🎯 Chốt lời chạm mục tiêu. 🟠 Sớm (đổi chiều trong phiên, chưa chốt; của trang hoặc bot ước tính) **không đẩy nữa** từ v1.5.0 (đo được: bán sớm theo 🟠 không hơn chờ trang chốt, đúng 48%): vẫn ghi lại, hiện ở báo cáo 15:30, trang web và `/stvquet`; `INTRADAY_EARLY=1` bật lại. Ngày hạn của một việc treo, **14:20** bot nhắc một lần nếu sổ chưa có lệnh bán (không phụ thuộc công tắc cảnh báo trong phiên). Mỗi cảnh báo được ghi lại và chấm **giá sau 5 và 10 phiên** (trang web và báo cáo thứ Sáu): giá thấp hơn nghĩa là bán theo cảnh báo là đúng. Chân mọi bản đẩy trong phiên: *Bot im lặng = không có việc cần làm.*

**Kỷ luật (v1.5.0, `vnbot/kyluat.py`)** - khoản lỗ đo được (24/10/2026) đến từ mua ngược tín hiệu BÁN hoặc cao hơn giá tín hiệu, giữ qua 🔴 của trang và dồn một mã quá nặng, không phải từ việc chọn mã (12 lệnh đã đóng thắng 8). Lớp này bịt đúng các lỗ đó quanh sổ tiền thật, **mọi quy tắc đều có số đo, không quy tắc nào là lệnh**:
- Cắt lỗ: mã chưa có thì lấy mức kế hoạch của trang (khi trang MUA và mức dưới giá đóng cửa); mức đã lưu không bao giờ tự hạ hay nâng (mức mới của trang chỉ ghi bên cạnh); anh hạ tay thì bot ghi lại, nói khoản lỗ-nếu-chạm tăng bao nhiêu và chấm sau 5/10/20 phiên.
- Rủi ro đang mở = tổng (giá − cắt lỗ) × khối lượng; 🟢 ≤ 5%, 🟡 đến 8%, 🔴 trên 8% hoặc có mã nặng (≥ 5% tài sản) chưa có cắt lỗ. Sàn tài sản `/tssan` chỉ nhắc và ghi (quy tắc sàn chưa được đo).
- Kiểm tra trước khi mua `/tskiemtra` và thẻ 📏 trên mọi `/tsmua`: sổ không bao giờ từ chối một lệnh, chỉ gắn nhãn (✅ trong ngân sách / ⚠️ có lưu ý / ⛔ ngoài quy tắc) để sau này so kết quả các nhóm bằng giá 10 và 20 phiên sau. Bán khi trang vẫn MUA và chưa có ⛔/🔴 được gắn «bán trước tín hiệu», cũng để đo.
- Việc treo: mỗi ⛔/🔴 có hạn bán là phiên sau (hoặc phiên đầu tiên bán được theo T+2 với cổ phiếu mới mua); đóng khi anh bán, khi anh giữ có lý do (`/tsgiu`, bắt buộc cắt lỗ), hoặc tự rút khi trang MUA lại; huỷ lệnh bán bằng `/tshuy` thì việc treo mở lại. Thước kỷ luật thứ Sáu đếm: bán trong 1 phiên / giữ có lý do / bỏ qua, và tài sản sẽ ra sao nếu mọi ⛔/🔴 được bán ở phiên sau (giá đóng cửa phiên sau, gần đúng).
- Mục tiêu hoàn vốn: số học trên số đo. Ví dụ 24/10/2026: cần +38%; VNINDEX 2022-10/2026 **chưa có cửa sổ 2 tháng nào** đạt mức đó (1.152 cửa sổ, tốt nhất +23,5%), 6 tháng 1,0% số cửa sổ, 12 tháng 9,6%; quy tắc ⛔/🔴 đo được +1,14%/lệnh (582 lệnh) nên mục tiêu 2 tháng đòi gấp ~10 lần mức đo được, hoàn vốn với mức đo được là việc ~20 tháng. Bot nói thẳng điều đó mỗi tuần thay vì «cần +x%/phiên». Chi tiết: `docs/backtest_2026-10-24/README.md`.
- **Bot không đặt lệnh; không khuyên margin hay bình quân giá xuống; không trừ phí/thuế vào giá anh nhập.** Mọi ý kiến của Claude vẫn chỉ là bóng để đo (xem *Claude chọn bóng*).

**Backtest các quy tắc bán** (23/10/2026, `docs/backtest_2026-10-23/thoat_tai_san.md`; 39 mã: 13 mã anh từng giao dịch và VN30, 582 lần MUA của trang): bán khi ⛔ **hoặc** 🔴 (cách bot đang báo) cho lãi trung bình như chỉ chờ trang BÁN (+1,1% so với +1,2% mỗi lệnh) nhưng số lệnh lỗ quá 10% giảm từ 31 xuống 5; trên 13 mã của anh đây là quy tắc tốt nhất (+0,6%/lệnh). Bán sớm theo 🟠 không tốt hơn chờ trang chốt (đúng 48% số lần), nên cảnh báo 🟠 khuyên theo dõi, chỉ bán sớm khi sát mức cắt lỗ. Giữ tới khi thủng cắt lỗ bắt được vài xu hướng rất lớn (VIC, VHM) nhưng thắng 23%, lỗ trung bình −7%, và thua trên 13 mã của anh.

**Excel:** xuất 4 sheet như app cũ (*Vị thế hiện tại*, *Lịch sử giao dịch*, *Tổng quan*) thêm sheet *Tiền mặt* (nạp, rút, cổ tức, điều chỉnh), nên file xuất ra nhập lại được đầy đủ. Nhập file của app cũ: dòng "Bán … giá 0" (cổ phiếu thưởng app cũ ghi nhầm) được sửa thành cổ phiếu thưởng; số lượng bị tách lẻ (CTS 3.947 + 1.053) được gộp lại theo lịch sử; "Vốn gốc" thành một lần nạp ngày đầu tiên; phần chênh tiền mặt thành một dòng "Lỗ trước khi theo dõi". Bản xem trước đối chiếu lãi/lỗ đã chốt, từng lệnh bán, số cổ phiếu, lãi/lỗ tổng và tiền mặt với file.

### Trang web

Giao diện Coral (v1.7.0, thiết kế lại theo Hallmark): giấy xám ấm, mỗi bản ghi là một tờ giấy cotton có cạnh sáng bên trái và bóng đổ nhẹ sang phải, nút như thẻ in nổi (nút chính màu mực), lựa chọn 2-4 mục là một rãnh có thẻ trượt. Màu coral chỉ dùng cho vòng focus, gạch chân link và con dấu "Đã chọn" trên phiếu của Claude; xanh lá/đỏ chỉ ở lãi/lỗ; trạng thái là khung viền mực theo màu của nó. Chữ Be Vietnam Pro (vẽ riêng cho tiếng Việt), số trong bảng bằng Geist Mono; số thiếu ghi "chưa có", không dùng gạch ngang. Giao diện tối được suy ra (không đảo màu): không bóng đổ, nền sáng dần theo tầng. Trên điện thoại các bảng của trang Claude thành từng khối có nhãn. Thanh bên trái liệt kê mọi trang; trên điện thoại thanh này gập thành thanh 48 px với nút menu mở trang chọn toàn màn hình (hoạt động cả khi tắt JavaScript). Có giao diện sáng và tối. Toàn bộ dùng được bằng bàn phím, đạt kiểm tra truy cập WCAG 2 A/AA (axe-core) ở mọi trang, cả hai giao diện, từ màn 390 px.

- **Thanh bên:** **Tổng quan** (`/`), **Danh mục** (`/taisan`); nhóm *Công cụ*: AI & cảnh báo, Claude chọn bóng, Bot giao dịch; nhóm *Hệ thống*: **Cài đặt** (`/caidat`), Cấu hình Zoom, Phiên bản. Dưới cùng: ngày (thứ, dương lịch, âm lịch), trạng thái thị trường, nút sáng/tối, số phiên bản (đỏ nếu code khác bản phát hành).
- **Tổng quan** (`/`): dải số đầu trang (lượt AI, thị trường, cảnh báo, rổ sàng lọc), phân tích một mã (AI phản biện, quyết định định lượng, phân tích chuyên sâu), lượt gần đây, ba tài khoản ảo, watchlist, nhà cung cấp AI.
- **Danh mục** (`/taisan`): danh mục tiền thật của anh, xem mục **Tài sản** bên trên.
- **Cài đặt** (`/caidat`): các tham số của bot (giờ chạy, sàng lọc và hạn mức AI, cảnh báo trong phiên, bot giao dịch ảo, Siêu tín hiệu, ngân sách rủi ro Tài sản, Claude chọn bóng, kết nối), mỗi nhóm một nút Lưu. Giá trị lưu vào `data/settings.json`, được ưu tiên hơn biến môi trường trên Botkeep và giữ qua các lần khởi động lại; mỗi mục ghi rõ giá trị đang lấy từ đâu (web, biến môi trường, mặc định) và có hiệu lực ngay hay sau khi khởi động lại. Khoá và token (Telegram, Anthropic, Zoom, mật khẩu web) chỉ hiện "đã đặt / chưa đặt", không sửa được trên web: đặt trên Botkeep rồi khởi động lại.
- **Claude chọn bóng** (`/claude`): xem mục **Claude chọn bóng**.
- **Bot giao dịch** (`/bot`): biểu đồ NAV, các danh mục ảo, ngưỡng và lịch sử hiệu chỉnh; nút **Chạy phiên ngay** như `/chay`.

### Báo cáo tự động

Ba lịch chạy độc lập (giờ Việt Nam):

1. **Bot giao dịch** (nếu `TRADE_MODE` khác `off`), mỗi ngày lúc `TRADE_TIME` (mặc định = `DAILY_REPORT_TIME` nếu sau 15:05, ngược lại 15:30; **không bao giờ trước 15:05**, khi nến ngày mới chốt): khớp các phiên chưa xử lý, ra quyết định, đặt lệnh cho phiên tới cho cả ba chiến lược, gửi báo cáo 🛡️ Phòng thủ, 🤖 Quant Engine, 🚀 Động lượng và 📊 so sánh qua Telegram. Sau đó **tự hiệu chỉnh** (nếu `AUTO_CALIBRATE=1`) trên phiên vừa xử lý, gửi kết quả ⚙️.
2. **Sàng lọc watchlist rồi mới gọi AI**, thứ Hai - thứ Sáu lúc `DAILY_REPORT_TIME` (có thể đặt buổi sáng; trước giờ đóng cửa phân tích dùng phiên đã đóng gần nhất). Quant Engine xét mọi mã của `SCAN_UNIVERSE` (mặc định `vn100`: rổ VN100 cộng watchlist; `vn30` hoặc `watchlist` để thu hẹp) mà không tốn lượt AI, rồi gửi một tin tóm tắt: số mã theo từng hành động, các mã được gửi AI, các mã đạt nhưng chưa gửi, và từng mã trong watchlist. Lượt quét VN100 mất vài phút vì phải tải giá, dòng tiền và tin tức của 100 mã. Ba tài khoản ảo không đổi rổ (`UNIVERSE_VN30`); chỉ `DAILY_AI_MAX` (mặc định 5) mã có hành động trong `DAILY_AI_ACTIONS` (mặc định `BUY,SELL`), xếp theo độ tin cậy của engine, mới được gửi AI, và chỉ khi hạn mức AI trong ngày còn đủ. Cách gửi theo `DAILY_AI_MODE`:
   - `critic` (mặc định, giống bot OKX): **mỗi mã 1 lượt gọi AI**. AI được giao nhiệm vụ tìm lý do để từ chối quyết định của hệ thống và trả lời đồng ý/phản đối, độ chắc, lý do, rủi ro chính. AI không đổi hành động hay mức giá, bot giao dịch không đi theo AI.
   - `full`: chuỗi agent đầy đủ như `/phantich` (khoảng 25 lượt gọi mỗi mã).
   - `off`: chỉ gửi kết quả của Quant Engine.

   **Kết quả bóng:** mỗi lần phản biện được lưu (`data/critic_log.json`) và chấm trên các phiên sau đó. Lệnh MUA: vào giá đóng cửa ngày quyết định, chạm stop trước (−1R), chạm mục tiêu (+R:R), hoặc hết 20 phiên. Lệnh BÁN: giá sau 10 phiên. Tin hằng ngày và `/phanbien` cho biết các lệnh AI phản đối kết thúc ra sao, tức AI chặn đúng bao nhiêu phần trăm. 5 kết quả gần nhất được đưa vào lời nhắc của lần phản biện sau, không tốn thêm lượt gọi.

   Mã khác muốn AI xem thì gửi `/phanbien MÃ` (1 lượt) hoặc `/phantich MÃ` (đầy đủ).
3. **Báo cáo Tài sản** (danh mục tiền thật), các ngày giao dịch lúc **15:30**, gửi Telegram và Zoom; chạy riêng, không phụ thuộc `TRADE_MODE` hay `SIEUTINHIEU`. Xem mục **Tài sản**.
4. **Claude chọn bóng** (nếu có `ANTHROPIC_API_KEY` và `SIEUTINHIEU=1`), các ngày giao dịch lúc `CLAUDE_PICK_TIME` (mặc định **15:50**), gửi Telegram và Zoom. Xem mục **Claude chọn bóng**.

- **Ngày lễ:** bot giao dịch chỉ xử lý những phiên VNINDEX thực sự có nến, nên ngày lễ tự bỏ qua; phân tích watchlist cũng bỏ qua ngày lễ khi chạy sau 15:05.
- **Chạy bù:** mỗi lần chạy (kể cả lúc server khởi động), bot xử lý lần lượt mọi phiên bị lỡ từ phiên cuối đã xử lý, mỗi phiên chỉ dùng dữ liệu tới phiên đó. Bù tối đa 20 phiên; phiên cũ hơn bị bỏ qua và lệnh chờ của chúng bị huỷ (báo cáo có ghi).
- **Lỗi dữ liệu:** bot thử lại sau 10, 20, 40, 60... phút (tối đa 6 lần/ngày), báo Telegram một lần; phiên chưa xử lý được chạy bù lần sau.
- **Nguồn giá trễ nến một mã:** nếu một mã đang giữ hoặc có lệnh chờ (kể cả tài khoản thật) chưa có nến của phiên hôm nay dù phiên trước vẫn có, hoặc một mã trong rổ sắp xếp hạng lại (VN30 của Động lượng, rổ vốn hoá của Phòng thủ) chưa có nến của phiên hôm nay dù đã giao dịch đủ 5 phiên trước đó, bot chờ và thử lại 15 phút một lần tới 18:00 (để không bỏ sót stop/lệnh và không xếp hạng thiếu mã); quá 18:00 mới coi như mã tạm ngừng giao dịch và báo cáo ghi rõ. Mã ít giao dịch (không có nến đủ 5 phiên liền) không bị chờ. Mã chưa có giá phiên hôm nay không được đề xuất MUA.
- **Không chạy trùng:** mỗi tài khoản ảo ghi lại phiên cuối đã xử lý; chạy lại (lịch, `/chay`, nút web) không khớp/đặt lệnh hai lần. Các file trạng thái được ghi nguyên tử.

### Bot giao dịch

- **Bắt đầu:** để `TRADE_MODE=paper` (mặc định) vài tháng. Theo dõi NAV ba chiến lược so với VNINDEX trên trang `/bot` hoặc bằng `/danhmuc`.
- **Chọn chiến lược cho tín hiệu thật:** `SIGNAL_STRATEGY=factor` (Phòng thủ, mặc định), `momentum` hoặc `engine`.
  - Phòng thủ: được chọn qua kiểm định độc lập ngày 15/10/2026 (xem README chính): 2013–10/2026 15,6%/năm, sụt giảm tối đa −26%; giai đoạn TEST 2024–2026 chỉ 8,1%/năm, thua VNINDEX (15,9%). Lợi thế chính là giảm rủi ro. **Lưu ý:** các con số này đến từ bộ kiểm định (research/eval) và danh sách mã point-in-time **không có trong gói này**, nên không tái lập được từ mã nguồn ở đây; hãy coi là số liệu tham khảo bên ngoài. Chiến lược tính trên giá đã điều chỉnh cổ tức, còn VNINDEX là chỉ số giá (không gồm cổ tức), nên phần so sánh với VNINDEX có lợi cho chiến lược khoảng 1,5–2,5 điểm %/năm (ước tính); so sánh NAV tài khoản ảo với VNINDEX trên trang `/bot` và trong báo cáo cũng lệch theo cách đó. Mỗi lần dựng rổ (cuối tháng) bot đọc dữ liệu khoảng 400 mã HOSE từ VCI; lần đầu mất vài phút.
  - Động lượng: con số backtest cũ tính trên rổ VN30 hôm nay (thiên lệch sống sót); trên rổ point-in-time sụt giảm tối đa 33–49%.
  - Quant Engine: rất ít lệnh và rủi ro thấp.
- **Khi muốn nhận lệnh cho tài khoản thật:**
  1. Đặt `TRADE_MODE=signal` rồi khởi động lại.
  2. Gửi `/von <tiền mặt>`.
  3. Mỗi tối bot gửi danh sách **MUA** (mã, khối lượng, giá tối đa, stop, T1/T2) và cảnh báo **chạm stop / T1 / chuyển SELL** cho các mã bạn đang giữ.
  4. Sau khi khớp trên app CTCK, báo `/khop` để bot cập nhật danh mục thật. Stop/T1/T2 lấy theo tín hiệu BUY/HOLD gần nhất của mã đó (chỉ khi stop thấp hơn giá khớp và T1 cao hơn; tín hiệu SELL không cho stop).
  5. Mỗi tối, trước khi tính lệnh, bot định giá tài khoản thật theo giá đóng cửa (khối lượng mua đề xuất tính trên tổng tài sản theo giá thị trường, không theo giá vốn).
  6. **Cổ tức, cổ phiếu thưởng:** khi giá bị điều chỉnh quyền, bot tự chia stop/T1/T2 theo tỷ lệ điều chỉnh (như tài khoản ảo) và báo ⚖️, nhưng **không tự đổi số cổ phiếu**: khi cổ phiếu thưởng/cổ tức về tài khoản, báo `/thuong MÃ SỐ_CP`; cổ tức tiền mặt thì cập nhật `/von` khi tiền về. Nếu nguồn dữ liệu chưa điều chỉnh lịch sử giá (giá giảm vượt biên độ), bot báo ⚠️ và không báo chạm stop cho mã đó hôm ấy; sau 3 phiên vẫn chưa điều chỉnh thì bot ước tính tỷ lệ từ khoảng trống giá.
- **Đêm đầu tiên chạy lâu hơn** (khoảng 20–30 phút): bot tải lịch sử khối ngoại, tự doanh và ETF cho cả rổ, cùng khoảng 400 phiên độ rộng HOSE (mỗi phiên một lượt gọi). Dữ liệu được lưu dần, nên khởi động lại giữa chừng không mất phần đã tải.
- **Cách tự hiệu chỉnh và các giới hạn:** xem README chính, mục "Bot giao dịch tự hiệu chỉnh". Quy tắc: dữ liệu kiểm tra (60 phiên gần nhất) chỉ được dùng **một lần** - sau mỗi đêm đã đem bộ thử ra kiểm tra (đổi hay không), chiến lược đó chờ đủ 60 phiên mới mới xét tiếp; và bộ thử phải thắng bộ hiện tại hơn **2 lần mức nhiễu** (độ lệch điểm của các bộ lân cận trên cùng dữ liệu kiểm tra). Trên dữ liệu ngẫu nhiên, bản cũ đổi tham số khoảng 4 lần/năm, bản mới không đổi lần nào. Muốn tắt: `AUTO_CALIBRATE=0`. Muốn quay về ngưỡng gốc: xoá `data/results/quant_params.json`.
- **Tham số thêm ngày 23/10/2026** (mặc định = hành vi cũ; hiệu chỉnh đêm được thử từng bước; kết quả backtest trong `BAO_CAO_RA_SOAT_2026-10-23.md`, mục E): `breakout_projected_ok` (0/1, MUA mã phá đỉnh không còn kháng cự, mục tiêu `projected_rr` × rủi ro), `confidence_sell_min` (ngưỡng SELL riêng khi đang giữ, mặc định 0,75), `stop_atr_mult` và `stop_min_pct` (khoảng stop = hệ số × ATR, không sát hơn % này), `buy_order_sessions` (lệnh mua giới hạn sống bao nhiêu phiên, giới hạn và stop theo quyết định mới nhất, huỷ khi engine hết BUY), `limit_fill_touch` (1 = khớp khi giá thấp nhất chạm giới hạn). Phòng thủ có thêm `fb_market_daily` + `fb_market_buffer_pct` (lọc MA200 kiểm tra hằng ngày với vùng đệm) và `fb_momentum_weight` (động lượng 6 tháng trong điểm xếp hạng), **không tự hiệu chỉnh**, chỉ đổi tay trong `quant_params.json` sau khi kiểm định.

### So sánh với sieutinhieu.vn (tuỳ chọn, dùng cá nhân)

Đặt `SIEUTINHIEU=1` để theo dõi tín hiệu của sieutinhieu.vn bên cạnh bot. **Tín hiệu này không bao giờ đổi quyết định, lệnh hay mức giá của bot.**

- **Watchlist và danh mục riêng:** Siêu tín hiệu có watchlist riêng (`/stvtheodoi`, tối đa 60 mã), không dùng watchlist `/theodoi` hay rổ VN30 của bot, và danh mục riêng (`/stvdanhmuc`); `/danhmuc` của bot chỉ còn một dòng chỉ sang. Watchlist mặc định 60 mã, lập ngày 21/10/2026: VN30 tại ngày đó (để so cùng mã với bot) và 30 mã khác có giá trị giao dịch lớn nhất (trung vị 20 phiên, trong VN100, HNX30 và các mã UPCoM lớn). Danh sách này cố định: khi VN30 đổi thành phần, tự sửa bằng `/stvtheodoi`. Mặc định đã đủ 60 mã, nên muốn thêm mã thì bỏ bớt trước. Mã không có trên sàn bị từ chối. Đặt sẵn bằng biến `STV_WATCHLIST` nếu muốn.
- **Lấy dữ liệu:** mỗi tối sau giờ đóng cửa, bot hỏi lịch sử tín hiệu của từng mã đang giữ rồi từng mã trong watchlist riêng (tối đa 60 mã watchlist cộng các mã đang giữ ngoài watchlist; 60 mã mất khoảng 2 phút), đúng như trang hiển thị khi tra một mã. Không quét cả thị trường (danh sách đó là sản phẩm trả phí), không giả trình duyệt, mỗi lượt cách nhau 1,5 giây; nếu trang từ chối (401/403/429) bot ngừng hỏi 24 giờ.
- **`/nhanh MÃ`** thêm một dòng 📡: tín hiệu gần nhất của trang và việc nó cùng hay khác chiều với hệ thống.
- **Sổ ảo Siêu tín hiệu:** mua theo tín hiệu MUA mới (phát đúng ngày, không tính lịch sử trang tạo lại) ở **giá mở cửa phiên sau**, bán ở giá mở cửa phiên sau tín hiệu BÁN, chia đều 10 phần, có phí, thuế, T+2 - cùng luật với các sổ của bot. Báo cáo tối có mục 📡 (NAV, lệnh, tín hiệu mới trong watchlist kèm quyết định của bot, mã bot MUA mà trang đang BÁN).
- **Bản tin sáng (Zoom)**, các ngày giao dịch lúc `STV_MORNING_TIME` (mặc định 08:30): danh sách tín hiệu toàn thị trường của phiên trước - phần xem miễn phí (trang khoá phần còn lại cho gói Premium, bot ghi rõ bao nhiêu mã bị khoá và không tìm cách mở khoá), tín hiệu mới và trạng thái của watchlist riêng (tín hiệu MUA kèm vùng vào, mục tiêu, cắt lỗ của trang), và danh mục của anh với lãi/lỗ thực tế. Gửi vào Zoom; chưa cài Zoom hoặc Zoom gửi lỗi thì gửi Telegram. Gửi ngay: `/stvsang`. Danh sách toàn thị trường được lấy lúc chạy tối (sau 15:10, khi trang đã chốt phiên); thiếu thì bot hỏi lại 15 phút một lần tới 21:00 (tối đa 8 lần). `STV_MORNING_TIME` phải trước 11:30; cần `TRADE_MODE` khác `off`. Nếu nguồn giá chưa có phiên gần nhất, bản tin ghi cảnh báo.
- **Danh mục tiền thật của anh** nằm ở mục **Tài sản** (`/ts`, trang `/taisan`); các lệnh cũ `/stvkhop`, `/stvcosan`, `/stvvon`, `/stvthuong`, `/stvxoa` đã bỏ, gõ vào bot sẽ chỉ sang lệnh `/ts` tương ứng. Mua mới ghi bằng `/tsmua`: bot lấy cắt lỗ và mục tiêu của trang nếu anh chưa tự đặt.
- **Quét tín hiệu (danh mục + watchlist):** trong ngày giao dịch, từ 09:00 tới 17:00, cứ `STV_SCAN_MINUTES` phút (mặc định 15) bot quét; một mã có ở cả danh mục và watchlist chỉ hỏi trang một lần.
  - **Danh mục của anh:** quét cả ngày (vài mã, rất nhẹ).
  - **60 mã watchlist:** tín hiệu từng mã của trang là tín hiệu ngày, chỉ có sau giờ đóng cửa (~15:08), trong phiên không đổi (đã kiểm tra trực tiếp ngày 22/10). Vì vậy watchlist được quét từ 15:15 tới 16:00 (khoảng 3 lượt, mỗi lượt 60 mã mất chừng 2 phút) và bot nhắn ngay các tín hiệu MUA/BÁN mới của ngày, kèm vùng vào, mục tiêu, cắt lỗ; không phải chờ báo cáo tối hay bản tin sáng. Muốn quét cả trong phiên: `STV_WATCH_INTRADAY=1` (khoảng 2.000 lượt hỏi trang mỗi ngày thay vì vài trăm).
  - **Đổi chiều trong phiên:** trang có phát hiện đổi chiều ngay trong phiên (ví dụ HDB MUA → BÁN lúc 09:54), nhưng gói miễn phí chỉ cho xem 2 mã mỗi bên (phần còn lại cần Premium). Bot làm hai việc:
    - xem phần miễn phí đó mỗi lượt quét và nhắn nếu đó là mã anh đang giữ hoặc theo dõi;
    - **tự ước tính cho mọi mã đang giữ và theo dõi** (khoảng 64 mã): lấy trạng thái MUA/BÁN hiện tại của trang, tính mức giá mà nếu đóng cửa ở đó thì xu hướng ngày đổi chiều, rồi mỗi lượt quét so với giá khớp mới nhất. Qua mức là nhắn (mã đang giữ đổi sang BÁN vào mục danh mục, kèm lãi/lỗ và T+2; các mã khác vào mục "🧮 Bot ước tính"). Không hỏi thêm trang lần nào: mức tính từ giá đóng cửa công khai (nguồn giá của bot), giá khớp từ DNSE.
    - Cách tính, dò lại từ lịch sử tín hiệu công khai của trang (12 mã, 360 tín hiệu): MUA → BÁN khi giá đóng cửa dưới trung bình 20 phiên và MACD(12,26,9) dưới đường tín hiệu; BÁN → MUA khi giá trên trung bình 20 phiên, MACD trên đường tín hiệu và giá cao hơn phiên trước. Trùng ngày với trang 88% tín hiệu, lệch tối đa 2 phiên 97% (phần lệch thường là bot báo sớm 1-2 phiên). Mức của HDB và MSR ngày 22/10 chỉ lệch 0,1% so với mức trang hiện miễn phí.
    - Đây là **ước tính của bot**, ghi rõ trong tin nhắn, không phải tín hiệu của trang; trang chốt sau giờ đóng cửa (~15:08). Sau 15:15 bot báo các mã đã đổi chiều trong phiên mà đóng cửa trang không chốt ("↩️"). Mỗi mã, mỗi chiều chỉ báo một lần một ngày (trang và bot cùng thấy thì chỉ một tin). Ngày GDKHQ mà nguồn giá chưa điều chỉnh có thể báo sai; giá lệch quá biên độ so với phiên trước thì bỏ qua. Tắt: `STV_ESTIMATE=0`. Xem bảng: `/stvdoichieu`.
    - Lượt quét đầu tiên trong phiên mỗi ngày tính mức cho cả danh sách (khoảng 1-2 phút); các lượt sau chỉ đọc giá khớp: cả danh sách trong một lần hỏi bảng giá (VCI), mã nào bảng giá không có thì hỏi DNSE. Ngày GDKHQ trên HOSE/HNX, giá tham chiếu của bảng giá đưa mức về giá đã điều chỉnh (UPCoM thì không, vì giá tham chiếu UPCoM là giá bình quân phiên trước).
  - **Quét toàn thị trường (300 mã):** danh sách đầy đủ của trang cần Premium, nên bot tự quét 300 mã có giá trị giao dịch bình quân 20 phiên cao nhất (HOSE, HNX, UPCoM), cùng cách tính trên. Khác phần watchlist: trạng thái MUA/BÁN ban đầu của mỗi mã cũng do bot tính, bot không hỏi trang về các mã này.
    - Dữ liệu: giá đóng cửa toàn thị trường mỗi phiên một lần hỏi (VNDirect, lưu `data/external/market_closes.json`, khoảng 4 MB). Lần đầu tải 135 phiên (khoảng 4 phút, chia ra các lượt quét); sau đó mỗi ngày 2 lần hỏi. Giá trong phiên: một lần hỏi bảng giá cho cả 300 mã.
    - **14:46** (sau ATC): nhắn danh sách mã đổi chiều của ngày, kèm phần trang cho xem miễn phí (số lần đổi chiều trên toàn thị trường và 2 mã mỗi bên) đặt cạnh kết quả của bot.
    - **Sau 15:15**, khi trang đã chốt: nhắn bản đối chiếu. Bot (300 mã) và trang (toàn thị trường, tính cả mã đổi chiều trong phiên rồi quay lại) không cùng phạm vi, nên chỉ so được đúng-sai trên các mã anh theo dõi (trang có tín hiệu từng mã): bot đúng bao nhiêu tín hiệu của trang, báo thừa, báo sót, trạng thái khớp bao nhiêu mã. Kết quả cộng dồn qua các phiên (lưu `data/external/market_compare.json`).
    - Thử thật ngày 22/10: bot thấy 13 mã sang MUA, 2 mã sang BÁN trong 300 mã. Trên 60 mã watchlist, trang chốt 4 MUA và bot đúng cả 4, không báo thừa; trạng thái khớp 60/60.
    - Xem: `/stvthitruong`. Tắt: `STV_MARKET=0`. Đổi số mã: `STV_MARKET_SIZE` (50-500).
- **Báo bán cho danh mục Tài sản:** từng lượt quét ở trên. Khi trang có tín hiệu **BÁN** cho một mã anh đang giữ, bot nhắn Zoom (Telegram dự phòng): 🔴 Mạnh khi trang đã chốt, giá hiện tại, lãi/lỗ theo giá vốn, khối lượng bán được (tính T+2). 🟠 Sớm (mới đổi chiều trong phiên, của trang hoặc bot ước tính) từ v1.5.0 chỉ được ghi lại, không đẩy (`INTRADAY_EARLY=1` để đẩy như cũ, kèm "có thể đổi khi đóng cửa"); tín hiệu được trang chốt lúc đóng cửa vẫn đẩy 🔴. Mỗi tín hiệu chỉ báo một lần; không lấy được tín hiệu thì bot báo một lần trong ngày. Quét ngay: `/stvquet` (vẫn hiện 🟠). Cảnh báo cắt lỗ/mục tiêu trong phiên vẫn như trước.
- **Lãi/lỗ của danh mục Tài sản** (`/ts`, `/stv`, bản tin sáng): trong giờ giao dịch theo giá khớp mới nhất, ngoài giờ theo giá đóng cửa; tính theo đúng giá anh nhập (giá mua đã gồm phí do anh cộng vào, giá bán không trừ phí/thuế). Sổ ảo so sánh vẫn tính phí và thuế như các sổ của bot.
- **Tin nhắn dễ đọc trên điện thoại:** chia mục có biểu tượng (📊 toàn thị trường, 👀 watchlist, 💼 tài khoản thật), mỗi mã một dòng, chi tiết (vùng vào, mục tiêu, cắt lỗ) ở dòng thụt vào bên dưới.
- **`/stv`**: tổng quan - dòng Tài sản của anh, sổ ảo, lợi nhuận của từng sổ so với các sổ của bot. **`/stvdanhmuc`**: từng mã của sổ ảo so sánh. **`/stv MÃ`**: tín hiệu của một mã và lịch sử của nó tính lại hai cách - theo giá tín hiệu (giá đóng cửa ngày phát, chưa phí) và làm được thật (mở cửa phiên sau, có phí). Lịch sử cũ của trang được tạo lại hàng loạt (backtest), nên chỉ sổ ảo chạy từ hôm bật mới là phép so sánh công bằng.

### Claude chọn bóng (tuỳ chọn, cần API của Claude hoặc 9Router)

Câu hỏi: dùng Claude đọc các lệnh MUA và danh mục thì có chọn tốt hơn, có giúp quyết định đúng lúc hơn không? Không ai biết trước, và **không backtest được**: Claude đã đọc tin tức của quá khứ, hỏi nó về một tín hiệu năm 2025 là hỏi khi đã có đáp án. Vì vậy bot chỉ **ghi lại và đo từ hôm bật trở đi**. **Mọi ý kiến của Claude không phải lệnh**: không đổi lệnh, sổ, nhật ký Tài sản hay cảnh báo nào; quy tắc ⛔/🔴 vẫn là quy tắc chính.

- **Bật:** đặt `ANTHROPIC_API_KEY` (tạo tại platform.claude.com, nên đặt luôn hạn mức chi tiêu ở đó) và `SIEUTINHIEU=1`, rồi khởi động lại. Chưa có khoá Claude nhưng đã cấu hình 9Router (`ROUTER9_BASE_URL`, `ROUTER9_API_KEY`) thì tính năng vẫn chạy, do 9Router trả lời.
- **9Router trả lời thay Claude** (`CLAUDE_PICK_ROUTER9=1`, mặc định bật): khi **chưa có `ANTHROPIC_API_KEY`**, khi **API của Claude lỗi** (mất kết nối, khoá sai 401/403, hết tín dụng 400, quá tải 5xx, quá thời gian) hoặc khi **chạm trần** `CLAUDE_PICK_MONTHLY_USD`, bot gửi đúng câu hỏi đó cho 9Router (model `CLAUDE_PICK_ROUTER9_MODEL`, bỏ trống thì `ROUTER9_MODEL`) qua cùng chuỗi nhà cung cấp của các agent: dùng chung hạn mức ngày (`LLM_MAX_CALLS_PER_DAY`, `ROUTER9_MAX_CALLS_PER_DAY`), giãn cách và thời gian nghỉ khi 9Router lỗi. 9Router **không tìm tin** nên câu trả lời không có nguồn, và không tính vào trần USD của Claude (lượt Claude đã trả tiền trước khi lỗi vẫn được tính). Lời từ chối hay câu trả lời hỏng của Claude vẫn là câu trả lời, không chuyển sang 9Router. Mỗi tin nhắn, bản ghi và trang web ghi rõ ai trả lời và vì sao. Cả hai cùng lỗi thì bot thử lại như cũ. Tắt: `CLAUDE_PICK_ROUTER9=0`.
- **Mỗi phiên lúc `CLAUDE_PICK_TIME`** (mặc định 15:50, sau khi trang chốt tín hiệu ~15:08), một lượt gọi Claude:
  1. **Ứng viên:** các tín hiệu MUA mới của phiên trong watchlist Siêu tín hiệu (60 mã, `/stvtheodoi`), mạnh nhất trước; rồi tới **các mã bot ước tính vừa đổi sang MUA** trong 300 mã thanh khoản cao nhất (cùng cách tính với trang, khớp ~88% ngày đổi chiều; tắt bằng `CLAUDE_PICK_BOT=0`), tối đa 25 mã. Nhờ vậy gần như phiên nào cũng có nhiều lệnh để chọn.
  2. **Danh mục Tài sản:** từng mã đang giữ với khối lượng, giá vốn đúng như anh nhập (đã gồm phí, bot không trừ gì), giá đóng cửa, lãi/lỗ, tỷ trọng, cắt lỗ/mục tiêu, trạng thái của trang và **quy tắc đang báo gì** (⛔ thủng cắt lỗ hoặc 🔴 trang BÁN → BÁN; còn lại GIỮ).
  3. Kèm số liệu code tính từ giá đóng cửa (thay đổi 1/20/60 phiên, khoảng cách tới TB20/50/200, đỉnh 52 tuần, giá trị giao dịch, khối lượng) và VNINDEX. Claude không tự đưa ra giá.
  4. Claude tìm tin (tối đa `CLAUDE_PICK_SEARCHES` lượt, chỉ trên CafeF, Vietstock, VnExpress, Tin nhanh chứng khoán, VnEconomy, Báo Đầu tư, Người quan sát, HOSE, HNX, UBCKNN): phát hành thêm, cổ đông lớn mua bán, bị cảnh báo/kiểm soát, kết quả kinh doanh, pháp lý, ngày GDKHQ.
  5. Claude trả lời qua một biểu mẫu cố định: **một mã chọn** (hoặc không chọn), **các mã nên tránh**, **xếp hạng mọi mã**, và **ý kiến GIỮ / CÂN NHẮC BÁN cho từng mã đang giữ**, kèm lý do và nguồn. Mã ngoài danh sách bị loại; nguồn chỉ được giữ khi đúng là trang Claude đã tìm thấy. Claude được dặn không khuyên mua thêm mã đang lỗ để bình quân giá.
  6. Bot nhắn Telegram và Zoom, ghi vào `data/claude_pick.json`. Không có lệnh MUA mới nhưng có danh mục thì Claude chỉ xem danh mục; không có cả hai thì không gọi Claude.
- **Khi có cảnh báo ⛔/🔴/🟠 cho mã đang giữ** (trong phiên hoặc sau giờ đóng cửa): ngay sau cảnh báo của bot, Claude tìm tin của mã đó (tối đa 3 lượt), cho biết giá đi như vậy vì **tin riêng của mã, theo thị trường chung hay chưa rõ**, và ý kiến GIỮ / CÂN NHẮC BÁN. Tối đa `CLAUDE_PICK_ALERTS` lần mỗi ngày (mặc định 5; 0 = tắt), mỗi cảnh báo một lần. Cảnh báo của bot vẫn gửi ngay, không chờ Claude.
- **Tổng kết tuần**, sau phiên cuối tuần: bot tự tính các dữ kiện (lệnh anh đã ghi và lúc đó trang đang MUA hay BÁN, mua cao hơn giá tín hiệu bao nhiêu; các cảnh báo ⛔/🔴 và anh đã bán trong 2 phiên chưa; tài sản cuối tuần), Claude viết vài dòng nhận xét về kỷ luật và một việc cho tuần tới. Tắt: `CLAUDE_PICK_WEEKLY=0`. Xem lại: `/claude tuan`.
- **Chấm điểm, như nhau cho mọi ứng viên** (kể cả mã Claude chọn và mã Claude bảo tránh), chưa trừ phí và thuế (như nhau cho mọi lệnh nên không ảnh hưởng phép so sánh):
  - **Sau 10 phiên** (cách đo chính, có kết quả sau 2 tuần): mua ở giá mở cửa phiên sau, so với giá đóng cửa 10 phiên sau.
  - **Theo quy tắc ⛔/🔴** (chỉ lệnh của trang): bán ở giá mở cửa sau khi trang báo BÁN (đợi đủ T+2), khi giá chạm cắt lỗ của trang (từ T+2; mở cửa dưới cắt lỗ thì bán ở giá mở cửa), hoặc sau 60 phiên.
  - **Ý kiến về danh mục và về cảnh báo:** giá 5 và 10 phiên sau so với giá đóng cửa hôm đó; bot tách riêng những lần Claude nói khác quy tắc.
- **So sánh:** chỉ các phiên có **từ 2 lệnh trở lên** (có lựa chọn thật) và mọi lệnh đã đủ 10 phiên. Lệnh Claude chọn được so với **trung bình mọi lệnh cùng ngày** và với **lệnh mạnh nhất của trang**; **nửa trên bảng xếp hạng** của Claude so với nửa dưới; lệnh **bảo tránh** so với phần còn lại.
- **Kết luận ở mốc định sẵn:** bot chỉ kết luận ở **20 phiên có lựa chọn (sơ bộ)** và **40 phiên**, mỗi mốc tính đúng trên chừng ấy phiên đầu tiên, kèm khoảng tin cậy 90%. Xem kết quả mỗi ngày rồi dừng khi thấy đẹp dễ tưởng nhầm may mắn là tài, nên kết luận ở một mốc không đổi theo các phiên sau. Với nhiễu thực tế của lệnh MUA (±5 điểm % sau 10 phiên), mốc 20 phiên chỉ thấy được khi Claude hơn trung bình khoảng 2 điểm %/lệnh trở lên. Chỉ nên cân nhắc dùng tiền thật khi kết quả rõ ràng, và vẫn giữ mức rủi ro nhỏ cho mỗi lệnh.
- **Xem:** `/claude` (phiên gần nhất, ý kiến danh mục, thành tích), `/claude lichsu`, `/claude tuan`, trang web **Claude chọn bóng** (`/claude`, trong Công cụ): mở đầu bằng **phiếu chọn của phiên** (mã được chọn in lớn, đóng dấu "Đã chọn", giá tín hiệu, cắt lỗ, mục tiêu, lý do, mã nên tránh, ai trả lời) và bảng xếp hạng mọi ứng viên với hai cách chấm; rồi danh mục, kết quả đo tới nay, các lần Claude xem cảnh báo, tổng kết tuần, lịch sử và nút chạy ngay.
- **Chi phí:** mặc định Claude Opus 5.5, mức suy nghĩ `medium`: khoảng 0,2-0,5 USD một phiên (10 lượt tìm tin là 0,1 USD), 0,03-0,08 USD một lần xem cảnh báo, khoảng 0,01 USD một bản tổng kết tuần; tổng khoảng 6-12 USD một tháng. Trần `CLAUDE_PICK_MONTHLY_USD` (mặc định 15 USD) tính trên số token và lượt tìm thực tế của mọi lượt gọi, theo giá của **model thực sự trả lời** (một lượt máy chủ chuyển sang model khác tính theo model đó; model lạ tính giá đắt nhất); các lượt gọi xếp hàng sau một khoá chung nên hai lượt không bao giờ cùng tiêu đồng cuối. Một lượt được ghi trước khi trả tiền với chi phí ước tính, nên bot chết giữa chừng vẫn không làm trần hụt; một lượt quá giờ (timeout) cũng tính theo ước tính và không gọi lại (SDK không tự thử lại để khỏi trả hai lần). Chạm trần thì bot ngừng gọi tới tháng sau và báo lý do. Mỗi phiên chỉ gọi một lần; Claude không trả lời được (mạng, quá tải) thì bot thử lại sau 15, 30, 45 phút rồi bỏ phiên đó và báo một lần (bỏ rồi thì không hỏi lại trong ngày dù có lỗi gì); ngày không có nến VNINDEX tới 18:00 (nghỉ lễ lịch không biết) thì bỏ qua, không ghi "không có lệnh"; chưa lấy được tín hiệu của trang thì bot chờ. Mốc kết luận 20/40 phiên được **ghim** vào đúng các phiên đạt mốc lần đầu (xoá dữ liệu cũ không làm mốc trôi). `/claude chay` chạy nền và nhắn khi xong.
- **An toàn:** key không bao giờ được in ra trong tin nhắn lỗi; nội dung trang web Claude đọc được coi là dữ liệu, không phải chỉ dẫn; trang web chỉ hiện link http(s). Nếu bộ lọc an toàn của Claude từ chối một yêu cầu, API tự chạy lại trên model dự phòng Anthropic khuyến nghị (tham số `fallbacks`).

### Cảnh báo trong phiên (không gọi AI)

Các tài khoản ảo vẫn khớp lệnh theo nến ngày sau 15:05 (đúng như kiểm định). Trong phiên, bot chỉ **báo** để bạn xem ngay thay vì đợi báo cáo buổi tối. Mỗi `INTRADAY_INTERVAL_MIN` phút (mặc định 15) trong giờ khớp lệnh liên tục (9:15–11:30, 13:00–14:30), bot đọc giá khớp gần nhất (DNSE) và gửi qua Telegram/Zoom:

- ⛔ mã đang giữ có giá ≤ stop, kèm số cổ phiếu bán được theo T+2 (cổ phiếu mua phiên T chỉ tính từ phiên chiều T+2); 🎯 chạm T1; 🏁 chạm T2; 🔒 giá sàn tính từ giá đóng cửa phiên trước (có thể không bán được). Áp dụng cho tài khoản thật (chế độ `signal`) và tài khoản ảo Quant Engine. Ngày GDKHQ: nếu nguồn đã điều chỉnh lịch sử, stop/T1/T2 được so theo giá đã điều chỉnh (⚖️); nếu chưa, giá thấp hơn giá sàn tính từ giá đóng cửa hôm trước được báo là có thể GDKHQ thay vì chạm stop;
- 🛒 lệnh mua giới hạn hôm nay của Quant Engine đang trong vùng mua, hoặc ⚠️ giá đã dưới stop dự kiến (nên bỏ lệnh);
- 📉 VNINDEX giảm `INTRADAY_VNI_DROP_PCT` % (mặc định 2) và gấp đôi mức đó so với phiên trước.

Mỗi cảnh báo gửi một lần mỗi ngày, kể cả khi khởi động lại (`data/intraday_alerts.json`). Giá đọc cách nhau vài phút nên có thể bỏ lỡ một cú xuyên stop rất ngắn. Bật/tắt bằng công tắc trên trang **AI & cảnh báo** (`INTRADAY_ALERTS` chỉ là giá trị mặc định). Bộ theo dõi này chạy ở mọi `TRADE_MODE` (kể cả `off`) vì lời nhắc 14:20 của việc treo Tài sản đi ra từ đây, không phụ thuộc công tắc.

### Bật/tắt kênh thông báo và đổi vốn tài khoản ảo trên web

- Trang **AI & cảnh báo** (`/ai`), thẻ **Kênh thông báo**: công tắc bật/tắt thật cho *Telegram · thông báo* (báo cáo, AI phản biện, cảnh báo; lệnh Telegram vẫn được trả lời khi tắt), *Zoom · thông báo*, *Zoom · nhận lệnh* và *Cảnh báo trong phiên*. Lưu trong `data/channels.json`, giữ qua lần khởi động lại. Kênh chưa cấu hình hiện "chưa cấu hình".
- Trang **Bot giao dịch** (`/bot`), thẻ **Vốn tài khoản ảo**: nhập vốn mới (tối thiểu 10 triệu), chọn tài khoản (Phòng thủ, Quant Engine, Động lượng), đánh dấu ô xác nhận rồi bấm **Đổi vốn**. Tài khoản được chọn bắt đầu lại từ đầu với vốn mới từ lần chạy phiên tới (bấm "Chạy phiên ngay" để bắt đầu luôn); sổ cũ được giữ lại thành `data/trading/<file>.reset-<thời điểm>`. Vốn mới lưu trong `data/trading/paper_capital.json` và thay cho `PAPER_CAPITAL`. Không đổi được khi bot đang chạy phiên.

### Zoom Team Chat

Ngang hàng Telegram, làm y như bot OKX (`vnbot/zoom.py`, `vnbot/pages.py`):

- **Thông báo** (báo cáo hằng ngày, AI phản biện, cảnh báo trong phiên) gửi vào Zoom qua Incoming Webhook (`ZOOM_WEBHOOK_URL`, `ZOOM_WEBHOOK_TOKEN`; trong kênh gõ `/inc connect vnbot`) và/hoặc chatbot tới JID đã ghép đôi.
- **Gõ lệnh trong Zoom** giống Telegram: `nhanh FPT`, `phanbien HPG`, `phantich FPT`, `danhmuc`, `lenh`, `trangthai`, `trogiup`... Trong kênh: `/vnbot nhanh FPT`.
- **Ghép đôi đúng một tài khoản** như bot OKX: người lạ nhắn bot bị từ chối, bạn được báo JID của họ qua Telegram; gõ `/zoompair <JID> [tên]` hoặc bấm **Ghép đôi** trên trang `/zoom`. `/zoomstatus` xem, `/zoomxoa` gỡ (chỉ trên Telegram). `ZOOM_ALLOWED_USERS` (JID/email) được tự ghép đôi ở tin nhắn đầu. Gỡ app khỏi Zoom thì bot tự xoá ghép đôi.

**Các trang phục vụ Zoom** (có sẵn trong bot):

| Trang | Dùng cho ô nào trên Marketplace |
|---|---|
| `/zoom/oauth/callback` | OAuth Redirect URL + OAuth Allow List |
| `/webhook/zoom` (hoặc `/zoom/bot`) | Bot endpoint URL, Event notification endpoint, Deauthorization endpoint |
| `/privacy` | Privacy Policy URL |
| `/terms` | Terms of Use URL |
| `/support` | Support URL |
| `/docs` | Documentation URL / Home URL |
| `/data-deletion` | Hướng dẫn xoá dữ liệu |
| `/zoom/install` | Nút "Thêm vào Zoom" |
| `/zoom` | Trang cấu hình (cần mật khẩu web): đúng các URL theo `PUBLIC_URL` kèm nút Chép, kiểm tra biến môi trường, trạng thái cài đặt/ghép đôi, người đang chờ ghép đôi, 20 request Zoom gần nhất (chữ ký đúng/sai và vì sao) |

Các trang công khai song ngữ Việt–Anh, không đặt cookie. Botkeep đã có HTTPS (ví dụ `https://vn.bot-keep.xyz`) nên chatbot chạy được ngay trên Botkeep.

**Cài đặt (Local Test):**
1. Đặt `PUBLIC_URL` (ví dụ `https://vn.bot-keep.xyz`), `SUPPORT_EMAIL`, rồi Restart.
2. Đăng nhập web, mở `<PUBLIC_URL>/zoom`, làm theo 7 bước và bấm "Chép" từng URL dán vào marketplace.zoom.us (General App → bật Team Chat → Chatbot, slash command `vnbot`).
3. Điền `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` (Development), `ZOOM_BOT_JID`, `ZOOM_SECRET_TOKEN`, `ZOOM_ACCOUNT_ID`, Restart, bấm **Validate** endpoint trên Marketplace (trang `/zoom` ghi lại request Validate).
4. Local Test → **Add App Now** → Allow → về trang callback báo "Đã cài thành công".
5. Zoom → Team Chat → Apps → nhắn `trangthai` → Telegram báo JID → gõ `/zoompair <JID>` (hoặc bấm Ghép đôi trên `/zoom`).

**Dùng chung với bot OKX:** mỗi app Zoom chỉ có **một** Bot endpoint URL. Muốn gõ lệnh cho cả hai bot thì tạo **app thứ hai** cho bot này (cùng cách làm). Nếu chỉ cần nhận thông báo của bot này trong Zoom, có thể mượn app của bot OKX: điền cùng `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_BOT_JID`, `ZOOM_ACCOUNT_ID`, đặt `ZOOM_TO_JID` = JID bot OKX đang ghép đôi và để trống `ZOOM_SECRET_TOKEN`; tin của hai bot hiện chung một cuộc trò chuyện, cùng tên bot.

File báo cáo HTML chỉ gửi qua Telegram; trên Zoom bot gửi kèm link trang web.

## 7. Hàng đợi và bảo vệ quota

- Mỗi lúc chạy **một** mã; yêu cầu khác xếp hàng.
- Yêu cầu **trùng mã và ngày** với một lượt đang chờ/đang chạy được **gộp** vào lượt đó: ai hỏi cũng nhận kết quả, AI chỉ chạy một lần.
- Web + Telegram chỉ được có tối đa `MAX_PENDING` (mặc định 10) lượt chờ; vượt thì báo "Hàng đợi đầy". Lượt tự động hằng ngày không bị giới hạn này.
- Trang web **bắt buộc mật khẩu**; form gửi từ trang web khác bị từ chối (chống CSRF); trang không cho nhúng vào iframe.
- Bot chỉ phục vụ id trong `TELEGRAM_ALLOWED_IDS`.

## 8. Tầng AI dự phòng

Mỗi lượt gọi AI đi theo `PROVIDER_ORDER` (mặc định `router9,groq,openrouter,xai`; nhà cung cấp chưa có key tự bỏ qua):

- Lỗi kết nối, timeout, lỗi máy chủ hoặc bị từ chối yêu cầu → chuyển ngay sang nhà cung cấp kế tiếp.
- Bị 429 (hết quota) → nghỉ theo `Retry-After`, hoặc `LLM_QUOTA_COOLDOWN_SEC` (mặc định 300 s).
- Key bị từ chối → nghỉ 1 giờ. 9Router lỗi → nghỉ `ROUTER9_RETRY_SEC` (mặc định 900 s).
- Khi tất cả đang nghỉ hoặc hết hạn mức, lượt gọi **báo lỗi ngay, không gửi gì** (trước đây vẫn thử lần lượt, chính điều này làm gateway đang quá tải bị dồn thêm). `LLM_TRY_RESTING=1` để quay lại cách cũ.
- Không retry bên trong SDK: một lượt gọi chỉ gửi một yêu cầu tới mỗi nhà cung cấp.
- Tối đa `LLM_MAX_CONCURRENT` (mặc định 2) yêu cầu cùng lúc.
- Hạn mức ngày (giờ VN, giữ qua lần khởi động lại trong `data/llm_usage.json`): tổng `LLM_MAX_CALLS_PER_DAY` (mặc định 60) và riêng từng nhà cung cấp `<TÊN>_MAX_CALLS_PER_DAY` (`ROUTER9_`, `GROQ_`, `OPENROUTER_`, `XAI_`; OpenRouter mặc định 45, dưới mức 50 lượt/ngày của gói free; 0 = không giới hạn). Nhà cung cấp hết hạn mức được bỏ qua tới hôm sau; hết tổng hạn mức thì mọi lượt gọi AI báo lỗi tới hôm sau; `/phantich` bị từ chối ngay khi hạn mức còn lại dưới 25 lượt (bạn vẫn nhận quyết định của Quant Engine). `/trangthai` và trang web hiện số lượt đã dùng.

- Khoảng nghỉ tối thiểu giữa hai lượt gọi tới cùng một nhà cung cấp: `<TÊN>_MIN_INTERVAL_SEC` (mặc định 0). Các lượt gọi cùng lúc xếp hàng lần lượt, không tới dồn một lúc.

**9Router đứng sau một tài khoản ChatGPT free** (vd chatgpt2api giả lập Codex): một tài khoản free chỉ chịu được ít lượt mỗi giờ, trả lời chậm, và dễ bị đánh dấu khi nhận nhiều yêu cầu dồn dập. Nên đặt:

```
DAILY_AI_MODE=critic             # 1 lượt gọi mỗi mã, chỉ là chat thường (không tool, không JSON mode)
DAILY_AI_MAX=5
LLM_MAX_CONCURRENT=1
ROUTER9_MIN_INTERVAL_SEC=30
ROUTER9_MAX_CALLS_PER_DAY=40     # chỉnh theo mức tài khoản chịu được
ROUTER9_RETRY_SEC=1800           # 9Router lỗi thì nghỉ 30 phút, chuyển sang OpenRouter
LLM_CALL_TIMEOUT_SEC=180
```

`/phantich` (chuỗi agent) cần gọi tool và structured output, mỗi mã khoảng 25 lượt: với tài khoản free nên hạn chế, dùng `/phanbien` thay thế. Bộ giả lập nào không hỗ trợ tool calling thì lượt đó báo lỗi và chuyển sang nhà cung cấp kế tiếp.

Một lượt phân tích đầy đủ tốn khoảng 15-35 lượt gọi AI (4 analyst kèm các vòng gọi tool, Bull/Bear, Research Manager, Trader, 3 góc nhìn rủi ro, Portfolio Manager). Giảm `MAX_TOOL_ROUNDS` nếu cần tiết kiệm thêm.

`QUICK_MODEL` / `DEEP_MODEL` chọn model trên 9Router cho hai tầng (tầng deep dùng cho Research Manager và Portfolio Manager). Các nhà cung cấp khác dùng `GROQ_MODEL`, `OPENROUTER_MODEL`, `XAI_MODEL`.

## 9. Toàn bộ biến môi trường

Phần lớn các biến dưới đây (trừ khoá, token và mật khẩu) sửa được ngay trên trang **Cài đặt** (`/caidat`); giá trị đặt trên web được ưu tiên hơn biến môi trường.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PROVIDER_ORDER` | `router9,groq,openrouter,xai` | Thứ tự dự phòng AI |
| `ROUTER9_BASE_URL`, `ROUTER9_API_KEY`, `ROUTER9_MODEL` | - / - / `gpt-4o-mini` | 9Router |
| `GROQ_API_KEY`, `GROQ_MODEL` | - / `openai/gpt-oss-20b` | Groq |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | - / model `:free` | OpenRouter |
| `XAI_API_KEY`, `XAI_MODEL` | - / `grok-4-fast` | Grok (tuỳ chọn) |
| `QUICK_MODEL`, `DEEP_MODEL` | `ROUTER9_MODEL` | Model 9Router cho từng tầng |
| `LLM_QUOTA_COOLDOWN_SEC` | 300 | Nghỉ khi 429 không có Retry-After |
| `ROUTER9_RETRY_SEC` | 900 | Nghỉ khi 9Router lỗi |
| `LLM_CALL_TIMEOUT_SEC` | 120 | Timeout mỗi lượt gọi AI |
| `LLM_MAX_CALLS_PER_DAY` | 60 | Tổng lượt gọi AI mỗi ngày (0 = không giới hạn) |
| `ROUTER9_MAX_CALLS_PER_DAY`, `GROQ_…`, `OPENROUTER_…`, `XAI_…` | 0, OpenRouter 45 | Hạn mức ngày từng nhà cung cấp (0 = không giới hạn) |
| `LLM_MAX_CONCURRENT` | 2 | Số lượt gọi AI chạy cùng lúc |
| `ROUTER9_MIN_INTERVAL_SEC`, `GROQ_…`, `OPENROUTER_…`, `XAI_…` | 0 | Số giây tối thiểu giữa hai lượt gọi tới nhà cung cấp đó |
| `LLM_TRY_RESTING` | 0 | 1 = vẫn gọi nhà cung cấp đang nghỉ khi không còn ai khác |
| `TELEGRAM_TOKEN` | - | Token bot (bỏ trống = tắt bot) |
| `TELEGRAM_ALLOWED_IDS` | - | Id được dùng bot |
| `TELEGRAM_REPORT_CHAT_IDS` | = allowed ids | Nơi nhận báo cáo hằng ngày |
| `WEB_PASSWORD` | - | **Bắt buộc** để mở trang web |
| `PUBLIC_URL` | - | Địa chỉ web, để bot gửi link báo cáo |
| `SERVER_PORT` / `PORT` / `APP_PORT` | 8080 | Cổng web (đọc theo thứ tự này) |
| `WATCHLIST` | - | Watchlist ban đầu (chỉ dùng lần chạy đầu) |
| `DAILY_REPORT_TIME` | `15:30` | Giờ phân tích watchlist tự động (HH:MM, giờ VN) |
| `INTRADAY_ALERTS` | 1 | Cảnh báo trong phiên (không gọi AI) |
| `INTRADAY_INTERVAL_MIN` | 15 | Số phút giữa hai lần kiểm tra trong phiên (tối thiểu 5) |
| `INTRADAY_VNI_DROP_PCT` | 2 | VNINDEX giảm bao nhiêu % thì báo (báo thêm khi giảm gấp đôi) |
| `INTRADAY_EARLY` | 0 | 1 = đẩy lại 🟠 (đổi chiều sớm trong phiên) cho mã đang giữ; mặc định chỉ ghi và hiện ở báo cáo 15:30 |
| `TAISAN_RISK_PCT` | 2 | Một lệnh mua được lỗ tối đa bao nhiêu % tài sản nếu chạm cắt lỗ (`/tskiemtra`) |
| `TAISAN_OPEN_RISK_PCT` | 6 | Trần của mọi cắt lỗ cộng lại (% tài sản) |
| `TAISAN_MAX_WEIGHT_NEW` | 30 | Một mã tối đa bao nhiêu % tài sản sau khi mua (không quá ngưỡng nguy hiểm `/tsnguong`) |
| `ZOOM_WEBHOOK_URL`, `ZOOM_WEBHOOK_TOKEN` | - | Gửi thông báo vào một kênh Zoom (Incoming Webhook) |
| `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_BOT_JID`, `ZOOM_ACCOUNT_ID` | - | Chatbot Zoom (thông báo tới JID đã ghép đôi, nhận lệnh) |
| `ZOOM_TO_JID` | - | Gửi thông báo tới JID này thay cho JID đã ghép đôi (mượn app của bot OKX) |
| `ZOOM_SECRET_TOKEN`, `ZOOM_VERIFICATION_TOKEN` | - | Nhận lệnh gõ trong Zoom tại `/webhook/zoom` (cần app riêng); Verification Token là cơ chế cũ |
| `ZOOM_ALLOWED_USERS` | - | JID/email được tự ghép đôi ở tin nhắn đầu |
| `SUPPORT_EMAIL` | - | Email liên hệ trên `/privacy`, `/support` |
| `SCAN_UNIVERSE` | `vn100` | Rổ mã lượt quét 15:30 xét, luôn cộng watchlist: `vn100`, `vn30`, `watchlist` |
| `DAILY_AI_MODE` | `critic` | Lượt tự động gọi AI thế nào: `critic` (1 lượt/mã), `full` (chuỗi agent, ~25 lượt/mã), `off` |
| `DAILY_AI_MAX` | 5 | Số mã watchlist được gửi AI mỗi ngày (0 = không gọi AI ở lượt tự động) |
| `DAILY_AI_ACTIONS` | `BUY,SELL` | Hành động của Quant Engine đáng để hỏi AI |
| `TRADE_TIME` | `DAILY_REPORT_TIME` nếu ≥ 15:05, ngược lại `15:30` | Giờ bot giao dịch + hiệu chỉnh chạy (HH:MM, giờ VN); giá trị trước 15:05 được nâng lên 15:05 |
| `MAX_PENDING` | 10 | Số lượt chờ tối đa từ web/Telegram |
| `TRADE_MODE` | `paper` | `paper` / `signal` / `off` |
| `PAPER_CAPITAL` | 1.000.000.000 | Vốn tài khoản ảo (VND) |
| `UNIVERSE_VN30` | 1 | 1 = rổ VN30 + watchlist; 0 = chỉ watchlist |
| `AUTO_CALIBRATE` | 1 | Tự hiệu chỉnh ngưỡng Quant Engine và Động lượng mỗi đêm (Phòng thủ không tự chỉnh) |
| `SIGNAL_STRATEGY` | `factor` | Chiến lược cho tín hiệu thật: `factor` (Phòng thủ) / `momentum` / `engine` |
| `DATA_DIR` | `data` | Thư mục dữ liệu (cache, báo cáo, jobs) |
| `MAX_DEBATE_ROUNDS`, `MAX_RISK_ROUNDS` | 1 / 1 | Số vòng tranh luận |
| `MAX_TOOL_ROUNDS` | 8 | Số lượt gọi tool tối đa của mỗi analyst |
| `KEEP_REPORTS` | 60 | Số thư mục báo cáo giữ lại |
| `CACHE_MAX_AGE_DAYS` | 3 | Xoá cache giá cũ hơn số ngày này |
| `ANTHROPIC_API_KEY` | - | Bật **Claude chọn bóng** (cần thêm `SIEUTINHIEU=1`); bỏ trống mà có 9Router thì 9Router trả lời |
| `CLAUDE_PICK` | 1 | 0 = tắt Claude chọn bóng dù đã có key |
| `CLAUDE_PICK_MODEL` | `claude-opus-5-5` | Model Claude |
| `CLAUDE_PICK_EFFORT` | `medium` | Mức suy nghĩ: `low` / `medium` / `high` (`high` được 20.000 token trả lời, hai mức kia 16.000) |
| `CLAUDE_PICK_MONTHLY_USD` | 15 | Trần chi phí mỗi tháng (USD), mọi lượt gọi Claude cộng lại |
| `CLAUDE_PICK_SEARCHES` | 10 | Số lượt tìm tin tối đa mỗi phiên (1-20) |
| `CLAUDE_PICK_TIME` | `15:50` | Giờ chọn mỗi ngày giao dịch (không sớm hơn 15:10) |
| `CLAUDE_PICK_BOT` | 1 | Thêm vào ứng viên các mã bot ước tính vừa đổi sang MUA (300 mã, cần `STV_MARKET=1`) |
| `CLAUDE_PICK_ALERTS` | 5 | Số lần tối đa mỗi ngày Claude xem tin khi có cảnh báo ⛔/🔴/🟠 cho mã đang giữ (0 = tắt) |
| `CLAUDE_PICK_WEEKLY` | 1 | Tổng kết tuần sau phiên cuối tuần (0 = tắt) |
| `CLAUDE_PICK_ROUTER9` | 1 | 9Router trả lời thay khi chưa có khoá Claude, Claude lỗi hoặc chạm trần USD (0 = tắt) |
| `CLAUDE_PICK_ROUTER9_MODEL` | - | Model trên 9Router khi trả lời thay Claude (bỏ trống = `ROUTER9_MODEL`) |

## 10. Giới hạn và xử lý sự cố

- Một lượt phân tích đầy đủ gọi AI khoảng 15-20 lần, mất vài phút. Model miễn phí có thể chậm hoặc bị giới hạn tốc độ.
- Khởi động lại khi đang phân tích thì lượt đó bị đánh dấu lỗi; gửi lại là được.
- Cache giá cũ và báo cáo vượt `KEEP_REPORTS` được tự xoá để không đầy 2 GB.

| Triệu chứng | Cách xử lý |
|---|---|
| Botkeep báo "This application is not responding" | So cổng trong dòng log `Web: listening on 0.0.0.0:<cổng>` với *assigned port* trên panel; khác nhau thì đặt `SERVER_PORT=<assigned port>`. Không có dòng đó thì kiểm tra file khởi động là `main_botkeep.py` |
| Mọi trang báo "Khởi động lỗi", `/health` trả 500 | Bot không khởi động được; lỗi in trên trang, chi tiết (`Start failed` + traceback) trong log Botkeep |
| Trang web báo "Trang web đang khoá" | Đặt `WEB_PASSWORD`, khởi động lại |
| Bot trả "Bot chưa cho phép tài khoản này" | Thêm id vào `TELEGRAM_ALLOWED_IDS`, khởi động lại |
| `Every LLM provider failed` | Xem `/trangthai`; kiểm tra key, quota, thêm nhà cung cấp dự phòng |
| `/nhanh` báo lỗi kết nối | DNSE/VCI không truy cập được từ host |
| Không có báo cáo hằng ngày | Kiểm tra watchlist, `DAILY_REPORT_TIME`, `TRADE_TIME`, ngày lễ, và log `Daily run` / `Trading run` |
| `/khop` báo sổ tài khoản thật bị hỏng | File `real.json` hỏng đã được chuyển sang `real.json.corrupt-...` (không xoá). Đổi tên lại thành `real.json` sau khi sửa, hoặc xoá file đó để bắt đầu sổ mới |
| Link báo cáo trong Telegram thiếu | Đặt `PUBLIC_URL` |
| `/hieuchinh` báo "chưa chạy" | Hiệu chỉnh chạy sau phiên đầu tiên lúc `TRADE_TIME`; cần đủ 80 phiên lịch sử |
| Bot không đặt lệnh nhiều ngày | Bình thường khi VNINDEX risk-off; xem lý do bằng `/nhanh MÃ` |

## 11. Phiên bản

Bot ghi số phiên bản ở một chỗ (`vnbot/version.py`) và hiện ở:

- **Web:** nhãn nhỏ `v1.1.0` cuối thanh bên ở mọi trang (chuyển đỏ kèm chữ “khác bản phát hành” nếu code khác bản phát hành), dòng cuối trang, trang **`/phienban`** (bản đang chạy, mã bản dựng, thời gian chạy, lịch sử phiên bản, cách cập nhật), và `/health`.
- **Telegram / Zoom:** lệnh **`/phienban`**, dòng đầu của `/trogiup` và `/trangthai`.
- **Tự báo khi cập nhật:** lần khởi động đầu tiên sau khi upload bản mới, bot nhắn vào Telegram/Zoom “🆕 Bot đã cập nhật: v1.0.0 → v1.1.0” kèm những gì mới; khởi động lại bình thường thì không nhắn.

**Mã bản dựng** (7 ký tự, ví dụ `1a2b3c4`) là dấu vân tay của toàn bộ code đang chạy. Cùng một bộ file cho cùng một mã trên mọi máy, nên bot biết server có chạy **đúng bản phát hành** không. Báo **khác bản phát hành** nghĩa là upload thiếu file, lẫn file của bản cũ, hoặc code đã bị sửa: hãy upload lại đủ gói.

Số phiên bản theo dạng `vMAJOR.MINOR.PATCH` (bản đầu tiên `v1.0.0`, lịch sử trước đó đã bỏ). Khi tự sửa code và muốn đánh số bản mới: tăng `VERSION`, thêm dòng vào `NOTES` trong `vnbot/version.py`, rồi chạy `python -m vnbot.version --stamp` để ghi mã bản dựng mới.

## 12. Bộ kiểm thử (chạy trước khi cập nhật bot)

Thư mục `tests/` có hơn 370 kiểm tra **chạy offline**. Chúng không cần mạng, không gửi Telegram/Zoom và không đọc `.env` hay dữ liệu thật của bot. Mỗi lần sửa code hoặc nhận bản mới, chạy trên máy tính (Python 3.11+) trước khi upload:

```bash
pip install -r requirements.txt -r requirements-dev.txt
python tests/run_all.py          # tất cả, khoảng 15-30 giây
python tests/run_all.py stv      # chỉ các bộ có tên chứa "stv"
python tests/run_all.py -v web   # in chi tiết một bộ
pytest -q                        # hoặc chạy qua pytest
```

Kết quả `18/18 bộ đạt` là an toàn để cập nhật. Có ❌ thì đừng upload: dòng `FAIL ...` cho biết kiểm tra nào hỏng. Thiếu `httpx` thì 3 bộ web bị bỏ qua (⏭️) và bộ `version` bỏ phần web, không tính là lỗi.

| Bộ | Kiểm tra |
|---|---|
| `vn_rules` | Bước giá, giá trần/sàn (chính xác tới từng giá, không lỗi làm tròn), phiên, ngày lễ, T+2 theo lịch sàn |
| `ledger_fills` | Khớp lệnh sổ ảo: gap qua stop, trần/sàn trắng bên, T+2, nến lỗi, ngày GDKHQ, thanh khoản theo tiền |
| `factor` | Chiến lược Phòng thủ: lệnh khớp phiên sau, tỷ trọng đều, khối lượng theo NAV thị trường |
| `fixes_round1`, `fixes_round2`, `catchup` | Các lỗi đã sửa: sổ thật khi có cổ tức/thưởng, NAV theo giá thị trường, hiệu chỉnh tham số, nến trễ, cảnh báo VNINDEX, chạy bù phiên |
| `stv`, `stv_catchup`, `stv_morning` | Siêu tín hiệu: lấy tín hiệu lịch sự, sổ ảo so sánh, bản tin sáng, tài khoản thật riêng, gửi Zoom rồi Telegram |
| `version` | Số phiên bản trên web và Telegram/Zoom, mã bản dựng, báo khi cập nhật |
| `review_2026_10_23` | Các lỗi sửa ngày 23/10/2026 (xem `BAO_CAO_RA_SOAT_2026-10-23.md`): Output Guard, khối ngoại khớp lệnh, cache giá ngày nghỉ, chấm điểm bộ nhớ, hạn mức AI, Zoom |
| `web`, `security_web`, `security_zoom`, `security_telegram` | Mật khẩu web, chặn POST chéo trang, giới hạn kích thước request, chữ ký Zoom, ghép đôi Zoom, chỉ người được phép mới ra lệnh Telegram, token không lộ trong log |

Một số kiểm tra so với **số đã ghi lại** (ví dụ kết quả chiến lược Phòng thủ trên dữ liệu giả lập). Sửa chiến lược **có chủ đích** thì kiểm tra đó báo đổi: xem lại số mới rồi cập nhật trong file bộ kiểm thử. Bot không bao giờ chạy bộ kiểm thử; script tạo gói Botkeep bỏ thư mục `tests/` ra (có upload kèm cũng không sao).

> Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.
