# Plan: Bot Altcoin và Cổ phiếu – sổ không AI, Grid spot, mốc ETF và phân tích trên điện thoại

29/11/2026 · kế hoạch đợt đo thứ hai (Altcoin 3.5.0, Stockvn v1.25.0). Phân tích đối chiếu với repo và các quyết định khi thực hiện ở cuối file.

## Bối cảnh và mục tiêu

Đợt đo này thêm các sổ paper và mốc so sánh để trả lời ba câu hỏi: AI có đang làm hại kết quả altcoin không, grid spot có tốt hơn futures theo xu hướng không, và bot cổ phiếu có hơn mua giữ ETF không. Toàn bộ là paper, không có tiền thật. Altcoin đo 4 tuần; cổ phiếu ít lệnh nên đo ít nhất 3 tháng.

Hiện trạng theo README của `altcoin/` (bản 3.4.0, 28/11/2026):

- 29 ngày paper, 59 lệnh: vốn 10,00 → 9,82 USDT, sụt 19,3% từ đỉnh, vượt ngưỡng dừng 15% của mục 5.4.
- R trung bình −0,04 ± 0,16; backtest 88 ngày −0,01R ± 0,07 mỗi lệnh sau phí.
- AI đoán ngược: tín hiệu AI từ chối +0,36R, lệnh AI duyệt −0,02R; Brier 0,345 so với 0,234 nếu đoán theo tỉ lệ nền.
- 1.335 tổ hợp tham số: xếp hạng trong mẫu và ngoài mẫu tương quan −0,56, dấu hiệu overfitting.

Mục tiêu của đợt này:

1. **Sổ “không AI” (Phần A):** cùng tín hiệu kỹ thuật, bỏ bước AI duyệt, để đo AI có giá trị không.
2. **Sổ Grid spot (Phần B):** grid không đòn bẩy trên BTC-USDT và ETH-USDT, so với giữ coin (HODL).
3. **Phân tích trên điện thoại (Phần C):** bot ghi bản sao lên Google Sheets, Claude trong app điện thoại viết báo cáo tuần, bạn quyết định.
4. **Cổ phiếu (Phần D):** thêm mốc ETF mua-giữ, tắt MUA breakout, đóng băng hiệu chỉnh đêm và đo AI thẩm định so với tỉ lệ nền.

## Nguyên tắc chung cho Claude Code

Mọi thay đổi chỉ thêm cách đo, không đổi chiến lược đang chạy và không chạm đường live.

- **Chỉ paper.** Hai sổ mới không bao giờ gọi API đặt lệnh của OKX, kể cả khi `MODE=live`. Chỉ dùng API công khai (giá, nến). Thêm test chứng minh điều này. Phần cổ phiếu giữ chế độ paper (TRADE\_MODE=paper) và vẫn không gửi lệnh tới CTCK.
- **Không đổi tham số chiến lược** (cổng, điểm, TP/SL, `MIN_SCORE`…) trong 4 tuần đo. Giữ `LEARN_MODE=suggest` (trong Stockvn: `ALT_LEARN_MODE=suggest`), không `/alt apply`.
- **Sổ hiện tại giữ nguyên hành vi.** Thêm sổ mới bằng cột `book` hoặc bảng riêng; dữ liệu cũ mặc định `book='main'`. Migration phải chạy được trên cả SQLite và Postgres (`ALT_DATABASE_URL`).
- **Hạ mặc định rủi ro trong `bot/config.py`:** `LEVERAGE` 10 → 3, `RISK_PER_TRADE` 0,03 → 0,01. Paper hiện tại đặt qua biến môi trường nên không đổi; việc này chỉ để tránh sơ suất khi sau này bật live. Ghi rõ trong changelog.
- **Tôn trọng quy trình phiên bản của repo:** nâng `bot/version.py`, chạy `cd altcoin && python -m bot.version --stamp`, cập nhật README mục tình trạng, thêm test vào `tests/suites/`.
- **Không tốn thêm lượt AI.** Sổ không AI và sổ Grid không gọi model nào; hạn mức AI dùng chung với Cổ phiếu giữ nguyên.
- **Không đưa khoá ra ngoài.** Dữ liệu ghi lên Google Sheets (Phần C) không chứa API key, token hay URL có mật khẩu; dùng `redact` có sẵn khi ghi log.

## Phần A: Sổ “không AI” chạy song song

Một `PaperEngine` thứ hai, `book='noai'`, nhận đúng danh sách ứng viên mà sổ chính nhận, nhưng vào lệnh không cần AI. Hai sổ cùng thị trường, cùng thời điểm, nên khác biệt chỉ đến từ AI.

