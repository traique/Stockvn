# OKX AI Altcoin Futures Bot

> **Trong Stockvn:** bot này nằm ở thư mục `altcoin/` của repo `traique/stockvn` và chạy trên cùng server Botkeep với bot cổ phiếu (một gói zip, `python main_botkeep.py`); web ở `/altcoin/`, cùng mật khẩu `WEB_PASSWORD` của stock.
> Lệnh gõ qua bot Telegram và app Zoom của stock: `/alt <lệnh>` (hoặc `/altcoin <lệnh>`); thông báo về cùng các chat của stock, có tiêu đề "🪙 Altcoin ›". Không dùng token Telegram hay app Zoom riêng.
> Không còn Render: chỉ một server Botkeep, không chạy song song hay failover (xem mục 3b).
> Cấu hình và vận hành trong Stockvn: [`docs/ALTCOIN_TRONG_STOCKVN.md`](../docs/ALTCOIN_TRONG_STOCKVN.md) ở gốc repo (thiết kế: [`docs/KE_HOACH_GOP_ALTCOIN.md`](../docs/KE_HOACH_GOP_ALTCOIN.md)).
> `bot/` và `run.py` dùng chung cho cả hai cách chạy (mã bản dựng ở `bot/version.py`). Phần dưới là hướng dẫn gốc cho chế độ chạy độc lập (`python run.py`).

Bot giao dịch hợp đồng tương lai (USDT-SWAP) altcoin trên OKX, đánh cả Long lẫn Short. Bộ lọc kỹ thuật khắt khe tự tìm tín hiệu; AI (qua **9Router** hoặc **OpenRouter**, model free) chỉ được gọi **2 lần cho mỗi lệnh**: 1 lần xác nhận trước khi vào, 1 lần tổng kết sau khi đóng. Có chế độ **chạy ảo (paper)**, Telegram, dashboard web. Chạy được trên **Botkeep.cloud** với runtime Python (không cần Docker).

> ⚠️ Future đòn bẩy với vốn nhỏ rủi ro rất cao, có thể mất toàn bộ vốn. Đây là công cụ, không phải lời khuyên đầu tư. Chỉ bật `live` khi đạt đủ tiêu chí ở mục 5.

> **Tình trạng 3.3.0 (02/11/2026).** Chẩn đoán sau đợt lỗ 30/10–02/11: sổ sách và cỡ lệnh đúng, lỗ chủ yếu do một đoạn thị trường xấu; nhưng backtest 88 ngày (50 coin, 706 lệnh, code của bot, phí 0,20%/vòng) cho **−0,01R ± 0,07 mỗi lệnh – chiến lược gần như không có lợi thế sau phí**, và 1.335 tổ hợp tham số không tổ hợp nào dương ngoài mẫu một cách chắc chắn (xếp hạng trong mẫu / ngoài mẫu tương quan ngược −0,56). Bản 3.3 chỉ **sửa lỗi** để số liệu chạy ảo và tín hiệu bóng phản ánh đúng thực tế; **không** chỉnh tham số chiến lược theo dữ liệu gần đây. Việc tiếp theo: khởi động 3.3.0 lần đầu với `LEARN_MODE` trống, rồi chạy ảo 4 tuần với `LEARN_MODE=suggest` (trong Stockvn: `ALT_LEARN_MODE=suggest` trên panel), rồi quyết định theo tiêu chí ở mục 5.

## 1. Cách bot ra quyết định

**Bước 1 – Quét**: mỗi 5 phút lấy top 50 (`TOP_N_COINS`) cặp USDT-SWAP **crypto** có volume 24h lớn nhất trên OKX. v3.3: hợp đồng cổ phiếu / hàng hoá bị loại theo loại tài sản OKX ghi cho từng hợp đồng (`instCategory`: 1 = crypto, 3 = cổ phiếu, 4 = hàng hoá) – bản cũ chỉ lọc theo tên nên 9/50 mã là cổ phiếu, dầu (SKHY, SOXS, CL, BZ…); BTC, ETH, stablecoin vẫn loại theo tên (`EXCLUDE`, lớp lọc thêm). Đang đủ `MAX_OPEN_POSITIONS` lệnh mở bot vẫn quét và ghi tín hiệu bóng (nguồn `max_positions`, không hỏi AI, không vào lệnh) – bản cũ ngừng quét im lặng, mất ~9% dữ liệu.

**Bước 2 – Cổng bắt buộc** (thiếu một cái là loại ngay, chỉ dùng nến đã đóng):

| Cổng | Điều kiện |
|---|---|
| Xu hướng khung 1h | Long: EMA21 > EMA50 và giá > EMA21 (short: ngược lại), ADX 1h ≥ 18 (`HTF_MIN_ADX`). Chỉ đánh **cùng chiều** khung 1h; 1h đi ngang → setup range (đang chặn sẵn) |
| Thị trường có xu hướng | ADX 15m ≥ 20 (`MIN_ADX`, lọc sideway) |
| Đủ biến động | ATR ≥ 0,35% giá (`MIN_ATR_PCT`) |
| Có volume | Volume nến tín hiệu ≥ 70% trung bình 20 nến (`MIN_VOL_RATIO`) |
| Không đuổi giá | Giá cách EMA21 không quá 1 ATR (`MAX_EXT_ATR`) |
| Không ngược nhịp 1h (v3.2) | Short khi coin vừa tăng ≥ 0.5% trong 1h (long: vừa giảm) → loại. 56 tín hiệu bóng kiểu này: −0.72R, thắng 18% |
| Cổng BTC (v3.2) | BTC đang tăng/giảm ≥ 0.3%/1h hoặc xu hướng 1h ngược → cấm short/long altcoin |
| RSI hợp lý | Long: 45–70, Short: 30–55 |
| Nến tín hiệu đúng màu, không râu dài | Long: nến tăng, đóng ≥ 60% biên độ (`WICK_CLOSE_POS`); Short: nến giảm, đóng ≤ 40% |

**Bước 3 – Trigger** (phải có ít nhất 1 sự kiện trong 3 nến gần nhất): MACD cắt, EMA9 cắt EMA21, breakout đỉnh/đáy 20 nến kèm volume ≥ 1,5× (**mặc định tắt** – `BREAKOUT_ENABLED`, xem mục "Trigger breakout"), pullback về EMA21 rồi bật, bật/gãy tại VWAP, nến nhấn chìm. Trigger chia nhóm: MACD & EMA9 là một nhóm (động lượng), Pullback EMA21 & VWAP là một nhóm (hồi về giá trị), mỗi trigger khác một nhóm; tối đa 2 nhóm được cộng 1,5 × trọng số. v3.3: trọng số nhóm = **trung bình** trọng số tự học của các trigger trong nhóm mà tín hiệu có (bản cũ lấy số lớn nhất, nên trọng số pullback 0,82 bị bỏ qua ở mọi tín hiệu có cả VWAP).

**Bước 4 – Chấm điểm hợp lưu**: trigger (1,5 × trọng số mỗi nhóm, tối đa 2 nhóm), EMA 9/21/50 xếp đúng thứ tự (+1), đúng phía EMA200 (+0,5) và VWAP (+0,5), ADX 15m ≥ 25 / ≥ 30 (+0,5 / +1), DI khung 1h đồng thuận (+0,5), MACD+RSI khung 1h đồng thuận (+1), nến đóng dứt khoát (+0,5); trừ điểm: volume nến tín hiệu cao trào ≥ 1,5× (`CLIMAX_VOL`, −1; trừ nến breakout khi trigger breakout bật), ADX 1h ≥ 35 (`LATE_TREND_ADX`, −1), coin đã chạy > 20% trong 24h (−1,5); rồi cộng/trừ điểm dòng tiền (OI, funding – mục 11). Cần ≥ 5 điểm (`MIN_SCORE`). v3.3: setup xu hướng không còn cổng "tỉ lệ lời/lỗ ≥ `MIN_RR`" – TP cố định được dựng ≥ 1,2 × `MIN_RR` × R nên cổng đó không bao giờ chặn; `MIN_RR` chỉ là mức sàn của TP cố định. Setup range vẫn cần R:R tới dải giữa ≥ `RANGE_MIN_RR`.

