# Altcoin trong Stockvn – hướng dẫn vận hành

Bot Altcoin (OKX, thư mục `altcoin/`) chạy kèm bot Cổ phiếu trên **một** server Botkeep: một gói zip, một lệnh khởi động `python main_botkeep.py`. Tài liệu này dành cho người vận hành: cấu hình, lệnh, chuyển từ server altcoin cũ sang, quay lui và xử lý sự cố.

Bản phát hành: **Stockvn v1.17.0** và **Altcoin 3.2.1** (bản dựng `e737954`). Thiết kế chi tiết và lý do từng lựa chọn nằm trong [`KE_HOACH_GOP_ALTCOIN.md`](KE_HOACH_GOP_ALTCOIN.md).

> ⚠️ Future đòn bẩy rủi ro rất cao. Mặc định Altcoin **tắt** (`ALTCOIN_ENABLED=0`), và khi bật thì chạy **ảo (paper)**. Live chỉ chạy khi đủ mọi điều kiện ở mục 10.

---

## 1. Thay đổi gì so với hai bot riêng

| | Trước | Bây giờ |
|---|---|---|
| Server | Hai server Botkeep (Cổ phiếu, Altcoin) | **Một** server: Altcoin là tiến trình con của Cổ phiếu, tự khởi động sau Cổ phiếu và dừng cùng Cổ phiếu |
| Trang web | Hai tên miền, hai mật khẩu | Cổ phiếu ở `/`, Altcoin ở **`/altcoin/`**, **một** mật khẩu `WEB_PASSWORD`. Thanh "Cổ phiếu / Altcoin" ở đầu trang để chuyển qua lại |
| Telegram | Hai bot | **Một** bot: bot Cổ phiếu (`TELEGRAM_TOKEN`). Lệnh Altcoin gõ **`/alt <lệnh>`** |
| Zoom | Hai app (`vnbot`, `okxbot`) | **Một** app: `vnbot` của Cổ phiếu. Lệnh Altcoin gõ `alt <lệnh>` |
| Thông báo Altcoin | Tới chat riêng của bot OKX | Tới **cùng** các chat Telegram và Zoom của Cổ phiếu, mở đầu bằng **`🪙 Altcoin ›`** |
| Quyền ra lệnh Altcoin | Theo chat id của bot OKX | Như lệnh Cổ phiếu: id trong `TELEGRAM_ALLOWED_IDS`; Zoom: người đã ghép đôi hoặc trong `ZOOM_ALLOWED_USERS` |
| Dữ liệu | DB của bot OKX | Vẫn DB **riêng**: `ALT_DATABASE_URL` (Postgres), trống thì SQLite `data/altcoin/bot.db`. Không bao giờ dùng chung DB Cổ phiếu |
| Render | Có thể chạy song song | **Bỏ hẳn**. Chỉ một instance Altcoin, không failover |

Altcoin không cầm token Telegram hay khoá Zoom nào: mọi lệnh và thông báo đi qua tiến trình Cổ phiếu. Nhờ vậy không còn lỗi 409 "terminated by other getUpdates" giữa hai bot.

Mỗi lần upload hoặc khởi động lại server, Altcoin cũng khởi động lại (vài giây; tối đa khoảng 2 phút nếu phải chờ instance cũ). Thông báo Altcoin của những giây cuối trước khi tắt (lệnh vừa mở, vừa đóng) được gửi xong trước khi server tắt, chờ tối đa vài giây.

---

## 2. Lệnh `/alt`

Gõ `/alt` (hoặc `/alt help`) để xem bảng lệnh. Bí danh: `/altcoin <lệnh>`.

| Telegram | Zoom chat 1:1 | Trong kênh Zoom | Việc |
|---|---|---|---|
| `/alt status` | `alt status` | `/vnbot alt status` | Vốn, lệnh đang mở, lần quét gần nhất |
| `/alt trades` | `alt trades` | `/vnbot alt trades` | 10 lệnh gần nhất |
| `/alt signals` | `alt signals` | … | Tín hiệu và quyết định AI |
| `/alt ai` | `alt ai` | … | Lượt gọi từng model |
| `/alt learn` · `/alt lessons` | `alt learn` · `alt lessons` | … | Trạng thái tự học · bài học AI |
| `/alt btc` | `alt btc` | … | Bối cảnh BTC |
| `/alt phienban` | `alt phienban` | … | Phiên bản Altcoin (`/phienban` là phiên bản Cổ phiếu) |
| `/alt pause` | `alt pause` | … | Tạm dừng mở lệnh mới (lệnh đang mở vẫn được quản lý) |
| `/alt resume` | `alt resume` | … | Giao dịch lại, xoá trạng thái dừng của bộ ngắt rủi ro – **cần xác nhận** |
| `/alt apply` | `alt apply` | … | Áp dụng mọi đề xuất tự học đang chờ – **cần xác nhận** |
| `/alt close` | `alt close` | … | Đóng **mọi** lệnh đang mở (lệnh thật khi chạy live) – **cần xác nhận** |
| `/alt zoomtest` | `alt zoomtest` | … | Gửi thử một thông báo Altcoin qua các kênh, báo từng kênh nhận hay không |

Ghép đôi Zoom chỉ có một, dùng chung với Cổ phiếu: `/zoompair`, `/zoomxoa`, `/zoomstatus` (gõ `/alt zoompair` chỉ nhận lời nhắc này).

**Xác nhận hai bước** cho `close`, `apply`, `resume`: gõ lại lệnh kèm `xacnhan` (hoặc `xácnhận`) trong **120 giây**, cùng người, cùng cuộc trò chuyện. Mỗi yêu cầu chỉ dùng được một lần.

```
Bạn: /alt close
Bot: ⚠️ Lệnh này đóng mọi vị thế Altcoin đang mở (lệnh thật khi chạy live). Gõ /alt close xacnhan trong 120 giây để thực hiện.
Bạn: /alt close xacnhan
Bot: ⏳ Đang gửi lệnh tới Altcoin…
Bot: 🪙 Altcoin › close
     Đã đóng 2 lệnh.
```

Trên Zoom gõ `alt close`, rồi `alt close xacnhan`. Quá 120 giây, hoặc người khác gõ `xacnhan`, thì bot trả "Không có yêu cầu /alt close nào của bạn ở cuộc trò chuyện này đang chờ xác nhận…".

