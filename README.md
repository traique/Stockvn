# OKX AI Altcoin Futures Bot

Bot giao dịch hợp đồng tương lai (USDT-SWAP) altcoin trên OKX, đánh cả Long lẫn Short. Bộ lọc kỹ thuật khắt khe tự tìm tín hiệu; AI (qua **9Router** hoặc **OpenRouter**, model free) chỉ được gọi **2 lần cho mỗi lệnh**: 1 lần xác nhận trước khi vào, 1 lần tổng kết sau khi đóng. Có chế độ **chạy ảo (paper)**, Telegram, dashboard web. Chạy được trên **Botkeep.cloud** với runtime Python (không cần Docker).

> ⚠️ Future đòn bẩy với vốn nhỏ rủi ro rất cao, có thể mất toàn bộ vốn. Đây là công cụ, không phải lời khuyên đầu tư. Chỉ bật `live` sau khi đã xem kết quả 2 tuần chạy ảo.

## 1. Cách bot ra quyết định

**Bước 1 – Quét**: mỗi 5 phút lấy top 25 cặp USDT-SWAP có volume 24h lớn nhất trên OKX (loại BTC, ETH, stablecoin, token hoá cổ phiếu/vàng/dầu).

**Bước 2 – Cổng bắt buộc** (thiếu một cái là loại ngay, chỉ dùng nến đã đóng):

| Cổng | Điều kiện |
|---|---|
| Xu hướng khung 1h | EMA21 so với EMA50, giá so với EMA21, DI+/DI-, ADX 1h ≥ 18. Chỉ đánh **cùng chiều** khung 1h |
| Thị trường có xu hướng | ADX 15m ≥ 22 (lọc sideway) |
| Đủ biến động | ATR ≥ 0.35% giá |
| Có volume | Volume nến tín hiệu ≥ trung bình 20 nến |
| Không đuổi giá | Giá cách EMA21 không quá 2 ATR |
| RSI hợp lý | Long: 45–70, Short: 30–55 |
| Nến tín hiệu đúng màu | Long phải là nến tăng, Short phải là nến giảm |

**Bước 3 – Trigger** (phải có ít nhất 1 sự kiện trong 3 nến gần nhất): MACD cắt, EMA9 cắt EMA21, breakout đỉnh/đáy 20 nến kèm volume ×1.5, pullback về EMA21 rồi bật, nến nhấn chìm.

**Bước 4 – Chấm điểm hợp lưu**: EMA 9/21/50 xếp đúng thứ tự, đúng phía EMA200 và VWAP, ADX mạnh, MACD+RSI khung 1h đồng thuận, volume, nến đóng dứt khoát, funding; trừ điểm nếu coin đã chạy > 20% trong 24h. Cần ≥ 5 điểm và tỉ lệ lời/lỗ ≥ 1.5.

**Bước 5 – AI lần 1 (phân tích)**: tối đa 3 **tín hiệu tốt nhất** của lượt quét được gửi lần lượt cho AI, kèm 5 bài học gần nhất. AI đồng ý ≥ 60% → vào lệnh đủ rủi ro; đồng ý nhưng chỉ 50–59% → vẫn vào với **nửa rủi ro** (`AI_WEAK_CONFIDENCE`); phản đối yếu (dưới ngưỡng phủ quyết) với tín hiệu điểm ≥ 6.5 → vào nửa rủi ro; còn lại bỏ. (v3.1: trên dữ liệu đã có, AI từ chối 90% tín hiệu nhưng nhóm bị từ chối không thua nhóm được duyệt – AI điều tiết **khối lượng** thay vì chặn cứng.)

**Bước 6 – Vào & quản lý lệnh**: SL theo cấu trúc (đáy/đỉnh 6 nến ± 0.15 ATR, tối thiểu 1.5 ATR; xa hơn 2.5 ATR thì bỏ tín hiệu). Khối lượng tính để chạm SL mất `RISK_PER_TRADE` = **3% vốn × hệ số chất lượng** (đã gồm phí + trượt giá): điểm ≥ 7 và AI ≥ 80 được 1.5×, AI chưa chắc / phản đối yếu 0.5×, mỗi lệnh thua liên tiếp (từ lệnh thứ 2) nhân 0.7, kẹp 0.35–1.5×. x10 isolated, tối đa **2 lệnh** nhưng tổng rủi ro đang mở ≤ `MAX_TOTAL_RISK` = 5% vốn; lot tối thiểu của sàn làm rủi ro vượt 2× dự tính thì bỏ tín hiệu. Thoát lệnh: hybrid (chốt 50% ở TP1 do bộ tự học chọn, phần còn lại bám sóng) hoặc cách khác mà bộ tự học chọn.