**Bước 5 – AI lần 1 (phân tích)**: tối đa 3 **tín hiệu tốt nhất** của lượt quét được gửi lần lượt cho AI (điểm cao trước), kèm 5 bài học gần nhất; vào được 1 lệnh thì dừng – v3.3: các ứng viên còn lại được ghi bóng (nguồn `slot_taken`; bản cũ bỏ mất). Lượt quét sau hỏi AI về đúng tín hiệu đã ghi bóng (cùng mã, hướng và nến 15m – `slot_taken`, `max_positions`, `not_sent`) thì dòng bóng cũ thành `DUP` ("Trùng"), bộ tự học chỉ đếm dòng hỏi AI – mỗi tín hiệu một lần. Đang có lệnh mở thì trong lượt quét (~30 giây) bot vẫn quản lý lệnh mở mỗi `MONITOR_INTERVAL`. AI đồng ý ≥ 60% (`AI_MIN_CONFIDENCE`) → vào lệnh (hệ số rủi ro: AI 60–64% 0,85×, 65–79% 1,0×, ≥ 80% 1,2×); đồng ý nhưng chỉ 50–59% → vẫn vào với **nửa rủi ro** (`AI_WEAK_CONFIDENCE`); phản đối với độ chắc dưới ngưỡng phủ quyết (`AI_VETO_CONF`: để trống thì chạy ảo 65%, chạy thật 0% = AI luôn được chặn; bộ tự học chỉnh ±5%) mà tín hiệu điểm ≥ 6,5 (`AI_OVERRIDE_MIN_SCORE`) → vào nửa rủi ro; còn lại bỏ. v3.3: AI nhận đúng **kế hoạch thoát** bot sẽ dùng (`exit_plan`; hybrid: TP1 = 1R, chốt 50%, SL về hoà vốn, phần còn lại bám sóng) và `take_profit` là mục tiêu chốt đầu tiên – bản cũ gửi TP của cách thoát fixed (≥ 1,8R) dù bot chạy hybrid. Bài học đưa vào prompt chỉ lấy tổng kết khớp kết quả (Bước 7). (v3.1: trên dữ liệu đã có, AI từ chối 90% tín hiệu nhưng nhóm bị từ chối không thua nhóm được duyệt – AI điều tiết **khối lượng** thay vì chặn cứng.)

**Bước 6 – Vào & quản lý lệnh**: SL theo cấu trúc (đáy/đỉnh 6 nến ∓ 0,15 ATR), tối thiểu 2 ATR (`ATR_SL_MULT`); cấu trúc xa hơn 3 ATR (`MAX_SL_ATR`) thì bỏ tín hiệu. Khối lượng tính để chạm SL mất `RISK_PER_TRADE` = **3% vốn × hệ số rủi ro** (đã gồm phí + trượt giá): theo AI (≥ 80% 1,2×; 60–64% 0,85×; AI chưa chắc / phản đối yếu 0,5×; AI lỗi, vào theo điểm 0,6×), từ lệnh thua liên tiếp thứ 3 mỗi lệnh nhân 0,7, kẹp 0,35–1,5× (v3.2: rủi ro không tăng theo điểm hợp lưu). x10 isolated, tối đa **2 lệnh** nhưng tổng rủi ro đang mở ≤ `MAX_TOTAL_RISK` = 5% vốn; lot tối thiểu của sàn làm rủi ro vượt 2× dự tính thì bỏ tín hiệu. Thoát lệnh: mặc định hybrid – chốt 50% ở TP1 = **1R** (`TP1_R`; bộ tự học có thể chọn 1,3 / 1,6 / 2 / 2,5R), SL về hoà vốn, phần còn lại bám sóng (mục 9).

**Phí**: mỗi vòng vào–ra tốn **0,20%** giá trị lệnh – phí taker 0,05% × 2 chiều (`TAKER_FEE`) + trượt giá 0,05% × 2 chiều (`SLIPPAGE`). Với SL trung vị ≈ 1,36% giá, phí ăn ≈ 0,15R mỗi lệnh – bằng toàn bộ lợi thế trước phí trong backtest 88 ngày (+0,14R). Lệnh chạy ảo, mô phỏng bóng, bộ tự học và backtest dùng cùng một mô hình (`bot/costs.py`); cột `fee` của lệnh ghi phí cả hai chiều.

**v3.3 – lệnh chạy ảo giống lệnh thật**: bot vào lệnh ở **giá last lúc quyết định** (cộng trượt giá `SLIPPAGE`), không ở giá đóng nến 15m đã cũ vài phút; SL giữ đúng mức theo cấu trúc, 1R, TP1 và khối lượng tính lại từ giá khớp. Mặc định bot chỉ bỏ tín hiệu khi giá khớp đã vượt SL (setup range: đã vượt TP cố định), lý do ghi vào tín hiệu bóng (nguồn "Không vào được"). Các cổng xét lại ở giá mới – giá chạy ngược về phía SL quá `MAX_FILL_DRIFT_R` = 0,5R, SL cách giá quá `MAX_SL_ATR`, R:R tới dải giữa (range) không còn đủ – làm đổi tập lệnh được vào, tức là đổi chiến lược, nên **tắt sẵn**; muốn dùng thì đặt `FILL_RECHECK=true`. Chạy thật: khớp xong bot đo lại rủi ro từ giá khớp thật, vượt kế hoạch quá `LIVE_RISK_TOL` = 20% (hoặc vượt `MIN_LOT_RISK_MULT` = 2× rủi ro dự tính với lệnh lot tối thiểu) thì cắt bớt hợp đồng (làm tròn ra dưới 1 lot thì giữ 1 lot), không cắt được mà vượt 2× thì đóng, và báo. SL/TP, TP1, trailing đều theo **giá last** trên **mọi nến 1 phút đã đóng** kể từ lần kiểm tra trước (bản cũ chỉ nhìn nến đang chạy nên sót râu chạm SL/TP1); nến mở cửa vượt qua SL thì khớp ở giá mở. Nến của lúc bot **không theo dõi** (vừa khởi động lại, vừa giành lại quyền giao dịch, vòng lặp kẹt quá 10 phút) chỉ được xét SL/TP đang đặt sẵn – như lệnh trên sàn vẫn chạy khi bot tắt – bot không chốt TP1, không dời SL, không đóng theo giá của lúc đó; chạy ảo và chạy thật làm y như nhau. Chạy thật còn: SL mới mà sàn chưa nhận (từ chối, lỗi mạng) thì bot tự giữ – giá last vượt là đóng bằng lệnh market – và đặt lại mỗi lượt; không đọc được vị thế thì bỏ qua lượt đối chiếu; lệnh đóng lỗi thì lệnh vẫn mở và được thử lại; đóng lệnh huỷ mọi SL/TP còn treo (cả loại oco). Chốt TP1 làm tròn xuống theo lot của sàn (1 hợp đồng thì giữ cả, dời SL về hoà vốn), tin báo ghi giá khớp thật. Cột phí ghi phí cả hai chiều của mọi phần; `risk_mult` là rủi ro thật so với rủi ro cơ bản.