**An toàn:**

- Lệnh Altcoin gõ trần (`/close`, `/status`, `/pause`, `/resume`, `/apply`…) **không bao giờ** chạy. Bot chỉ nhắc: "Lệnh Altcoin giờ gõ /alt <lệnh>, ví dụ /alt close".
- Lệnh lạ sau `/alt` chỉ nhận bảng lệnh, không gửi tới Altcoin.
- `close` và `apply` chạy từng lệnh một. Gửi thêm khi lệnh trước chưa xong: "⏳ Đang có lệnh Altcoin chạy (lệnh trước chưa xong). Đừng gửi lại."
- Lệnh ghi không bao giờ tự gửi lại. Quá giờ chờ (`close` 60 giây, `apply` 30 giây, lệnh khác 15 giây) thì lệnh vẫn chạy tiếp trong Altcoin; kết quả từng lệnh đóng tới qua thông báo.
- Không lệnh nào đổi được paper/live (chỉ panel, mục 10).

**Quyền:** như mọi lệnh Cổ phiếu. Ai có trong `TELEGRAM_ALLOWED_IDS` (hoặc được phép trên Zoom) đều đọc được và đóng được lệnh Altcoin, vì không có danh sách quyền riêng. Hiện chỉ một người dùng nên không cần gì thêm; nếu sau này thêm người vào `TELEGRAM_ALLOWED_IDS`, người đó cũng chạy được `/alt close xacnhan`.

**Lệnh cũ → lệnh mới:**

| Bot OKX cũ | Bây giờ |
|---|---|
| Telegram `/status`, `/trades`, `/close`… trên bot OKX | `/alt status`, `/alt trades`, `/alt close` → `/alt close xacnhan`… trên bot Cổ phiếu |
| Zoom `/okxbot status` | `alt status` (1:1) hoặc `/vnbot alt status` (trong kênh) |
| `/phienban` (của Altcoin) | `/alt phienban` |
| `zoomtest` | `/alt zoomtest` |
| `zoompair`, `zoomxoa`, `zoomstatus` của bot OKX | `/zoompair`, `/zoomxoa`, `/zoomstatus` của Cổ phiếu |

Khi Altcoin bật, `/trogiup` của Cổ phiếu có thêm dòng `/alt <lệnh>` và trang `/docs` có thêm dòng `alt status`.

---

## 3. Thông báo Altcoin

- Đi tới đúng nơi thông báo Cổ phiếu đi: các chat `TELEGRAM_REPORT_CHAT_IDS` (trống thì `TELEGRAM_ALLOWED_IDS`), Zoom tới người đã ghép đôi (hoặc `ZOOM_TO_JID`) và Incoming Webhook nếu có.
- Mỗi tin mở đầu **`🪙 Altcoin › `**. Tin dài được chia phần, phần nào cũng có tiêu đề: `🪙 Altcoin › (2/3) …`.
- Công tắc trên trang `/ai`, thẻ **Kênh thông báo** (`/ai#kenh`):
  - **Altcoin · thông báo**: tắt thì không gửi gì của Altcoin; Cổ phiếu không đổi.
  - **Telegram · thông báo**, **Zoom · thông báo**: áp cho cả hai mục.
  - **Zoom · nhận lệnh**: áp cho cả lệnh `alt …`.
- Bộ lọc của cầu nối:
  - bỏ tin "👑 … tiếp quản" (chỉ có một instance);
  - tin "🚀 … khởi động" tối đa 1 tin / 30 phút;
  - cùng nội dung trong 120 giây chỉ gửi một lần;
  - tối đa 15 tin / phút và 5 tin / 20 giây, cách nhau ≥ 1 giây. Tin bắt đầu bằng 🛑 hoặc ⚠️ luôn được gửi. Phần bị bỏ: 2.000 ký tự đầu trong `/dulieu` của Cổ phiếu, toàn văn ở log Botkeep (dòng `[altcoin-notify] …`), kèm một tin "Bỏ qua N thông báo vì quá dày – xem /dulieu";
  - gợi ý lệnh trong tin được viết lại: "gõ /resume" thành "gõ /alt resume".
- Mọi tin và câu trả lời đều qua bước che bí mật (token, khoá, mật khẩu DB) của Cổ phiếu.
- Bộ đếm `notified`, `muted`, `dropped`, `deduped`, `send_failed` nằm trong `/altcoin/hub.json` (mục 5).

---

## 4. Biến môi trường

Đặt trên panel Botkeep của **server Cổ phiếu** (khung `.env`), cùng chỗ với các biến Cổ phiếu. Mẫu có ở cuối `.env.example`, mục `ALTCOIN`.

> **Đừng dán nguyên panel của altcoin cũ** (tên không tiền tố) vào panel Cổ phiếu: `TELEGRAM_TOKEN`, `DATABASE_URL`, `PUBLIC_URL`… của altcoin sẽ đè biến cùng tên của Cổ phiếu. Biến của Altcoin luôn mang tiền tố `ALT_`.

### 4.1 Công tắc của server (chỉ Cổ phiếu đọc)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `ALTCOIN_ENABLED` | `0` | `1` = chạy Altcoin. Khác `1` thì không có gì chạy: thanh ghi "Altcoin (tắt)", `/altcoin/` chỉ là trang "Altcoin đang tắt" của Cổ phiếu (cách bật, mục 5.1), mọi đường khác dưới `/altcoin/` là 404, `/alt` trả "Altcoin đang tắt" |
| `ALTCOIN_FRESH_START` | trống | `1` = cho chạy lần đầu trên DB **trống** (chỉ paper). Bỏ ngay sau lần chạy đầu. Không bao giờ đặt khi chuyển DB cũ sang |

### 4.2 Biến của Altcoin (`ALT_…`)

