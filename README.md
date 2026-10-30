# TradingAgents VN trên Botkeep

Hướng dẫn chạy TradingAgents VN liên tục trên Botkeep (hoặc bất kỳ máy chủ Python nào luôn bật). Một tiến trình `main_botkeep.py` gồm:

- **Trang web**: nhập mã cổ phiếu, xem hàng đợi, đọc báo cáo HTML của các agent, sửa danh sách theo dõi.
- **Bot Telegram**: `/phantich`, `/phanbien`, `/nhanh`, `/theodoi`, `/trangthai`, `/id`. Zoom Team Chat ngang hàng (tuỳ chọn).
- **Cảnh báo trong phiên**: stop, mục tiêu, vùng mua, VNINDEX giảm mạnh, không gọi AI.
- **Báo cáo tự động** cho danh sách theo dõi sau giờ đóng cửa mỗi ngày giao dịch (ngày lễ tự bỏ qua).
- **Bot giao dịch** chạy ba chiến lược song song (Phòng thủ - mặc định, Quant Engine và Động lượng VN30), mỗi chiến lược một tài khoản ảo, kèm (tuỳ chọn) lệnh đề xuất cho tài khoản thật. **Mỗi đêm bot tự hiệu chỉnh ngưỡng** của Quant Engine và Động lượng (Phòng thủ giữ bộ đã kiểm định). Không bao giờ gửi lệnh tới CTCK.

Hành động (BUY / HOLD / WATCH / SELL / NO_TRADE) và mọi mức giá do **Quant Engine** chốt bằng code. Các agent AI (4 analyst, Bull/Bear, Trader, 3 risk, Portfolio Manager) diễn giải quanh quyết định đó, rồi **Output Guard** kiểm tra lại bằng code. Mỗi lần chỉ phân tích một mã để vừa gói free (2 GB RAM, 2 GB ổ đĩa).

## Hai phần: Cổ phiếu (`/`) và Altcoin (`/altcoin/`)

Cùng server còn chạy được **bot Altcoin** (futures OKX, thư mục `altcoin/`, mặc định **tắt**): một server Botkeep, một gói zip, một lệnh `python main_botkeep.py`.

| | Cổ phiếu | Altcoin |
|---|---|---|
| Trang web | `/` | `/altcoin/` (thanh "Cổ phiếu / Altcoin" ở đầu trang) |
| Mật khẩu | `WEB_PASSWORD` | cùng `WEB_PASSWORD` |
| Telegram | bot của `TELEGRAM_TOKEN` | cùng bot: `/alt <lệnh>` (`/alt close` → `/alt close xacnhan`) |
| Zoom | app `vnbot` | cùng app: `alt <lệnh>` (trong kênh `/vnbot alt <lệnh>`) |
| Thông báo | các chat báo cáo, Zoom | cùng các chat và Zoom, mở đầu `🪙 Altcoin ›`, công tắc riêng trên `/ai#kenh` |
| Dữ liệu | `data/`, `DATABASE_URL` | riêng: `data/altcoin/`, `ALT_DATABASE_URL` |
| Bật | luôn chạy | `ALTCOIN_ENABLED=1`; biến riêng mang tiền tố `ALT_` |

Altcoin là tiến trình con: khởi động sau Cổ phiếu, dừng cùng Cổ phiếu, lỗi thì tự chạy lại, không bao giờ cầm token Telegram hay khoá Zoom. Altcoin lỗi hay tắt thì Cổ phiếu vẫn chạy như thường. Cấu hình, lệnh, chuyển từ server altcoin cũ sang, chạy live, quay lui và xử lý sự cố: [`docs/ALTCOIN_TRONG_STOCKVN.md`](docs/ALTCOIN_TRONG_STOCKVN.md).

---

## 1. Chuẩn bị