**Bước 7 – AI lần 2 (tổng kết)**: khi lệnh đóng, AI đánh giá GOOD_TRADE / BAD_TRADE / LUCKY / UNLUCKY và rút 1–2 câu bài học. Bài học được lưu và đưa lại vào prompt của các lần phân tích sau. Xem bằng `/lessons` hoặc trên dashboard. v3.3: verdict phải **khớp kết quả** – lệnh thua đủ 1R (≤ −0,9R) không thể là GOOD_TRADE: AI giải thích là thua đúng quy tắc (trường `rule_correct`) thì đổi thành UNLUCKY; không thì tổng kết bị đánh dấu "không khớp kết quả" (tin báo, `/lessons`, web) và **không** đưa vào prompt; LUCKY cho lệnh thua, UNLUCKY cho lệnh lãi cũng vậy. Bản cũ chấm GOOD_TRADE cho lệnh BEAT thua −1R rồi đưa "bài học" đó vào prompt. Tổng kết đã lưu từ trước 3.3 được xét lại theo cùng luật mỗi lần đọc.

**Cầu dao**: lỗ 12% vốn trong ngày (~4 lệnh thua) → nghỉ đến ngày mai; drawdown 30% từ đỉnh → dừng hẳn, chờ `/resume`. Hạn mức AI mặc định 80 lần/ngày (`AI_MAX_CALLS_PER_DAY`), hết hạn mức bot không vào lệnh mới.

Trong lần quét thử 40 coin thật chỉ có 3 coin qua được bộ lọc, và chỉ 1 được hỏi AI → thực tế bot gọi AI khoảng 5–15 lần/ngày.

## 2. Chuẩn bị

| Cần gì | Lấy ở đâu |
|---|---|
| 9Router (khuyên dùng) | `npm install -g 9router` rồi chạy `9router`, mở `http://localhost:20128/dashboard`, thêm provider free (Qwen, Gemini, NVIDIA NIM, OpenRouter, Cloudflare AI...). API: `http://localhost:20128/v1` |
| OpenRouter key (dự phòng) | https://openrouter.ai/keys |
| Telegram bot token | Chat `@BotFather` → `/newbot` |
| Telegram chat id | Chat `@userinfobot` |
| OKX API key (chỉ khi `live`) | OKX → Profile → API → tạo key quyền **Trade** (không bật Withdraw). Bật Futures, nạp ≥ 10 USDT vào Trading account |

Nhớ nhắn `/start` cho bot Telegram một lần để bot gửi được tin.

## 3. Deploy lên Botkeep.cloud (runtime Python, không Docker)

> **Ổ đĩa Botkeep có hạn (1 GB ở gói free).** Bản này đã bỏ pandas/numpy, cài pip không cache và tự dọn cache → tổng thư viện ~170 MB. Nếu server từng bị báo `disk I/O error` / `readonly database`: vào File Manager của Botkeep xoá thư mục `.cache` (nếu còn) và `data/`, rồi Start lại. Khuyên dùng `DATABASE_URL` Supabase cả trên Botkeep để dữ liệu không nằm trên ổ đĩa server.

Botkeep chạy app theo runtime, nhận code qua **ZIP upload** hoặc **GitHub**, biến môi trường nhập ở mục **Secrets** của panel.

1. Tạo server mới → chọn profile **Python** (phiên bản 3.10 trở lên).
2. Nguồn code: upload file ZIP này (hoặc đẩy lên GitHub rồi chọn repo/branch).
3. **Start command**: `python run.py` (file này tự cài thư viện trong `requirements.txt` rồi chạy bot, tự khởi động lại nếu bot crash).
4. **Secrets**: nhập các biến trong `.env.example` mà anh cần (tối thiểu: `MODE=paper`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `OPENROUTER_API_KEY` hoặc cấu hình 9Router, `DASHBOARD_PASS`). Hoặc tạo file `.env` trong ZIP – nhưng để key trong Secrets an toàn hơn.
5. Bấm Start, xem console: dòng `🚀 Bot khởi động – chế độ PAPER` là chạy được.
6. Dashboard: bot mở cổng theo biến `PORT` / `SERVER_PORT` / `APP_PORT` nếu Botkeep cấp, không thì `DASHBOARD_PORT` (8080). Trong console lúc khởi động có dòng `Dashboard: http://0.0.0.0:<cổng>` – đó là cổng cần mở/expose trong panel Botkeep (mục Network/Ports nếu có). Truy cập `http://<ip-server>:<cổng>/` – có trang đăng nhập (`DASHBOARD_USER`/`DASHBOARD_PASS`, nhớ 30 ngày); để trống `DASHBOARD_PASS` nếu không muốn hỏi. Cổng web được mở ngay trong 1–2 giây đầu, trước khi bot tải dữ liệu OKX, để host không báo "not responding". Nếu Botkeep vẫn báo vậy: so sánh **assigned port** trong panel với số cổng trong dòng log `Dashboard: cổng …` – nếu khác, đặt Secret `PORT=<assigned port>`. Thử `http://<ip>:<cổng>/health` trước: trả `{"ok":true}` nghĩa là server đã chạy, chỉ còn vấn đề cổng/đường dẫn. File `botkeep.env` có sẵn danh sách biến. **Khuyên dùng:** trên panel chỉ khai các bí mật (token Telegram, key AI, Zoom, mật khẩu dashboard) cùng `MODE`, `INSTANCE_NAME`, `PUBLIC_URL`. Mọi thông số khác đã có mặc định tốt nhất trong code và tự cập nhật theo từng bản; nếu panel còn khai biến thông số thì giá trị cũ đó sẽ thắng, nên hãy xoá chúng. Trong khung `.env` của Botkeep, dòng `__BOTKEEP_KEEP_SECRET_…__` giữ nguyên để giữ giá trị; không dán dòng `<...>`. Lúc khởi động, log in dòng `Thông số đang dùng: …` để kiểm tra.

**Chạy 9Router trên Botkeep**: 9Router là app Node.js, chạy thành **một server riêng** trên Botkeep (profile Node.js, start command `npx 9router` hoặc `9router`), rồi trong Secrets của bot đặt `NINEROUTER_BASE_URL=http://<địa-chỉ-server-9router>:20128/v1`. Nếu hai server trên Botkeep không gọi được nhau qua mạng nội bộ (tuỳ gói), anh có thể chạy 9Router trên máy tính cá nhân/VPS khác và trỏ URL public vào, hoặc đơn giản là chỉ dùng OpenRouter (`AI_PROVIDER_ORDER=openrouter`). Bot dùng ~200 MB RAM, 9Router thêm ~150–300 MB – vừa gói 2 GB.

## 3b. Postgres dùng chung trên Supabase (tuỳ chọn)

Bản cũ có mục chạy song song Render free + Botkeep: đã bỏ, chỉ còn một server Botkeep (`render.yaml` lấy lại bằng `git show 8332222:render.yaml`).
Supabase vẫn dùng được để dữ liệu không nằm trên ổ đĩa server:

1. Vào supabase.com → New project → đặt tên, **Database password** (ghi lại; nên đặt chỉ gồm chữ + số để khỏi phải mã hoá URL), region gần server nhất (Singapore).
2. Trong project bấm nút **Connect** (góc trên) → tab **Connection string** → chọn **Session pooler** (KHÔNG chọn "Direct connection": kết nối trực tiếp chỉ có IPv6, máy không ra được IPv6 sẽ báo lỗi mạng).
3. Chuỗi có dạng:
   `postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres`
   Thay `[YOUR-PASSWORD]` bằng mật khẩu. Nếu mật khẩu có ký tự đặc biệt phải mã hoá URL (`@`→`%40`, `#`→`%23`, `/`→`%2F`, `:`→`%3A`).
4. Dán vào `DATABASE_URL` (trong Stockvn: `ALT_DATABASE_URL`, không dùng chung DB với stock). Bot tự thêm `sslmode=require` và tự tắt prepared statement (bắt buộc với pooler của Supabase).
5. Kiểm tra trước khi deploy: `python check_db.py` (đọc `.env`/`bot.env` cạnh nó, ưu tiên `ALT_DATABASE_URL`; từ biến môi trường chỉ lấy `ALT_DATABASE_URL`) → phải in `✅ Kết nối OK`.
6. Bot tự tạo bảng khi chạy lần đầu, không cần làm gì trong SQL Editor. Dữ liệu xem được ở Supabase → Table Editor (bảng `trades`, `signals`, `reviews`, `locks`, `heartbeats`).

