# Chẩn đoán 28/11/2026: Altcoin sau 29 ngày chạy ảo bản 3.3 – vì sao long thua, AI có giúp không, regime BTC khung ngày

Dữ liệu: bản "sao lưu toàn bộ JSON" của bot Altcoin tải từ dashboard lúc 11:55 ngày 28/11/2026 (59 lệnh chạy ảo 30/10–27/11,
1.005 tín hiệu bóng, đường vốn, tổng kết AI, lịch sử tự học; file không nằm trong repo). Chạy lại các bảng ở mục 1–3:
`python docs/chandoan_2026-11-28/chandoan_paper.py <okxbot-all-….json>` (chỉ thư viện chuẩn). Sai số chuẩn **gom cụm theo
ngày** (giờ Việt Nam) như bộ tự học của bot; t = kỳ vọng / sai số.

Bối cảnh: câu hỏi ban đầu là một prompt lan truyền trên X ("Paste this into Claude Opus 5.5…") đề xuất dựng lại bot với
AgenKit (sản phẩm trả phí) và model Jev của TypeSafe AI. Kết luận: không dùng thêm dịch vụ AI nào; bot đã có tương đương
70% kiến trúc prompt mô tả; thứ bot thiếu là **regime BTC khung lớn**, thứ bot thừa là **quyền chặn của AI**. Bản 3.4.0 làm
đúng hai việc đó cộng cầu dao 15% và Brier score (mục 4).

## 1. Lệnh chạy ảo và tín hiệu bóng

| Số đo | Kết quả |
|---|---|
| 59 lệnh chạy ảo 30/10 → 27/11, vốn 10 USDT | 10,00 → 9,82; lãi gộp +5,18, lỗ gộp −5,36, **phí 0,63** (6,3% vốn/tháng, 12% lãi gộp) |
| R trung bình / lệnh | −0,04 ± 0,16, thắng 54% (t −0,3: không khác 0) |
| Long / short (lệnh) | 26 lệnh −0,33R, thắng 38% / 33 lệnh +0,18R, thắng 67% |
| Sụt giảm lớn nhất từ đỉnh | **19,3%** (01/11 → 09/11) – vượt ngưỡng 15% "tạm dừng & thiết kế lại" của README mục 5.4; cầu dao 30% không kêu |
| 991 tín hiệu bóng có kết quả | −0,08R ± 0,10, thắng 50% |
| **Bóng long** | **320 tín hiệu, 22 ngày, −0,45R ± 0,11, t = −4,1**, thắng 31% – nhóm duy nhất có ý nghĩa thống kê trong cả log |
| Bóng short | 671 tín hiệu, +0,09R ± 0,11, t = +0,8 |
| BTC trung vị theo tuần 44 → 48 | 77.600 → 76.800 → 73.400 → 63.400 → 60.600 (−22% trong 29 ngày) |
| Long theo tuần | âm cả 5 tuần: −0,47 / −0,49 / −0,96 / −0,50 / −0,36R |
| Short theo tuần | −0,35 / −0,16 (BTC chưa sập) rồi +0,31 / +0,14 / +0,16 (BTC sập) |
| Long khi BTC **1h đang tăng** (cổng 1h cho qua) | 176 tín hiệu, −0,38R, t −2,2; BTC 1h đi ngang: 144 tín hiệu −0,53R, t −4,1 |
| Long khi BTC 4h ≥ 0 | 166 tín hiệu −0,55R ± 0,08, t −6,8 |

Toàn bộ lãi của tháng là "short trong lúc BTC sập". Cổng BTC khung 1h (`btc_trend_1h`, `BTC_GATE_CHG` 0,3%/1h) không nhìn
thấy xu hướng kéo dài một tháng, nên long vẫn được vào suốt một tháng BTC mất 22%, và mọi nguồn long đều âm (được vào −0,39R,
bị luật chặn −0,55R, suýt đạt −0,35R) trừ nhóm AI từ chối. Dữ liệu còn thiếu để nhận ra điều này là **regime BTC khung ngày**.

Giờ Việt Nam: 0–6h âm rõ (−0,47 / −0,34R) – luật `night_session` chặn đúng; 13–19h bị `afternoon_session` chặn thì quanh 0
(vô hại); 06–09h sáng là khung duy nhất dương rõ (+0,21R; riêng tín hiệu tới AI +0,38R, t +2,4, n = 54) – không thêm luật
"ưu tiên" vì sẽ là lát cắt thứ n trên cùng dữ liệu.

## 2. AI (9Router/OpenRouter model free, 2 lần/lệnh)

| Số đo | Kết quả |
|---|---|
| AI nói SKIP | 99 tín hiệu, **+0,33R**, thắng 69% (t +2,1) |
| AI duyệt LONG / SHORT | 23 tín hiệu −0,45R, thắng 35% / 33 tín hiệu +0,16R |
| Nguồn `ai_rejected` (AI chặn, bot không vào) | 92 tín hiệu **+0,36R ± 0,16, t +2,3**, thắng 71% |
| Nguồn `taken` (AI duyệt, bot vào) | 52 tín hiệu −0,02R |
| AI từ chối, điểm 5,5–6,5 (dưới sàn ghi đè 6,5) | 47 tín hiệu **+0,51R** – nhóm tốt nhất trong log, bị chặn vì `AI_OVERRIDE_MIN_SCORE` = 6,5 |
| Mọi tín hiệu tới AI nếu không có AI, vào hết | 156 tín hiệu **+0,18R ± 0,13**; short 115 tín hiệu +0,30R, t +2,2 |
| **Brier** (155 quyết định; p = confidence/100 khi đồng ý, 1 − confidence/100 khi SKIP) | **0,345** so với **0,234** nếu luôn đoán theo tỉ lệ thắng nền 63% → skill **−0,47**: AI đoán ngược chiều kết quả |
| Bộ tự học | đã đẩy `ai_veto_conf` 65 → 70 → 75 → 80 → 85 → 90 (5 lần, mỗi lần "AI phản đối vẫn +0,27…+0,42R") nhưng kẹt ở sàn 6,5 |

Cách đo Brier nằm trong `bot/learner.py` (`ai_calibration`) từ 3.4.0 và hiện trên trang Tự học.

## 3. Thoát lệnh (cùng tín hiệu, hiệu theo cặp, sai số theo ngày)

| So sánh | Mọi tín hiệu | Riêng short | Riêng long |
|---|---|---|---|
| TP1 2,0R − TP1 1R | +0,13 ± 0,07 (t 1,8) | +0,23 ± 0,08 (t 2,8) | −0,08 (t −1,1) |
| TP1 2,5R − TP1 1R | +0,16 ± 0,10 (t 1,7) | +0,30 ± 0,11 (t 2,6) | −0,14 (t −1,5) |
| SL 2,5 ATR + bám sóng − hybrid | +0,08 ± 0,05 (t 1,5) | +0,12 ± 0,06 (t 2,1) | −0,03 |

Bộ tự học chưa đổi TP1 vì t < 2 trên mọi tín hiệu – đúng thiết kế. Không chỉnh tay: phần "tốt hơn" nằm hết ở short trong
tháng BTC sập, tách tham số theo phía sẽ chia đôi mẫu. Tiếp tục đo.

## 4. Backtest 88 ngày: cổng regime BTC khung ngày

(xem mục 4 bên dưới – được điền sau khi chạy `regime_backtest.py`)