Quy tắc chung: `ALT_<TÊN>` trên panel thành `<TÊN>` cho Altcoin. Ví dụ `ALT_LEVERAGE=5` là `LEVERAGE=5`, `ALT_TIMEZONE`, `ALT_LOG_LEVEL`. Thông số giao dịch có mặc định tốt trong code: không cần khai.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `ALT_MODE` | `paper` | `paper` hoặc `live` (mục 10). Giá trị khác: Altcoin không chạy. `MODE` trần trên panel không có tác dụng |
| `ALT_I_UNDERSTAND_THE_RISK` | trống | Live cần `yes` |
| `ALT_LIVE_ACK` | trống | Live cần đúng **tên instance** (`ALT_INSTANCE_NAME`, hoặc `botkeep` nếu để trống) |
| `ALT_OKX_API_KEY`, `ALT_OKX_API_SECRET`, `ALT_OKX_PASSPHRASE` | trống | Khoá OKX, **chỉ** dùng khi live và chỉ lấy từ panel (khoá trong `altcoin/bot.env` bị bỏ). Paper thì luôn bị xoá |
| `ALT_OKX_DEMO` | trống | `1` = tài khoản demo của OKX (khi live) |
| `ALT_DATABASE_URL` | trống | Postgres riêng của Altcoin (Supabase "Session pooler"). Trống = SQLite `<ALT_DATA_DIR>/bot.db`. Không bao giờ lấy `DATABASE_URL` của Cổ phiếu |
| `ALT_DATA_DIR` | `<DATA_DIR>/altcoin` (thường là `data/altcoin`) | Thư mục dữ liệu riêng. Không được trùng `DATA_DIR` của Cổ phiếu |
| `ALT_INSTANCE_NAME` | `botkeep` | Tên instance (khoá leader, `ALT_LIVE_ACK`). Khi chuyển từ server cũ, dùng tên cũ: `crypto` |
| `ALT_PUBLIC_URL` | `<PUBLIC_URL>/altcoin` | Địa chỉ trang Altcoin, ít khi cần đặt |
| `ALT_LEADER_TTL` | 90 | Số giây một instance được coi là còn sống (khoá leader, chặn instance thứ hai) |
| `ALT_OPENROUTER_API_KEY` | trống | Khoá OpenRouter riêng của Altcoin |
| `ALT_NINEROUTER_BASE_URL`, `ALT_NINEROUTER_API_KEY`, `ALT_NINEROUTER_MODELS` | trống | 9Router riêng của Altcoin |
| `ALT_CUSTOM_*` | trống | Nhà cung cấp AI tự chọn của Altcoin (không dùng chung với Cổ phiếu) |

### 4.3 AI dùng chung khi thiếu khoá riêng

Thiếu khoá riêng (cả trên panel lẫn trong `altcoin/bot.env`) thì Altcoin dùng khoá của Cổ phiếu, log ghi "Altcoin dùng … của Cổ phiếu (chung hạn mức)":

| Altcoin cần | Lấy từ Cổ phiếu |
|---|---|
| `OPENROUTER_API_KEY` | `OPENROUTER_API_KEY` |
| `NINEROUTER_BASE_URL` | `ROUTER9_BASE_URL` |
| `NINEROUTER_API_KEY` | `ROUTER9_API_KEY` |

Hai bot khi đó chia nhau hạn mức (OpenRouter free khoảng 50 lượt/ngày). Nên đặt `ALT_OPENROUTER_API_KEY` riêng nếu có.

### 4.4 Khoá bị bỏ qua

Các khoá sau **không bao giờ** tới Altcoin. Chúng chỉ sinh một WARNING trong log và hiện tên (không có giá trị) trong `ignored_keys` của `/altcoin/hub.json`:

- trên panel: `ALT_TELEGRAM_*`, `ALT_ZOOM_*`, `ALT_PEER_*`, `ALT_DASHBOARD_*`, `ALT_SELF_URL`, `ALT_KEEPALIVE_INTERVAL`, `ALT_SUPPORT_EMAIL`;
- trong `altcoin/bot.env`: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, mọi `ZOOM_*`, `PEER_URL`, `SELF_URL`, `DASHBOARD_USER`, `DASHBOARD_PASS`;
- giá trị bị Botkeep che (`__BOTKEEP_KEEP_SECRET_…__`): bỏ, tên vào `dropped_secrets`. Phải nhập lại giá trị thật từ nguồn.

Biến của Cổ phiếu (`TELEGRAM_TOKEN`, `ZOOM_*`, `WEB_PASSWORD`, `DATABASE_URL`…) không bao giờ được chuyển cho Altcoin.

### 4.5 `altcoin/bot.env` (tuỳ chọn)

Altcoin còn đọc `altcoin/.env` và `altcoin/bot.env` trên server (tên biến không tiền tố, như bot cũ). Nên dùng panel; file này chỉ tiện khi muốn chép nguyên `bot.env` cũ. Thứ tự ưu tiên:

| Khoá | Giá trị thắng |
|---|---|
| Thư mục dữ liệu, URL, tên instance, cổng | Cổ phiếu đặt (`ALT_DATA_DIR`, `ALT_PUBLIC_URL`, `ALT_INSTANCE_NAME`); giá trị trong file bị bỏ, có WARNING |
| `MODE`, `I_UNDERSTAND_THE_RISK` | `ALT_MODE`, `ALT_I_UNDERSTAND_THE_RISK` trên panel; `MODE` khác trong file ghi ERROR |
| `OKX_*` | live: `ALT_OKX_*` trên panel; paper: bị xoá |
| `DATABASE_URL`, `OPENROUTER_API_KEY`, `NINEROUTER_API_KEY`, `CUSTOM_API_KEY` | Panel (`ALT_…`), nếu không có thì file |
| Khoá kênh, Render, đăng nhập riêng | Luôn rỗng (mục 4.4) |
| Thông số còn lại | File thắng panel (như bot cũ) |

`altcoin/botkeep.env` trong repo là hồ sơ **test gắt** (rủi ro 10%/lệnh, cầu dao 30%/50%): đừng chép các dòng rủi ro đó khi chạy live.

---

## 5. Trạng thái và xem ở đâu

### 5.1 Xem trang Altcoin

Mở **`<PUBLIC_URL>/altcoin/`** (ví dụ `https://vn.bot-keep.xyz/altcoin/`), hoặc bấm **Altcoin** trên thanh "Cổ phiếu | Altcoin" của mọi trang Cổ phiếu (thanh bên, dưới logo; trên điện thoại ở thanh trên cùng, trạng thái gọn một chữ: `(chờ)`, `(lỗi)`, `(tắt)`). Trình duyệt hỏi đăng nhập: tên gì cũng được, mật khẩu là `WEB_PASSWORD` của Cổ phiếu (đã đăng nhập Cổ phiếu thì không hỏi lại). Bấm **Cổ phiếu** để về `/`. Gói upload không có thư mục `altcoin/` thì không có thanh này và `/altcoin/` là 404.