Lưu ý Supabase free: project bị **tạm dừng (pause) sau 7 ngày không có truy vấn** – bot chạy liên tục thì không bị; nếu anh tắt bot hơn 1 tuần thì vào dashboard Supabase bấm Restore rồi chạy lại. Giới hạn 500 MB dữ liệu là dư thừa (bot ghi vài MB/tháng).

### Chạy trên VPS/máy thường

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env
python run.py
```

## 4. Lệnh Telegram

`/status` vốn, PnL, lệnh mở, hạn mức AI · `/trades` 10 lệnh gần nhất · `/signals` tín hiệu & quyết định AI · `/ai` số lượt gọi từng model, quyết định gần nhất · `/lessons` tổng kết của AI · `/pause` `/resume` · `/close` đóng tất cả. Bot tự gửi báo cáo ngày lúc 0h theo `TIMEZONE` (mặc định giờ Việt Nam).

## 5. Đo 4 tuần ảo → quyết định (v3.3)

1. `MODE=paper` **ít nhất 4 tuần** với tham số mặc định của 3.3. Thứ tự: khởi động 3.3.0 lần đầu với `LEARN_MODE` **trống** (chạy ảo = `auto`: bot tự đưa tham số tự học cũ – trọng số pullback 0,82 – về mặc định một lần, có tin "🧬 TỰ HỌC – v3.3: …"), **sau đó** mới đặt `LEARN_MODE=suggest` (trong Stockvn: `ALT_LEARN_MODE=suggest` trên panel) để bộ tự học chỉ đề xuất, không đổi tham số trong lúc đang đo. Các file mẫu (`.env.example`, `botkeep.env`) để trống sẵn vì lý do này. Chỉnh theo dữ liệu gần đây thường hại (backtest: xếp hạng trong mẫu / ngoài mẫu tương quan −0,56). Lỡ đặt `suggest` trước lần khởi động 3.3 đầu tiên: tham số cũ vẫn chạy, có một đề xuất "Khôi phục" chờ duyệt – duyệt **ngay** đúng đề xuất đó trên trang Tự học (hoặc `/apply` khi chưa có đề xuất nào khác, vì `/apply` duyệt mọi đề xuất đang chờ).
2. Mục tiêu đo: ≥ 150 tín hiệu bóng **mới** (sau 3.3) và ≥ 30 lệnh chạy ảo; tính sai số chuẩn **theo ngày** (tín hiệu dồn cụm). Bảng so sánh "SL 2,5 ATR + bám sóng" (mục 9) chạy song song trên cùng tín hiệu.
3. **Chỉ chuyển live nhỏ (`RISK_PER_TRADE=0.01`) khi đủ cả bốn điều kiện:** đang chạy bản ≥ 3.3.0 (cỡ lệnh live theo giá mới – bug 2 đã sửa); ≥ 30 lệnh chạy ảo sau 3.3 với R trung bình dương; ≥ 150 tín hiệu bóng sau 3.3 với R trung bình dương; backtest walk-forward dương ngoài mẫu và vượt 2 lần sai số chuẩn. Với vốn 10 USDT, 1% rủi ro thường nhỏ hơn 1 lot (UNI: 1 hợp đồng ≈ 1,2% vốn). Khi đó bot **vẫn vào 1 lot** nếu rủi ro của lot đó ≤ `MIN_LOT_RISK_MULT` (mặc định 2) × rủi ro dự tính, chỉ bỏ lệnh khi vượt 2× – nên rủi ro thật mỗi lệnh live có thể tới gần 2% (và gần 2× mức đã giảm khi AI chưa chắc). Muốn rủi ro thật sát 1% trong lần live đầu: đặt `MIN_LOT_RISK_MULT=1.2` (trần ≈ 1,2%: lot vượt mức đó thì bỏ lệnh, giá khớp trượt làm vượt thì đóng ngay); cần vốn lớn hơn để mức 1% có nghĩa.
4. **Tạm dừng và thiết kế lại chiến lược nếu một trong hai điều sau xảy ra:** sau 150 tín hiệu bóng mới, trải trên ≥ 3 tuần, R trung bình < −0,1 và cận trên khoảng tin cậy 95% (theo ngày) vẫn < 0; hoặc chạy ảo sụt quá 15% từ đỉnh.
5. (Nên) trước khi live chạy thêm vài ngày `OKX_DEMO=true` với API key demo của OKX để kiểm tra đặt lệnh thật trên sàn ảo. Chuyển thật: `MODE=live`, API key OKX, `I_UNDERSTAND_THE_RISK=yes` (trong Stockvn: các bước ở `docs/ALTCOIN_TRONG_STOCKVN.md` mục 10).

## 6. Tinh chỉnh

- **Đừng chỉnh tham số chiến lược theo vài ngày dữ liệu** (mục 5): backtest 88 ngày, 1.335 tổ hợp, không tổ hợp nào dương ngoài mẫu chắc chắn. Các tham số chính: `MIN_SCORE` 5,0, `MIN_ADX` 20, `ATR_SL_MULT` 2,0, `TP1_R` 1,0.
- `OPENROUTER_MODELS=auto` (mặc định): bot tự đọc danh sách model `:free` hiện còn trên OpenRouter, làm mới mỗi 12h; model trả 404 (bị gỡ) tự loại và dò lại ngay. `openrouter/free` luôn đứng cuối làm lưới an toàn.
- `AI_FAIL_POLICY`: khi mọi model lỗi – `score` vẫn vào nếu điểm KT ≥ `AI_FAIL_MIN_SCORE` (mặc định 7), `skip` bỏ qua. Để trống: chạy ảo = score, chạy thật = skip.
- `AI_VETO_CONF` / `AI_OVERRIDE_MIN_SCORE`: AI phản đối với độ chắc < ngưỡng (để trống: chạy ảo 65%, chạy thật 0%) thì tín hiệu điểm ≥ 6,5 vẫn vào nửa rủi ro, ghi nguồn `ai_override`. Bộ tự học so kết quả các tín hiệu AI phản đối để nâng/hạ ngưỡng ±5% (tối đa 1 lần/48h).
- `BREAKOUT_ENABLED`: trigger breakout, mặc định `false` (mục "Trigger breakout").
- Phiên bản đang chạy hiện ở góc tiêu đề dashboard, `/health`, lệnh `status` và tin nhắn khởi động.
- `AI_MAX_CALLS_PER_DAY`: hạn mức tổng (phân tích + tổng kết), chỉ tính lượt AI trả lời thành công. `AI_REVIEW_ENABLED=false` nếu muốn tiết kiệm chỉ gọi 1 lần/lệnh.
- `AI_ENABLED=false`: chạy thuần kỹ thuật để so sánh.
- `RISK_PER_TRADE`, `LEVERAGE`: hạ nếu thấy chuỗi thua dài. Bot đã tự co rủi ro sau chuỗi thua (`LOSS_STREAK_CUT`) và theo chất lượng tín hiệu (`RISK_MIN_MULT`–`RISK_MAX_MULT`); `MAX_TOTAL_RISK` là trần cho mọi lệnh đang mở.


## 8. Ghi nhận dữ liệu & tự học

**Trên web** (cuộn xuống dưới dashboard):
- Tab **Hoạt động**: toàn bộ log của bot (giống console Botkeep), lọc theo mức INFO/WARNING/ERROR và tìm kiếm. Lưu 14 ngày.
- Tab **Lịch sử quét**: mỗi lượt quét bao nhiêu coin đạt, loại vì lý do gì. Lưu 30 ngày.
- Tab **Lịch sử quét** (v3.3): lượt quét lúc đang đủ lệnh mở có nhãn "đủ N lệnh – chỉ ghi bóng".
- Tab **Tín hiệu & AI**: mỗi tín hiệu kèm nguồn và **kết quả thật** sau đó (TP / SL / hết giờ, tính bằng R). Nguồn (cột `source`):

  | Nguồn | Trên web | Nghĩa |
  |---|---|---|
  | `taken` | Đã vào lệnh | AI duyệt, bot vào lệnh |
  | `ai_weak` | Vào (AI chưa chắc) | AI đồng ý 50–59%, vào nửa rủi ro |
  | `ai_override` | Vào dù AI phản đối yếu | AI phản đối dưới ngưỡng phủ quyết, điểm ≥ 6,5 |
  | `ai_rejected` | AI từ chối | |
  | `ai_error` | AI lỗi/hết lượt | |
  | `skipped` (v3.3) | Không vào được | AI duyệt nhưng engine không vào được: giá đã vượt SL, hết ngân sách rủi ro, lot, kiểm tra rủi ro live – lý do ở `features.skip` (bản cũ ghi nhầm là AI từ chối) |
  | `not_sent` | Không gửi AI | Đạt nhưng xếp sau `AI_CANDIDATES_PER_SCAN` ứng viên |
  | `slot_taken` (v3.3) | Không gửi AI (đã vào lệnh khác) | Xếp sau ứng viên vừa vào lệnh trong cùng lượt quét (bản cũ bỏ mất) |
  | `max_positions` (v3.3) | Không gửi AI (đủ lệnh mở) | Đang đủ `MAX_OPEN_POSITIONS` lệnh (bản cũ ngừng quét) |
  | `below_score` | Suýt đạt | Điểm dưới ngưỡng tối đa 1,5 điểm |
  | `blocked` | Luật chặn | Bị luật chặn sẵn hoặc luật tự học (ghi đúng luật nào, `features.blocked_rule`) |

  Kết quả `DUP` ("Trùng", v3.3): dòng bóng của tín hiệu mà lượt quét sau đã hỏi AI (cùng mã, hướng, nến 15m) – bộ tự học đếm mỗi tín hiệu một lần. Cột `r_sl25_trail` (v3.3): kết quả thử nghiệm A/B "SL 2,5 ATR + bám sóng toàn bộ" trên cùng tín hiệu (mục 9).
- Thẻ **Tải dữ liệu**: tải CSV (mở được bằng Excel / Google Sheets, đúng tiếng Việt) hoặc sao lưu toàn bộ JSON.

**Tự học hoạt động thế nào:**
1. Mọi tín hiệu đã qua bộ lọc kỹ thuật, kể cả tín hiệu không vào lệnh, đều được theo dõi "bóng": bot xem nến 5 phút sau đó để biết nếu vào thì sẽ chạm TP hay SL trước. Kết quả tính bằng R (1R = khoảng cách tới SL), đã trừ phí. v3.3: cùng giá và phí với lệnh chạy ảo – vào ở giá last lúc quyết định cộng trượt giá, trừ phí + trượt giá cả hai chiều (mặc định 0,20%; bản cũ 0,15% và vào ở giá đóng nến cũ); bỏ phút chứa lúc vào như lệnh chạy ảo (khung 5 phút chứa lúc vào được xét bằng nến 1 phút). Kết quả bóng của bản trước 3.3 không được dùng để thống kê và tự điều chỉnh – sau khi nâng cấp, bộ tự học chờ đủ mẫu mới.
2. Mỗi giờ bot thống kê theo nhóm: nguồn tín hiệu, loại trigger, luật lọc, khung giờ, điểm số. Khi một nhóm có đủ mẫu (mặc định 30) trải trên ≥ `LEARN_MIN_DAYS` = 7 ngày khác nhau **và** kết quả khác 0 có ý nghĩa thống kê (|kỳ vọng| / sai số chuẩn ≥ `LEARN_MIN_T` = 2,0; so sánh cách thoát / mức TP1 theo từng cặp trên cùng tín hiệu, TP1 cần ≥ 40 mẫu), bot điều chỉnh:
   - bật/tắt luật chặn trong danh mục cố định (vd. "short khi RSI 1h < 32"): chặn khi kỳ vọng < −0,25R, gỡ chặn (kể cả luật chặn sẵn) khi > +0,1R – chặn ở bộ lọc thì không tốn lượt AI;
   - trọng số từng trigger (0,6 – 1,4; vùng chết ±0,1R, mỗi lần đổi tối đa 0,2);
   - ngưỡng điểm MIN_SCORE (4,0 – 7,0, mỗi lần ±0,5);
   - cách thoát lệnh (mục 9), mức TP1, ngưỡng phủ quyết AI.
   Mỗi tham số chỉ đổi tối đa 1 lần/48h (`LEARN_COOLDOWN_H`). v3.2: `LEARN_MIN_T` = 2,0; không bao giờ chặn cả một phía (long_side/short_side chỉ để thống kê); cách thoát lệnh cần ≥ `LEARN_EXIT_MIN_SAMPLES` = 60 cặp.
   v3.3: sai số chuẩn **gom cụm theo ngày** (tín hiệu cùng ngày là cùng một nhịp thị trường, không độc lập – bản cũ coi chúng độc lập nên t quá lớn); trọng số trigger và ngưỡng điểm chỉ học từ tín hiệu **không bị luật đang chặn** (tín hiệu bị chặn không vào lệnh được dù điểm thế nào; trọng số pullback 0,82 của bản cũ học từ 32 tín hiệu, 22 trong đó đã bị luật chặn sẵn). Lần đầu chạy 3.3 ở chế độ **chạy ảo + `LEARN_MODE=auto`** bot đưa mọi tham số tự học của bản cũ về mặc định **một lần** (ghi log, dòng `migrate v3.3` trong lịch sử tự học và báo "🧬 TỰ HỌC – v3.3: …" qua Telegram/Zoom). **Chạy thật** hoặc `LEARN_MODE` `suggest` / `off`: bot không tự đổi luật đang chạy (tham số có thể do anh/chị duyệt tay) mà tạo một đề xuất "Khôi phục" chờ duyệt (web hoặc `/apply`). Luật phiên giờ được thống kê theo đúng giờ bot đã xét luật (lúc chẩn đoán). Luật đã chặn vẫn được theo dõi bóng, nên nếu nhóm đó tốt lên bot sẽ tự bỏ chặn. (v3.1: trước đây 13–20 mẫu đã đổi, trọng số trigger nhảy qua lại trong 1 ngày – nay không còn.)
3. Mỗi sáng (7h) AI đọc thống kê, viết nhận xét và gợi ý – **không tự áp dụng**, anh bấm "Áp dụng" nếu đồng ý. Tốn 1 lượt AI/ngày.
4. `LEARN_MODE` để trống: chạy ảo tự áp dụng (`auto`), chạy thật chỉ đề xuất (`suggest`) – anh duyệt trên web hoặc gõ `/apply`; `off` tắt hẳn. **Khuyến nghị v3.3: `LEARN_MODE=suggest` trong 4 tuần đo, đặt sau lần khởi động 3.3.0 đầu tiên** (mục 5). Nút "Khôi phục mặc định" đưa mọi thứ về ban đầu.

Bot **không bao giờ tự sửa code** – chỉ chỉnh các tham số trong danh mục, có giới hạn và có lịch sử. Telegram: `/learn` xem trạng thái, `/apply` duyệt đề xuất.


## 9. Cách thoát lệnh – "ăn sóng" altcoin

Ba cách, chọn bằng `EXIT_MODE` (mặc định `auto` = bộ tự học chọn, khởi đầu là hybrid):

| Cách | Mô tả | Hợp với |
|---|---|---|
| `fixed` | TP cố định = xa hơn trong hai mức 3 ATR và 1,8R (1,2 × `MIN_RR`); lời ≥ 1,2R thì kéo SL về hoà vốn rồi bám cách giá 1R; giữ tối đa 12h | Thị trường giật, sóng ngắn |
| `hybrid` | Chốt 50% khi lời **1R** (`TP1_R`; khoá lợi nhuận, SL về hoà vốn); 50% còn lại **bám sóng**: SL = đỉnh/đáy kể từ khi vào ∓ 2,5 ATR, siết còn 1,5 ATR khi lời ≥ 4R; thoát nếu 4h không lập đỉnh mới; giữ tối đa 24h | Sóng mạnh kéo dài |
| `trail` | Toàn bộ vị thế bám sóng khi đỉnh đạt ≥ 1,2R, không chốt trước | Xu hướng rất mạnh, chấp nhận trả lại lãi |

Bot mô phỏng **song song cả 3 cách** trên mọi tín hiệu (bằng nến 5 phút thật sau tín hiệu), hiển thị so sánh trong thẻ "Tự học" → "So sánh cách thoát lệnh". Với `EXIT_MODE=auto`, khi có ≥ 60 tín hiệu trải trên ≥ 7 ngày và một cách hơn cách đang dùng ≥ 0,25R/tín hiệu có ý nghĩa thống kê thì bot tự chuyển (tối đa 1 lần/48h, có lịch sử, khôi phục được).

v3.3 – **thử nghiệm A/B "SL 2,5 ATR + bám sóng toàn bộ"**: mỗi tín hiệu bóng setup xu hướng còn được mô phỏng với SL rộng hơn (xa hơn trong hai mức 2,5 ATR và SL cấu trúc – như đặt `ATR_SL_MULT=2.5`), cùng giá vào và phí, bám sóng toàn bộ; kết quả ở cột `r_sl25_trail` (ghi sau, khi biến thể kết thúc – không giữ kết quả chính chờ; tối đa 25 giờ) và hàng "thử nghiệm" của bảng so sánh, kèm chênh lệch với cách đang dùng trên cùng tín hiệu (sai số chuẩn theo ngày). Backtest 88 ngày cho phương án này +0,08R cả kỳ nhưng ngoài mẫu chỉ +0,04 ± 0,07 – chưa xác nhận, nên bot **không tự chuyển**; đổi SL là quyết định của anh/chị khi đủ dữ liệu.

Lưu ý thật lòng: không cách nào "ăn trọn" sóng. Trailing đổi tỉ lệ thắng lấy những lệnh thắng lớn; gặp sóng giật mạnh rồi mới chạy tiếp (như STRK ngày 17/10: hồi về EMA21 rồi mới giảm thêm 3R) thì trailing vẫn bị chốt sớm. Vì vậy hãy để dữ liệu quyết định thay vì tin cảm giác.


## 10. Kênh Zoom Team Chat

Kênh Zoom làm theo đúng mô hình đã chạy thực tế trong repo **lananh** (`channels/zoom.py`): chatbot app, xác thực chữ ký HMAC, ghép đôi đúng 1 JID, trả lời đúng JID (chat 1:1 trả về người gửi, kênh nhóm trả về kênh), chuyển markdown sang kiểu Zoom, cắt tin dài, thử lại 3 lần. Mọi thông báo gửi đồng thời Telegram + Zoom; lệnh gõ ở đâu cũng được (chỉ máy leader gửi).

**Trang phục vụ Zoom (có sẵn trong bot, theo cách repo lananh làm):**

| Trang | Dùng cho ô nào trên Marketplace |
|---|---|
| `/zoom/oauth/callback` | OAuth Redirect URL + OAuth Allow List |
| `/webhook/zoom` | Bot endpoint URL, Event notification endpoint, Deauthorization endpoint |
| `/privacy` | Privacy Policy URL |
| `/terms` | Terms of Use URL |
| `/support` | Support URL |
| `/docs` | Documentation URL / Home URL |
| `/data-deletion` | Hướng dẫn xoá dữ liệu |
| `/zoom/install` | Nút "Thêm vào Zoom" |
| `/zoom` | Trang cấu hình (cần đăng nhập dashboard): liệt kê đúng các URL theo domain của anh, nút chép, kiểm tra biến môi trường, trạng thái cài đặt/ghép đôi |

Với Botkeep đã có HTTPS (vd `https://okx.bot-keep.xyz`), chatbot Zoom chạy được ngay trên Botkeep, không cần Render.