**Điểm gắn vào:** trong `Bot.scan()` (`bot/main.py`), ngay sau khi các tín hiệu đã qua cổng và đạt `MIN_SCORE`, đã xếp theo điểm, và trước vòng gọi `self.ai.analyze(sig)` (quanh dòng 385–390). Danh sách này được chuyển cho sổ `noai` trước khi sổ chính hỏi AI.

**Luật của sổ `noai`:**

1. Vốn ban đầu bằng vốn paper của sổ chính lúc bắt đầu đợt đo (`NOAI_CAPITAL`, mặc định 10 USDT).
2. Còn chỗ (`MAX_OPEN_POSITIONS`, `MAX_TOTAL_RISK` như sổ chính) thì vào ứng viên điểm cao nhất chưa có vị thế cùng mã; mỗi lượt quét tối đa 1 lệnh, giống sổ chính.
3. Hệ số rủi ro cố định 1,0× (không có 0,85× / 1,2× theo độ tự tin AI).
4. Cùng cách tính cỡ lệnh, phí `TAKER_FEE`, trượt giá, `plan_exit` và `EXIT_MODE` như sổ chính; tái dùng `BaseEngine`, không chép code.
5. Có cầu dao riêng: sụt 15% từ đỉnh thì sổ `noai` ngừng mở lệnh mới và ghi sự kiện, không ảnh hưởng sổ chính.
6. Không gửi thông báo mở/đóng từng lệnh lên Telegram/Zoom; chỉ có trong báo cáo và trang web.

**Lưu trữ:** thêm cột `book TEXT DEFAULT 'main'` vào `trades` và `equity`; mọi truy vấn của sổ chính lọc `book='main'` để dashboard, `/alt status`, bộ tự học và cầu dao cũ không bị lẫn số liệu. Mỗi lệnh `noai` ghi thêm điểm kỹ thuật và, nếu sổ chính có hỏi AI về cùng tín hiệu, phán quyết AI đó (để phân tích sau, không dùng để quyết định).

**Hiển thị:** thêm bảng so sánh trên dashboard `/altcoin/` và lệnh `/alt compare`: số lệnh, R trung bình, sai số chuẩn theo ngày, tỉ lệ thắng, vốn, sụt tối đa, tổng phí cho từng sổ.

**Test cần có:** sổ `noai` không gọi `ai.analyze`; sổ chính cho cùng chuỗi tín hiệu ra kết quả y hệt trước khi có sổ `noai`; migration giữ nguyên dữ liệu cũ; `MODE=live` vẫn không đặt lệnh thật nào từ sổ `noai`.

## Phần B: Sổ Grid spot paper

Một module mới `bot/grid.py` giả lập grid spot không đòn bẩy trên BTC-USDT và ETH-USDT, mỗi cặp một sổ riêng, với tham số cố định trong 4 tuần. Nó chạy trong cùng tiến trình Altcoin, trên một luồng riêng, và không dùng chung vốn với sổ futures.

**Cấu hình (biến môi trường, trong Stockvn có tiền tố `ALT_`):**

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `GRID_ENABLED` | false | Bật sổ Grid |
| `GRID_PAIRS` | BTC-USDT,ETH-USDT | Cặp spot, mỗi cặp một sổ |
| `GRID_CAPITAL` | 100 | Vốn ảo mỗi sổ (USDT) |
| `GRID_LEVELS` | 20 | Số mức giá |
| `GRID_RANGE_DAYS` | 30 | Vùng giá = đáy/đỉnh nến ngày đã đóng trong N ngày |
| `GRID_SPACING` | geometric | Cách chia lưới: geometric hoặc arithmetic |
| `GRID_MAKER_FEE` | 0.0008 | Phí maker spot (0,08%, cần kiểm tra lại theo cấp tài khoản OKX) |
| `GRID_STOP_OUT_PCT` | 0.05 | Giá ra khỏi vùng quá 5% thì dừng sổ |

**Cách chạy:**