| Cần có | Lấy ở đâu |
|---|---|
| Ít nhất một nhà cung cấp AI | 9Router (endpoint kiểu OpenAI), hoặc key Groq / OpenRouter / xAI |
| Bot Telegram (tuỳ chọn) | Chat với [@BotFather](https://t.me/BotFather) → `/newbot` → lấy token |
| Mật khẩu trang web | Tự đặt, **bắt buộc** nếu muốn dùng trang web |

Dữ liệu thị trường (DNSE, VCI, CafeF, Google News) không cần key.

## 2. Tạo gói upload

Trong thư mục mã nguồn, trên máy có **Python 3.12 hoặc 3.13** (Botkeep chạy 3.13):

```bash
python scripts/build_botkeep_bundle.py            # tạo gói
python scripts/build_botkeep_bundle.py --check    # chỉ kiểm tra, không ghi gì
```

Lệnh tạo `dist/botkeep/`, `dist/stockvn-botkeep-<phiên bản>.zip` và `dist/botkeep.json` (số file, dung lượng, chỗ còn trống, mã bản dựng). Gói hiện có 165 file trong 20 thư mục, 2,61 MB khi giải nén, zip 0,94 MB.

Gói chỉ gồm `main_botkeep.py`, `requirements.txt`, `.env.example`, README này, LICENSE, các file `.py` của `tradingagents/` và `vnbot/`, cùng 3 file ảnh (`vnbot/static/favicon.png`, `vnbot/static/logo.png`, `tradingagents/assets/tauric-logo.svg`), và phần chạy của altcoin (`altcoin/bot/`, `altcoin/run.py`, `altcoin/hosted.py`, `altcoin/requirements.txt`). Không bao giờ có `.env`, `bot.env`, `data/`, file `.db`, `__pycache__/`, `tests/`, `docs/`, script thử, `tools/` và `dist/` của altcoin.

Script dừng (thoát 1) và không ghi gì khi:

- vượt giới hạn upload của Botkeep: 250 file (tính cả thư mục), 4.000.000 B khi giải nén và 4.000.000 B file zip. Gần 85% giới hạn thì cảnh báo;
- gói thiếu file, có file bị cấm, hoặc có file `.py` lỗi cú pháp;
- mã bản dựng khác bản phát hành, tức sửa code mà quên `python -m vnbot.version --stamp` (mục 11), hoặc với altcoin `cd altcoin && python -m bot.version --stamp`;
- file được đóng gói có thay đổi chưa commit. Thêm `--allow-dirty` để bỏ qua riêng kiểm tra này;
- chạy bằng Python mới hơn 3.13: bản mới nhận cú pháp mà Botkeep báo lỗi. Thêm `--allow-python` để bỏ qua (chỉ cảnh báo).

Mọi file trong zip mang thời điểm của commit mới nhất: cùng commit cho cùng một zip, còn bản mới luôn có thời điểm mới, nên Python trên server không chạy nhầm `__pycache__/` cũ.

Zip không ghi mục thư mục, để giữ số mục dưới 250. Nếu Botkeep giải nén thiếu thư mục lồng (`vnbot/static`, `tradingagents/agents/analysts`), tạo lại gói với `--dir-entries`.

### Deploy từ GitHub: nhánh `botkeep`

Không trỏ Botkeep vào `main`: cả repo có khoảng 270 mục (file + thư mục) và 3,7 MB, vượt giới hạn 250 mục. Mỗi lần `main` đổi, GitHub Actions (`.github/workflows/botkeep-branch.yml`) tạo gói như trên, rồi đẩy **đúng nội dung gói** lên nhánh **`botkeep`** (≈186 mục, kèm một `.gitignore` cho `data/` và `.env`). Gói vượt giới hạn hoặc sai mã bản dựng thì lượt chạy báo đỏ và nhánh giữ bản cũ. File zip của mỗi lượt cũng nằm ở mục **Artifacts** trên trang Actions, tải về để upload tay khi cần.

Trên Botkeep chọn nguồn GitHub, repo `traique/Stockvn`, nhánh **`botkeep`**, lệnh khởi động `main_botkeep.py`. Trước khi bỏ cách upload zip:

- Bí mật để ở mục Secrets của panel, không để trong file `.env` trên server, vì một lần deploy lại từ GitHub có thể tạo lại thư mục code.
- Sao lưu `data/` rồi thử một lần redeploy để chắc `data/` (lịch sử, tài sản, `data/altcoin/`) còn nguyên. Nếu bị xoá, dùng `DATABASE_URL` (và `ALT_DATABASE_URL`) Postgres để dữ liệu không nằm trên ổ server, hoặc quay lại upload zip.
- Repo riêng tư thì Botkeep cần được cấp quyền đọc repo trên GitHub.

Chạy tay (ví dụ khi Actions tắt): `python scripts/build_botkeep_bundle.py && scripts/publish_botkeep_branch.sh dist/botkeep`.

## 3. Tạo server trên Botkeep

1. Tạo workload **Python 3.12 trở lên** (Botkeep hiện chạy 3.13). Nếu gói cho phép chia tài nguyên, cấp khoảng 1,5 GB RAM.
2. Upload file zip rồi giải nén vào **thư mục gốc** của server (hoặc upload nội dung `dist/botkeep/`). Kiểm tra có đủ thư mục lồng như `vnbot/static` và `tradingagents/agents/analysts`.
3. File khởi động: `main_botkeep.py`. File requirements: `requirements.txt` (Botkeep cài lại mỗi lần upload).
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

# Tuỳ chọn: AI thẩm định tín hiệu (Gemini qua 9Router, cần thêm SIEUTINHIEU=1), xem mục "AI thẩm định tín hiệu"
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
| `/dinhgia FPT` · `/dinhgia` (gõ tắt `/dg`) | **Định giá bot tự tính** của một mã (vùng Rẻ/Hợp lý/Đắt, MOS, Bear/eab/Base/Bull, độ tin cậy, có gì đổi so với lần xem trước) · bảng các mã theo dõi xếp theo MOS. Xem mục **Định giá bot tự tính** |
| `/phanbien FPT [YYYY-MM-DD]` | AI phản biện quyết định của Quant Engine: **1 lượt gọi AI**, trả lời đồng ý/phản đối kèm lý do |
| `/phanbien` | AI phản biện đúng bao nhiêu (kết quả bóng của các lần phản biện) |
| `/theodoi FPT,HPG,VCB` | Đặt danh sách báo cáo tự động (tối đa 30 mã) |
| `/theodoi` | Xem danh sách đang theo dõi |
| `/trangthai` | Hàng đợi và trạng thái các nhà cung cấp AI (dòng đầu là phiên bản đang chạy) |
| `/phienban` | Phiên bản đang chạy, mã bản dựng (đúng bản phát hành hay không), thời gian chạy, có gì mới |
| `/danhmuc` | Danh mục cả ba tài khoản ảo (và tài khoản thật ở chế độ `signal`) |
| `/lenh` | Lệnh bot đặt cho phiên tới |
| `/hieuchinh` | Ngưỡng Quant Engine đang dùng và kết quả tự hiệu chỉnh 5 đêm gần nhất |
| `/vikhongmua` · `/vikhongmua FPT` · `/vikhongmua dunglai 120` | **Vì sao chưa có MUA**: thị trường (regime, ngày phân phối, lọc MA200/MA100 của Phòng thủ/Động lượng), cổng chặn MUA của từng mã, mã suýt MUA, kết quả bóng · lịch sử một mã · dựng lại 120 phiên đã qua (thêm `vn100` cho rổ VN100). Xem mục **Vì sao chưa có MUA & kho dữ liệu** |
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
| `/thamdinh` · `/thamdinh lichsu` · `/thamdinh tuan` | AI thẩm định tín hiệu: phiên gần nhất (mã chọn, mã nên tránh, ý kiến về danh mục) và thành tích · các phiên gần đây · tổng kết tuần (cần 9Router và `SIEUTINHIEU=1`) |
| `/thamdinh chay` (hoặc `ngay`) | Chạy ngay với dữ liệu mới nhất: trong phiên (09:15-15:10) là giá khớp hôm nay, sau 15:10 là tín hiệu đã chốt của hôm nay, trước giờ mở cửa hoặc ngày nghỉ là phiên gần nhất |
| `/ts` ... | Danh mục tiền thật của anh (Tài sản): xem mục **Tài sản** bên dưới |
| `/alt <lệnh>` (hoặc `/altcoin <lệnh>`) | Bot Altcoin chạy kèm (khi `ALTCOIN_ENABLED=1`): `/alt` xem lệnh, `/alt status`, `trades`, `signals`, `ai`, `learn`, `lessons`, `btc`, `phienban`, `pause`. `/alt close`, `apply`, `resume` phải gõ thêm `/alt <lệnh> xacnhan` trong 120 giây (cùng người, cùng cuộc trò chuyện). `/alt zoomtest` gửi thử một thông báo Altcoin và báo từng kênh nhận hay không. Lệnh Altcoin gõ trần (`/close`, `/status`...) không bao giờ chạy, chỉ nhận gợi ý `/alt`. Altcoin tắt thì `/alt` trả lời "Altcoin đang tắt" |
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
| `/tsdinhgia CTG 27904 30164 33309 [eab 26039] [han 30/10/2026] [nguon ...]` · `/tsdinhgia CTG xoa` · `/tsdinhgia` | **Thẻ định giá anh nhập, tuỳ chọn** (bot đã tự tính thẻ cho watchlist và mã đang giữ, `/dinhgia`, và tự báo vùng; thẻ anh nhập **thay** thẻ của bot cho mã đó. Bear Base Bull, mã nào cũng được, không cần đang giữ): giá theo đồng, `30164`, `30.164` hay `30,164` đều là 30.164 đ (dấu chấm/phẩy chỉ ngăn hàng nghìn; `30,1` bị từ chối), cần Bear < Base < Bull, eab < Base, hạn dùng không ở quá khứ, tối đa 30 thẻ. Bot báo trong phiên khi giá vào vùng Rẻ (≤ Base) hoặc Đắt (≥ Bull) theo thẻ này thay thẻ của bot; hết hạn hoặc qua ngày GDKHQ thì báo một lần rồi quay về thẻ của bot (`/tsdinhgia MÃ xoa` cũng vậy). Chỉ để tham khảo: không đổi cắt lỗ, mục tiêu hay quyết định của bot · xoá · xem các thẻ (giá, vùng, MOS, hạn) |
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
- Các mục thu gọn: Chi tiết danh mục, Thêm giao dịch (nút **Kiểm tra lệnh mua** = `/tskiemtra`, không ghi gì vào sổ), Nhật ký giao dịch (lọc theo loại và mã, có nút huỷ), Nạp/rút/cổ tức/điều chỉnh, Cắt lỗ & mục tiêu, Thẻ định giá anh nhập, Nhận xét & chiến lược, Báo cáo cuối ngày, Nhập file Excel, Ngưỡng cảnh báo tỷ trọng.
  - **Thẻ định giá anh nhập** (`/tsdinhgia`, tuỳ chọn: bot tự tính và tự báo, thẻ anh nhập thay thẻ của bot cho mã đó; có cả khi sổ trống): form gõ mã tự do để lưu hoặc xoá thẻ; bảng mỗi thẻ: giá, vùng hôm nay, MOS, chip hạn dùng / **HẾT HẠN** (vùng ghi «theo thẻ cũ») / **cần cập nhật** (đã qua một ngày GDKHQ sau ngày nhập: cổ tức tiền, cổ phiếu thưởng/cổ tức bằng cổ phiếu, quyền mua; ESOP, phát hành riêng lẻ, chuyển đổi trái phiếu không tính vì sở không điều chỉnh giá tham chiếu; khi đó không hiện vùng, MOS hay % lệch vì mốc theo giá cũ còn giá đã theo giá mới); thẻ của bot (mọi mã từ v1.17.0: Bear/Base/Bull, BCTC, Ke đặt tay, độ tin cậy, link `/dinhgia/MÃ`) đặt cạnh và % lệch hai Base, gắn cờ khi lệch quá 10% (thẻ của bot tính ngầm, tối đa 2 thẻ cùng lúc, không chặn trang: lần xem đầu ghi «đang tính»).
  - **Nhận xét & chiến lược**: tín hiệu Siêu tín hiệu của từng mã, nút Quant Engine và AI phản biện, kết quả cảnh báo bán và vùng định giá (cột *Đúng hướng*: giá thấp hơn sau 10 phiên, riêng 🟢 Rẻ là giá cao hơn).
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
- **Bot không đặt lệnh; không khuyên margin hay bình quân giá xuống; không trừ phí/thuế vào giá anh nhập.** Mọi ý kiến của AI vẫn chỉ là bóng để đo (xem *AI thẩm định tín hiệu*).

**Backtest các quy tắc bán** (23/10/2026, `docs/backtest_2026-10-23/thoat_tai_san.md`; 39 mã: 13 mã anh từng giao dịch và VN30, 582 lần MUA của trang): bán khi ⛔ **hoặc** 🔴 (cách bot đang báo) cho lãi trung bình như chỉ chờ trang BÁN (+1,1% so với +1,2% mỗi lệnh) nhưng số lệnh lỗ quá 10% giảm từ 31 xuống 5; trên 13 mã của anh đây là quy tắc tốt nhất (+0,6%/lệnh). Bán sớm theo 🟠 không tốt hơn chờ trang chốt (đúng 48% số lần), nên cảnh báo 🟠 khuyên theo dõi, chỉ bán sớm khi sát mức cắt lỗ. Giữ tới khi thủng cắt lỗ bắt được vài xu hướng rất lớn (VIC, VHM) nhưng thắng 23%, lỗ trung bình −7%, và thua trên 13 mã của anh.

**Excel:** xuất 4 sheet như app cũ (*Vị thế hiện tại*, *Lịch sử giao dịch*, *Tổng quan*) thêm sheet *Tiền mặt* (nạp, rút, cổ tức, điều chỉnh), nên file xuất ra nhập lại được đầy đủ. Nhập file của app cũ: dòng "Bán … giá 0" (cổ phiếu thưởng app cũ ghi nhầm) được sửa thành cổ phiếu thưởng; số lượng bị tách lẻ (CTS 3.947 + 1.053) được gộp lại theo lịch sử; "Vốn gốc" thành một lần nạp ngày đầu tiên; phần chênh tiền mặt thành một dòng "Lỗ trước khi theo dõi". Bản xem trước đối chiếu lãi/lỗ đã chốt, từng lệnh bán, số cổ phiếu, lãi/lỗ tổng và tiền mặt với file.

### Trang web

Giao diện Coral (v1.7.0, thiết kế lại theo Hallmark): giấy xám ấm, mỗi bản ghi là một tờ giấy cotton có cạnh sáng bên trái và bóng đổ nhẹ sang phải, nút như thẻ in nổi (nút chính màu mực), lựa chọn 2-4 mục là một rãnh có thẻ trượt. Màu coral chỉ dùng cho vòng focus, gạch chân link và con dấu "Đã chọn" trên phiếu của AI thẩm định tín hiệu; xanh lá/đỏ chỉ ở lãi/lỗ; trạng thái là khung viền mực theo màu của nó. Chữ Be Vietnam Pro (vẽ riêng cho tiếng Việt), số trong bảng bằng Geist Mono; số thiếu ghi "chưa có", không dùng gạch ngang. Giao diện tối được suy ra (không đảo màu): không bóng đổ, nền sáng dần theo tầng. Trên điện thoại các bảng của trang AI thẩm định tín hiệu thành từng khối có nhãn. Thanh bên trái liệt kê mọi trang; trên điện thoại thanh này gập thành thanh 48 px với nút menu mở trang chọn toàn màn hình (hoạt động cả khi tắt JavaScript). Có giao diện sáng và tối. Toàn bộ dùng được bằng bàn phím, đạt kiểm tra truy cập WCAG 2 A/AA (axe-core) ở mọi trang, cả hai giao diện, từ màn 390 px.

- **Thanh bên:** **Tổng quan** (`/`), **Danh mục** (`/taisan`); nhóm *Công cụ*: **Định giá** (`/dinhgia`), AI & cảnh báo, AI thẩm định tín hiệu, Bot giao dịch; nhóm *Hệ thống*: **Cài đặt** (`/caidat`), Cấu hình Zoom, Phiên bản. Dưới cùng: ngày (thứ, dương lịch, âm lịch), trạng thái thị trường, nút sáng/tối, số phiên bản (đỏ nếu code khác bản phát hành).
- **Tổng quan** (`/`): dải số đầu trang (lượt AI, thị trường, cảnh báo, rổ sàng lọc), phân tích một mã (AI phản biện, quyết định định lượng, phân tích chuyên sâu), lượt gần đây, ba tài khoản ảo, watchlist, nhà cung cấp AI.
- **Danh mục** (`/taisan`): danh mục tiền thật của anh, xem mục **Tài sản** bên trên.
- **Định giá** (`/dinhgia`, `/dinhgia/FPT`): thẻ định giá bot tự tính, xem mục **Định giá bot tự tính**.
- **Cài đặt** (`/caidat`): các tham số của bot (giờ chạy, sàng lọc và hạn mức AI, cảnh báo trong phiên, bot giao dịch ảo, Siêu tín hiệu, ngân sách rủi ro Tài sản, AI thẩm định tín hiệu, kết nối), mỗi nhóm một nút Lưu. Giá trị lưu vào `data/settings.json`, được ưu tiên hơn biến môi trường trên Botkeep và giữ qua các lần khởi động lại; mỗi mục ghi rõ giá trị đang lấy từ đâu (web, biến môi trường, mặc định) và có hiệu lực ngay hay sau khi khởi động lại. Khoá và token (Telegram, 9Router, Zoom, mật khẩu web) chỉ hiện "đã đặt / chưa đặt", không sửa được trên web: đặt trên Botkeep rồi khởi động lại.
- **AI thẩm định tín hiệu** (`/thamdinh`): xem mục **AI thẩm định tín hiệu**.
- **Bot giao dịch** (`/bot`): biểu đồ NAV, các danh mục ảo, **công tắc MUA breakout** của Quant Engine, ngưỡng và lịch sử hiệu chỉnh;
  nút **Chạy phiên ngay** như `/chay`.
- **Vì sao chưa MUA** (`/tinhieu`, trong Công cụ) và **Dữ liệu & nhật ký** (`/dulieu`, trong Hệ thống): xem mục **Vì sao chưa có MUA & kho dữ liệu**.

### Vì sao chưa có MUA & kho dữ liệu (v1.13.0, học từ bot OKX)

**Chẩn đoán ngày 28/10/2026 (dữ liệu thật tới phiên 27/10, code của bot):** chưa có MUA không phải lỗi, mà là ba lớp lọc cùng chặn:

- **Thị trường:** 250 phiên gần nhất (24/10/2025 - 27/10/2026) Quant Engine xếp VNINDEX vào **risk-off 213 phiên (85%)**, và
  phiên risk-off nào cũng có từ 4 ngày phân phối trở lên trong 25 phiên (116 phiên risk-off chỉ vì ngày phân phối). Phiên 27/10:
  9 ngày phân phối (ngưỡng 4). Risk-off chặn mọi lệnh MUA mới của engine và trừ 0,15 độ tin cậy.
- **Phòng thủ** (chiến lược mặc định của tín hiệu thật) chỉ đầu tư khi VNINDEX trên MA200, kiểm tra cuối tuần: VNINDEX dưới MA200
  từ 24/9 (27/10: 1.761,87 so với MA200 1.796,41, -1,9%) nên giữ tiền mặt. **Động lượng** dưới MA100 (1.783,30) cũng giữ tiền mặt.
- **Cổng của từng mã:** 20 phiên gần nhất trên VN100, 58% cặp (mã, phiên) đang xu hướng giảm; kể cả khi coi thị trường là trung
  tính, **không mã nào** đạt MUA: 405/600 cặp xu hướng tăng thiếu độ tin cậy 0,75, 159 cặp có R:R tới kháng cự gần nhất dưới 1,5
  (nhịp hồi trong xu hướng tăng thường nằm sát đỉnh cũ), 33 cặp phá đỉnh không còn kháng cự để đo R:R. Sàn cắt lỗ 3% (23/10) gần
  như không liên quan: bỏ nó, 250 phiên chỉ thêm 2 MUA.
- **Các cổng có đang làm mất tiền không?** Trên VN100, 250 phiên: engine ra **15 MUA** (trên 24.750 cặp), 10 phiên sau trung bình
  **-3,4%** (thua VNINDEX 3,5 điểm %); đi theo kế hoạch như sổ ảo: 14 lệnh khớp, **13 chạm cắt lỗ**, trung bình -0,90R.
  **22 MUA bị chặn chỉ vì thị trường** cũng lỗ: 10 phiên sau **-4,4%**, thắng 27%; theo kế hoạch 19/22 chạm cắt lỗ, -0,64R.
  Mẫu nhỏ, rổ VN100 hôm nay (thiên lệch sống sót), nhưng năm qua bộ lọc thị trường **không làm mất lệnh tốt**: nới cổng lúc này
  không có cơ sở; cùng chiều với backtest 23/10 (nới ngày phân phối, R:R 1,2, tin cậy 0,65/0,70 đều xấu hơn). Vì sao chính các lệnh
  MUA lỗ (so với 2023-2025, theo setup): `docs/chandoan_2026-10-28/README.md`, mục **Bổ sung**. Lệnh breakout lỗ ở cả hai giai
  đoạn, nên từ v1.14.0 có **công tắc MUA breakout** (trang `/bot`, xem mục Bot giao dịch) để thử tắt trên sổ ảo.

Vì vậy bot giờ **ghi lại và đo**, như bot OKX: mọi tín hiệu kể cả tín hiệu bị loại đều được theo dõi "bóng", và chỉ số đo mới được
dùng để quyết định đổi luật.

- **Kho dữ liệu** (`vnbot/store.py`): SQLite `data/bot.db` trên máy chủ (mặc định), hoặc **PostgreSQL dùng chung** khi đặt
  `DATABASE_URL` (Supabase/Neon free, chuỗi "Session pooler"): dữ liệu nằm ngoài máy chủ, không mất khi máy chủ bị xoá. Kiểm tra
  trước bằng `python check_db.py`. Không kết nối được thì bot tự dùng SQLite và báo trên trang `/dulieu`; kho lỗi không bao giờ làm
  dừng bot. Các file JSON (sổ ảo, Tài sản, cài đặt) vẫn là nguồn chính như trước. Bảng: `signals` (quyết định từng mã mỗi lượt quét),
  `scans` (phễu của mỗi lượt), `market` (VNINDEX mỗi phiên), `equity` và `fills` (NAV và lệnh khớp các sổ, không bị cắt ở 500 lệnh),
  `events` (nhật ký hoạt động), `kv`.
- **Cổng chặn MUA** (`policy.BUY_GATES`): mỗi quyết định ghi cổng đầu tiên mã trượt (xu hướng giảm, mâu thuẫn, chưa rõ xu hướng, độ tin
  cậy, thị trường risk-off, phiên rủi ro, dữ liệu cũ, không tính được stop, phá đỉnh chưa đo được R:R, R:R dưới ngưỡng, MUA breakout
  đang tắt) và mọi cổng nó trượt, cùng **quyết định nếu thị trường trung tính** (cùng code, chỉ nhấc cổng thị trường; mã chỉ vướng
  công tắc MUA breakout thì là quyết định khi công tắc bật). Chỉ để ghi: quyết định, lệnh và mức giá
  của bot không đổi (đã so 4.000 quyết định trên dữ liệu thật, cả mã đang giữ và chưa giữ: giống hệt bản cũ).
- **Suýt MUA:** chỉ vướng thị trường; hoặc chỉ vì MUA breakout đang tắt; hoặc độ tin cậy cách ngưỡng ≤ 0,05; R:R cách ngưỡng ≤ 0,3;
  hoặc phá đỉnh chưa đo được R:R.
- **Kết quả bóng** (`vnbot/chandoan.py`): mỗi tối, mã-phiên đủ phiên được chấm: giá 5/10/20 phiên sau tính từ giá mở cửa phiên kế
  tiếp, so với VNINDEX cùng khoảng; lệnh MUA (và MUA bị chặn vì thị trường) đi theo kế hoạch như sổ ảo: lệnh giới hạn tại cận trên
  vùng mua một phiên, bán được từ T+2, cắt lỗ (giá mở cửa dưới cắt lỗ thì bán ở giá mở cửa) hoặc mục tiêu, tối đa 30 phiên, tính bằng
  R sau phí (1R = từ cận trên vùng mua tới cắt lỗ, khoảng sổ ảo dùng để tính khối lượng). Thống kê theo nhóm (MUA thật, bị chặn vì thị trường, MUA breakout bị tắt, suýt MUA khác, theo cổng) kèm **t**: một nhóm chỉ nói lên điều gì
  khi có ≥ 30 mẫu và |t| ≥ 2 (bài học của bot OKX: học trên 13-20 mẫu là học nhiễu). **Bot không tự nới cổng nào.**
- **Dựng lại lịch sử:** trang `/dulieu` hoặc `/vikhongmua dunglai 120 [vn100]`: engine quyết định lại từng mã ở từng phiên đã qua
  (tối đa 250 phiên; giá điều chỉnh tới hôm nay, rổ mã hôm nay, không tin tức/dòng tiền), ghi với nguồn `replay` rồi chấm kết quả
  bóng, để có số đo ngay ngày đầu. VN30 120 phiên khoảng 1-3 phút.
- **Lượt sàng lọc 15:30** thêm một đoạn "🔎 Vì sao chưa có MUA": thị trường, cổng chặn nhiều nhất, mã suýt MUA.
- **Trang `/tinhieu`:** phễu của lượt mới nhất, thị trường 8 phiên, mã suýt MUA, kết quả bóng theo nhóm và theo cổng, quyết định
  từng mã, các lượt quét gần đây. **Trang `/dulieu`:** dung lượng, tải CSV (mở bằng Excel, đúng tiếng Việt) hoặc JSON từng bảng và
  bản sao lưu toàn bộ, dựng lại lịch sử, **nhật ký hoạt động** (lọc theo mức và chữ).
- **Che bí mật trong mọi dòng log** (console, kho, file tải về): token Telegram, khoá AI, mật khẩu web, `DATABASE_URL` và mật khẩu
  của nó, Bearer, `sk-...` - bot OKX từng lộ token qua file sao lưu log nên bản này che ngay từ dòng log đầu tiên.
- Biến: `DATABASE_URL` (bí mật, chỉ đặt trên Botkeep), `STORE` (1; 0 = tắt hẳn), `STORE_KEEP_DAYS` (730), `EVENTS_KEEP_DAYS` (14);
  ba biến sau sửa được ở `/caidat`, nhóm **Lưu trữ dữ liệu**.

**Những gì của bot OKX chưa đưa sang** (không hợp với bot ngày cho cổ phiếu VN hoặc cần đo trước): khoá leader cho hai máy chạy song
song (Render + Botkeep), tự điều chỉnh tham số (bot này đã có hiệu chỉnh đêm thận trọng hơn), AI rút bài học sau mỗi lệnh, đổi chặn
cứng thành giảm khối lượng (cần số đo kết quả bóng trước).

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
4. **AI thẩm định tín hiệu** (nếu có 9Router và `SIEUTINHIEU=1`), các ngày giao dịch lúc `AI_PICK_TIME` (mặc định **15:50**), gửi Telegram và Zoom. Xem mục **AI thẩm định tín hiệu**.

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
- **Công tắc MUA breakout** (`breakout_buy`, v1.14.0; mặc định bật = như cũ): trang `/bot`, thẻ **Công tắc Quant Engine**, nút **Tắt MUA
  breakout** / **Bật lại MUA breakout**. Tắt: Quant Engine không mở MUA mới ở mã breakout (giá đóng cửa vượt kênh Donchian 20 phiên),
  kể cả mã phá đỉnh khi `breakout_projected_ok=1`; mã đó thành THEO DÕI (đang giữ: GIỮ, không kèm kế hoạch mua thêm). Áp dụng cho
  mọi quyết định của engine: sổ ảo Quant Engine, sàng lọc 15:30, phân tích, tín hiệu thật nếu `SIGNAL_STRATEGY=engine`; từ lượt quyết
  định kế tiếp (lệnh mua đã đặt cho phiên tới không bị huỷ). Lý do: dựng lại 03/2023 - 10/2026 trên VN100, lệnh breakout theo kế
  hoạch 46 lệnh, 5 chốt lời, 38 cắt lỗ, -0,68R/lệnh; pullback hoà vốn (`docs/chandoan_2026-10-28/README.md`, mục Bổ sung). Các mã bị
  tắt vẫn được chấm kết quả bóng như thể đã mua theo kế hoạch: nhóm **MUA breakout bị tắt** ở `/tinhieu` và `/vikhongmua`, để so
  với MUA thật trước khi quyết định giữ hay bật lại (cần ≥ 30 mẫu và |t| ≥ 2). Công tắc lưu trong `quant_params.json`; hiệu chỉnh đêm
  không bao giờ đổi nó, và khi lưu một thay đổi chỉ ghi tham số của chính chiến lược đó. `/hieuchinh` hiện `breakout_buy 1→0` khi tắt.

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

### AI thẩm định tín hiệu (tuỳ chọn, Gemini có tìm web qua 9Router)

Câu hỏi: dùng AI đọc các lệnh MUA và danh mục thì có chọn tốt hơn, có giúp quyết định đúng lúc hơn không? Không ai biết trước, và **không backtest được**: AI đã đọc tin tức của quá khứ, hỏi nó về một tín hiệu năm 2025 là hỏi khi đã có đáp án. Vì vậy bot chỉ **ghi lại và đo từ hôm bật trở đi**. **Mọi ý kiến của AI không phải lệnh**: không đổi lệnh, sổ, nhật ký Tài sản hay cảnh báo nào; quy tắc ⛔/🔴 vẫn là quy tắc chính.

- **AI là gì:** model trên **9Router**, tức [UniRouter](https://github.com/traique/unirouter) của anh: Gemini web (đăng nhập bằng cookie trong trang quản trị UniRouter) **tự tìm tin trên web**, không tốn phí API.
- **Bật:** cần `ROUTER9_BASE_URL`, `ROUTER9_API_KEY` (chuỗi AI của bot đã dùng hai biến này) và `SIEUTINHIEU=1`, rồi khởi động lại. Trong UniRouter, nguồn *Gemini Web (cookie)* phải đang chạy; nếu key `sk-uni-…` của bot bị giới hạn nguồn thì cho phép nguồn này.
- **Hai model, tự chuyển cho nhau:** `AI_PICK_MODEL` (mặc định `gmw/gemini-pro`) chọn mã buổi chiều, 1-2 lượt mỗi ngày nên vừa hạn mức Pro của tài khoản free; `AI_PICK_MODEL_FAST` (mặc định `gmw/gemini-flash`) xem tin khi có cảnh báo và viết tổng kết tuần, những việc xảy ra nhiều lần hơn. Model đang dùng lỗi, hết hạn mức (429) hoặc quá thời gian thì bot hỏi ngay model kia trong cùng lượt; tin nhắn và trang web ghi rõ model nào đã trả lời và vì sao. Đổi cả hai ở trang `/caidat`, có hiệu lực ngay. Tên `gmw/…` là chế độ của web Gemini (Flash, Pro, Thinking), không gắn số phiên bản: Google tự đổi model phía sau. Model không phải Gemini web vẫn trả lời nhưng không tìm tin.
- **Tên cũ:** trước v1.9.0 tính năng mang tên model trả phí cũ. Khi khởi động, bot tự đổi file dữ liệu, giá trị đã lưu trên `/caidat` và biến môi trường theo tên cũ sang tên mới (`ai_pick.json`, `AI_PICK_*`), nên lịch sử đo không mất. Nên đổi tên biến trên Botkeep cho gọn.
- **Vì sao không dùng tools:** UniRouter chuyển mọi request có `tools` sang nguồn khác (Gemini web không gọi tool được), nên bot gửi câu hỏi dạng chữ kèm **JSON Schema** của câu trả lời và đọc **một khối JSON** Gemini trả về. Không phải JSON thì bot hỏi lại một lần; vẫn không được thì ghi lỗi và lần chạy sau hỏi lại.
- **Mỗi phiên lúc `AI_PICK_TIME`** (mặc định 15:50, sau khi trang chốt tín hiệu ~15:08), một lượt hỏi:
  1. **Ứng viên:** các tín hiệu MUA mới của phiên trong watchlist Siêu tín hiệu (60 mã, `/stvtheodoi`), mạnh nhất trước; rồi tới **các mã bot ước tính vừa đổi sang MUA** trong 300 mã thanh khoản cao nhất (cùng cách tính với trang, khớp ~88% ngày đổi chiều; tắt bằng `AI_PICK_BOT=0`), tối đa 25 mã.
  2. **Danh mục Tài sản:** từng mã đang giữ với khối lượng, giá vốn đúng như anh nhập (đã gồm phí, bot không trừ gì), giá đóng cửa, lãi/lỗ, tỷ trọng, cắt lỗ/mục tiêu, trạng thái của trang và **quy tắc đang báo gì** (⛔ thủng cắt lỗ hoặc 🔴 trang BÁN → BÁN; còn lại GIỮ).
  3. Kèm số liệu code tính từ giá đóng cửa (thay đổi 1/20/60 phiên, khoảng cách tới TB20/50/200, đỉnh 52 tuần, giá trị giao dịch, khối lượng) và VNINDEX. AI không tự đưa ra giá.
  4. Gemini tìm tin trên web, ưu tiên CafeF, Vietstock, VnExpress, Tin nhanh chứng khoán, VnEconomy, Báo Đầu tư, Người quan sát, HOSE, HNX, UBCKNN: phát hành thêm, cổ đông lớn mua bán, bị cảnh báo/kiểm soát, kết quả kinh doanh, pháp lý, ngày GDKHQ.
  5. Câu trả lời: **một mã chọn** (hoặc không chọn), **các mã nên tránh**, **xếp hạng mọi mã**, và **ý kiến GIỮ / CÂN NHẮC BÁN cho từng mã đang giữ**, kèm lý do và nguồn. Mã ngoài danh sách bị loại. **Nguồn** là link AI tự nêu: bot chỉ giữ link tới các trang tin trên (không xem được AI đã tìm gì), trang web ghi rõ điều đó.
  6. Bot nhắn Telegram và Zoom, ghi vào `data/ai_pick.json` (giữ tên file cũ để không mất lịch sử). Không có lệnh MUA mới nhưng có danh mục thì AI chỉ xem danh mục; không có cả hai thì không hỏi.
- **Khi có cảnh báo ⛔/🔴/🟠 cho mã đang giữ**: ngay sau cảnh báo của bot, AI tìm tin của mã đó, cho biết giá đi như vậy vì **tin riêng của mã, theo thị trường chung hay chưa rõ**, và ý kiến GIỮ / CÂN NHẮC BÁN. Tối đa `AI_PICK_ALERTS` lần mỗi ngày (mặc định 5; 0 = tắt). Cảnh báo của bot vẫn gửi ngay, không chờ AI.
- **Tổng kết tuần**, sau phiên cuối tuần: bot tự tính các dữ kiện (lệnh anh đã ghi, cảnh báo ⛔/🔴 và anh đã bán trong 2 phiên chưa, tài sản cuối tuần, thước kỷ luật), AI viết vài dòng nhận xét và một việc cho tuần tới. Tắt: `AI_PICK_WEEKLY=0`. Xem lại: `/thamdinh tuan`.
- **Chấm điểm, như nhau cho mọi ứng viên** (kể cả mã AI chọn và mã AI bảo tránh), chưa trừ phí và thuế (như nhau cho mọi lệnh nên không ảnh hưởng phép so sánh):
  - **Sau 10 phiên** (cách đo chính, có kết quả sau 2 tuần): mua ở giá mở cửa phiên sau, so với giá đóng cửa 10 phiên sau.
  - **Theo quy tắc ⛔/🔴** (chỉ lệnh của trang): bán ở giá mở cửa sau khi trang báo BÁN (đợi đủ T+2), khi giá chạm cắt lỗ của trang (từ T+2; mở cửa dưới cắt lỗ thì bán ở giá mở cửa), hoặc sau 60 phiên.
  - **Ý kiến về danh mục và về cảnh báo:** giá 5 và 10 phiên sau so với giá đóng cửa hôm đó; bot tách riêng những lần AI nói khác quy tắc.
- **So sánh:** chỉ các phiên có **từ 2 lệnh trở lên** (có lựa chọn thật) và mọi lệnh đã đủ 10 phiên. Lệnh AI chọn được so với **trung bình mọi lệnh cùng ngày** và với **lệnh mạnh nhất của trang**; **nửa trên bảng xếp hạng** của AI so với nửa dưới; lệnh **bảo tránh** so với phần còn lại.
- **Kết luận ở mốc định sẵn:** bot chỉ kết luận ở **20 phiên có lựa chọn (sơ bộ)** và **40 phiên**, mỗi mốc tính đúng trên chừng ấy phiên đầu tiên, kèm khoảng tin cậy 90%. Xem kết quả mỗi ngày rồi dừng khi thấy đẹp dễ tưởng nhầm may mắn là tài, nên kết luận ở một mốc không đổi theo các phiên sau. Với nhiễu thực tế của lệnh MUA (±5 điểm % sau 10 phiên), mốc 20 phiên chỉ thấy được khi AI hơn trung bình khoảng 2 điểm %/lệnh trở lên. Chỉ nên cân nhắc dùng tiền thật khi kết quả rõ ràng, và vẫn giữ mức rủi ro nhỏ cho mỗi lệnh.
- **Xem:** `/thamdinh` (phiên gần nhất, ý kiến danh mục, thành tích), `/thamdinh lichsu`, `/thamdinh tuan`, trang web **AI thẩm định tín hiệu** (`/thamdinh`, trong Công cụ): mở đầu bằng **phiếu chọn của phiên** (mã được chọn in lớn, đóng dấu "Đã chọn", giá tín hiệu, cắt lỗ, mục tiêu, lý do, mã nên tránh, model đã trả lời), bảng xếp hạng mọi ứng viên, danh mục, kết quả đo, các lần AI xem cảnh báo, tổng kết tuần, lịch sử và nút chạy ngay. Lệnh và đường dẫn vẫn là `/thamdinh` để thói quen cũ không đổi. Lệnh `/chonbong` và địa chỉ `/chonbong` cũ vẫn dùng được, tự chuyển sang `/thamdinh`.
- **Chạy tay** (nút **Chạy ngay với dữ liệu mới nhất** trên trang, hoặc `/thamdinh chay`): luôn hỏi lại, kể cả khi phiên đã có kết quả, và dùng dữ liệu mới nhất lúc bấm. Dưới nút ghi sẵn bấm lúc này sẽ dùng dữ liệu nào.
  - **Trong phiên (09:15 tới 15:10):** trang sieutinhieu.vn chưa có tín hiệu của hôm nay (chốt sau 15:10), nên ứng viên là các mã **bot ước tính** vừa qua mức đổi sang MUA theo **giá khớp hiện tại** trong 300 mã thanh khoản cao (như `/stvthitruong`), và danh mục tính theo giá khớp. Kết quả ghi là **tạm**: lượt tự động lúc `AI_PICK_TIME` hỏi lại với tín hiệu đã chốt và thay nó (lần hỏi trước vẫn được ghi lại).
  - **Sau 15:10:** tín hiệu đã chốt của hôm nay.
  - **Trước giờ mở cửa hoặc ngày nghỉ:** phiên gần nhất (hôm nay chưa có dữ liệu mới), và nói rõ như vậy.
  - Sau khi bấm, trang hiện ngay dòng "Chạy tay lúc …: phiên …, dữ liệu …", tự tải lại mỗi 10 giây trong lúc chạy, rồi báo kết quả (mã AI chọn) hoặc lỗi.
- **Hạn mức và lỗi:** mỗi lượt hỏi đi qua chuỗi nhà cung cấp của các agent cho riêng 9Router, nên tính vào hạn mức ngày (`LLM_MAX_CALLS_PER_DAY`, `ROUTER9_MAX_CALLS_PER_DAY`, mặc định 60 lượt/ngày cho mọi việc AI), và 9Router lỗi thì nghỉ `ROUTER9_RETRY_SEC` giây như với các agent. Một phiên thường là 1-2 lượt, mỗi lần cảnh báo 1-2 lượt, tổng kết tuần 1 lượt. 9Router không trả lời được thì bot thử lại sau 15, 30, 45 phút rồi bỏ phiên đó và báo một lần. Gemini web có thể mất 1-3 phút một lượt (bot chờ tối đa 5 phút).
- **An toàn:** khoá 9Router không bao giờ được in ra trong tin nhắn lỗi; nội dung trang web AI đọc được coi là dữ liệu, không phải chỉ dẫn; trang web chỉ hiện link http(s) tới các trang tin đã định.

### Cảnh báo trong phiên (không gọi AI)

Các tài khoản ảo vẫn khớp lệnh theo nến ngày sau 15:05 (đúng như kiểm định). Trong phiên, bot chỉ **báo** để bạn xem ngay thay vì đợi báo cáo buổi tối. Mỗi `INTRADAY_INTERVAL_MIN` phút (mặc định 15) trong giờ khớp lệnh liên tục (9:15–11:30, 13:00–14:30), bot đọc giá khớp gần nhất (DNSE) và gửi qua Telegram/Zoom:

- ⛔ mã đang giữ có giá ≤ stop, kèm số cổ phiếu bán được theo T+2 (cổ phiếu mua phiên T chỉ tính từ phiên chiều T+2); 🎯 chạm T1; 🏁 chạm T2; 🔒 giá sàn tính từ giá đóng cửa phiên trước (có thể không bán được). Áp dụng cho tài khoản thật (chế độ `signal`) và tài khoản ảo Quant Engine. Ngày GDKHQ: nếu nguồn đã điều chỉnh lịch sử, stop/T1/T2 được so theo giá đã điều chỉnh (⚖️); nếu chưa, giá thấp hơn giá sàn tính từ giá đóng cửa hôm trước được báo là có thể GDKHQ thay vì chạm stop;
- 🛒 lệnh mua giới hạn hôm nay của Quant Engine đang trong vùng mua, hoặc ⚠️ giá đã dưới stop dự kiến (nên bỏ lệnh);
- 📉 VNINDEX giảm `INTRADAY_VNI_DROP_PCT` % (mặc định 2) và gấp đôi mức đó so với phiên trước;
- 🟢 giá vào vùng **Rẻ** (≤ Base) hoặc 🔺 vào vùng **Đắt** (≥ Bull) của **thẻ định giá bot tự tính** (v1.17.0, mục **Định giá bot tự tính**) cho mọi mã watchlist và mã đang giữ trong Tài sản, **không cần nhập gì**; mã có thẻ anh nhập (`/tsdinhgia`, cả mã không giữ) thì báo theo **thẻ của anh** thay thẻ của bot. Tin ghi rõ "thẻ của bot" (kèm kỳ BCTC, phiên tính, độ tin cậy thấp nếu có, `/dinhgia MÃ`) hay "thẻ anh nhập". Mỗi lần **vào** vùng báo một lần (không theo ngày), tối đa một Rẻ và một Đắt mỗi thẻ mỗi phiên: vùng cuối của mỗi thẻ lưu ở `data/valuation_zones.json` (thẻ của bot dưới khoá `MÃ@bot`), giữ qua ngày và qua khởi động lại; thẻ mới nhập bắt đầu từ vùng lúc lưu, thẻ của bot tính lại với mốc khác (BCTC mới, cổ tức, số cổ phiếu) bắt đầu từ vùng lúc đóng cửa (giá đã ở Rẻ thì không báo «vào vùng Rẻ»); đã báo Rẻ thì giá phải lên quá Base 0,5% mới coi là rời vùng (Đắt: xuống dưới Bull 0,5%), nên giá dao động quanh Base không báo liên tục. **Thẻ của bot** chỉ dùng khi được tính sau phiên trước (việc 16:00, hoặc một lần xem hôm nay) và chưa tới ngày GDKHQ kế tiếp; việc 16:00 tính lại mỗi phiên (BCTC mới, qua GDKHQ), không hỏi VCI trong phiên (chỉ thẻ đã lưu và giá khớp). Tắt riêng phần này bằng công tắc **Vùng Rẻ/Đắt theo thẻ của bot** ở `/ai#kenh` (mặc định bật; thẻ anh nhập vẫn báo). **Thẻ anh nhập** hết hạn hoặc đã qua ngày GDKHQ từ ngày nhập (các mốc còn theo giá cũ) thì bot báo một lần rồi dùng lại thẻ của bot (không có thẻ của bot thì ngừng báo mã đó); ngày GDKHQ đã công bố được nhớ trước, nên sáng hôm đó VCI lỗi vẫn biết. Các dòng này gửi sau, thành tin riêng, để việc hỏi VCI (lịch GDKHQ của thẻ anh nhập, mỗi thẻ một lần mỗi ngày)/giá từng mã không làm chậm ⛔/🎯/🔒; VCI lỗi thì thẻ anh nhập đó hỏi lại sau một giờ và hôm đó chưa báo vùng tới khi đọc được lịch GDKHQ. Mỗi lần báo được ghi vào kết quả cảnh báo (🟢 Rẻ đúng khi giá 10 phiên sau cao hơn, 🔺 Đắt đúng khi thấp hơn) **tách theo thẻ của bot và thẻ anh nhập** để sau này so độ chính xác (mỗi loại giữ 500 dòng mới nhất riêng, không đẩy mất dòng ⛔/🔴), không gọi AI.

Mỗi cảnh báo khác gửi một lần mỗi ngày, kể cả khi khởi động lại (`data/intraday_alerts.json`). Giá đọc cách nhau vài phút nên có thể bỏ lỡ một cú xuyên stop rất ngắn. Bật/tắt bằng công tắc trên trang **AI & cảnh báo** (`INTRADAY_ALERTS` chỉ là giá trị mặc định). Bộ theo dõi này chạy ở mọi `TRADE_MODE` (kể cả `off`) vì lời nhắc 14:20 của việc treo Tài sản đi ra từ đây, không phụ thuộc công tắc.

### Định giá tham khảo (v1.16.0)

Chữ để **đọc**, không vào quyết định: không đổi hành động, cắt lỗ, mục tiêu, ngưỡng, cổng chặn của Quant Engine hay kết quả kiểm định/hiệu chỉnh (không module nào của engine, policy, simulator đọc nó). Ý tưởng lấy từ một hồ sơ định giá CTG (PDF); bot tự tính lại từ dữ liệu VCI, không đọc PDF.

- **Dòng bách phân vị** (báo cáo cơ bản của `/phantich`, mọi mã), ví dụ CTG 30/10/2026: *P/B phiên gần nhất có dữ liệu 1,20 (giá đóng cửa 29/10/2026: 30.700; vốn chủ sở hữu công ty mẹ tới Q2/2026, công bố 19/08/2026): rẻ hơn 74% số phiên từ 05/2018 (2.124 phiên; thấp nhất 0,83, cao nhất 2,22)*. Đọc là: 74% số phiên kể từ khi có BCTC quý có P/B **cao hơn** hôm nay; số càng cao, mã càng rẻ so với **chính lịch sử của nó** (không so với mã khác). P/B cho ngân hàng, chứng khoán, bảo hiểm, BĐS; P/E (LNST công ty mẹ 4 quý) cho mã khác. Mỗi phiên tính bằng giá chưa điều chỉnh × số cổ phiếu của chính phiên đó và sổ sách của quý đã công bố tới phiên đó, nên phân tích ngày cũ không dùng số công bố sau. *Phiên gần nhất có dữ liệu*: chưa có giá đóng cửa của ngày phân tích. Dưới 120 phiên: *chưa đủ dữ liệu*. Dòng P/E, P/B của VCI ghi rõ là theo giá lúc VCI cập nhật, không phải hôm nay.
- **Thẻ định giá ngân hàng** (cuối báo cáo Quant Engine của `/phantich`, dòng 📐 của `/nhanh`, một dòng trong báo cáo cơ bản):
  - **Base** = sổ sách/cp × P/B hợp lý, với **P/B = 1 + (ROE − Ke) / (1 + Ke − (1 − λ)(1 + g))**, λ 0,4, g 0; ROE = trung vị ROE 5 năm đã công bố. **Bear**/**Bull** theo ROE p20/p80 của chính mã (mọi năm từ 2018); **eab** = Base × (1 − 13,7%). Sổ sách/cp = vốn chủ sở hữu công ty mẹ của quý đã công bố, trừ cổ tức tiền có ngày GDKHQ sau ngày khoá sổ quý, chia số cổ phiếu lưu hành của phiên (qua ngày chia thưởng vẫn đúng).
  - Vùng: **Rẻ** khi giá ≤ Base, **Đắt** khi giá ≥ Bull, còn lại **Hợp lý**; MOS = (Base − giá)/Base.
  - **Ke** (chi phí vốn chủ sở hữu) mặc định **12%**, đổi bằng `VALUATION_KE_BANK` (%, 4-30). Base rất nhạy với Ke nên thẻ luôn in Base ở Ke ±2 điểm; hồ sơ PDF dùng 7,94%, cho Base CTG cao hơn khoảng 8,5%.
  - Mỗi dòng có **nhãn và ngày**: `[FACT dd/mm]` số trong BCTC và sự kiện, `[THỐNG KÊ dd/mm]` trung vị, phân vị, kết quả mô hình; **PHÁN ĐOÁN** chỉ dành cho chữ do AI viết (AI được dặn giữ nguyên nhãn khi trích). Đầu thẻ ghi giá phiên, kỳ BCTC, ngày công bố và **hạn dùng**: tới khi có BCTC quý mới hoặc tới ngày GDKHQ kế tiếp.
  - **Output Guard**: giá trị hợp lý, Base, Bear, Bull, eab mà AI nêu (cả trong báo cáo cơ bản) phải khớp thẻ ±0,5%, nếu không thì bị gắn cảnh báo *giá trị hợp lý không có trên thẻ định giá*. Mốc đã có trong dữ liệu (hỗ trợ, kháng cự, kế hoạch của Quant) đứng sau Bull/Bear là kịch bản, không bị bắt; mã không phải ngân hàng thì Bull/Bear là hai phe tranh luận; giá trị hợp lý VCI/CTCK nêu là quan điểm bên ngoài.
  - Ví dụ `/nhanh CTG` ngày 30/10/2026: `📐 Định giá tham khảo (không vào quyết định): Bear 26.236 · Base 27.643 · Bull 28.066 → Đắt: giá 30.700 (phiên 29/10) trên Base 11,1%. Ke 12% đặt tay (Ke 10%-14%: Base 28.749-26.619), BCTC Q2/2026`.
  - Báo cáo của `/phantich`, dòng 📐 của `/nhanh` và Output Guard: chỉ ngân hàng (ICB 8300 theo danh sách mã của VCI; ở đó Bull/Bear của mã khác là hai phe tranh luận). Từ v1.17.0 mọi mã có thẻ ở `/dinhgia` (mục dưới). Giá từ 2018 lấy một lần (lần đầu một mã khoảng 10-15 giây), sau đó mỗi ngày chỉ lấy thêm phiên mới (`data/cache/valuation/`; mã không dùng quá 30 ngày thì xoá, lần sau lấy lại). Phiên VCI chưa đăng lấy từ giá của engine, trừ ngày GDKHQ chia thưởng/cổ tức cổ phiếu/phát hành thêm (số cổ phiếu đổi đúng ngày đó): khi ấy dòng P/B và thẻ dùng phiên trước.
- **Thẻ anh nhập, tuỳ chọn** (`/tsdinhgia CTG 27904 30164 33309 [eab 26039] [han 30/10/2026] [nguon ...]` · `/tsdinhgia CTG xoa` · `/tsdinhgia`, cả trên trang `/taisan`; chi tiết ở bảng lệnh **Tài sản**): từ v1.17.0 bot tự tính thẻ và tự báo vùng (mục dưới), không cần nhập; thẻ anh nhập **thay** thẻ của bot cho mã đó. Mã nào cũng được, không cần đang giữ, bán hết vẫn còn. Trong phiên bot báo 🟢 khi giá **vào** vùng Rẻ, 🔺 khi vào vùng Đắt theo thẻ này, mỗi lần vào vùng một lần, vùng đệm 0,5% (xem **Cảnh báo trong phiên** ở trên); thẻ hết hạn hoặc đã qua ngày GDKHQ thì báo một lần rồi quay về thẻ của bot.

### Định giá bot tự tính (v1.17.0)

Bot **tự tính** thẻ định giá như hồ sơ PDF cho **mọi mã** và **tự báo** khi giá vào vùng Rẻ/Đắt, không cần nhập; thẻ anh nhập (`/tsdinhgia`) thành tuỳ chọn: nằm cạnh thẻ của bot (không bao giờ hiện như thẻ của bot) và thay thẻ của bot khi báo vùng cho mã đó. Chỉ để đọc: không đổi hành động, cắt lỗ, mục tiêu hay cổng chặn của Quant.

- **Cùng một mô hình** cho mọi mã: Base = sổ sách/cp × [1 + (ROE − Ke)/(1 + Ke − (1 − λ)(1 + g))], Bear/Bull theo ROE p20/p80 năm của chính mã, eab = Base × (1 − 13,7%), λ 0,4, g 0. Mã không phải ngân hàng: Bull không thấp hơn Base với Ke trừ 2 điểm (dòng độ nhạy), vì ROE tăng đều đặt p80 sát trung vị (FPT 30/10/2026: Bull chỉ cao hơn Base 0,17%) và vùng Hợp lý biến mất; thẻ ghi rõ khi Bull lấy theo cách này. Thẻ ngân hàng giữ cách tính v1.16.0. **Ke** ngân hàng 12% (`VALUATION_KE_BANK`), mã khác **13%** (`VALUATION_KE_DEFAULT`: thêm 1 điểm vì không có lợi thế tiền gửi và luật vốn giữ lợi nhuận, lợi nhuận chu kỳ hơn; giữa khoảng 12-15% các CTCK hay dùng, chưa kiểm chứng).
- **Hồ sơ** theo bản đồ ngành, mã ngoài bản đồ theo mã ICB của VCI: ngân hàng (8300), bảo hiểm (8500), chứng khoán (8700), bất động sản (8600), còn lại "doanh nghiệp".
- **Độ tin cậy** in trên mỗi thẻ: **cao** cho ngân hàng, bảo hiểm, chứng khoán (sổ sách là tài sản tài chính, là neo giá trị); **thấp** cho BĐS (quỹ đất, dự án ghi giá gốc, lợi nhuận theo đợt bàn giao); **trung bình** cho doanh nghiệp khác, xuống **thấp** khi là công ty công nghệ (ICB 9500), ROE bền vững ≥ 20% (lợi thế có thể bền hơn λ 0,4 giả định) hoặc vốn chủ tăng ≥ 20%/năm trong 3 năm (g 0 bỏ qua tăng trưởng); thẻ ghi rõ lý do.
- **Không lập thẻ** (và nói vì sao) khi vốn chủ sở hữu công ty mẹ ≤ 0, chưa đủ 3 năm ROE đã công bố, LNST công ty mẹ 4 quý ≤ 0 hoặc ROE bền vững ≤ 0.
- **`/dinhgia FPT`** (Telegram, Zoom gõ `dinhgia FPT` hoặc `/vnbot dinhgia FPT` trong kênh; gõ tắt `dg`): vùng và MOS, Bear/eab/Base/Bull, P/E và/hoặc P/B so với mọi phiên từ 2018, Base đứng trên gì, độ nhạy Ke ±2 điểm, độ tin cậy, kỳ BCTC và hạn dùng, **có gì đổi so với lần xem trước** (mốc đổi từ 0,3%, đổi vùng, BCTC mới, đổi giả định; "không đổi" nếu không có gì), thẻ anh nhập và Base lệch bao nhiêu (không tính lệch khi thẻ anh nhập đã qua ngày GDKHQ). Thẻ độ tin cậy thấp ghi "(tin cậy THẤP)" ngay cạnh vùng. **`/dinhgia`**: bảng các mã theo dõi xếp theo MOS (rẻ nhất so với Base trước, thẻ tin cậy thấp xếp sau), mã không lập thẻ kèm lý do. "⏳ Đang tính" chỉ gửi khi bot thật sự phải hỏi VCI. Tin dài tự chia theo giới hạn 3.900 ký tự của Telegram.
- **Trang web** `/dinhgia` (bảng như lệnh, ô gõ mã bất kỳ) và `/dinhgia/FPT` theo bố cục hồ sơ PDF: độ mới của dữ liệu, "30 giây" (MOS, P/B/P/E hôm nay so với lịch sử, eab, Bear), bảng vùng Rẻ/Hợp lý/Đắt, Base đứng trên gì (và ROE mà giá hôm nay ngầm định), trục Sổ sách – eab – Bear – Base – Bull, sổ sách và ROE từng năm, độ nhạy Ke, có gì đổi, chỉ số chính (ngân hàng: so với **trung vị mọi ngân hàng niêm yết cùng kỳ** và hạng ROE 5 năm), thẻ anh nhập, lời miễn trừ; số nào cũng kèm nhãn FACT/THỐNG KÊ và ngày. Mã chưa tính thì trang chờ tối đa 8 giây rồi báo "đang tính" và tự tải lại.
- **Sau 16:00 mỗi phiên** bot tự tính thẻ của watchlist, mã đang giữ trong Tài sản và mã có thẻ anh nhập (luồng riêng, 2 mã một lúc, qua cầu dao VCI; mã đã tính với giá phiên hôm nay, hoặc không lập thẻ khi tính sau 16:00, thì bỏ qua; mã lỗi hoặc chưa có giá hôm nay thử lại sau hơn 1 giờ, tối đa 3 lượt; VCI lỗi khi lấy giá lần đầu của một mã là lỗi, không phải "không lập thẻ"), rồi đọc chỉ số của mọi ngân hàng niêm yết một lần trong ngày nếu có ngân hàng trong danh sách (đọc lỗi hoặc VCI không trả ngân hàng nào thì không lưu và thử lại như mã lỗi; trang ngân hàng dùng lần đọc gần nhất). Log ghi tổng thời gian. Mã không trong danh sách này chỉ được tính khi có người xem.
- **Ngân hàng so với các ngân hàng** (đề xuất 3 của hồ sơ CTG): NIM, chi phí/thu nhập (CIR), nợ xấu (NPL), bao phủ nợ xấu, chi phí tín dụng (dự phòng/dư nợ), CASA, LDR của quý mới nhất đã công bố, kèm **cùng kỳ** năm trước, so với **trung vị các ngân hàng niêm yết (ICB 8300) đã công bố đúng quý đó** và **hạng N/M** theo chiều của chỉ số (CIR, NPL, chi phí tín dụng: thấp là tốt; LDR không xếp hạng); ngày 30/10/2026 có 15 ngân hàng đã ra Q3 và 12 ngân hàng còn Q2, nên CTG (Q2) so với 27 ngân hàng Q2, không trộn kỳ; số ngân hàng đã có kỳ mới hơn được ghi rõ. Thêm **ROE trung vị 5 năm** (chính là ROE bền vững của thẻ) và hạng trong mọi ngân hàng. Số VCI giữ nguyên định nghĩa: NIM và chi phí tín dụng là 4 quý gần nhất (nhiều báo cáo dùng quý quy năm), dấu âm VCI trả cho CIR, bao phủ, chi phí tín dụng được bỏ, số 0 (VCI chưa có số) không tính; CAR không hiện (VCI ghi 0 phần lớn các quý). Kiểm live 30/10/2026: CTG CIR 24,1% (trung vị 35,2%, hạng 3/27), NPL 1,20% (1,87%, 6/27), bao phủ 134,0% (66,8%, 3/27), ROE 5 năm 17,1% (14,9%, hạng 11/27) - số của chính CTG khớp hồ sơ PDF; trung vị là của bot, không khớp PDF tới 0,1 điểm. Việc 16:00 đọc mọi ngân hàng một lần mỗi ngày (khi watchlist, Tài sản hoặc thẻ anh nhập có một ngân hàng) và lưu kèm ngày công bố từng quý, từng năm (`data/cache/valuation/peers-8300-NGÀY.json`), nên một ngày trong quá khứ cũng được trả lời **đúng thời điểm** (chỉ quý và năm đã công bố tới ngày đó); trang, lệnh và AI không bao giờ tự hỏi VCI cho các ngân hàng khác. Có ở trang `/dinhgia/MÃ` (bảng: chỉ số · mã · trung vị cùng kỳ · hạng/ghi chú · nhãn và ngày), một dòng 🏦 trong `/dinhgia MÃ`, và hai dòng trong báo cáo cơ bản cho AI (`get_fundamentals`, chỉ để đọc).
- **Tự báo vùng trong phiên:** xem **Cảnh báo trong phiên** ở trên (thẻ của bot cho watchlist và mã đang giữ, thẻ anh nhập thay khi có, công tắc riêng ở `/ai#kenh`).
- **Báo cáo watchlist hằng ngày** (giờ `DAILY_REPORT_TIME`) thêm một khối 📐: các mã mà việc 16:00 gần nhất thấy **đổi vùng** so với lần tính trước (theo giá đóng cửa, kèm giá, Base, Bull), báo một lần (`data/valuation_zone_changes.json`).
- **Lưu** mỗi (ngày, mã) một dòng trong bảng `valuations` của kho dữ liệu (`data/bot.db` hoặc PostgreSQL; tải CSV/JSON ở `/dulieu`); kho tắt (`STORE=0`) thì lưu `data/valuation_cards.json`. "Lần xem trước" là dòng gần nhất của một ngày trước hôm nay.

### Bật/tắt kênh thông báo và đổi vốn tài khoản ảo trên web

- Trang **AI & cảnh báo** (`/ai`), thẻ **Kênh thông báo** (`/ai#kenh`): công tắc bật/tắt thật cho *Telegram · thông báo* (báo cáo, AI phản biện, cảnh báo; lệnh Telegram vẫn được trả lời khi tắt), *Zoom · thông báo*, *Zoom · nhận lệnh*, *Cảnh báo trong phiên*, *Vùng Rẻ/Đắt theo thẻ của bot* (trong cảnh báo trong phiên; mặc định bật, thẻ anh nhập vẫn báo khi tắt) và *Altcoin · thông báo* (tin của bot Altcoin, mở đầu “🪙 Altcoin ›”, tới cùng chat Telegram và Zoom như tin Cổ phiếu; Telegram/Zoom tắt thì tin Altcoin cũng không qua kênh đó). Lưu trong `data/channels.json`, giữ qua lần khởi động lại. Kênh chưa cấu hình hiện "chưa cấu hình".
- Trang **Bot giao dịch** (`/bot`), thẻ **Vốn tài khoản ảo**: nhập vốn mới (tối thiểu 10 triệu), chọn tài khoản (Phòng thủ, Quant Engine, Động lượng), đánh dấu ô xác nhận rồi bấm **Đổi vốn**. Tài khoản được chọn bắt đầu lại từ đầu với vốn mới từ lần chạy phiên tới (bấm "Chạy phiên ngay" để bắt đầu luôn); sổ cũ được giữ lại thành `data/trading/<file>.reset-<thời điểm>`. Vốn mới lưu trong `data/trading/paper_capital.json` và thay cho `PAPER_CAPITAL`. Không đổi được khi bot đang chạy phiên.

### Zoom Team Chat

Ngang hàng Telegram, làm y như bot OKX (`vnbot/zoom.py`, `vnbot/pages.py`):

- **Thông báo** (báo cáo hằng ngày, AI phản biện, cảnh báo trong phiên) gửi vào Zoom qua Incoming Webhook (`ZOOM_WEBHOOK_URL`, `ZOOM_WEBHOOK_TOKEN`; trong kênh gõ `/inc connect vnbot`) và/hoặc chatbot tới JID đã ghép đôi.
- **Gõ lệnh trong Zoom** giống Telegram: `nhanh FPT`, `phanbien HPG`, `phantich FPT`, `danhmuc`, `lenh`, `trangthai`, `trogiup`... Trong kênh: `/vnbot nhanh FPT`. Lệnh Altcoin cũng gõ ở đây: `alt status` (trong kênh: `/vnbot alt status`).
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

**Một bot Telegram, một app Zoom cho cả Cổ phiếu và Altcoin:** bot Altcoin (OKX) chạy kèm trên cùng server không có token Telegram hay app Zoom riêng. Lệnh của nó gõ `/alt <lệnh>` trên bot Telegram này và `alt <lệnh>` trong app Zoom này; thông báo của nó tới cùng các chat và cùng người ghép đôi Zoom, mở đầu “🪙 Altcoin ›”. Không cần app thứ hai, không mượn app nào.

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
| `VALUATION_KE_BANK` | 12 | Ke (%/năm, 4-30) của thẻ định giá ngân hàng; chỉ đổi số tham khảo, không đổi quyết định (mục **Định giá tham khảo**) |
| `VALUATION_KE_DEFAULT` | 13 | Ke (%/năm, 4-30) của thẻ định giá mọi mã không phải ngân hàng (mục **Định giá bot tự tính**) |
| `ZOOM_WEBHOOK_URL`, `ZOOM_WEBHOOK_TOKEN` | - | Gửi thông báo vào một kênh Zoom (Incoming Webhook) |
| `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_BOT_JID`, `ZOOM_ACCOUNT_ID` | - | Chatbot Zoom (thông báo tới JID đã ghép đôi, nhận lệnh) |
| `ZOOM_TO_JID` | - | Gửi thông báo (cả của Altcoin) tới JID này thay cho JID đã ghép đôi |
| `ZOOM_SECRET_TOKEN`, `ZOOM_VERIFICATION_TOKEN` | - | Nhận lệnh gõ trong Zoom tại `/webhook/zoom` (cả lệnh `alt …` của Altcoin); Verification Token là cơ chế cũ |
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
| `CACHE_MAX_AGE_DAYS` | 3 | Xoá cache giá cũ hơn số ngày này (giá từ 2018 cho định giá: 30 ngày không dùng) |
| `AI_PICK` | 1 | 0 = tắt **AI thẩm định tín hiệu** (cần 9Router và `SIEUTINHIEU=1`) |
| `AI_PICK_MODEL` | `gmw/gemini-pro` | Model chính (chọn mã buổi chiều); Gemini web (`gmw/…`) tự tìm tin trên web |
| `AI_PICK_MODEL_FAST` | `gmw/gemini-flash` | Model nhanh (cảnh báo, tổng kết tuần) và dự phòng khi model kia lỗi |
| `AI_PICK_TIME` | `15:50` | Giờ chọn mỗi ngày giao dịch (không sớm hơn 15:10) |
| `AI_PICK_BOT` | 1 | Thêm vào ứng viên các mã bot ước tính vừa đổi sang MUA (300 mã, cần `STV_MARKET=1`) |
| `AI_PICK_ALERTS` | 5 | Số lần tối đa mỗi ngày AI xem tin khi có cảnh báo ⛔/🔴/🟠 cho mã đang giữ (0 = tắt) |
| `AI_PICK_WEEKLY` | 1 | Tổng kết tuần sau phiên cuối tuần (0 = tắt) |
| `DATABASE_URL` | - | PostgreSQL dùng chung cho kho dữ liệu (Supabase "Session pooler"); trống = SQLite `data/bot.db`. Kiểm tra: `python check_db.py` |
| `STORE` | 1 | 0 = tắt kho dữ liệu (không ghi quyết định, kết quả bóng, nhật ký) |
| `STORE_KEEP_DAYS` | 730 | Số ngày giữ quyết định, lượt quét, VNINDEX, NAV |
| `EVENTS_KEEP_DAYS` | 14 | Số ngày giữ nhật ký hoạt động trên trang `/dulieu` |
| `ALTCOIN_ENABLED` | 0 | 1 = chạy bot Altcoin kèm theo (trang `/altcoin/`, lệnh `/alt`) |
| `ALTCOIN_FRESH_START` | - | 1 = cho Altcoin chạy lần đầu trên DB trống (chỉ paper); bỏ đi sau lần chạy đầu |
| `ALT_MODE`, `ALT_I_UNDERSTAND_THE_RISK`, `ALT_LIVE_ACK`, `ALT_OKX_API_KEY`, `ALT_OKX_API_SECRET`, `ALT_OKX_PASSPHRASE` | `paper` / - | Altcoin chạy thật chỉ khi `ALT_MODE=live`, `ALT_I_UNDERSTAND_THE_RISK=yes`, `ALT_LIVE_ACK` = tên instance và đủ 3 khoá OKX; thiếu một cái thì Altcoin không chạy |
| `ALT_DATABASE_URL` | - | Postgres riêng của Altcoin; trống = SQLite trong `ALT_DATA_DIR`. Không bao giờ là `DATABASE_URL` |
| `ALT_DATA_DIR`, `ALT_INSTANCE_NAME`, `ALT_PUBLIC_URL` | `data/altcoin` / `botkeep` / `<PUBLIC_URL>/altcoin` | Thư mục dữ liệu, tên instance, địa chỉ trang Altcoin |
| `ALT_OPENROUTER_API_KEY`, `ALT_NINEROUTER_*` | khoá của Cổ phiếu | AI riêng của Altcoin; trống thì dùng chung `OPENROUTER_API_KEY`, `ROUTER9_*` |
| `ALT_<TÊN>` | - | Mọi biến khác của Altcoin, ví dụ `ALT_LEVERAGE=5`. Không có `ALT_TELEGRAM_*`, `ALT_ZOOM_*` (bị bỏ qua). Đủ danh sách: `docs/ALTCOIN_TRONG_STOCKVN.md` mục 4 |

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
| Bot không đặt lệnh nhiều ngày | Bình thường khi VNINDEX risk-off hoặc dưới MA200; xem `/vikhongmua` (thị trường, cổng chặn, mã suýt MUA, kết quả bóng) hoặc trang `/tinhieu`; một mã: `/nhanh MÃ` |
| Altcoin không chạy, nhãn "Altcoin (tắt)" / "(chờ instance khác)" / "(lỗi)" | Xem lý do ở `/altcoin/hub.json` (cần mật khẩu) hoặc trả lời của `/alt status`; bảng xử lý ở `docs/ALTCOIN_TRONG_STOCKVN.md` mục 12. Tắt hẳn: `ALTCOIN_ENABLED=0` |
| Trang `/dulieu` báo dùng SQLite dù đã đặt `DATABASE_URL` | Chuỗi kết nối sai hoặc Supabase đang tạm dừng: chạy `python check_db.py`, lấy chuỗi "Session pooler" (không phải Direct connection), mã hoá URL ký tự đặc biệt của mật khẩu |

## 11. Phiên bản

Bot ghi số phiên bản ở một chỗ (`vnbot/version.py`) và hiện ở:

- **Web:** nhãn nhỏ `v1.1.0` cuối thanh bên ở mọi trang (chuyển đỏ kèm chữ “khác bản phát hành” nếu code khác bản phát hành), dòng cuối trang, trang **`/phienban`** (bản đang chạy, mã bản dựng, thời gian chạy, lịch sử phiên bản, cách cập nhật), và `/health`.
- **Telegram / Zoom:** lệnh **`/phienban`**, dòng đầu của `/trogiup` và `/trangthai`.
- **Tự báo khi cập nhật:** lần khởi động đầu tiên sau khi upload bản mới, bot nhắn vào Telegram/Zoom “🆕 Bot đã cập nhật: v1.0.0 → v1.1.0” kèm những gì mới; khởi động lại bình thường thì không nhắn.

**Mã bản dựng** (7 ký tự, ví dụ `1a2b3c4`) là dấu vân tay của toàn bộ code đang chạy. Cùng một bộ file cho cùng một mã trên mọi máy, nên bot biết server có chạy **đúng bản phát hành** không. Báo **khác bản phát hành** nghĩa là upload thiếu file, lẫn file của bản cũ, hoặc code đã bị sửa: hãy upload lại đủ gói.

Bot Altcoin có phiên bản riêng (`altcoin/bot/version.py`, hiện 3.2.1; mã bản dựng của nó tính `altcoin/bot/` và `altcoin/run.py`): xem ở `/altcoin/phienban` hoặc `/alt phienban`, đóng dấu bằng `cd altcoin && python -m bot.version --stamp`. File nối `altcoin/hosted.py` nằm trong mã bản dựng của Cổ phiếu (`/phienban`).

Số phiên bản theo dạng `vMAJOR.MINOR.PATCH` (bản đầu tiên `v1.0.0`, lịch sử trước đó đã bỏ). Khi tự sửa code và muốn đánh số bản mới: tăng `VERSION`, thêm dòng vào `NOTES` trong `vnbot/version.py`, rồi chạy `python -m vnbot.version --stamp` để ghi mã bản dựng mới.

## 12. Bộ kiểm thử (chạy trước khi cập nhật bot)

Thư mục `tests/` có hơn 1.000 kiểm tra **chạy offline**. Chúng không cần mạng, không gửi Telegram/Zoom và không đọc `.env` hay dữ liệu thật của bot. Mỗi lần sửa code hoặc nhận bản mới, chạy trên máy tính (Python 3.12+) trước khi upload:

```bash
pip install -r requirements.txt -r requirements-dev.txt
python tests/run_all.py          # tất cả, khoảng 15-30 giây
python tests/run_all.py stv      # chỉ các bộ có tên chứa "stv"
python tests/run_all.py -v web   # in chi tiết một bộ
pytest -q                        # hoặc chạy qua pytest
```

Kết quả `31/31 bộ đạt` là an toàn để cập nhật. Có ❌ thì đừng upload: dòng `FAIL ...` cho biết kiểm tra nào hỏng. Thiếu `httpx` thì các bộ web bị bỏ qua (⏭️) và bộ `version` bỏ phần web, không tính là lỗi.

| Bộ | Kiểm tra |
|---|---|
| `vn_rules` | Bước giá, giá trần/sàn (chính xác tới từng giá, không lỗi làm tròn), phiên, ngày lễ, T+2 theo lịch sàn |
| `ledger_fills` | Khớp lệnh sổ ảo: gap qua stop, trần/sàn trắng bên, T+2, nến lỗi, ngày GDKHQ, thanh khoản theo tiền |
| `factor` | Chiến lược Phòng thủ: lệnh khớp phiên sau, tỷ trọng đều, khối lượng theo NAV thị trường |
| `fixes_round1`, `fixes_round2`, `catchup` | Các lỗi đã sửa: sổ thật khi có cổ tức/thưởng, NAV theo giá thị trường, hiệu chỉnh tham số, nến trễ, cảnh báo VNINDEX, chạy bù phiên |
| `stv`, `stv_catchup`, `stv_morning` | Siêu tín hiệu: lấy tín hiệu lịch sự, sổ ảo so sánh, bản tin sáng, tài khoản thật riêng, gửi Zoom rồi Telegram |
| `version` | Số phiên bản trên web và Telegram/Zoom, mã bản dựng, báo khi cập nhật |
| `valuation`, `valuation_card` | Định giá tham khảo (dữ liệu VCI ghi ngày 29/10/2026): P/B, P/E so với mọi phiên từ 2018 theo giá chưa điều chỉnh và sổ sách đã công bố; thẻ định giá ngân hàng (Base của hồ sơ CTG tính lại khớp ±0,5%, sổ sách trừ cổ tức tiền sau ngày khoá sổ trên số cổ phiếu trước ngày GDKHQ (TPB: cổ tức tiền và cổ phiếu cùng ngày), qua ngày chia thưởng, phát hành riêng lẻ/ESOP/quyền mua, ROE năm theo ngày công bố, nhãn FACT/THỐNG KÊ, hạn dùng, thẻ ngày cũ giữ lại chỉ khi đã có phiên của ngày đó); Output Guard bắt giá trị hợp lý không có trên thẻ (kể cả có nhãn, trong bảng, trong báo cáo cơ bản, cả khoảng 28.000-29.000) nhưng không bắt nhầm stop/T1/hỗ trợ/kháng cự của Quant sau Bull/Bear, phe Bull/Bear của mã không phải ngân hàng hay giá trị hợp lý/giá mục tiêu VCI; tối ngày GDKHQ chia thưởng VCI chưa đăng thì không nối phiên mới với số cổ phiếu cũ; cầu dao VCI riêng, không chặn dòng tiền của Quant; không module nào của Quant Engine đọc thẻ |
| `valuation_manual` | Thẻ định giá anh nhập (`/tsdinhgia`, Telegram và Zoom chung bộ xử lý, trang `/taisan`): số kiểu VN (`30.164`, `30,164`, `30164`; từ chối `30,1`), Bear < Base < Bull, eab, hạn dùng, lỗi; thẻ còn sau khi bán hết, huỷ dòng, reset; đường giá 30,7 → 30,1 → 30,3 → 33,4 ra đúng 2 cảnh báo, vùng đệm 0,5%, một Rẻ mỗi phiên, thẻ nhập lại bắt đầu từ vùng lúc lưu, khởi động lại không báo trùng, hôm sau không báo lại; hết hạn và qua GDKHQ thì báo một lần rồi ngừng, không hiện vùng/MOS trên mốc giá cũ; sự kiện VCI ngày 29/10/2026: ESOP, riêng lẻ, chuyển đổi trái phiếu không là GDKHQ, CTG 44,6% nhớ trước ngày GDKHQ; VCI lỗi: ⛔ gửi trước khi hỏi thẻ, không hỏi lại trong một giờ, một lần thử 5 giây sau cầu dao VCI; dòng vùng định giá không đẩy mất dòng stop/bán; chiều chấm của 🟢 Rẻ/🔺 Đắt; form web cần mật khẩu, chặn POST chéo trang, thẻ của bot cạnh thẻ ngân hàng và cờ lệch quá 10%; thẻ của bot (v1.17.0): báo cho mã watchlist và mã đang giữ, ghi «thẻ của bot», không hỏi VCI; thẻ anh nhập thay thẻ của bot; thẻ anh nhập hết hạn thì thẻ của bot thay ngay; thẻ của bot chưa tính lại sau phiên trước, qua GDKHQ hôm nay, mã không lập thẻ hay không theo dõi thì không báo; tính lại cùng mốc không báo lại, mốc khác bắt đầu từ vùng lúc đóng cửa; công tắc `zones_bot` tắt thì chỉ thẻ anh nhập báo; kết quả cảnh báo tách theo nguồn thẻ, 500 dòng riêng mỗi nguồn; Quant Engine không đọc thẻ |
| `dinhgia` | Định giá bot tự tính (dữ liệu VCI ghi ngày 30/10/2026: FPT, HPG, VHM; mọi ngân hàng niêm yết): hồ sơ và Ke, độ tin cậy, không lập thẻ, lưu và «có gì đổi», `/dinhgia` trên Telegram/Zoom, trang `/dinhgia`, việc 16:00; ngân hàng so với trung vị **cùng kỳ** (kỳ trộn: Q3 so với các ngân hàng đã có Q3 tới ngày đó), hạng theo chiều chỉ số, dấu âm của VCI, số 0 là chưa có số, ngày cũ đúng thời điểm (18/08: Q1, 30/03: ROE 2020-2024), CTG CIR 24,1% hạng 3/27, ROE 5 năm 17,1% hạng 11/27, dòng 🏦 của `/dinhgia CTG`, bảng trên trang, báo cáo cơ bản cho AI; khối đổi vùng trong báo cáo watchlist (một lần) |
| `review_2026_10_23` | Các lỗi sửa ngày 23/10/2026 (xem `BAO_CAO_RA_SOAT_2026-10-23.md`): Output Guard, khối ngoại khớp lệnh, cache giá ngày nghỉ, chấm điểm bộ nhớ, hạn mức AI, Zoom |
| `store` | Kho dữ liệu (SQLite, migration, chọn dòng cho thống kê), cổng chặn MUA và quyết định khi nhấc cổng thị trường, kế hoạch bóng (T+2, khoảng trống giá, không khớp), kết quả 5/10/20 phiên, thống kê và t, che bí mật trong log, trang `/tinhieu` `/dulieu`, tải CSV/JSON, lệnh `/vikhongmua` |
| `breakout` | Công tắc MUA breakout: breakout thành THEO DÕI (đang giữ: GIỮ không kèm kế hoạch), pullback không đổi, kết quả bóng nhóm riêng, hiệu chỉnh đêm không đổi công tắc, nút trên trang `/bot` (mật khẩu, chặn POST chéo trang) |
| `bundle` | Gói Botkeep: chỉ file trong danh sách cho phép, không `.env`/`.db`/`data/`/`__pycache__`/`tests/` (thử với file mồi), giới hạn 250 file và 4 MB, zip tái lập được, mã bản dựng đúng bản phát hành, thay đổi chưa commit bị chặn |
| `altcoin_scripts` | Các script thử offline của altcoin (`altcoin/test_engine.py`: cả vùng găng – SIGTERM giữa đặt lệnh live và ghi DB, báo tin, dời SL sau TP1; tắt bot chờ `/alt close` đóng hết; `test_learner`, `test_risk`, `test_strategy`, `test_web`) trên bản chép tạm, không đọc `.env`/`bot.env`; `altcoin/check_db.py` chỉ đọc `.env`/`bot.env` cạnh nó (ưu tiên `ALT_DATABASE_URL`) và `ALT_DATABASE_URL` của biến môi trường, không bao giờ `DATABASE_URL` của stock. `test_smoke.py` cần OKX thật: chạy tay `cd altcoin && python test_smoke.py` |
| `altcoin_host` | Altcoin là tiến trình con của stock (`ALTCOIN_ENABLED=1`): env của nó chỉ từ danh sách cho phép, không bao giờ có token Telegram/Zoom, mật khẩu web hay `DATABASE_URL` của stock; khoá kênh, Render và đăng nhập riêng trong `altcoin/bot.env` bị xoá; chỉ chạy live khi đủ `ALT_MODE`, `ALT_I_UNDERSTAND_THE_RISK`, `ALT_LIVE_ACK`, `ALT_OKX_*` (thiếu thì không chạy, không lùi về paper); chặn DB của stock, DB rỗng (`ALTCOIN_FRESH_START` chỉ cho paper, `hosted.funded`), lệnh đang mở của chế độ khác và instance thứ hai (75, cả lúc đang chạy); khung trạng thái/thông báo qua pipe; khởi động lại, backoff, dừng khi stock dừng; che bí mật trong log `[altcoin]` |
| `altcoin_proxy` | Hub trước trang web (`/altcoin/`): đường dẫn lạ (`..`, `//`, `#`, `%2e`, `%5F`…) bị chặn 400 trước mọi bước; `/altcoin/_hub*` và các đường Zoom/công tắc kênh cũ của altcoin 404 (kể cả có mật khẩu); trang công khai và `/altcoin/zoom` chuyển sang trang của stock; cùng `WEB_PASSWORD`, cùng bộ đếm khoá khi sai mật khẩu và cùng kiểm tra chống gửi form từ trang khác như stock; `/altcoin/hub.json` cần mật khẩu, không có token; altcoin không nhận `Authorization` hay `X-Hub-*` của client; body ≤ 64 KB, ≤ 8 yêu cầu cùng lúc, viết lại `Location`; trang 503 khi altcoin chưa chạy, stock vẫn chạy |
| `altcoin_web` | Trang altcoin sau khi `hosted.py` thích nghi: mọi link nằm dưới `/altcoin/` trừ đúng 3 link sang stock (`/`, `/ai#kenh`, `/zoom`); `/altcoin/caidat` có thẻ “Kênh do Stock quản lý” thay hai thẻ kênh; altcoin chỉ nhận yêu cầu có `X-Hub-Token` (trừ `GET /health`); thanh chuyển Cổ phiếu / Altcoin ở cả hai phía, chỉ khi bật altcoin; `/_hub/command` chỉ với `X-Hub-Cmd-Token`, chỉ lệnh trong danh sách, lệnh ghi chỉ khi giữ quyền giao dịch và từng lệnh một (close quá giờ chờ rồi close lần hai: "đang có lệnh", không đóng hai lần) |
| `altcoin_bridge` | Lệnh `/alt` và thông báo Altcoin qua bot Telegram và app Zoom của stock: cú pháp `/alt`, `/altcoin`, Zoom `alt status`; lệnh lạ và lệnh Altcoin gõ trần không bao giờ tới altcoin; HELP, `/docs` và lệnh stock không đổi khi altcoin tắt; quyền như lệnh stock; `xacnhan` đúng người (Zoom: theo JID thật), đúng cuộc trò chuyện, trong 120 giây, một lần; câu trả lời khi altcoin tắt, đang khởi động, đang chờ, chưa giữ quyền, đang bận, quá giờ, lỗi; thông báo có tiền tố, mỗi chat một lần, lọc tin tiếp quản, chống trùng, tối đa 15 tin/phút (🛑/⚠️ luôn gửi), công tắc `altcoin`, đếm tin bỏ/lỗi, che bí mật; kênh bị treo không làm kẹt pipe; `/alt zoomtest` |
| `web`, `security_web`, `security_zoom`, `security_telegram` | Mật khẩu web, chặn POST chéo trang, giới hạn kích thước request, chữ ký Zoom, ghép đôi Zoom, chỉ người được phép mới ra lệnh Telegram, token không lộ trong log |

Một số kiểm tra so với **số đã ghi lại** (ví dụ kết quả chiến lược Phòng thủ trên dữ liệu giả lập). Sửa chiến lược **có chủ đích** thì kiểm tra đó báo đổi: xem lại số mới rồi cập nhật trong file bộ kiểm thử. Bot không bao giờ chạy bộ kiểm thử; script tạo gói Botkeep bỏ thư mục `tests/` ra (có upload kèm cũng không sao).

> Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.