Mở `/altcoin/` sẽ thấy, tuỳ trạng thái (chữ cạnh "Altcoin" trên thanh):

| Thanh ghi | `/altcoin/` là |
|---|---|
| `Altcoin` | Các trang của bot Altcoin (tổng quan, AI, cài đặt, phiên bản), mọi link nằm dưới `/altcoin/` trừ 3 link sang Cổ phiếu (`/`, `/ai#kenh`, `/zoom`) |
| `(đang khởi động)`, `(chờ instance khác)`, `(lỗi)` | Trang 503 "Altcoin đang khởi động" nêu lý do, 50 dòng log cuối, tự tải lại |
| `(tắt)`, `ALTCOIN_ENABLED=1` | Trang 503 "Altcoin đang tắt" nêu lý do bị từ chối (mục 12) và log cuối: sửa rồi khởi động lại |
| `(tắt)`, `ALTCOIN_ENABLED` khác `1` | Trang **"Altcoin đang tắt"** của Cổ phiếu (từ v1.17.0, cùng mật khẩu, cùng giao diện Cổ phiếu) với cách bật: (1) đặt `ALTCOIN_ENABLED=1` trên panel Botkeep; (2) chạy mới, ảo: thêm `ALTCOIN_FRESH_START=1` cho lần chạy đầu rồi xoá; đã có bot Altcoin cũ: chuyển DB cũ sang theo mục 9, không đặt `ALTCOIN_FRESH_START` (trang ghi đúng chỗ đặt `bot.db` cũ: `<ALT_DATA_DIR>/bot.db` của server, mặc định `data/altcoin/bot.db`); (3) khởi động lại server; (4) mở lại `/altcoin/` với cùng mật khẩu. Không có gì được chuyển tới Altcoin: `/altcoin/hub.json`, `/altcoin/_hub…` và mọi đường khác dưới `/altcoin/` là 404 |

### 5.2 Trạng thái

| Nơi xem | Cho thấy |
|---|---|
| Thanh "Cổ phiếu / Altcoin" trên mọi trang | `Altcoin` (đang chạy), `(đang khởi động)`, `(chờ instance khác)`, `(lỗi)`, `(lỗi kênh)` hoặc `(tắt)`; trên điện thoại gọn một chữ: `(chờ)`, `(lỗi)`, `(tắt)` |
| `/alt status` | Trả lời của Altcoin, hoặc vì sao Altcoin chưa trả lời |
| `/health` (công khai) | Chỉ thêm `"altcoin": {"state": …}`. Mã HTTP chỉ phụ thuộc Cổ phiếu |
| `/altcoin/hub.json` (cần mật khẩu) | Đầy đủ: `state`, `reason`, `pid`, `restarts`, `last_exit`, `version`, `build`, `leader`, `mode`, `live_error`, `seam_ok`, `bridge` (bộ đếm thông báo, lệnh cuối), `ignored_keys`, `dropped_secrets`, `rss_mb` (RAM của Cổ phiếu và Altcoin) |
| Trang `/altcoin/…` khi Altcoin đang chạy | Banner đỏ đầu trang khi `seam_ok=false` (thông báo không tới Cổ phiếu) hoặc `dropped_secrets` không rỗng |
| `/altcoin/` khi Altcoin chưa chạy | Đã bật: trang 503 "Altcoin đang khởi động" / "Altcoin đang tắt", kèm lý do và 50 dòng log cuối. `ALTCOIN_ENABLED` khác `1`: trang "Altcoin đang tắt" của Cổ phiếu, cách bật (mục 5.1) |
| Log Botkeep | Dòng `Altcoin: …` của Cổ phiếu (bật, khởi động, đang chạy, giữ quyền giao dịch, tắt – lý do, chờ), dòng `[altcoin] …` là log của Altcoin (đã che bí mật), `[altcoin-notify] …` là từng thông báo |
| `/dulieu` của Cổ phiếu | Sự kiện của Altcoin phía Cổ phiếu: lệnh `/alt` (ai, lệnh gì, kết quả), thông báo bị bỏ (2.000 ký tự đầu; toàn văn ở log Botkeep) |

| `state` | Nghĩa |
|---|---|
| `off` | `ALTCOIN_ENABLED` khác `1` |
| `starting` | Đang khởi động (chờ Cổ phiếu sẵn sàng, rồi chờ trang Altcoin trả lời, tối đa 120 giây) |
| `running` | Đang chạy |
| `waiting` | Mã thoát **75**: một instance Altcoin khác còn sống. Tự thử lại sau `LEADER_TTL + 10` giây (100 giây) |
| `disabled` | Mã thoát **78** hoặc kiểm tra trước khi chạy không đạt: cấu hình bị từ chối. **Không tự thử lại**: sửa rồi khởi động lại server |
| `backoff` | Altcoin thoát vì lỗi khác (hoặc bị kill, `-9`: thường là hết RAM). Tự chạy lại sau 20 giây, gấp đôi mỗi lần tới 10 phút |
| `stopped` | Đã dừng vì server đang tắt |

`seam_ok=false` (nhãn "(lỗi kênh)"): Altcoin chạy nhưng không gửi được thông báo qua Cổ phiếu (một chỗ nối với code altcoin bị gãy). Giao dịch vẫn chạy; báo lại để sửa.

---

## 6. Tạo gói zip và upload

Trên máy có Python 3.12 hoặc 3.13, trong thư mục repo đã commit sạch:

```bash
python tests/run_all.py                              # phải kết thúc bằng "tất cả đều đạt"
python scripts/build_botkeep_bundle.py --check       # chỉ kiểm tra
python scripts/build_botkeep_bundle.py               # tạo dist/stockvn-botkeep-v1.17.0-alt3.2.1.zip
```