1. Khởi tạo: lấy nến 1D từ API công khai, tính vùng giá, chia `GRID_LEVELS` mức. Đặt lệnh mua ảo ở các mức dưới giá hiện tại; mua sẵn lượng coin tương ứng các mức bán phía trên (như grid spot thật của OKX). Ghi lại giá bắt đầu để tính HODL.
2. Mỗi phút: đọc nến 1m đã đóng. Một mức chỉ khớp khi giá **đi qua** nó (low ≤ mức mua, high ≥ mức bán), không khớp khi chỉ chạm sát, để tránh lạc quan. Khớp mua thì đặt lệnh bán ở mức ngay trên, và ngược lại.
3. Nến 1m chạm nhiều mức cùng lúc: xử lý theo thứ tự giá (nến tăng: low trước rồi high; nến giảm: high trước rồi low), mỗi mức tối đa một lần mỗi nến.
4. Mất kết nối: khi chạy lại, lấy bù nến 1m bị thiếu rồi xử lý tuần tự, không bỏ qua khoảng trống.
5. Dừng: giá đóng nến 1m ra ngoài vùng quá `GRID_STOP_OUT_PCT` thì sổ dừng, giữ nguyên coin/USDT đang có, ghi sự kiện và gửi một thông báo “🪙 Altcoin › Grid dừng”. **Không tự đặt lại vùng**; việc dời vùng do người quyết định (Phần C) và ghi thành một “vòng” mới.

**Lưu trữ:** bảng `grid_runs` (mỗi vòng: cặp, vùng, số mức, vốn, giá bắt đầu, lúc bắt đầu/kết thúc, lý do dừng), `grid_fills` (mỗi lần khớp: thời điểm, mức, chiều, lượng, phí) và `grid_equity` (mỗi 15 phút: USDT, coin, giá, giá trị sổ, giá trị nếu HODL).

**Chỉ số cần tính:** lãi grid đã khóa (các cặp mua–bán đã khớp, trừ phí), lãi/lỗ tồn kho coin, tổng giá trị sổ so với HODL 100% coin và so với giữ 100% USDT, sụt tối đa, số vòng giao dịch mỗi ngày, tỉ lệ thời gian giá nằm trong vùng.

**Hiển thị:** thẻ Grid trên dashboard và lệnh `/alt grid` (chỉ đọc).

**Test cần có:** chuỗi nến giả đi ngang cho lãi grid dương đúng số vòng; nến chỉ chạm sát mức không khớp; giá phá vùng thì dừng và không đặt thêm lệnh; lấy bù nến sau khi mất kết nối cho cùng kết quả như chạy liên tục; không có lệnh gọi nào tới API đặt lệnh.

## Phần C: Phân tích bằng Claude trên điện thoại (Google Sheets)

Bot ghi một bản sao dữ liệu lên một bảng tính Google Sheets; mỗi Chủ nhật bạn nhắn Claude trong app điện thoại để đọc bảng tính và viết báo cáo tuần; mọi thay đổi cấu hình do bạn tự áp dụng. Không cần máy tính: bot chạy trên Botkeep, Claude Code nhận việc từ app Claude, Claude đọc Sheets qua connector Google đã kết nối.

### C1. Bot ghi lên Google Sheets (việc của Claude Code)

Một bảng tính chung tên `BotLab`. Altcoin và Cổ phiếu mỗi bên tự ghi các tab của mình (`altcoin/bot/sheets.py`, `vnbot/sheets.py`), để một bên lỗi thì bên kia vẫn chạy, đúng cách repo tách hai phần.

| Tab | Bên ghi | Nội dung | Khi nào |
| --- | --- | --- | --- |
| `alt_trades` | Altcoin | Lệnh đã đóng của sổ `main` và `noai`: R, phí, lý do thoát, phán quyết AI nếu có | Mỗi giờ |
| `alt_signals` | Altcoin | Tín hiệu và tín hiệu bóng, kèm nguồn (`blocked`, `slot_taken`…) | Mỗi giờ |
| `alt_equity` | Altcoin | Vốn mỗi 15 phút của `main`, `noai`, từng sổ Grid và đường HODL | Mỗi giờ (gộp) |
| `grid_runs`, `grid_fills` | Altcoin | Vòng grid và từng lần khớp | Mỗi giờ |
| `alt_summary` | Altcoin | Chỉ số tính sẵn theo sổ, phiên bản, tham số đang chạy | 07:05 hằng ngày |
| `ck_nav` | Cổ phiếu | NAV ba sổ ảo, VNINDEX và ETF mua-giữ (Phần D) | 16:30 ngày giao dịch |
| `ck_trades` | Cổ phiếu | Lệnh đã đóng của ba sổ ảo, R sau phí và thuế | 16:30 |
| `ck_shadow` | Cổ phiếu | Kết quả bóng, nhất là nhóm “MUA breakout bị tắt” | 16:30 |
| `ck_ai_pick` | Cổ phiếu | Lượt AI thẩm định và kết quả 10 phiên sau | 16:30 |
| `ck_summary` | Cổ phiếu | Chỉ số tính sẵn, ảnh chụp `quant_params.json` | 16:30 |
| `proposals` | Claude | Đề xuất kèm lý do, trạng thái “chờ duyệt” | Khi có đề xuất |