**Bước 7 – AI lần 2 (tổng kết)**: khi lệnh đóng, AI đánh giá GOOD/BAD/LUCKY/UNLUCKY và rút 1–2 câu bài học. Bài học được lưu và đưa lại vào prompt của các lần phân tích sau → bot "học" dần từ chính lịch sử của nó. Xem bằng `/lessons` hoặc trên dashboard.

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

## 3b. Chạy song song trên Render free + Botkeep (dự phòng lẫn nhau)

Hai máy chạy cùng lúc nhưng **chỉ một máy giao dịch** (leader), máy kia đứng chờ (standby). Chúng dùng chung một cơ sở dữ liệu Postgres nên thấy cùng số dư, lệnh, lịch sử, bài học AI. Leader gia hạn "khoá" mỗi 20 giây; nếu leader chết/ngủ quá `LEADER_TTL` (90 giây) thì standby tự tiếp quản và báo Telegram. Hai máy ping `/health` của nhau mỗi 4 phút để Render free không bị ngủ (Render tắt web service free sau 15 phút không có request).

**Bước 1 – Tạo Postgres dùng chung trên Supabase (free)**

1. Vào supabase.com → New project → đặt tên, **Database password** (ghi lại; nên đặt chỉ gồm chữ + số để khỏi phải mã hoá URL), region gần Render nhất (Singapore nếu Render chọn Singapore).
2. Trong project bấm nút **Connect** (góc trên) → tab **Connection string** → chọn **Session pooler** (KHÔNG chọn "Direct connection": kết nối trực tiếp chỉ có IPv6, Render free không ra được IPv6 nên sẽ báo lỗi mạng).
3. Chuỗi có dạng:
   `postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres`
   Thay `[YOUR-PASSWORD]` bằng mật khẩu. Nếu mật khẩu có ký tự đặc biệt phải mã hoá URL (`@`→`%40`, `#`→`%23`, `/`→`%2F`, `:`→`%3A`).
4. Dán vào `DATABASE_URL` của **cả Render lẫn Botkeep**. Bot tự thêm `sslmode=require` và tự tắt prepared statement (bắt buộc với pooler của Supabase).
5. Kiểm tra trước khi deploy: `python check_db.py` (đọc `.env`) → phải in `✅ Kết nối OK`.
6. Bot tự tạo bảng khi chạy lần đầu, không cần làm gì trong SQL Editor. Dữ liệu xem được ở Supabase → Table Editor (bảng `trades`, `signals`, `reviews`, `locks`, `heartbeats`).

Lưu ý Supabase free: project bị **tạm dừng (pause) sau 7 ngày không có truy vấn** – bot chạy liên tục thì không bị; nếu anh tắt cả hai bot hơn 1 tuần thì vào dashboard Supabase bấm Restore rồi chạy lại. Giới hạn 500 MB dữ liệu là dư thừa (bot ghi vài MB/tháng). Nếu Render của anh ở region khác, chọn region Supabase tương ứng để giảm độ trễ (mỗi truy vấn vẫn chỉ vài chục ms, không ảnh hưởng giao dịch).

**Bước 2 – Render**: New → Web Service → kết nối repo GitHub (hoặc dùng file `render.yaml` qua New → Blueprint). Runtime Python, Build command `pip install -r requirements.txt`, Start command `python run.py`, plan Free. Environment: `DATABASE_URL` (chuỗi Supabase ở trên), `INSTANCE_NAME=render`, `MODE=paper`, Telegram, OpenRouter key, `AI_PROVIDER_ORDER=openrouter` (Render không chạy được 9Router cùng service), `PEER_URL=<URL public của Botkeep>` nếu có. Render tự cấp `PORT` và `RENDER_EXTERNAL_URL`, bot tự dùng.

**Bước 3 – Botkeep**: như mục 3, thêm Secrets `DATABASE_URL` (cùng chuỗi), `INSTANCE_NAME=botkeep`, `PEER_URL=https://<tên-service>.onrender.com`. Nếu có 9Router ở Botkeep thì để `AI_PROVIDER_ORDER=9router,openrouter` ở đây.