Builder in hai dòng `Stock v1.17.0: bản dựng … = bản phát hành` và `Altcoin 3.2.1: bản dựng e737954 = bản phát hành`. Nó dừng khi một trong hai khác bản phát hành, tức đã sửa code mà quên đóng dấu: `python -m vnbot.version --stamp` (Cổ phiếu) hoặc `cd altcoin && python -m bot.version --stamp` (Altcoin).

Upload lên server Cổ phiếu:

1. Tải zip lên File Manager và giải nén vào **thư mục gốc**, ghi đè file cũ. **Giữ nguyên `data/`** (gồm `data/altcoin/`) và `altcoin/bot.env` nếu có.
2. Botkeep tự cài lại `requirements.txt` mỗi lần upload. File này có `ccxt==4.5.90` và `httpx` cho Altcoin.
3. Lệnh khởi động không đổi: `main_botkeep.py`. Python trên Botkeep là 3.13 (cần ≥ 3.12).

Gói không bao giờ chứa `.env`, `bot.env`, `data/`, file `.db`, `tests/`, `docs/` hay `altcoin/tools/`. Chi tiết ở README, mục 2.

**Cách khác: deploy từ GitHub.** Trỏ Botkeep vào nhánh **`botkeep`** (không phải `main`, vì `main` vượt 250 mục). GitHub Actions tự cập nhật nhánh này sau mỗi lần `main` đổi; file zip của mỗi lượt nằm ở mục Artifacts trên trang Actions. Trước khi chuyển: để bí mật trong Secrets của panel, sao lưu `data/` và thử một lần redeploy xem `data/` (gồm `data/altcoin/`) còn nguyên không (README, mục 2, "Deploy từ GitHub").

---

## 7. Giai đoạn 0: khảo sát và sao lưu (anh/chị làm)

Làm trước mọi việc khác, khi cả hai server cũ còn chạy.

**Sao lưu:**

- [ ] Tải về thư mục `data/` của server **Cổ phiếu** (`bot.db`, `channels.json`, `zoom_state.json`, `trading/`…). Nếu Cổ phiếu dùng `DATABASE_URL` (Supabase): tải bản sao lưu trên Supabase hoặc `pg_dump`.
- [ ] Altcoin cũ dùng **SQLite**: ghi nhớ đường dẫn `data/bot.db` trên server altcoin cũ (tải về ở bước chuyển, sau khi dừng server). Altcoin cũ dùng **Supabase**: lấy chuỗi "Session pooler" **từ Supabase** (Connect → Connection string), không từ panel; `pg_dump` nếu được.

**Kiểm kê altcoin cũ** (ghi **tên** biến, không chép giá trị bị che):

- [ ] Danh sách tên biến trên panel altcoin cũ và trong `bot.env` của nó. Với mỗi biến, quyết định:
  - chuyển thành `ALT_<TÊN>` trên panel Cổ phiếu (bảng mục 4.2): `DATABASE_URL`, `OKX_*`, `OPENROUTER_API_KEY`, `NINEROUTER_*`, `INSTANCE_NAME`, và các thông số đã chỉnh tay (nếu muốn giữ);
  - hoặc bỏ: `TELEGRAM_*`, `ZOOM_*`, `PEER_URL`, `SELF_URL`, `KEEPALIVE_INTERVAL`, `DASHBOARD_*`, `SUPPORT_EMAIL`, `PUBLIC_URL`, `PORT`, `RENDER_*`.
- [ ] Bí mật nào hiện dạng `__BOTKEEP_KEEP_SECRET_…__` thì **lấy lại từ nguồn**: OKX (trang API), Supabase (Connect), OpenRouter, 9Router. Giá trị bị che dán sang sẽ bị bỏ (`dropped_secrets`).
- [ ] Altcoin đang **paper** hay **live**? Có lệnh đang mở không (`/status` trên bot OKX cũ)? Live thì ghi lại vị thế trên OKX.
- [ ] Ghi lại để so sau khi chuyển: vốn, số lệnh, tỉ lệ thắng (`/status`), ngưỡng tự học (`/learn`), phiên bản.
- [ ] Còn service Render nào của altcoin không (service `okx-ai-bot`)? Có thì sẽ tắt ở bước chuyển.
- [ ] Altcoin cũ có kênh Zoom **riêng** không (`ZOOM_WEBHOOK_URL`/`ZOOM_WEBHOOK_TOKEN`, hay `ZOOM_TO_JID` riêng, trên panel hoặc trong `bot.env`)? Tin tới kênh / người nào? Sau khi chuyển, kênh đó không nhận tin Altcoin nữa (mục 13).

**Kiểm tra server Cổ phiếu:**

- [ ] `TELEGRAM_ALLOWED_IDS` có id của anh/chị; `/zoomstatus` cho thấy Zoom đã ghép đôi (app `vnbot` nhận lệnh, cần `ZOOM_SECRET_TOKEN`).
- [ ] Đo thời gian `/health` để so ở mục 9 (lấy p95 của 20 lần): `for i in $(seq 20); do curl -o /dev/null -s -w '%{time_total}\n' https://HOST/health; done | sort -n | sed -n 19p`.
- [ ] Ổ đĩa còn trống ≥ 300 MB (ccxt và thư viện đi kèm khoảng 100–120 MB, cộng dữ liệu Altcoin).
- [ ] DB của Cổ phiếu chưa có bảng của Altcoin (`trades`, `locks`, `learn_log`). Supervisor tự kiểm và không chạy nếu có. Liệt kê bảng một Postgres (chỉ đọc, chạy trên máy tính):

  ```bash
  python -c "import psycopg,sys;c=psycopg.connect(sys.argv[1]);print([r[0] for r in c.execute(\"select table_name from information_schema.tables where table_schema='public' order by 1\")])" "$URL"
  ```

---

## 8. Canary trên Botkeep (trước khi chuyển hẳn)

Mục đích: thử trên đúng server Botkeep những điểm không thử được trên máy tính (tiến trình con, múi giờ, cài ccxt, RAM), khi server altcoin cũ **vẫn chạy**. Canary chạy paper trên một DB tạm riêng, nên không đụng DB thật và không thấy instance cũ.