Luật ghi:

- **Sheets chỉ là bản sao.** Database chính vẫn là SQLite/Postgres hiện tại. Ghi Sheets lỗi thì xếp hàng, thử lại lượt sau và ghi sự kiện; không bao giờ làm dừng hay chậm bot.
- **Ghi gộp, không trùng.** Mỗi tab tối đa một lần gọi mỗi lượt (append nhiều dòng), dưới hạn mức khoảng 60 lần ghi mỗi phút. Mỗi dòng có khóa; bot nhớ khóa cuối đã ghi (bảng `kv`) nên chạy lại không ghi trùng.
- **Không lưu nến 1 phút**, để cách xa giới hạn 10 triệu ô mỗi bảng tính.
- **Xác thực bằng service account:** `SHEETS_ID` và `SHEETS_SA_JSON` (cổ phiếu), `ALT_SHEETS_ID` và `ALT_SHEETS_SA_JSON` (altcoin, qua allowlist `ALT_` của `altcoin_host`). Thiếu biến thì tính năng tắt, bot chạy như cũ.
- **Không đọc ngược.** Bot không bao giờ đọc tab `proposals` hay bất kỳ cấu hình nào từ Sheets.

### C2. Project trong app Claude

Tạo một Project tên `BotLab` trong app Claude trên điện thoại, dán mẫu hướng dẫn ở C4 vào phần hướng dẫn của Project, kèm link bảng tính. Từ đó mỗi tuần chỉ cần một câu ngắn.

### C3. Quy trình mỗi tuần

1. Chủ nhật, mở Project `BotLab`, nhắn “Viết báo cáo tuần”.
2. Claude đọc các tab qua connector Google Sheets, tính lại chỉ số bằng code và đối chiếu với `alt_summary` / `ck_summary`; lệch nhau thì báo lỗi thay vì chọn một số.
3. Claude viết báo cáo tuần thành một doc (đọc và bình luận được trên điện thoại) và ghi đề xuất, nếu có, vào tab `proposals`.
4. Trong đợt đo, Claude **chỉ báo cáo**, không đề xuất chỉnh tham số. Ngoại lệ duy nhất: sổ Grid đã dừng vì phá vùng thì đề xuất vùng mới theo đúng luật `GRID_RANGE_DAYS`.
5. Bạn đọc trên điện thoại; đồng ý thì tự áp dụng (đổi biến trên panel Botkeep rồi khởi động lại, hoặc lệnh `/alt` tương ứng), rồi đổi trạng thái dòng đề xuất thành “đã áp dụng” hoặc “bỏ”.

### C4. Mẫu hướng dẫn cho Project