**Cài đặt (Local Test):**
1. Secrets: `PUBLIC_URL=https://okx.bot-keep.xyz`, `SUPPORT_EMAIL=...`, rồi Redeploy.
2. Đăng nhập dashboard → mở `https://okx.bot-keep.xyz/zoom` → làm theo 7 bước và bấm "Chép" từng URL dán vào marketplace.zoom.us (General App → bật Team Chat → Chatbot).
3. Điền `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` (Development), `ZOOM_BOT_JID`, `ZOOM_SECRET_TOKEN`, `ZOOM_ACCOUNT_ID`, Redeploy, bấm **Validate** endpoint trên Marketplace.
4. Local Test → **Add App Now** → Allow → về trang callback báo "Đã cài thành công".
5. Zoom → Team Chat → Apps → nhắn `status` → Telegram báo JID → gõ `/zoompair <JID>`. `/zoomstatus` xem, `/zoomxoa` gỡ. Gỡ app khỏi Zoom thì bot tự xoá ghép đôi.

**Lưu ý:** mỗi Zoom app chỉ có 1 endpoint. Nếu anh đã dùng app Zoom cho bot lananh thì tạo **app thứ hai** cho bot trading (cùng cách làm), không dùng chung.

**Chỉ chạy trên Botkeep (không có HTTPS):** dùng Incoming Webhook để nhận thông báo vào 1 kênh Zoom – cài app "Incoming Webhook", trong kênh gõ `/inc connect okxbot`, dán Endpoint + Token vào `ZOOM_WEBHOOK_URL`, `ZOOM_WEBHOOK_TOKEN`. Khi đó lệnh vẫn gõ qua Telegram hoặc dashboard.

