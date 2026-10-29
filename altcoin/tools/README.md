# Công cụ phân tích (không phải code của bot)

Các script ở đây **không** được bot import và **không** nằm trong bản dựng (`bot/version.py` chỉ tính `bot/*.py` và `run.py`).
Chạy trên máy tính hoặc VPS, không chạy trên Botkeep.

## Đánh giá Kronos trên tín hiệu của bot

[Kronos](https://github.com/shiyu-coder/Kronos) là mô hình nền tảng dự báo nến (MIT). Hai script này kiểm tra xem nó có
dự báo được kết quả các tín hiệu bot đã tạo ra hay không, trước khi tính chuyện tích hợp.

**Cách làm**: với mỗi tín hiệu đã có kết quả bóng, lấy 400 nến 15m đã đóng *trước* thời điểm tín hiệu (không nhìn tương lai),
cho Kronos sinh 24 kịch bản 16 nến (4h), tính xác suất chạm +1R trước SL (`p_tp1`, `p_sl`) và kỳ vọng di chuyển theo R,
rồi so với `outcome_r` thật: tương quan Spearman + kiểm định hoán vị, chia nhóm, so với quyết định AI, hiệu chuẩn.

### Chạy

```bash
git clone https://github.com/shiyu-coder/Kronos.git ../Kronos
python -m venv kvenv && source kvenv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cpu torch
pip install -r tools/requirements-kronos.txt

# file JSON: dashboard → Tải dữ liệu → sao lưu toàn bộ
python tools/kronos_eval.py --kronos ../Kronos --data okxbot-all.json --out res_mini.json \
    --model NeoQuasar/Kronos-mini --tok NeoQuasar/Kronos-Tokenizer-2k --ctx 2048
python tools/kronos_analyze.py res_mini.json

# Kronos-small (chậm ~5×) – chia 2 tiến trình song song rồi gộp
for i in 0 1; do python tools/kronos_eval.py --kronos ../Kronos --data okxbot-all.json --shard $i/2 \
    --out res_small_$i.json --model NeoQuasar/Kronos-small --tok NeoQuasar/Kronos-Tokenizer-base --ctx 512 & done; wait
python tools/kronos_analyze.py res_small_0.json res_small_1.json
```

Cần mạng tới OKX (nến lịch sử, API công khai) và HuggingFace (tải model lần đầu).

### Kết quả lần 1 (24/10/2026 – 80 tín hiệu từ 20–23/10, bot v2.9.6–v3.0.2)

| | Kronos-mini | Kronos-small |
|---|---|---|
| Spearman(p_tp1 − p_sl, kết quả) | −0,08 (p = 0,46) | +0,08 (p = 0,51) |
| Kronos ủng hộ | −0,25R/lệnh (n = 26) | +0,06R/lệnh (n = 25) |
| Kronos phản đối | −0,05R/lệnh (n = 54) | −0,19R/lệnh (n = 55) |
| Thời gian / tín hiệu (1 luồng CPU) | 20 s | 96 s |
| RAM đỉnh | 719 MB | ~1 GB |

**Kết luận**: không có giá trị dự báo (hai bản ngược dấu, đều xa ý nghĩa thống kê); xác suất dự báo lệch thực tế
(dự báo thắng < 35% nhưng thực tế ~40%). 80 mẫu chỉ đủ phát hiện chênh lệch lớn (~0,6R) → **chưa tích hợp vào bot**.
Chạy lại khi có 200–300 tín hiệu mới của v3.1; chỉ tính chế độ bóng nếu tương quan có ý nghĩa (p < 0,05).

## Backtest phiên / phá giả biên độ / Volume Profile (`backtest_sessions.py`)

Chỉ dùng thư viện chuẩn + code của bot (`strategy.diagnose`, `exits.simulate`), nến 5m lịch sử OKX (API công khai, có cache).

- **A. orb** – phá giả biên độ 4h đầu phiên (mốc 0h / 4h / 8h / 12h UTC): nến 5m đóng ngoài biên rồi đóng quay lại trong biên
  → vào ngược hướng cú phá, SL ở cực trị cú phá. Thử 6 cách thoát: giữa biên, biên đối diện, 1R, 2R, trail, hybrid.
- **B. bot** – chạy lại strategy của bot trên từng nến 15m, chia nhóm theo giờ VN (như bộ tự học) và theo phiên Á / London / NY.
- **C. vp** – POC / VAH / VAL của 24h trước tín hiệu: vị trí giá so với vùng giá trị, khoảng trống tới POC, TP tại mức VP kế tiếp.

Không có AI, OI, funding, long/short → đây là kết quả của tín hiệu kỹ thuật trước AI. Báo cáo có `t_ngày` (kiểm định trên
R trung bình mỗi ngày) vì các tín hiệu cùng ngày tương quan mạnh; chỉ tin nhóm có `t_ngày` đủ lớn và cùng dấu ở cả hai nửa thời gian.

```bash
python tools/backtest_sessions.py --days 90 --top 30 --cache .bt_cache --out bt_result.json > bt_report.md
python tools/backtest_sessions.py --report bt_result.json
```

### Kết quả lần 1 (28/10/2026 – 30/07 → 28/10/2026, 30 coin, phí + trượt 0,15%/lệnh, bot v3.1.0)

| | n | R/lệnh | t_ngày | Kết luận |
|---|---|---|---|---|
| A. Phá giả biên 4h – cách thoát tốt nhất (trail) | 7491 | −0,33 | −11,7 | **Không dùng.** Âm ở mọi mốc giờ, mọi phiên, mọi cách thoát, cả 2 nửa thời gian |
| B. Tín hiệu bot đạt điểm, thoát trail | 1316 | +0,05 | −0,9 | Gần hoà vốn trước AI; trail > hybrid (−0,01) ≈ fixed (+0,01) |
| B. LONG / SHORT (trail) | 646 / 670 | +0,17 / −0,08 | +0,5 / −2,7 | SHORT yếu ổn định theo ngày |
| B. Giờ VN 13h–19h (bộ tự học đang chặn) | 334 | +0,04 | −0,6 | Không tệ hơn các khung khác → luật chặn là nhiễu 31 mẫu |
| B. 21–24h UTC (4h–7h VN) | 135 | −0,20 | −1,3 | Khung yếu nhất – nằm trong luật chặn đêm sẵn có |
| C. Vị trí so với vùng giá trị / khoảng tới POC | | ±0,1 | \|t\| < 1,6 | Không tách được tín hiệu tốt/xấu |
| C. TP tại mức VP kế tiếp vs trail (cùng 249 tín hiệu) | 249 | +0,05 vs +0,17 | | TP theo VP kém hơn trail |