```markdown
Bạn là nhà phân tích cho bot Altcoin và Cổ phiếu (chỉ paper).
- Dữ liệu: bảng tính BotLab <dán link>. Chỉ được ghi vào tab proposals.
- Tính mọi số bằng code, sai số chuẩn theo NGÀY (tín hiệu dồn cụm).
- Altcoin: main vs noai; Grid từng cặp vs HODL vs giữ USDT.
- Cổ phiếu: ba sổ ảo vs ETF mua-giữ vs VNINDEX; nhóm breakout bị tắt;
  AI thẩm định so với tỉ lệ nền.
- Luôn ghi cỡ mẫu; dưới ngưỡng thì viết "chưa đủ dữ liệu".
- Không đề xuất chỉnh tham số trước khi hết đợt đo, trừ vùng mới
  cho grid đã dừng.
- Không đảo ngược AI, không tìm tham số "tốt nhất" trên dữ liệu vừa qua.
- Kết quả: một doc "Báo cáo tuần NN"; đề xuất: thêm dòng vào tab proposals.
- Mỗi số đều ghi nguồn: tab và cột, hoặc công thức đã tính. Kết quả mô phỏng
  không được gọi là "đã kiểm chứng", "ổn định" hay "không rủi ro".
- Báo tin xấu trước, tin tốt sau. Lãi lỗ ghi cả tiền và R, sau phí.
- Mỗi tuần một thẻ cho mỗi sổ (main, noai, từng cặp grid; ba sổ cổ phiếu
  và ETF) theo mẫu dưới; tuần sau chỉ cập nhật số, không viết lại thẻ.
- Cuối báo cáo: một "sổ bài học" – điều gì đúng, điều gì sai so với tuần
  trước, và luật đang chạy nào bị dữ liệu phản bác (kèm cỡ mẫu và t).
- Tín hiệu trong cửa sổ CPI/FOMC (cột event của alt_signals, luật bóng
  event_window): báo riêng R của nhóm này so với phần còn lại, không đề xuất chặn
  trước khi đủ 30 mẫu trên 7 ngày và |t| ≥ 2 (luật của bộ tự học).

Thẻ sổ (một thẻ mỗi sổ, mỗi tuần):
【Tên sổ】【một câu về luật vào/ra đang chạy】
- Lợi thế: 【vì sao nó phải có lãi, ai đang trả tiền cho mình】
- Tuần này: 【số lệnh】, 【R trung bình ± sai số theo ngày】, 【thắng %】,
  【lãi/lỗ sau phí】, 【sụt tối đa】, 【phí / lãi gộp】
- Từ đầu đợt: 【cùng các số trên】, 【so với mốc: noai/HODL/USDT/ETF/VNINDEX】
- Nhóm đáng chú ý: 【ví dụ long khi BTC ngày giảm, cửa sổ sự kiện】 kèm n và t
- Nó sẽ thua trong thị trường nào: 【một câu】; tuần này có đang ở đó không
- Kết luận: 【tiếp tục đo / chưa đủ dữ liệu / đạt ngưỡng dừng theo bảng tiêu chí】

Báo cáo tuần (thứ tự cố định):
1. Ba câu đầu: điều tệ nhất tuần, điều tốt nhất, một việc cần bạn quyết.
2. Cảnh báo: cầu dao, lỗi ghi Sheets, tham số đổi giữa đợt (ck_summary),
   grid dừng, lệch số giữa tính lại và alt_summary/ck_summary.
3. Các thẻ sổ.
4. Sổ bài học.
5. Đề xuất (chỉ ghi vào tab proposals, kèm lý do và số).
```

## Phần D: Phần cổ phiếu

Phần cổ phiếu giữ nguyên kiến trúc hiện tại (Quant Engine quyết định bằng code, AI chỉ diễn giải, Output Guard kiểm tra, không gửi lệnh tới CTCK). Đợt này chỉ thêm mốc so sánh ETF, đóng băng tham số và đo AI, để trả lời một câu: bot có hơn mua và giữ một quỹ ETF sau phí và thuế không.

Hiện trạng theo tài liệu trong repo (`README.md`, `docs/chandoan_2026-10-28/`, `docs/BAO_CAO_RA_SOAT_2026-10-23.md`):

- **Phòng thủ:** 15,6%/năm giai đoạn 2013–10/2026, nhưng đoạn kiểm tra 2024–2026 chỉ 8,1%/năm, thua VNINDEX 15,9%. Số liệu từ bộ kiểm định không có trong repo nên chưa tái lập được.
- **Động lượng:** trên rổ point-in-time, sụt tối đa 33–49%.
- **Năm gần nhất (tới 27/10/2026):** 15 lệnh MUA thật, 13 chạm cắt lỗ, trung bình −0,90R. VNINDEX +4,7% nhưng mã VN100 trung vị −10,7%.
- **Breakout lỗ ở cả hai giai đoạn** (−0,61R và −0,86R); pullback +0,32R trước năm cuối. Bộ lọc thị trường chặn đúng: lệnh bị chặn còn lỗ hơn.
- **Độ tin cậy không phải xác suất thắng:** nhóm 0,75–0,85 không hơn VNINDEX sau 10 phiên.

Việc cần làm:

1. **Mốc ETF mua-giữ (code).** Thêm một sổ so sánh: toàn bộ vốn ảo mua một quỹ ETF (`BENCH_ETF`, mặc định E1VFVN30) từ ngày bắt đầu đợt đo, giá điều chỉnh (có cổ tức), cùng mức phí và thuế bán 0,1% mà sổ ảo đang dùng. Hiện trên trang `/bot`, trong `/danhmuc` và tab `ck_nav`. So với ETF loại bỏ phần lệch 1,5–2,5 điểm %/năm khi so với VNINDEX (chỉ số giá) mà README đã ghi.
2. **Tắt MUA breakout (việc của bạn).** Bấm “Tắt MUA breakout” trên trang `/bot`. Không cần sửa code: repo đã chấm kết quả bóng cho nhóm bị tắt; chỉ bật lại theo luật của repo (≥ 30 mẫu và |t| ≥ 2).
3. **Đóng băng hiệu chỉnh đêm.** Bạn đặt `AUTO_CALIBRATE=0`; code ghi ảnh chụp `quant_params.json` vào `ck_summary` lúc bắt đầu và cảnh báo nếu file đổi giữa đợt.
4. **Đo AI thẩm định so với tỉ lệ nền (code).** Mỗi ngày có lượt AI: lợi nhuận 10 phiên của mã AI chọn trừ trung bình mọi ứng viên cùng ngày. Nếu AI trả độ tự tin dạng xác suất thì thêm Brier score và bảng hiệu chuẩn theo nhóm, như phía altcoin. Hiện trên trang `/ai` và tab `ck_ai_pick`. AI vẫn không đổi lệnh nào.
5. **Giữ `TRADE_MODE=paper`.** Không dùng chế độ `signal` cho tiền thật trong đợt đo.
6. **(Tuỳ chọn) Tái lập được số liệu.** Đưa code dựng rổ point-in-time và bộ kiểm định vào `research/` để con số của Phòng thủ chạy lại được và không thiên lệch sống sót. Việc lớn, làm sau, không chặn các việc trên.

