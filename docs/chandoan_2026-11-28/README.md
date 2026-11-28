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

`altcoin/tools/backtest_sessions.py --parts bot` (code của bot, chạy lại `strategy.diagnose` trên nến 15m đã đóng, thoát hybrid,
phí 0,20%/vòng, không AI, không OI/funding), 01/09 → 28/11/2026, 50 hợp đồng USDT-SWAP crypto volume cao nhất hôm nay (lọc
`instCategory`, có thiên lệch sống sót), nến OKX tải ngày chạy (không nằm trong repo). Regime ngày = `market.daily_regime` trên
nến 1D OKX đã đóng (mở 00:00 giờ Hồng Kông): EMA21 > EMA50 và đóng > EMA21 = tăng, ngược lại = giảm, còn lại = chưa rõ.
Chỉ xét tín hiệu bot thật sự vào được (đạt điểm, không dính luật chặn sẵn). **Backtest phải chạy với `BTC_DAILY_GATE=false`**
(lần đầu chạy lúc cổng đã bật nên không còn tín hiệu để đo – `regime_backtest.py` ghi rõ). Chạy lại:
`python docs/chandoan_2026-11-28/regime_backtest.py <bt88.json>`. 60% đầu trong mẫu (tới 22/10), 40% cuối ngoài mẫu.

Số ngày theo regime: 41 ngày BTC tăng (toàn bộ trong mẫu, Sep–Oct, BTC 75.000–86.000), 28 chưa rõ, 23 giảm (toàn bộ ngoài
mẫu, từ 04/11, BTC 80.000 → 58.000). Tức là backtest chỉ có **một** đợt giảm để kiểm, và nó trùng với 29 ngày chạy ảo ở mục 1.

| Nhóm (R hybrid, sai số theo ngày) | Trong mẫu | Ngoài mẫu | Cả kỳ |
|---|---|---|---|
| Mọi lệnh vào được | 475, −0,03 ± 0,08 | 318, +0,03 ± 0,14 | 793, −0,01 ± 0,07 |
| Long khi BTC ngày **giảm** (cổng chặn) | 0 | **50 tín hiệu, 11 ngày, −0,42 ± 0,34, thắng 30%** | 50, −0,42 |
| Short khi BTC ngày **tăng** (cổng chặn) | 166, −0,05 ± 0,18 | 8 (1 ngày), −0,56 | 174, −0,07 ± 0,17 |
| Long khi BTC ngày tăng | 187, +0,00 | 0 | 187, +0,00 |
| Short khi BTC ngày giảm | 0 | 140, +0,32 ± 0,18, thắng 71% | 140, +0,32 |
| Long / short khi chưa rõ | 46, +0,21 / 76, −0,23 | 40, −0,16 / 80, −0,02 | 86, +0,03 / 156, −0,13 |
| **Bị cổng ngày loại** | 166, **−0,05 ± 0,18** (t −0,3) | 58, **−0,44 ± 0,29** (t −1,5), thắng 29% | 224, −0,15 ± 0,17 |
| **Còn lại sau cổng** | 309, −0,03 ± 0,09 | 260, **+0,14 ± 0,14** | 569, +0,05 ± 0,08 |

Đọc thế nào:

1. Cổng ngày **không bao giờ loại một nhóm có lãi**: trong mẫu nó chỉ bỏ 166 short lúc BTC ngày tăng (−0,05R, gần 0), ngoài mẫu
   bỏ 58 tín hiệu −0,44R (50 long trên 11 ngày BTC giảm). Nhóm bị loại âm ở cả hai nửa, nhưng chỉ ngoài mẫu mới âm rõ
   (t −1,5, chưa tới 2) và lợi ích gần như nằm trọn trong một đợt giảm – chính là đợt làm bot chạy ảo lỗ ở mục 1.
2. Cổng 1h không làm được việc này: ngoài mẫu, long lúc BTC 1h đang tăng vẫn được vào trên những ngày BTC ngày giảm.
3. Tổng thể sau cổng vẫn chỉ +0,05R ± 0,08 cả kỳ: **cổng ngày không tạo lợi thế, nó cắt một nhóm lỗ** – đúng như backtest 88
   ngày của đợt 02/11 (chiến lược gần hoà sau phí). Việc chuyển live vẫn theo đúng tiêu chí README mục 5.
4. Các định nghĩa regime khác (giá dưới EMA21 ngày, giá dưới EMA50 ngày, % đổi 7 / 14 ngày) đã thử trên cùng dữ liệu: "% đổi 7
   ngày < 0" loại nhầm nhóm long tốt nhất trong mẫu (long sau khi BTC giảm nhẹ 7 ngày trong xu hướng tăng: +0,44R, t +3,9),
   nên không dùng; EMA21/EMA50 + giá đóng (cùng định nghĩa với khung 1h) là cách đơn giản nhất không loại nhầm nhóm lãi nào.
   Không chọn định nghĩa theo số đẹp nhất.
5. Tham khảo, **không hành động**: short khi BTC ngày "chưa rõ" trong mẫu −0,23R (t −3,5, 10 ngày) và short khi BTC vừa tăng
   > 5%/7 ngày −0,70R (17 tín hiệu, 3 ngày) – mẫu dồn vài ngày, để bộ tự học theo dõi qua luật ứng viên nếu lặp lại.

**Quyết định 3.4.0:** `BTC_DAILY_GATE=true` (cổng cứng), kèm hai luật ứng viên `long_btc_bear_1d` / `short_btc_bull_1d` để
trang Tự học vẫn thống kê bóng của nhóm bị cổng chặn (tín hiệu bị cổng loại không thành dòng bóng; luật chỉ có tác dụng khi
tắt cổng – `BTC_DAILY_GATE=false` – lúc đó bộ tự học sẽ chặn/bỏ chặn theo kết quả như mọi luật BTC khác).

## 5. Thay đổi trong 3.4.0 và tiêu chí xem lại

| Thay đổi | Ở đâu | Xem lại khi |
|---|---|---|
| Cổng regime BTC khung ngày | `market.daily_regime`, `strategy.btc_gate`, `BTC_DAILY_GATE` | sau 4 tuần chạy ảo: nếu bóng của hai luật ngày (khi tắt cổng để đo) hoặc backtest lần sau cho nhóm bị chặn ≥ 0 |
| AI chỉ điều tiết khối lượng | `AI_OVERRIDE_MIN_SCORE` 6,5 → 5,0 | trả lại quyền chặn (6,5) chỉ khi Brier của AI < tỉ lệ nền (skill > 0) trên ≥ 150 quyết định |
| Cầu dao 15% | `MAX_DRAWDOWN` 0,30 → 0,15 | không |
| Brier trên trang Tự học | `learner.ai_calibration` | – |
| TP1, SL 2,5 ATR, giờ giao dịch | không đổi | bộ tự học quyết, t ≥ 2 |

Giới hạn chung của cả đợt chẩn đoán: 29 ngày chạy ảo và 23 ngày BTC giảm trong backtest là cùng một đợt thị trường; mọi lát cắt
ở mục 1–3 đều lấy từ cùng dữ liệu nên lạc quan hơn thực tế; vốn 10 USDT nên phí và lot tối thiểu chi phối kết quả tiền thật.