1. Upload gói mới (mục 6) với `ALTCOIN_ENABLED` chưa đặt. Kiểm tra: `/health` 200, `/phienban` báo đúng bản vừa upload (ví dụ v1.17.0) "đúng bản phát hành", `/alt status` trả "🪙 Altcoin đang tắt (ALTCOIN_ENABLED khác 1).", thanh ghi "Altcoin (tắt)" và `/altcoin/` là trang "Altcoin đang tắt" (mục 5.1).
2. Thêm trên panel rồi khởi động lại:

   ```env
   ALTCOIN_ENABLED=1
   ALTCOIN_FRESH_START=1
   ALT_DATA_DIR=data/altcoin-canary
   ALT_INSTANCE_NAME=canary
   ```

   Không đặt `ALT_MODE` (paper) và **không** đặt `ALT_DATABASE_URL` (SQLite tạm). Thông báo của canary tới chat của anh/chị với tiêu đề `🪙 Altcoin ›`; muốn im thì tắt **Altcoin · thông báo** trên `/ai#kenh`.
3. Kiểm tra trong 15–30 phút:
   - [ ] Log Botkeep có `Altcoin: bật – chế độ paper, instance canary`, `[altcoin] oom_score_adj=800`, `Altcoin: đang chạy`, `Altcoin: giữ quyền giao dịch – paper, bản 3.2.1 (e737954)`. Không có lỗi múi giờ hay "thiếu thư viện".
   - [ ] `/altcoin/hub.json`: `state` `running`, `seam_ok` `true`, `leader` `true`, `mode` `paper`, `build` `e737954`, `dropped_secrets` rỗng.
   - [ ] `/health` chỉ có `"altcoin": {"state": "running"}`.
   - [ ] `/altcoin/` mở bằng cùng mật khẩu; chuyển qua lại hai mục được; `/altcoin/phienban` báo 3.2.1 "đúng bản phát hành".
   - [ ] Telegram `/alt status`; Zoom `alt status` (1:1) và `/vnbot alt status` (trong kênh) đều được trả lời.
   - [ ] `/alt close` hỏi xác nhận; `/alt close xacnhan` trả "Đã đóng N lệnh."; `/close` trần chỉ nhận lời nhắc.
   - [ ] `/alt zoomtest` báo "Zoom: nhận" và "Telegram …: OK".
   - [ ] RAM: `rss_mb` trong `hub.json`; chạy thử `/phantich FPT` rồi xem lại: tổng Cổ phiếu + Altcoin dưới 1,2 GB.
   - [ ] Từ máy ngoài: `curl -i https://HOST/altcoin/_hub/command` → 404; `curl -i https://HOST/altcoin/%5Fhub/command` → 400.
4. Dọn canary: đặt lại `ALTCOIN_ENABLED=0`, xoá `ALTCOIN_FRESH_START`, `ALT_DATA_DIR`, `ALT_INSTANCE_NAME` khỏi panel, khởi động lại, xoá thư mục `data/altcoin-canary/` bằng File Manager. `/health` vẫn 200; bật lại **Altcoin · thông báo** nếu đã tắt.

Canary có lỗi thì dừng ở đây (Altcoin tắt, Cổ phiếu chạy như cũ) và sửa trước khi chuyển hẳn.

---

## 9. Chuyển hẳn (Giai đoạn 6) – từng bước (anh/chị làm)

Chọn lúc Altcoin cũ không có lệnh live đang mở nếu được (paper thì lúc nào cũng được).

1. **Render** (nếu còn service altcoin): tạm dừng ngay. Nó giữ token bot OKX cũ và có thể giữ khoá leader trên Supabase.
2. **Chuẩn bị panel Cổ phiếu** (chưa bật): thêm các `ALT_…` theo kiểm kê ở mục 7, giá trị nhập lại từ nguồn, và `ALT_INSTANCE_NAME=crypto`. **Không** đặt `ALTCOIN_FRESH_START`. Live thì xem mục 10 trước.
3. **Dừng server altcoin cũ** trên Botkeep (Stop). Dừng êm thì nó nhả khoá leader.
4. **Chuyển DB:**
   - **Supabase:** đặt `ALT_DATABASE_URL` = đúng chuỗi Session pooler cũ. Không cần chép gì.
   - **SQLite:** tải `data/bot.db` của server cũ (cùng `bot.db-journal` / `bot.db-wal` nếu có), rồi tải lên server Cổ phiếu thành **`data/altcoin/bot.db`** (tạo thư mục `data/altcoin` nếu chưa có). File Manager giới hạn 4 MB: file lớn hơn thì chạy `sqlite3 bot.db "VACUUM;"` trên máy tính trước, hoặc chuyển lên Supabase.
5. Đặt **`ALTCOIN_ENABLED=1`** rồi khởi động lại server Cổ phiếu.
   - Nếu `hub.json` báo `waiting` với "instance khác đang chạy: crypto@<host cũ>": heartbeat của server cũ còn mới (dưới 90 giây). Altcoin tự chạy sau khoảng 100 giây. Nếu vẫn chờ mãi: server cũ (hoặc Render) chưa thật sự dừng.
6. **Kiểm tra chấp nhận:**
   - [ ] `/altcoin/hub.json`: `state` `running`, `mode` đúng ý (`paper`), `seam_ok` `true`, `leader` `true`, `version` `3.2.1`, `build` `e737954`, `dropped_secrets` rỗng, `ignored_keys` chỉ gồm khoá anh/chị biết là thừa.
   - [ ] `/health` chỉ có `altcoin.state`.
   - [ ] `/altcoin/phienban`: 3.2.1, `e737954`, "đúng bản phát hành". Vốn, số lệnh, tỉ lệ thắng, ngưỡng tự học giống số đã ghi ở mục 7.
   - [ ] `/alt status` được trả lời trên Telegram và trên Zoom (`alt status`, `/vnbot alt status`); `/trangthai` của Cổ phiếu vẫn chạy.
   - [ ] Thông báo Altcoin đầu tiên ("🆕 Bot đã cập nhật: v3.2.0 → v3.2.1" hoặc "🚀 … khởi động") tới **đúng một lần** mỗi chat báo cáo và một lần trên Zoom, có tiêu đề `🪙 Altcoin ›`. `/alt zoomtest` báo Zoom nhận.
   - [ ] `/alt status` dòng "Instances": chỉ `crypto` còn sống. Dòng cũ (`render`, hoặc `crypto` của server cũ) hiện "(mất liên lạc)": chỉ là hiển thị.
   - [ ] Trong 1 giờ log Botkeep không có `409` hay `terminated by other getUpdates`.
   - [ ] Từ máy ngoài, có hay không có `-u x:<mật khẩu>`: `curl -i -X POST https://HOST/altcoin/_hub/command` → 404; `curl -i https://HOST/altcoin/%5Fhub/command` → 400; `curl -i https://HOST/_hub/command` → 404.
   - [ ] Cổng của Altcoin không mở ra ngoài: log có `Altcoin: khởi động pid …, cổng 127.0.0.1:<P>`; từ máy ngoài `curl -m 5 http://HOST:<P>/health` không kết nối được.
   - [ ] Thời gian `/health` (cùng lệnh đo ở mục 7) không tăng so với trước.
   - [ ] `rss_mb` trong `hub.json`: RAM tăng dưới 200 MB so với trước.