**Thời gian đo khác altcoin:** thị trường đang risk-off nên bot cổ phiếu rất ít lệnh; 4 tuần không đủ để kết luận. Báo cáo tuần vẫn theo dõi, nhưng quyết định cho cổ phiếu đợi ít nhất 3 tháng.

**Test cần có:** NAV ETF đúng với phí, thuế, ngày nghỉ và ngày điều chỉnh giá; chỉ số AI so với tỉ lệ nền trên dữ liệu giả; `AUTO_CALIBRATE=0` thì không tham số nào đổi; xuất Sheets không ghi trùng.

## Tiêu chí đánh giá: altcoin sau 4 tuần, cổ phiếu sau 3 tháng

Mỗi câu hỏi có cỡ mẫu tối thiểu và luật quyết định đặt trước; chưa đủ mẫu thì kéo dài thêm, không kết luận sớm. Sai số chuẩn luôn tính theo ngày.

| Câu hỏi | Chỉ số | Mẫu tối thiểu | Quyết định |
| --- | --- | --- | --- |
| AI có giúp altcoin không? | Chênh R trung bình mỗi lệnh: noai − main | ≥ 30 lệnh mỗi sổ, ≥ 3 tuần | noai tốt hơn hoặc ngang → bỏ AI khỏi quyết định vào lệnh, tiết kiệm lượt gọi. main tốt hơn hơn 2 sai số chuẩn → giữ AI |
| Chiến lược futures có lợi thế không? | R trung bình sổ tốt hơn trong hai sổ | ≥ 30 lệnh và ≥ 150 tín hiệu bóng mới | Theo mục 5 README altcoin: R < −0,1 và cận trên 95% < 0, hoặc sụt > 15% → dừng futures, thiết kế lại |
| Grid có đáng không? | Giá trị sổ grid so với HODL và với giữ USDT | 4 tuần mỗi cặp | Chỉ cân nhắc demo OKX khi grid hơn cả hai mốc và sụt tối đa nhỏ hơn HODL |
| Phí ăn bao nhiêu? | Tổng phí / tổng lãi gộp mỗi sổ | Cả đợt | Phí > 50% lãi gộp → ưu tiên khung lớn hơn (1h/4h) hoặc lệnh maker trong vòng thiết kế sau |
| Bot cổ phiếu có hơn ETF không? | NAV từng sổ ảo so với NAV ETF mua-giữ, sau phí và thuế | ≥ 3 tháng | Thua ETF → không bật chế độ signal cho tiền thật |
| AI thẩm định cổ phiếu có chọn tốt hơn không? | Lợi nhuận 10 phiên của mã AI chọn trừ trung bình ứng viên cùng ngày (thêm Brier nếu có xác suất) | ≥ 30 lượt chọn | Không hơn tỉ lệ nền → AI chỉ là ghi chú, không đưa vào quyết định |
| Có bật lại MUA breakout không? | R của nhóm “MUA breakout bị tắt” (kết quả bóng) | ≥ 30 mẫu, trị tuyệt đối của t ≥ 2 | Chỉ bật lại khi R dương có ý nghĩa, đúng luật của repo |

Lưu ý khi đọc: 4 tuần chỉ là một đoạn thị trường. Grid thắng trong tháng đi ngang có thể thua nặng trong tháng giảm mạnh, nên báo cáo cuối phải ghi rõ xu hướng BTC trong đợt đo. Không sổ nào chuyển sang tiền thật trong đợt này; bước kế tiếp tốt nhất cũng chỉ là `OKX_DEMO=true`.

## Thứ tự thực hiện và checklist cho Claude Code