**Bước 4 – Kiểm tra**: mở dashboard của cả hai, góc trên hiển thị `render · LEADER` / `botkeep · STANDBY` và danh sách instance. Hoặc gõ `/status` trên Telegram. Tắt thử máy leader, sau ~1.5 phút máy kia sẽ nhắn "👑 … tiếp quản".

Lưu ý: Render free cho 750 giờ/tháng – đủ chạy 1 service 24/7; ổ đĩa Render bị xoá mỗi lần deploy nên **bắt buộc** dùng `DATABASE_URL` (Supabase) khi chạy trên Render. Không dùng Postgres free của Render (hết hạn sau 30 ngày). Telegram chỉ cho một máy nhận lệnh, bot đã xử lý: chỉ leader nhận và gửi tin.

### Chạy trên VPS/máy thường

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env
python run.py
```

## 4. Lệnh Telegram

`/status` vốn, PnL, lệnh mở, hạn mức AI · `/trades` 10 lệnh gần nhất · `/signals` tín hiệu & quyết định AI · `/ai` số lượt gọi từng model, quyết định gần nhất · `/lessons` tổng kết của AI · `/pause` `/resume` · `/close` đóng tất cả. Bot tự gửi báo cáo ngày lúc 0h theo `TIMEZONE` (mặc định giờ Việt Nam).

## 5. Quy trình 2 tuần ảo → thật

1. `MODE=paper` 14 ngày, không đổi tham số giữa chừng.
2. Cần ≥ 30 lệnh để đánh giá: winrate, profit factor > 1.3, drawdown chịu được. Với bộ lọc khắt khe, có thể mất 2–3 tuần mới đủ 30 lệnh – đó là bình thường.
3. (Nên) chạy thêm vài ngày `OKX_DEMO=true` với API key demo của OKX để kiểm tra đặt lệnh thật trên sàn ảo.
4. Chuyển thật: `MODE=live`, điền API key OKX, `I_UNDERSTAND_THE_RISK=yes`, xoá thư mục `data/` để reset thống kê, khởi động lại.

## 6. Tinh chỉnh

- Ít tín hiệu quá → hạ `MIN_SCORE` 5 → 4.5, `MIN_ADX` 22 → 20. Nhiều lệnh thua → tăng lên.
- `OPENROUTER_MODELS=auto` (mặc định): bot tự đọc danh sách model `:free` hiện còn trên OpenRouter, làm mới mỗi 12h; model trả 404 (bị gỡ) tự loại và dò lại ngay. `openrouter/free` luôn đứng cuối làm lưới an toàn.
- `AI_FAIL_POLICY`: khi mọi model lỗi – `score` vẫn vào nếu điểm KT ≥ `AI_FAIL_MIN_SCORE` (mặc định 7), `skip` bỏ qua. Để trống: chạy ảo = score, chạy thật = skip.
- `AI_VETO_CONF` / `AI_OVERRIDE_MIN_SCORE`: AI phản đối với độ chắc < ngưỡng (mặc định chạy ảo 80%, chạy thật 0%) thì tín hiệu điểm ≥ 6.5 vẫn vào, ghi nguồn `ai_override`. Bộ tự học so kết quả các tín hiệu AI phản đối để nâng/hạ ngưỡng ±5%/ngày.
- Phiên bản đang chạy hiện ở góc tiêu đề dashboard, `/health`, lệnh `status` và tin nhắn khởi động. Nếu Render và Botkeep chạy khác bản, nhãn phiên bản chuyển màu đỏ.
- `AI_MAX_CALLS_PER_DAY`: hạn mức tổng (phân tích + tổng kết), chỉ tính lượt AI trả lời thành công. `AI_REVIEW_ENABLED=false` nếu muốn tiết kiệm chỉ gọi 1 lần/lệnh.
- `AI_ENABLED=false`: chạy thuần kỹ thuật để so sánh.
- `RISK_PER_TRADE`, `LEVERAGE`: hạ nếu thấy chuỗi thua dài. Bot đã tự co rủi ro sau chuỗi thua (`LOSS_STREAK_CUT`) và theo chất lượng tín hiệu (`RISK_MIN_MULT`–`RISK_MAX_MULT`); `MAX_TOTAL_RISK` là trần cho mọi lệnh đang mở.


## 8. Ghi nhận dữ liệu & tự học

**Trên web** (cuộn xuống dưới dashboard):
- Tab **Hoạt động**: toàn bộ log của bot (giống console Botkeep), lọc theo mức INFO/WARNING/ERROR và tìm kiếm. Lưu 14 ngày.
- Tab **Lịch sử quét**: mỗi lượt quét bao nhiêu coin đạt, loại vì lý do gì. Lưu 30 ngày.
- Tab **Tín hiệu & AI**: mỗi tín hiệu kèm nguồn (đã vào lệnh / AI từ chối / không gửi AI / suýt đạt / bị luật chặn) và **kết quả thật** sau đó (TP / SL / hết giờ, tính bằng R).
- Thẻ **Tải dữ liệu**: tải CSV (mở được bằng Excel / Google Sheets, đúng tiếng Việt) hoặc sao lưu toàn bộ JSON.

**Tự học hoạt động thế nào:**
1. Mọi tín hiệu đã qua bộ lọc kỹ thuật, kể cả tín hiệu không vào lệnh, đều được theo dõi "bóng": bot xem nến 5 phút sau đó để biết nếu vào thì sẽ chạm TP hay SL trước. Kết quả tính bằng R (1R = khoảng cách tới SL), đã trừ phí.
2. Mỗi giờ bot thống kê theo nhóm: nguồn tín hiệu, loại trigger, luật lọc, khung giờ, điểm số. Khi một nhóm có đủ mẫu (mặc định 30) **và** kết quả khác 0 có ý nghĩa thống kê (|kỳ vọng| / sai số chuẩn ≥ `LEARN_MIN_T` = 1.3; so sánh cách thoát / mức TP1 theo từng cặp trên cùng tín hiệu, TP1 cần ≥ 40 mẫu), bot điều chỉnh:
   - bật/tắt luật chặn trong danh mục cố định (vd. "short khi RSI 1h < 32") – chặn ở bộ lọc thì không tốn lượt AI;
   - trọng số từng trigger (0,6 – 1,4);
   - ngưỡng điểm MIN_SCORE (4,0 – 7,0, mỗi lần ±0,5).
   Mỗi tham số chỉ đổi tối đa 1 lần/48h (`LEARN_COOLDOWN_H`). Luật đã chặn vẫn được theo dõi bóng, nên nếu nhóm đó tốt lên bot sẽ tự bỏ chặn. (v3.1: trước đây 13–20 mẫu đã đổi, trọng số trigger nhảy qua lại trong 1 ngày – nay không còn.)
3. Mỗi sáng (7h) AI đọc thống kê, viết nhận xét và gợi ý – **không tự áp dụng**, anh bấm "Áp dụng" nếu đồng ý. Tốn 1 lượt AI/ngày.
4. Chạy ảo: tự áp dụng. Chạy thật: chỉ đề xuất, anh duyệt trên web hoặc gõ `/apply` trên Telegram. Nút "Khôi phục mặc định" đưa mọi thứ về ban đầu.

Bot **không bao giờ tự sửa code** – chỉ chỉnh các tham số trong danh mục, có giới hạn và có lịch sử. Telegram: `/learn` xem trạng thái, `/apply` duyệt đề xuất.


## 9. Cách thoát lệnh – "ăn sóng" altcoin

Ba cách, chọn bằng `EXIT_MODE` (mặc định `auto` = bộ tự học chọn, khởi đầu là hybrid):

| Cách | Mô tả | Hợp với |
|---|---|---|
| `fixed` | TP cố định 3 ATR; lời 1,5R thì kéo SL về hoà vốn | Thị trường giật, sóng ngắn |
| `hybrid` | Chốt 50% khi lời 2R (khoá lợi nhuận, SL về hoà vốn); 50% còn lại **bám sóng**: SL = đỉnh/đáy kể từ khi vào ∓ 2,5 ATR, siết còn 1,5 ATR khi lời ≥ 4R; thoát nếu 4h không lập đỉnh mới | Sóng mạnh kéo dài |
| `trail` | Toàn bộ vị thế bám sóng, không chốt trước | Xu hướng rất mạnh, chấp nhận trả lại lãi |

Bot mô phỏng **song song cả 3 cách** trên mọi tín hiệu (bằng nến 5 phút thật sau tín hiệu), hiển thị so sánh trong thẻ "Tự học" → "So sánh cách thoát lệnh". Với `EXIT_MODE=auto`, khi có ≥ 20 tín hiệu và một cách hơn cách đang dùng ≥ 0,25R/tín hiệu thì bot tự chuyển (tối đa 1 lần/24h, có lịch sử, khôi phục được).

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

Bot **không chặn sẵn** luật nào. Mỗi luật được theo dõi kết quả bóng; chỉ khi một nhóm có đủ mẫu và thua rõ rệt (kỳ vọng < −0,25R) thì mới bị chặn – xem bảng "Theo luật lọc" trong thẻ Tự học.

## 7. Cấu trúc

```
run.py            – start command cho Botkeep/Render: tự cài thư viện, chạy & tự khởi động lại bot
render.yaml       – blueprint tạo Web Service free trên Render
bot/config.py     – đọc biến môi trường / .env
bot/exchange.py   – OKX qua ccxt
bot/indicators.py – EMA/RSI/ATR/MACD/BB/ADX/VWAP
bot/strategy.py   – cổng bắt buộc + trigger + chấm điểm hợp lưu
bot/ai.py         – 9Router/OpenRouter, 2 lần gọi/lệnh, hạn mức ngày, bài học
bot/risk.py       – khối lượng lệnh, cầu dao
bot/engine.py     – PaperEngine & LiveEngine
bot/storage.py    – SQLite (1 máy) hoặc Postgres dùng chung (nhiều máy) + khoá leader
bot/exits.py      – logic thoát lệnh (fixed/hybrid/trail) dùng chung cho engine & mô phỏng
bot/learner.py    – tự học: kết quả bóng, điều chỉnh tham số có giới hạn, AI tổng kết ngày
bot/market.py     – bối cảnh BTC, Open Interest, long/short, funding
bot/notify.py     – gửi thông báo đa kênh (Telegram, Zoom webhook/chatbot), nhận lệnh Zoom
bot/eventlog.py   – ghi log hoạt động vào DB để xem trên web
bot/telegram.py, bot/dashboard.py, bot/main.py
test_smoke.py     – quét thật OKX, in tín hiệu (không cần key)
test_engine.py    – kiểm tra toán PnL paper
botkeep.env       – các biến cần khai trên panel Botkeep (chỉ bí mật + thông tin riêng)
check_db.py       – kiểm tra kết nối Supabase trước khi deploy
```

## Tối ưu lợi nhuận kỳ vọng (v2.9)
Mục tiêu là **lợi nhuận kỳ vọng** = tỉ lệ thắng × lãi trung bình − tỉ lệ thua × lỗ trung bình, không chỉ tỉ lệ thắng.
- **Cổng BTC, chống râu nến, SL theo cấu trúc, trigger không cộng trùng, điểm dòng tiền trước xếp hạng**: giữ từ v2.8.
- **Không đuổi giá**: giá cách EMA21 quá `MAX_EXT_ATR` = 1 ATR thì bỏ. Backtest 10 ngày: nhóm cách hơn 1 ATR lỗ ở cả hai nửa dữ liệu.
- **Phiên 13h–19h (giờ VN)**: backtest thấy lỗ ở cả 4 giai đoạn nhưng mặc định KHÔNG chặn (v2.9.2). Bộ tự học theo dõi và tự chặn nếu thực tế lỗ (kỳ vọng < −0.25R trên đủ mẫu). Muốn chặn ngay: thêm `afternoon_session` vào `DEFAULT_BLOCKS`.
- **TP1 tự học**: mỗi tín hiệu được mô phỏng với TP1 = 1 / 1.3 / 1.6 / 2 / 2.5R; bot chọn mức có kỳ vọng cao nhất. Mặc định `TP1_R=1.0`.
- **Nhiều cơ hội hơn**: quét `TOP_N_COINS=50`; AI từ chối ứng viên #1 thì hỏi #2, #3 (`AI_CANDIDATES_PER_SCAN=3`).
- **Setup range** (đảo chiều tại biên Bollinger khi 1h đi ngang, TP = dải giữa): đã có, nhưng chặn sẵn (`setup_range`) vì chưa đủ dữ liệu chứng minh có lãi; bộ tự học gỡ khi có lãi.
- **AI**: đánh giá theo lợi nhuận kỳ vọng; `confidence` khi đồng ý = điểm chất lượng (50 = trung bình), duyệt khi ≥ `AI_MIN_CONFIDENCE` = 60.

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