Lệnh dùng chung: `status, trades, signals, ai, learn, apply, lessons, btc, pause, resume, close` (Telegram thêm `/`, Zoom gõ trực tiếp hoặc `/okxbot <lệnh>`).

## 11. Bối cảnh BTC, Open Interest, tỉ lệ long/short

Mỗi lượt quét bot lấy BTC 15m/1h (xu hướng, % thay đổi 1h/4h). Với mỗi tín hiệu đã qua bộ lọc kỹ thuật, bot lấy thêm Open Interest thay đổi 1h, tỉ lệ tài khoản long/short và funding của coin đó. Các dữ liệu này:
- được gửi cho AI khi phân tích;
- được lưu cùng tín hiệu (tải được trong signals.csv);
- tạo thành các **luật ứng viên** cho bộ tự học: long khi BTC 1h giảm, short khi BTC tăng, ngược xu hướng BTC, BTC biến động mạnh, giá tăng nhưng OI giảm, OI tăng đột biến, đám đông long/short quá đông, funding cực đoan.

Các luật BTC / OI / đám đông này **không chặn sẵn** (luật chặn sẵn – `DEFAULT_BLOCKS` – là setup range, phiên 0h–7h, phiên 13h–19h, volume cao trào). Mỗi luật được theo dõi kết quả bóng; chỉ khi một nhóm có đủ mẫu (≥ 30, ≥ 7 ngày) và thua rõ rệt (kỳ vọng < −0,25R, t ≥ 2) thì mới bị chặn – xem bảng "Theo luật lọc" trong thẻ Tự học.

## 12. Trigger breakout (v3.3 – mặc định tắt)