Làm theo năm PR nhỏ (thêm một PR tuỳ chọn), mỗi PR có test và chạy paper ít nhất một ngày trước khi làm PR kế. Giao việc cho Claude Code ngay trong app Claude trên điện thoại: chọn repo `traique/stockvn`, dán nội dung doc này (xuất ra Markdown), yêu cầu đọc `README.md`, `altcoin/README.md` và `docs/ALTCOIN_TRONG_STOCKVN.md` trước khi sửa. Duyệt và gộp PR trong app GitHub.

**PR 1 – Nền tảng (nửa ngày)**

- [ ] Migration cột `book` cho `trades`, `equity` (SQLite + Postgres), dữ liệu cũ = `main`
- [ ] Mọi truy vấn sổ chính lọc `book='main'`
- [ ] Hạ mặc định `LEVERAGE` 3, `RISK_PER_TRADE` 0,01; ghi changelog
- [ ] Test: sổ chính cho kết quả y hệt trước migration

**PR 2 – Sổ không AI (Phần A)**

- [ ] Sổ `noai` nhận danh sách ứng viên trong `Bot.scan()` trước bước AI
- [ ] Cầu dao 15% riêng, không gửi thông báo từng lệnh
- [ ] Bảng so sánh trên dashboard + `/alt compare`
- [ ] Test: không gọi AI, không đặt lệnh thật khi `MODE=live`

**PR 3 – Sổ Grid (Phần B)**

- [ ] `bot/grid.py`, ba bảng `grid_runs`, `grid_fills`, `grid_equity`
- [ ] Khớp lệnh theo nến 1m đã đóng, lấy bù nến khi mất kết nối, dừng khi phá vùng
- [ ] Thẻ Grid trên dashboard + `/alt grid`
- [ ] Test theo danh sách ở Phần B

**PR 4 – Ghi Google Sheets phía altcoin (Phần C1)**

- [ ] `altcoin/bot/sheets.py`: tab `alt_trades`, `alt_signals`, `alt_equity`, `grid_runs`, `grid_fills` mỗi giờ; `alt_summary` lúc 07:05
- [ ] Biến `ALT_SHEETS_ID`, `ALT_SHEETS_SA_JSON` qua allowlist của `altcoin_host`; thiếu biến thì tính năng tắt
- [ ] Ghi gộp, không trùng (khóa cuối lưu trong `kv`), lỗi thì thử lại, không làm dừng bot
- [ ] Test: dữ liệu ghi không chứa khoá, token hay mật khẩu; chạy lại không ghi trùng

**PR 5 – Cổ phiếu (Phần D và C1)**

- [ ] Sổ so sánh ETF mua-giữ (`BENCH_ETF`), hiện trên `/bot` và `/danhmuc`
- [ ] Đo AI thẩm định so với tỉ lệ nền (thêm Brier nếu có xác suất), hiện trên `/ai`
- [ ] Ảnh chụp `quant_params.json` lúc bắt đầu, cảnh báo nếu đổi giữa đợt
- [ ] `vnbot/sheets.py`: tab `ck_nav`, `ck_trades`, `ck_shadow`, `ck_ai_pick`, `ck_summary` lúc 16:30 ngày giao dịch
- [ ] Test theo danh sách ở Phần D

**PR 6 – (Tuỳ chọn) Bộ kiểm định point-in-time**

- [ ] Đưa code dựng rổ point-in-time và bộ kiểm định vào `research/`, kèm cách chạy lại

**Việc của bạn (không phải Claude Code)**

- [ ] Tạo bảng tính `BotLab` trên Google Sheets
- [ ] Tạo service account trên Google Cloud (trình duyệt điện thoại, bật “trang web cho máy tính”), bật Google Sheets API, chia sẻ bảng tính cho email của service account với quyền chỉnh sửa
- [ ] Dán JSON key vào `SHEETS_SA_JSON` và `ALT_SHEETS_SA_JSON` trên panel Botkeep, cùng `SHEETS_ID` và `ALT_SHEETS_ID`; không dán key vào chat
- [ ] Đặt `ALT_LEARN_MODE=suggest`, `ALT_GRID_ENABLED=true`, `AUTO_CALIBRATE=0`; tắt MUA breakout trên trang `/bot`; khởi động lại
- [ ] Tạo Project `BotLab` trong app Claude, dán hướng dẫn C4 và link bảng tính
- [ ] Mỗi Chủ nhật nhắn Project “Viết báo cáo tuần”
- [ ] Hết tuần 4: quyết định phần altcoin; hết tháng 3: quyết định phần cổ phiếu, theo bảng tiêu chí