7. **Sau 1–2 tuần ổn định** (hết thời gian có thể quay lui):
   - [ ] Xoá server altcoin cũ trên Botkeep; xoá service Render (nếu có) và ngắt kết nối repo.
   - [ ] App Zoom `okxbot`: gỡ Bot endpoint / Event Subscription, hoặc deactivate. Không cần đăng ký URL `/altcoin/...` nào.
   - [ ] Tuỳ chọn: revoke token bot OKX cũ ở @BotFather.
   - [ ] Đẩy một commit README "đã chuyển sang traique/stockvn" lên repo `altcoin_bot` rồi **archive** repo (không xoá).

---

## 10. Chạy live

Altcoin 3.2.1 có **vùng găng**: khi server khởi động lại (mỗi lần upload), tín hiệu dừng rơi vào giữa lúc đặt lệnh trên OKX và lúc ghi DB sẽ được giữ lại tới khi ghi xong (cùng tin báo, và việc dời SL trên OKX sau chốt TP1). Lệnh `/alt close` đang chạy được chờ đóng hết các lệnh, tối đa 12 giây. Supervisor cho Altcoin 15 giây trước khi buộc dừng.

**Điều kiện (thiếu một cái là Altcoin không chạy, không bao giờ lùi về paper):**

```env
ALT_MODE=live
ALT_I_UNDERSTAND_THE_RISK=yes
ALT_LIVE_ACK=crypto            # đúng ALT_INSTANCE_NAME
ALT_OKX_API_KEY=...
ALT_OKX_API_SECRET=...
ALT_OKX_PASSPHRASE=...
```

Thiếu điều kiện thì `state` là `disabled`, lý do "ALT_MODE=live nhưng thiếu …: Altcoin không chạy (không chạy paper trên DB live)".

**Quy tắc:**

- Chạy paper ít nhất 2 tuần trên server mới trước khi bật live.
- Khoá API OKX chỉ cấp quyền giao dịch, không cấp quyền rút tiền.
- DB đang có lệnh **paper** mở thì live bị từ chối (78): đóng chúng ở paper trước (`/alt close` → `xacnhan`). Ngược lại, DB có lệnh **live** mở thì paper bị từ chối, để không đóng ảo vị thế thật.
- Không đổi chế độ bằng web hay chat được. `MODE` trên panel hoặc trong `altcoin/bot.env` bị bỏ qua.
- Khi live, `/alt close xacnhan` đóng **vị thế thật** trên OKX.
- Tránh upload hay khởi động lại server khi đang có lệnh `/alt close`. Lệnh SL/TP đã đặt sẵn trên OKX, nên vị thế vẫn được bảo vệ khi bot tạm dừng.
- Đừng dùng hồ sơ rủi ro của `altcoin/botkeep.env` (10%/lệnh) cho live.

---

## 11. Quay lui

**Tắt Altcoin** (mọi lúc, an toàn nhất): đặt `ALTCOIN_ENABLED=0`, khởi động lại. Cổ phiếu chạy như cũ; `/altcoin/` chỉ còn trang "Altcoin đang tắt" (cách bật lại), mọi đường khác dưới `/altcoin/` là 404; `/alt` trả "🪙 Altcoin đang tắt (ALTCOIN_ENABLED khác 1)."

**Quay về server altcoin cũ** (trong 1–2 tuần đầu):

1. Đặt `ALTCOIN_ENABLED=0` trên server Cổ phiếu và khởi động lại **trước**. Log có `Altcoin: tắt (ALTCOIN_ENABLED khác 1)`.
2. SQLite: tải `data/altcoin/bot.db` từ server Cổ phiếu về, tải lên server cũ thành `data/bot.db` (dữ liệu từ lúc chuyển nằm ở đây). Supabase: không cần làm gì.
3. Khởi động server cũ. Nó vẫn dùng token bot OKX và app `okxbot`, nên chỉ revoke / deactivate chúng sau thời gian ổn định.

**Quay về gói Cổ phiếu cũ** (v1.14.0): upload gói cũ như bình thường. `data/` vẫn dùng được (khoá `altcoin` thừa trong `channels.json` bị bỏ qua); `data/altcoin/` nằm yên.

---

## 12. Xử lý sự cố