Breakout = nến tín hiệu đóng vượt đỉnh (short: thủng đáy) 20 nến trước đó với volume ≥ `BREAKOUT_VOL` = 1,5× trung bình. Từ v3.2 trigger này **chết im lặng**: luật `climax_volume` (chặn sẵn) chặn mọi nến volume ≥ `CLIMAX_VOL` = 1,5×, đúng bằng ngưỡng xác nhận của breakout. v3.3 sửa xung đột: khi trigger bật, volume của chính nến breakout là xác nhận của nó – luật `climax_volume` và điểm trừ "volume cao trào" không áp dụng cho tín hiệu có breakout (tín hiệu khác vẫn như cũ). Tham số chiến lược không đổi.

Backtest riêng trước khi bật (88 ngày 05/08–01/11/2026, 50 coin, code của bot đã sửa với `BREAKOUT_ENABLED=true`, vào ở giá đóng nến tín hiệu + trượt giá, hybrid TP1 1R, phí 0,20%/vòng, 60% đầu trong mẫu / 40% cuối ngoài mẫu, sai số chuẩn theo ngày):

| Nhóm | Số tín hiệu | R trung bình | Trong mẫu | Ngoài mẫu |
|---|---|---|---|---|
| Nến breakout thô (đóng vượt đỉnh/đáy 20 nến, volume ≥ 1,5×) | 23.308 | – | – | – |
| Tín hiệu breakout qua mọi cổng (điểm bất kỳ) | 2 | +0,50 ± 1,63 | 2 (APR short 11/08, KORU long 13/08) | **0** |
| Lệnh bot thật sự vào được (điểm ≥ 5, không luật chặn sẵn) | 0 | – | 0 | 0 |
| *Tham khảo, không dùng để quyết định:* breakout chỉ bị cổng chống đuổi giá `MAX_EXT_ATR` loại | 523 | −0,04 ± 0,07 | −0,08 (n=324) | +0,04 ± 0,12 (n=199) |

Cổng chống đuổi giá (giá cách EMA21 ≤ 1 ATR) loại gần hết nến breakout, nên trigger gần như không bao giờ xuất hiện; ngoài mẫu không có tín hiệu nào → **không có bằng chứng R ≥ 0 ngoài mẫu → giữ tắt** (`BREAKOUT_ENABLED=false`). Khi tắt, trigger breakout không được tính (không cộng điểm); tín hiệu chỉ có breakout bị loại với lý do riêng "chỉ có trigger breakout – đang tắt" (đếm trong log quét), log khởi động in `BREAKOUT=tắt`. Tín hiệu đó vẫn được chấm **như khi bật** và, nếu qua ngưỡng điểm (sau dòng tiền), được ghi bóng nguồn `breakout_off` ("Breakout (tắt – chỉ đo)"): không hỏi AI, không vào lệnh, bộ tự học không dùng; kết quả hiện ở bảng "Kết quả theo nguồn tín hiệu" trang Tự học – đó là số liệu ngoài mẫu để quyết định có bật hay không. Tín hiệu có breakout kèm trigger khác ghi `breakout_off: true` trong features. Bỏ cổng chống đuổi giá riêng cho breakout là đổi chiến lược và cũng không khác 0 (dòng tham khảo). (Script `bt_breakout.py` chạy trên bộ backtest 88 ngày của đợt chẩn đoán 02/11/2026 – nến OKX lưu sẵn, không nằm trong repo.)

## 7. Cấu trúc

```
run.py            – start command khi chạy độc lập (Botkeep/VPS): tự cài thư viện, chạy & tự khởi động lại bot
bot/config.py     – đọc biến môi trường / .env
bot/exchange.py   – OKX qua ccxt
bot/indicators.py – EMA/RSI/ATR/MACD/BB/ADX/VWAP
bot/strategy.py   – cổng bắt buộc + trigger + chấm điểm hợp lưu
bot/ai.py         – 9Router/OpenRouter, 2 lần gọi/lệnh, hạn mức ngày, bài học
bot/risk.py       – khối lượng lệnh, cầu dao
bot/engine.py     – PaperEngine & LiveEngine
bot/storage.py    – SQLite (1 máy) hoặc Postgres dùng chung (nhiều máy) + khoá leader
bot/exits.py      – logic thoát lệnh (fixed/hybrid/trail) dùng chung cho engine & mô phỏng; kế hoạch thoát gửi AI
bot/costs.py      – mô hình giá khớp & phí dùng chung (paper, live, bóng, tự học): 0,20%/vòng
bot/learner.py    – tự học: kết quả bóng, điều chỉnh tham số có giới hạn, AI tổng kết ngày
bot/market.py     – bối cảnh BTC, Open Interest, long/short, funding
bot/notify.py     – gửi thông báo đa kênh (Telegram, Zoom webhook/chatbot), nhận lệnh Zoom
bot/eventlog.py   – ghi log hoạt động vào DB để xem trên web
bot/telegram.py, bot/dashboard.py, bot/main.py
test_smoke.py     – quét thật OKX, in tín hiệu (không cần key)
test_engine.py, test_risk.py, test_strategy.py, test_learner.py, test_web.py – kiểm tra offline (Stockvn chạy qua tests/run_all.py)
tools/            – backtest_sessions.py (backtest theo phiên trên nến OKX), Kronos (thử nghiệm)
botkeep.env       – mẫu biến cho panel Botkeep khi chạy độc lập (chỉ bí mật + thông tin riêng; v3.3 bỏ hồ sơ "test gắt" 10%/lệnh)
check_db.py       – kiểm tra kết nối Supabase trước khi deploy
```

> Các mục dưới đây là **lịch sử thay đổi** theo từng bản; giá trị đang dùng ở mục 1, 8, 9, 12 và `bot/config.py`.

## Tối ưu lợi nhuận kỳ vọng (v2.9)
Mục tiêu là **lợi nhuận kỳ vọng** = tỉ lệ thắng × lãi trung bình − tỉ lệ thua × lỗ trung bình, không chỉ tỉ lệ thắng.
- **Cổng BTC, chống râu nến, SL theo cấu trúc, trigger không cộng trùng, điểm dòng tiền trước xếp hạng**: giữ từ v2.8.
- **Không đuổi giá**: giá cách EMA21 quá `MAX_EXT_ATR` = 1 ATR thì bỏ. Backtest 10 ngày: nhóm cách hơn 1 ATR lỗ ở cả hai nửa dữ liệu.
- **Phiên 13h–19h (giờ VN)**: backtest thấy lỗ ở cả 4 giai đoạn nhưng mặc định KHÔNG chặn (v2.9.2). Bộ tự học theo dõi và tự chặn nếu thực tế lỗ (kỳ vọng < −0.25R trên đủ mẫu). Muốn chặn ngay: thêm `afternoon_session` vào `DEFAULT_BLOCKS`.
- **TP1 tự học**: mỗi tín hiệu được mô phỏng với TP1 = 1 / 1.3 / 1.6 / 2 / 2.5R; bot chọn mức có kỳ vọng cao nhất. Mặc định `TP1_R=1.0`.
- **Nhiều cơ hội hơn**: quét `TOP_N_COINS=50`; AI từ chối ứng viên #1 thì hỏi #2, #3 (`AI_CANDIDATES_PER_SCAN=3`).
- **Setup range** (đảo chiều tại biên Bollinger khi 1h đi ngang, TP = dải giữa): đã có, nhưng chặn sẵn (`setup_range`) vì chưa đủ dữ liệu chứng minh có lãi; bộ tự học gỡ khi có lãi.
- **AI**: đánh giá theo lợi nhuận kỳ vọng; `confidence` khi đồng ý = điểm chất lượng (50 = trung bình), duyệt khi ≥ `AI_MIN_CONFIDENCE` = 60.