## Phụ lục: đối chiếu với repo và quyết định khi thực hiện (29/11/2026)

- **Mặc định rủi ro:** hạ `LEVERAGE` / `RISK_PER_TRADE` chỉ khi `MODE=live` (x3, 1%); chạy ảo giữ x10 / 3% vì tài liệu vận hành bảo xoá `ALT_RISK_PER_TRADE`, `ALT_LEVERAGE` khỏi panel – đổi mặc định paper là đổi sổ đang đo.
- **Mốc đỉnh cầu dao:** `peak_equity` lưu vĩnh viễn trong kv là đỉnh trước đợt sụt 19,3%; bản 3.5.0 ghi kv `period` và đặt lại mốc đỉnh về vốn lúc khởi động để sổ chính và sổ noai cùng điều kiện.
- **So sánh hai sổ:** thêm hiệu theo cặp tín hiệu (cùng `sid`), không chỉ chênh trung bình; từ 3.4 AI gần như không chặn nên khác biệt phần lớn là hệ số rủi ro. Sổ noai vào ở giá lúc quét, sổ chính vẫn vào ở giá mới sau khi AI trả lời (độ trễ AI tính vào chi phí của AI). Sổ chính dừng vì cầu dao thì sổ noai vẫn nhận ứng viên.
- **Lệnh `/alt compare`, `/alt grid`, `/alt sheets`:** thêm ở ba nơi (`vnbot/altcoin_bridge.READ`, `altcoin/hosted.COMMANDS`, `Bot.handle_command`).
- **Grid:** vòng mới không tự mở; tăng `ALT_GRID_ROUND` trên panel rồi khởi động lại. Giá bắt đầu lấy từ ticker (API công khai). Lấy bù nến tối đa 20.000 nến 1m (~14 ngày).
- **Sheets:** không thêm thư viện Google; JWT RS256 bằng `cryptography`, REST v4 bằng `requests`; `ALT_SHEETS_*` tự đi qua allowlist `ALT_` (không sửa `altcoin_host`). Dòng tiêu đề đi cùng lần ghi đầu của mỗi tab.
- **Cổ phiếu:** mốc ETF mua ở giá đóng cửa phiên bắt đầu, phí mua 0,15% + phí bán 0,15% + thuế 0,1% như sổ ảo; AI thẩm định dùng mốc 20/40 phiên sẵn có của `ai_pick` (thêm Brier và bảng hiệu chuẩn); ảnh chụp `quant_params.json` chỉ so các tham số hiệu chỉnh đêm có thể đổi (nút "Tắt MUA breakout" không gây cảnh báo); Sheets ghi lúc 18:05 (sau nến muộn 18:00 và các lần thử lại của AI thẩm định).
- **Test:** script mới của altcoin phải thêm vào tuple `SCRIPTS` của `tests/suites/suite_altcoin_scripts.py` (đã thêm `test_books`, `test_grid`, `test_sheets`, `test_calendar`).
- **Lấy từ mẫu "một người AI trading desk" (29/11, sau khi đối chiếu):** chỉ hai ý repo chưa có – (1) cửa sổ sự kiện vĩ mô: Altcoin 3.5.1 thêm lịch CPI/FOMC (`bot/calendar.py`) và luật **bóng** `event_window` (±30 phút), chỉ đo, không chặn, bộ tự học xét như mọi luật ứng viên; (2) mẫu thẻ sổ và sổ bài học cho báo cáo tuần (C4 ở trên). Không theo: điều kiện lên tiền thật "30 lệnh mô phỏng" (mục 5 README altcoin chặt hơn), thử 20 ý chiến lược một ngày (overfitting – chẩn đoán 28/11), kiểm tra sức khoẻ mỗi giờ để khi bật live.
- **PR 6 (bộ kiểm định point-in-time, 29/11):** `research/pit/` – thành phần HOSE từng phiên theo VNDirect (từ 2013), giá VCI, luật rổ của chính `factor.py`, chạy bằng `factor.run` và `momentum.decide/plan`. Phòng thủ 2013–11/2026: 16,0% / −23,0% / Sharpe 1,17, khớp bộ kiểm định ngoài 15/10 (15,6%); TEST 7,7% thua VNINDEX 14,0%; thiên lệch sống sót +0,3 điểm %/năm. Hai phương án treo từ 23/10 không tốt hơn ở cả TRAIN lẫn VALID → giữ `FACTOR_FIXED`. Kết quả: `research/README.md`, `research/KET_QUA.md`.