| Triệu chứng (`hub.json` / log) | Nguyên nhân | Cách xử lý |
|---|---|---|
| `off`: log `Altcoin: tắt (ALTCOIN_ENABLED khác 1)`, thanh "Altcoin (tắt)", `/altcoin/` là trang "Altcoin đang tắt" (không có `hub.json`) | `ALTCOIN_ENABLED` khác `1` | Làm theo trang đó (mục 5.1): `ALTCOIN_ENABLED=1`, DB, khởi động lại |
| `disabled`: "thiếu thư viện – cài: pip install --no-cache-dir ccxt==4.5.90" | ccxt hoặc httpx chưa cài | Kiểm tra gói có `requirements.txt` và Botkeep đã cài lại sau upload; upload lại gói |
| `disabled`: "thiếu hosted.py (gói upload thiếu file)" | Gói thiếu file | Upload lại đủ gói zip |
| `disabled`: "ALT_DATA_DIR trùng DATA_DIR của Cổ phiếu" | `ALT_DATA_DIR` sai | Để trống (mặc định `data/altcoin`) |
| `disabled`: "DB đang dùng chung: DB của Cổ phiếu có bảng …" | Hai bot từng dùng chung một DB | Tách DB trước (DB riêng cho Altcoin), rồi mới bật |
| `disabled`: "ALT_MODE=live nhưng thiếu …" / "ALT_MODE=… không hợp lệ" | Điều kiện live thiếu | Mục 10, hoặc bỏ `ALT_MODE` để chạy paper |
| `disabled` (78): "DB rỗng – chép DB cũ hoặc đặt ALTCOIN_FRESH_START=1" | DB trống | Chép DB cũ (mục 9 bước 4). Chỉ đặt `ALTCOIN_FRESH_START=1` khi thật sự muốn bắt đầu mới (paper) |
| `disabled` (78): "DB rỗng, nhưng thư mục dữ liệu này đã chạy trên DB có dữ liệu…" | DB bị đổi lặng lẽ: `ALT_DATABASE_URL` bị che hoặc mất `bot.db` | Sửa `ALT_DATABASE_URL` / chép lại `bot.db`. Chỉ khi muốn bắt đầu mới: xoá `<ALT_DATA_DIR>/hosted.funded` (mặc định `data/altcoin/hosted.funded`) **và** đặt `ALTCOIN_FRESH_START=1` (chỉ paper), khởi động lại, rồi bỏ biến này |
| `disabled` (78): "DATABASE_URL của Altcoin là DB của Cổ phiếu…" / "DB của Altcoin có bảng market, fills của Cổ phiếu…" | Trỏ nhầm DB Cổ phiếu | Đặt `ALT_DATABASE_URL` riêng, hoặc để trống (SQLite) |
| `disabled` (78): "DB có lệnh live đang mở…" / "DB có lệnh paper đang mở…" | Chế độ khác với lệnh đang mở trong DB | Mục 10 |
| `disabled` (78): "múi giờ …: giờ của máy lệch và không có gói tzdata" | Server thiếu dữ liệu múi giờ | Kiểm tra `tzdata` trong `requirements.txt` đã cài |
| `waiting` (75): "instance khác đang chạy: <tên>@<host> – dừng server altcoin cũ" | Server altcoin cũ (hoặc Render) còn chạy trên cùng DB | Dừng nó. Altcoin tự thử lại mỗi 100 giây |
| `waiting` (75): "child khác trên cùng host còn chạy (hosted.lock)" | Tiến trình Altcoin cũ chưa tắt hẳn khi khởi động lại | Thường tự hết sau một lượt; kéo dài thì khởi động lại server |
| `backoff`: "thoát mã N" | Altcoin lỗi | Xem dòng `[altcoin] …` trong log Botkeep, hoặc 50 dòng cuối trên trang `/altcoin/` |
| `backoff`: "bị kill (OOM?)" | Hết RAM: kernel giết Altcoin trước Cổ phiếu | Xem `rss_mb`; tránh `/phantich` dồn dập; tạm `ALTCOIN_ENABLED=0` nếu lặp lại |
| `starting` lâu, rồi khởi động lại | Trang Altcoin không trả lời trong 120 giây | Xem log `[altcoin]` |
| Nhãn "(lỗi kênh)", banner trên `/altcoin/`, `seam_ok` `false` | Thông báo Altcoin không đi qua được Cổ phiếu | Giao dịch vẫn chạy; báo lại để sửa |
| `/alt …` trả "Altcoin vừa khởi động, chưa nhận quyền giao dịch" | Altcoin chưa giữ khoá leader (sau khởi động) | Thử lại sau khoảng 1 phút |
| Không có thông báo Altcoin | Công tắc tắt, hoặc kênh tắt | `/ai#kenh`: **Altcoin · thông báo**, **Telegram · thông báo**, **Zoom · thông báo**; `bridge.muted` / `send_failed` trong `hub.json`; `/alt zoomtest` |
| `ignored_keys` có khoá lạ | Khoá cũ còn trên panel hoặc trong `altcoin/bot.env` | Xoá khoá đó (mục 4.4); vô hại nếu để |
| `dropped_secrets` không rỗng (banner trên `/altcoin/`) | Giá trị bị che được chép sang | Nhập lại giá trị thật từ nguồn, khởi động lại |

Mã thoát của Altcoin: **78** = cấu hình bị từ chối (dừng, chờ sửa), **75** = có instance khác (tự thử lại), **-9** = bị kill, mã khác = lỗi (tự chạy lại có giãn cách).

---

## 13. Giới hạn đã chấp nhận

- Không có chat Telegram riêng cho Altcoin: thông báo đi theo chat của Cổ phiếu, phân biệt bằng tiêu đề `🪙 Altcoin ›`.
- Không còn công tắc "trả lời lệnh Telegram" riêng của Altcoin; muốn chặn hẳn lệnh Altcoin thì `ALTCOIN_ENABLED=0`. Công tắc kênh cũ trong DB altcoin không còn tác dụng, và trang `/altcoin/caidat` thay hai thẻ kênh bằng thẻ "Kênh do Stock quản lý".
- Công tắc Telegram/Zoom áp chung cho cả hai mục; công tắc Altcoin chỉ bật/tắt cả mục.
- Kênh Zoom riêng của Altcoin (Incoming Webhook `ZOOM_WEBHOOK_URL`, hay `ZOOM_TO_JID` riêng) bỏ: tin Altcoin đi theo Zoom của Cổ phiếu (người đã ghép đôi, `ZOOM_TO_JID`, Incoming Webhook của Cổ phiếu).
- Không còn failover: Altcoin dừng vài giây mỗi lần upload hay khởi động lại server.
- Nhật ký `events` của Altcoin chỉ giữ 200 ký tự đầu mỗi thông báo; tin bị cầu nối bỏ có 2.000 ký tự đầu ở `/dulieu` của Cổ phiếu, toàn văn ở log Botkeep (dòng `[altcoin-notify] …`).
- Chế độ trong Stockvn không chạy `altcoin/run.py`: không tự cài thư viện (Botkeep đã cài theo `requirements.txt`), không dọn pip cache và `__pycache__`, không log dung lượng đĩa.
- Hộp "Cập nhật bot" trên `/altcoin/phienban` viết cho bot chạy độc lập. Trong Stockvn: cập nhật bằng gói zip chung (mục 6), lệnh là `/alt phienban`.