## Học từ 5 ngày chạy ảo (v3.2)
Rút từ 211 tín hiệu bóng có kết quả (23–27/10/2026, v3.1): kỳ vọng chung −0,24R, thắng 38%; 190 short / 21 long; lệnh thật +9,3%
chỉ nhờ 2 ngày giảm mạnh đầu tiên, 3 ngày sau short thua 24%. Những gì dữ liệu nói rõ (n ≥ 27, khoảng tin cậy 90% không chứa 0):
- **Short khi coin vừa tăng ≥ 0.5%/1h** (bắt đỉnh nhịp hồi): −0,72R, thắng 18% (n=56); nhóm còn lại −0,13R → **cổng mới `CHASE_1H_PCT`**.
- **Volume nến tín hiệu 1,5–2,5×**: −0,68R, thắng 16% (n=31); dưới 1,5×: −0,16R → **trừ điểm + luật `climax_volume` chặn sẵn** (trước đây được CỘNG điểm).
- **ADX 1h ≥ 35** (xu hướng đã chạy lâu): short −0,44R (n=76) so với −0,22R → **trừ 1 điểm** (trước đây "ADX mạnh" được cộng).
- **Short khi BTC đang +0,3%/1h trở lên**: −0,60R (n=27) → **`BTC_GATE_CHG` 0,6 → 0,3**.
- **13h–19h VN** (06–12 UTC) lỗ ở mọi khung 2h; **21h–23h VN** (Mỹ mở cửa) là khung duy nhất lãi rõ (+0,42R, n=27) → **chặn sẵn `afternoon_session`**.
- **Điểm hợp lưu ngược dự báo**: điểm ≥ 7 thua nặng nhất (−0,33R, thắng 25%, n=20), điểm < 5 gần hoà → **rủi ro không còn tăng theo điểm** (`risk.quality_mult` chỉ theo AI).
- **Bộ tự học tự thắt cổ bot**: với t ≥ 1,3 nó đổi trọng số trigger rồi đảo ngược sau 48h, chuyển hybrid→trail trên 31 mẫu (trên 211 mẫu hybrid vẫn hơn), rồi **chặn toàn bộ short** sau 2 ngày xấu → bot đứng im 2,5 ngày. Nay: `LEARN_MIN_T` 2,0, không bao giờ chặn cả một phía, đổi cách thoát cần ≥ 60 cặp, thống kê nhóm theo mốc hybrid cố định. Tham số học bằng ngưỡng cũ được **đưa về mặc định một lần** khi khởi động bản 3.2 (giữ ngưỡng phủ quyết AI).
- **Chuỗi thua**: co rủi ro từ lệnh thua thứ 3 thay vì thứ 2 (4 lệnh thua liên tiếp là bình thường với tỉ lệ thắng 40%; co sớm làm bot vào 0,35× đúng lúc thị trường đổi chiều).

Áp lại 4 bộ lọc mới lên chính 173 tín hiệu trend đó (R theo hybrid): còn 78 tín hiệu, +0,36R/tín hiệu, thắng 72%; 95 tín hiệu bị loại
có −0,58R. Đây là kết quả **trên cùng dữ liệu dùng để chọn lọc** nên lạc quan hơn thực tế; nửa sau (25–27/10) chỉ từ −0,50R
lên −0,13R. Cần chạy ảo thêm ≥ 2 tuần (≥ 150 tín hiệu bóng) trước khi tin.

**Bảo mật**: bản cũ in nguyên URL Telegram (có token) khi lỗi mạng và file "sao lưu toàn bộ" kèm log đó. v3.2 che token / khoá API /
mật khẩu DB trong mọi dòng log. Nếu anh đã chia sẻ file sao lưu của bản cũ: **đổi token qua @BotFather (`/revoke`)**.

**Giới hạn vốn**: với 10 USDT, rủi ro 3%/lệnh là 0,30 USDT; kỳ vọng +0,3R và 1,5 lệnh/ngày cho ~4 USDT/tháng dù bot chạy đúng.
Vốn 10 USDT chỉ để kiểm tra hệ thống, không để đo "hiệu quả đầu tư".

## Nhạy hơn nhưng ít rủi ro hơn (v3.1)
Rút từ 2,8 ngày chạy ảo (80 tín hiệu bóng có kết quả, 4 lệnh đóng): điểm hợp lưu gần như không dự báo kết quả (điểm 6.x thua điểm 4.x), AI từ chối 90% nhưng không chọn tốt hơn ngẫu nhiên, phiên 0–7h thắng 1/14, bộ tự học đổi tham số trên 13–20 mẫu rồi đổi ngược lại.
- **Chất lượng → khối lượng, không → chặn cứng**: `MIN_SCORE` 5.5 → 5.0, volume 0.8 → 0.7; mỗi lệnh có hệ số rủi ro theo điểm KT, AI, chuỗi thua (xem Bước 6). Lệnh "vừa đủ" vào nhỏ, lệnh đẹp vào lớn hơn.
- **Rủi ro cơ bản 3%/lệnh** thay vì 10%, cầu dao 12%/ngày và 30% drawdown, tối đa 2 lệnh với trần tổng 5%.
- **AI điều tiết thay vì phủ quyết**: đồng ý 50–59% vẫn vào nửa rủi ro (nguồn `ai_weak`, bộ tự học theo dõi riêng).
- **Trigger mới**: hồi về VWAP rồi bật (tính chung nhóm với pullback EMA21, không cộng trùng).
- **Chặn sẵn phiên 0h–7h** (`night_session`); bộ tự học gỡ nếu bóng có lãi.
- **Bộ tự học cần ý nghĩa thống kê** (t ≥ 1.3, ≥ 30 mẫu, TP1 ≥ 40 mẫu so theo cặp, 48h/lần đổi).
- **AI tổng kết nhận số liệu tính sẵn** (SL theo ATR, kết quả R, MFE) – trước đây bịa "SL 0,35 ATR" trong khi thật là 1,56 ATR rồi đưa bài học sai vào prompt sau.
- **Live an toàn hơn**: dời SL trên sàn = đặt lệnh mới trước rồi mới huỷ lệnh cũ (bản cũ huỷ trước – đặt lỗi là trống SL); sàn tự khớp SL/TP → ghi giá khớp thật từ lịch sử giao dịch thay vì ước lượng.
- Log `Thông số đang dùng` in giá trị **thực** (sau khi tự học ghi đè), không in mặc định nữa.

## Giao diện web & phiên bản (v3.0)
- **Trang**: Tổng quan (vốn, lệnh mở, đường vốn, nhật ký) · AI & Tự học · Cài đặt · Phiên bản. Đổi sáng/tối bằng nút trên đầu trang; tối ưu điện thoại.
- **Cài đặt → Thông báo & tin nhắn**: 4 công tắc – Telegram thông báo, Telegram trả lời lệnh, Zoom thông báo, Zoom trả lời lệnh. Lưu trong DB, khởi động lại vẫn giữ. Tắt chỉ làm im kênh, không xoá cấu hình.
- **Cài đặt → Zoom → Gửi thử thông báo** (hoặc lệnh `zoomtest`): bot thử lần lượt các cách gửi (địa chỉ lần trả lời thành công, `ZOOM_TO_JID`, JID đã ghép đôi có/không kèm `user_jid`…), nhớ cách Zoom nhận và dùng cách đó cho mọi thông báo sau.
- **Phiên bản**: số phiên bản, mã bản dựng (dấu vân tay toàn bộ code) và lịch sử. Mã bản dựng khác bản phát hành nghĩa là upload thiếu file hoặc lẫn file cũ. Khi lên bản mới bot tự nhắn "Bot đã cập nhật" kèm danh sách thay đổi; lệnh `phienban` xem trên Telegram/Zoom.
- Biến tuỳ chọn mới: `ZOOM_TO_JID` – JID nhận thông báo cố định (khi cần ép một địa chỉ).
- Phát hành bản mới (cho người sửa code): tăng `VERSION`, thêm `NOTES` trong `bot/version.py`, rồi `python -m bot.version --stamp`.
