# Báo cáo rà soát toàn bộ dự án - 23/10/2026

**Phạm vi:** toàn bộ gói `tradingagents-vn-botkeep-v13` (148 file, khoảng 31.000 dòng Python). Đọc tay phần lõi ra quyết định (policy, engine, features, ledger, factor, momentum, calibrate, simulator, vn_rules); bốn lượt rà soát song song cho trader/jobs/intraday, dataflows, web/Telegram/Zoom/LLM và chuỗi agent/graph/memory. Mỗi phát hiện đều được chạy lại bằng code thật trước khi ghi vào đây; hai phát hiện về dữ liệu được đối chiếu với API thật của VCI và VNDirect.

**Kết quả:** mã nguồn nhìn chung chắc chắn: luật T+2, ngày lễ, GDKHQ, chạy bù, khớp lệnh, bảo mật web/Telegram/Zoom đều đúng và có test. Không tìm thấy lỗi làm mất dữ liệu tài khoản thật. Tìm được **14 lỗi đã sửa trong bản v14** và **9 lỗi/điểm yếu chưa sửa** (ghi rõ bên dưới để anh quyết định). Bộ kiểm thử: 18/18 bộ đạt (thêm bộ `review_2026_10_23`, 20 kiểm tra; `ledger_fills` thêm 2).

Thuật ngữ: *T1/T2* là mục tiêu chốt lời 1 và 2; *stop* là giá cắt lỗ; *lô* là 100 cổ phiếu; *sổ ảo (paper)* là tài khoản giả lập của bot; *Output Guard* là lớp code kiểm tra lại văn bản AI.

---

## A. Đã sửa (v14)

### Phải sửa (ảnh hưởng tiền hoặc quyết định)

1. **Vị thế một lô chạm T1 thì không bán được gì nhưng vẫn bị coi là "đã chốt T1"** (`tradingagents/trading/ledger.py`, hàm `_exit_phase`)
   - **Là gì:** khi giá chạm T1, sổ bán một nửa (làm tròn lô 100), dời stop về hoà vốn.
   - **Lỗi:** vị thế 100 cp → nửa lô = 0 → không bán, nhưng vẫn đánh dấu T1 xong và kéo stop lên giá vốn. Chạy lại bằng code: mua 20.000, chạm T1 22.000 không bán; phiên sau về 20.000 → bán ở stop hoà vốn, **lỗ phí 9.988đ** thay vì lãi 10%. Xảy ra với tài khoản nhỏ hoặc mã giá cao (cả sổ ảo và lệnh đề xuất cho tài khoản thật).
   - **Sửa:** khi không chia được nửa lô thì bán toàn bộ tại T1 (nhãn "chốt toàn bộ tại T1"). `t1_sell_fraction = 0` giữ nguyên nghĩa cũ (không bán, chỉ dời stop).

2. **Dòng tiền khối ngoại của từng mã tính cả giao dịch thoả thuận** (`tradingagents/dataflows/vendors/flows.py`, `fetch_stock_flows`)
   - **Là gì:** Quant Engine cộng/trừ độ tin cậy theo tỷ lệ khối ngoại mua ròng trên giá trị khớp lệnh 20 phiên và chuỗi phiên mua ròng.
   - **Lỗi:** code lấy `foreignNetValueTotal` (gồm thoả thuận) chia cho `totalMatchValue` (chỉ khớp lệnh). Hàm `_foreign_net_matched` đã viết sẵn để dùng cột khớp lệnh nhưng cột đó chưa bao giờ được tải. Đối chiếu VCI, FPT ngày 23/10/2025: ròng khớp lệnh +43,6 tỷ, ròng tổng +225,9 tỷ (một lô thoả thuận 213 tỷ) → tỷ lệ 35% thay vì 6,8%. Một giao dịch thoả thuận đủ đảo dấu cả 20 phiên.
   - **Sửa:** tải thêm cột `ForeignNetMatched` (`foreignNetValueMatched`, dự phòng mua trừ bán khớp lệnh). Cache cũ thiếu cột thì tự rơi về số tổng như trước.

3. **Output Guard bỏ qua mọi dòng AI bắt đầu bằng nhãn hệ thống** (`tradingagents/agents/output_guard.py`)
   - **Là gì:** Guard xoá phần diễn giải của AI nếu nó nêu hành động/giá khác hệ thống.
   - **Lỗi:** mọi dòng bắt đầu bằng `**Recommendation**:`, `**Stop Loss**:`... đều được coi là của renderer và không kiểm tra. AI (hoặc tin tức bị trích vào) viết `**Recommendation**: SELL - bán ra toàn bộ` + `**Stop Loss**: 40.000` dưới quyết định BUY → lọt nguyên vào Telegram, báo cáo HTML và bộ nhớ dùng cho lần chạy sau. Chạy lại được.
   - **Sửa:** chỉ miễn kiểm tra dòng **trùng khớp từng chữ** với dòng renderer sinh ra cho quyết định đó (`schemas.plan_lines` và các dòng nhãn).

4. **Guard coi "tín hiệu BÁN", "đặt lệnh BÁN" là mô tả thị trường** (`tradingagents/quant/report_format.py`, `_THIRD_PARTY_BEFORE`)
   - **Lỗi:** danh sách "hành động của người khác" (khối ngoại, tự doanh...) có cả `tín hiệu|điểm|lệnh|chiều`, nên "đặt lệnh BÁN ngay hôm nay" dưới quyết định BUY không bị bắt.
   - **Sửa:** bỏ bốn từ đó; "lực BÁN áp đảo bên MUA" vẫn được coi là mô tả.

5. **Chấm điểm bộ nhớ/backtest AI vào lệnh ở giá đóng cửa của chính ngày quyết định** (`tradingagents/memory/settlement.py`, `fetch_returns`)
   - **Lỗi:** quyết định ra sau khi phiên D đóng cửa, nhưng điểm vào tính tại đóng cửa D (giá không ai mua được), trong khi sổ ảo khớp ở phiên D+1. Kết quả "AI đúng/sai" bị lệch một phiên.
   - **Sửa:** vào tại đóng cửa phiên kế tiếp, giữ đủ `holding_days` phiên sau đó; cần thêm một nến mới chốt.

6. **Sổ ảo so sánh Siêu tín hiệu bỏ lỡ hẳn tín hiệu của ngày nếu bot chạy trước 15:10** (`vnbot/trader.py`, `_stv_run`)
   - **Lỗi:** với `TRADE_TIME` 15:05-15:09, trang chưa chốt tín hiệu, sổ xử lý phiên với trạng thái hôm qua rồi đóng dấu "đã xong" → không bao giờ chạy lại phiên đó, mất mọi lệnh MUA của ngày, không báo gì.
   - **Sửa:** trước 15:10 (hằng số mới `external.SITE_FINAL_TIME`) không xử lý phiên hôm nay, ghi một dòng "xử lý ở lượt sau"; `/chay` và lịch tự quay lại vì sổ còn chậm.

7. **Zoom webhook chặn toàn bộ web (kể cả `/health`)** (`vnbot/web.py`)
   - **Lỗi:** route `async` nhưng gọi ghi file có fsync và gửi Telegram (HTTP 30 giây, có ngủ khi bị 429) ngay trên vòng lặp sự kiện. Người lạ nhắn bot trên Zoom → web đứng 1-150 giây → Botkeep báo "not responding" và có thể restart giữa lượt quét.
   - **Sửa:** chạy `record/lifecycle/handle` trong threadpool; `/health` thành `async` để luôn trả lời.

### Nên sửa

8. **Request Zoom không có chữ ký vẫn ghi vào `zoom_state.json`** - 20 POST rác đẩy các request thật khỏi trang `/zoom`, mỗi cái một fsync. Sửa: request bị từ chối chỉ giữ trong bộ nhớ (5 dòng), vẫn hiện trên `/zoom`.
9. **`/zoom/oauth/callback` không cần mật khẩu, mỗi lần gọi Zoom 15 giây** - 40 request giả chiếm hết 40 worker. Sửa: tối đa 2 lượt đổi mã cùng lúc, quá thì trả 429 (không bắt buộc `state` vì luồng "Add App Now" của Marketplace không gửi `state`).
10. **Cache giá bị từ chối vào ngày nghỉ khi nguồn lỗi** (`tradingagents/dataflows/ohlcv.py`, `_read_source`) - file ghi sáng thứ Bảy (đủ nến thứ Sáu) bị coi là cũ vì so với ngày thứ Bảy; DNSE trả 400/404 (`NoMarketDataError`) cũng không được dùng cache → mã niêm yết bình thường bị báo "không có dữ liệu". Sửa: so với phiên gần nhất có thể có nến, và coi "no data" như mất kết nối (mã chết thật vẫn bị `assert_not_stale` chặn sau 12 ngày).
11. **Hạn mức AI ngày bị trừ cho lượt chưa tới nhà cung cấp** (`llm_clients/fallback_client.py`) - 9Router/Groq mất kết nối 20 phút lúc 15:30 đủ đốt 60 lượt mà không có câu trả lời nào. Sửa: hoàn lại khi lỗi kết nối/timeout.
12. **Lượt chạy bù không lưu `signals.json`** khi phải tính lại tín hiệu (`vnbot/trader.py`, `_catch_up`) → mỗi lần thử lại 15 phút phân tích lại cả rổ, `/khop mua` lấy stop/T1 của ngày cũ. Sửa: ghi file như `_engine_session`.
13. **Kết quả bóng của AI phản biện lệch một phiên khi báo cáo chạy buổi sáng** (`vnbot/critic.py`) - dòng ghi ngày chạy, nhưng giá là đóng cửa hôm trước, nên phiên đầu tiên (có thể chạm stop) bị bỏ qua. Sửa: ghi theo `last_bar_date`.
14. **`data/` phình không giới hạn**: mỗi lượt `/phantich` ghi một `full_states_log_*.json` (hàng chục-trăm KB) không bao giờ xoá; `trading_memory.md` không giới hạn và được đọc lại toàn bộ mỗi lượt. Sửa: giữ 300 mục bộ nhớ (`memory_log_max_entries`), xoá state log cũ cùng ngưỡng `KEEP_REPORTS`. Thêm: lệnh đề xuất cho tài khoản thật theo rổ Phòng thủ/Động lượng chỉ tính trên mã có nến phiên đó (trước đây mã tạm ngừng vẫn được tính khối lượng theo giá cũ).

Guard thứ hai (`create_output_guard`) không kiểm tra lại văn bản mà `guard_early` đã sửa, nên hết cảnh báo lặp đôi.

---

## B. Chưa sửa - cần anh quyết định

1. **Giá đóng cửa toàn thị trường (VNDirect `adClose`) đã điều chỉnh trước cho ngày GDKHQ *sắp tới*** (`vendors/flows.py`, `fetch_market_day`; dùng ở `market_scan`). GVR 23/10/2025: `close` 26.100, `adClose` 25.732 vì cổ tức 400đ GDKHQ 13/11. Mức đổi chiều TB20/MACD của quét 300 mã lệch ~1,5% cho tới ngày GDKHQ (README nói 0,1%). Sửa cần lấy thêm `close` thô và chia lại chuỗi theo `close/adClose` phiên mới nhất; tôi chưa làm vì đụng cấu trúc file cache 4 MB.
2. **Tài khoản thật: GDKHQ rơi giữa ngày báo `/khop` và lần đánh dấu tối đầu tiên** (`ledger.mark_to_history`): vị thế mới chưa có mốc giá đóng cửa nên không phát hiện được tỷ lệ; stop cũ 55.000 trên giá đã chia 2:1 → báo "chạm stop" giả. Cửa sổ một phiên, hiếm. Sửa: khi ghi `/khop`, lưu luôn giá đóng cửa phiên đó làm mốc.
3. **`/danhmuc`, `/stv` ghi lô T+2 là "đã về" từ sáng T+2** dù chỉ bán được từ 13:00 (cảnh báo trong phiên tính đúng). Chỉ hiển thị.
4. **`CriticLog.settle` giữ khoá chung trong lúc tải giá** → `/phanbien` chờ tới khi tải xong.
5. **Ghi bộ nhớ không nguyên tử** (`memory/log.py`): chết giữa chừng thì hai mục dính làm một. Sửa an toàn cần đổi cách phân tách mục.
6. **Sàn không xác định (VCI listing lỗi) làm phát hiện gap trên UPCoM quá chặt**: phiên sàn -14% hợp lệ bị coi là GDKHQ chưa điều chỉnh → khoá tín hiệu 66 phiên. Sửa: cache listing cuối cùng ra đĩa.
7. **`fetch_hose_day` trả giá đơn vị nghìn đồng** trong khi mọi frame khác là VND (hiện chỉ dùng tỷ lệ nên chưa sai; ai dùng thêm sẽ lệch 1000 lần).
8. **Chưa có test cho luật cache/nến đang hình thành** (`_cache_is_fresh`, `_final_through`, `drop_forming_bar`, `money_flows._rebase`): đổi `<` thành `<=` ở đó test vẫn xanh. Bộ mới chỉ phủ đường dự phòng cache.
9. **Các mục "Chưa sửa" của bản 21/10 vẫn còn** (hiệu chỉnh trên rổ hiện tại - thiên lệch sống sót; GDKHQ UPCoM; OAuth `state`; khoá đăng nhập theo IP).

---

## C. Nâng cấp để tìm điểm mua/bán tốt hơn

Đây là phần quan trọng nhất cho mục tiêu của anh. Tôi xếp theo mức ảnh hưởng, kèm chỗ trong code. **Không thể đo lợi nhuận ở đây** vì gói không có dữ liệu lịch sử và bộ kiểm định điểm-theo-thời-gian; mọi đề xuất cần chạy qua `calibrate`/backtest trước khi bật cho tài khoản thật.

1. **Quant Engine không bao giờ MUA cổ phiếu phá đỉnh (không còn kháng cự phía trên).** `policy.py` dòng ~1198: không có kháng cự thật → luôn WATCH, trừ khi `breakout_projected_ok = 1`; tham số này mặc định 0 và **không nằm trong `TUNABLE`**, nên hiệu chỉnh đêm không bao giờ thử. Đây là lý do lớn nhất khiến engine "rất ít lệnh" và bỏ qua đúng những mã mạnh nhất. Đề xuất: thêm `breakout_projected_ok: (0, 1, 1)` vào `TUNABLE` để calibrate tự thử (mục tiêu `projected_rr` × rủi ro, đã có code), hoặc bật thủ công trong `quant_params.json` cho sổ ảo engine vài tháng để so.
2. **Lệnh mua chỉ sống một phiên và chỉ khớp khi giá *xuyên qua* cận trên vùng mua** (`ledger._buy`, `plan_orders`). Với mã đang tăng mạnh, phiên sau thường mở cao hơn → không khớp, tín hiệu mất. Đề xuất đo trước: ghi vào báo cáo tối "số BUY đặt / số khớp" (có sẵn `paper.orders` và `fills`). Nếu tỷ lệ khớp thấp, thử: giữ lệnh 2-3 phiên với giá giới hạn cập nhật theo đóng cửa mới, hoặc cho phép khớp tại cận trên khi `low == limit`.
3. **Sau T1 chỉ dời stop về hoà vốn, không trail** (`trail_pct = 0`). Phần còn lại chờ T2 hoặc hết `max_hold_sessions` 30 phiên. `trail_pct` có trong `TUNABLE` nhưng hiệu chỉnh chỉ xét 60 phiên một lần và cần thắng hơn 2 lần nhiễu nên gần như không bao giờ đổi. Đề xuất: so sánh thủ công hai sổ ảo (trail 0 và trail 8-10%) trên dữ liệu cache 460 phiên bằng `simulator.run`, đưa kết quả vào `/hieuchinh`.
4. **Stop đặt theo ATR×1,5 kẹp 1-10%, hoặc dưới hỗ trợ 1%** (`_compute_stop_target`). Với mã biến động thấp (ATR 1,5%), stop 2,3% là quá sát so với biên độ ±7% một phiên → dễ bị quét. Đề xuất: sàn dưới cho khoảng stop bằng `max(1,5×ATR, 1 phiên biên độ)` hoặc kẹp dưới 3%; để calibrate quyết định (thêm `stop_atr_mult` và `stop_min_pct` vào `TUNABLE`).
5. **Điểm bán của engine phụ thuộc ngưỡng độ tin cậy 0,75 cho SELL** giống như MUA, nên khi đang giữ, tín hiệu xấu dần chỉ ra WATCH; vị thế thực tế thoát bằng stop hoặc hết 30 phiên. Đề xuất: ngưỡng SELL riêng (ví dụ 0,65) hoặc bán khi `trend_score` dưới 40 và giá dưới SMA20 hai phiên liền; cần thêm `confidence_sell_min` vào `Params`/`TUNABLE`.
6. **Dòng tiền giờ đã đúng (mục A.2)** nên có thể tăng `flow_weight` trong khoảng 0-0,15 bằng calibrate; trước đây tín hiệu này bị nhiễu bởi thoả thuận nên hiệu chỉnh khó thấy lợi ích.
7. **Chiến lược Phòng thủ (mặc định cho tài khoản thật) thua VNINDEX trong pha tăng 2024-2026 (8,1% so với 15,9%)**: nó chỉ đầu tư khi VNINDEX trên MA200 và xếp hạng theo biến động thấp + gần đỉnh 52 tuần. Hai hướng nhỏ, dễ kiểm định với `factor.run`: (a) lọc thị trường theo MA200 kiểm tra hằng ngày với vùng đệm 2% thay vì hằng tuần (vào lại sớm hơn sau nhịp giảm); (b) thêm thành phần động lượng 6 tháng vào điểm xếp hạng (`rank`: hiện là rank(-vol) + rank(hi)). Lưu ý bộ `FACTOR_FIXED` được cố tình khoá vì chỉ điểm đó đã kiểm định; mọi thay đổi cần backtest điểm-theo-thời-gian trước.
8. **Trong phiên bot chỉ *báo*, không hành động** (đúng thiết kế). Với tài khoản thật, cảnh báo ⛔ stop và 🎯 T1 hiện đã đúng T+2; đề xuất thêm cảnh báo "giá đang trong vùng mua của lệnh đề xuất" cho chiến lược Phòng thủ (hiện chỉ có cho engine: 🛒), để anh đặt lệnh đúng lúc thay vì chờ mở cửa.
9. **Đo lường trước khi tin**: mọi kết luận "AI đúng bao nhiêu" (`/phanbien`) và bộ nhớ phản tư từ nay chấm theo giá vào phiên kế tiếp (A.5, A.13). Nên xoá kết quả bóng cũ (`data/critic_log.json`) hoặc coi số trước 23/10 là tham khảo.

---

## E. Đợt 2 cùng ngày: sửa mục B, cài mục C và backtest

### E.1 Mục B đã sửa thêm (commit `27bcc7f`)
B1 hệ số `raw/adClose` cho quét 300 mã (mức đổi chiều về đúng bảng giá tới ngày GDKHQ) · B2 `/khop mua` ghi mốc giá đóng cửa ngày khớp (phát hiện GDKHQ trước lần đánh dấu đầu) · B3 `/danhmuc` hiện số cổ phiếu bán được theo giờ (chiều T+2) · B4 `CriticLog.settle` tải nến ngoài khoá · B5 bộ nhớ ghi nguyên tử · B6 listing VCI lưu đĩa, dùng lại khi VCI lỗi · B7 ghi rõ đơn vị nghìn đồng của `fetch_hose_day` · B8 thêm test cho luật cache/nến đang hình thành và `_rebase` độ rộng. **Chưa làm:** B9 (các mục cũ của bản 21/10: thiên lệch sống sót của hiệu chỉnh, GDKHQ UPCoM, OAuth `state` - luồng "Add App Now" của Marketplace không gửi `state` nên không bắt buộc được, khoá đăng nhập theo IP sau proxy).

### E.2 Mục C: tham số mới (mặc định = hành vi cũ, trừ một thay đổi ở E.4)
`breakout_projected_ok` vào `TUNABLE`; `confidence_sell_min`; `stop_atr_mult`, `stop_min_pct`; `buy_order_sessions` (lệnh mua sống nhiều phiên, giới hạn/stop theo quyết định mới, huỷ khi engine hết BUY); `limit_fill_touch`; Phòng thủ: `fb_market_daily` + `fb_market_buffer_pct`, `fb_momentum_weight`. C8 (cảnh báo "vào vùng mua" cho Phòng thủ) **không làm**: lệnh của Phòng thủ/Động lượng là lệnh giá mở cửa, không có vùng mua để so.

### E.3 Backtest (chi tiết, log và script: `docs/backtest_2026-10-23/`)
Dữ liệu 900 phiên 17/03/2022 → 24/10/2025, rổ **hôm nay** (thiên lệch sống sót - chỉ dùng để so các phương án với nhau). Train tới 13/08/2024, Test sau đó. VNINDEX: toàn bộ +15,2%, train −15,8%, test +36,8%.

**Quant Engine (VN30):**

| Phương án | Toàn bộ lãi / DD / lệnh | Train lãi | Test lãi / DD | Đầu tư TB |
|---|---|---|---|---|
| Mặc định v13 | +5,4% / 4,5% / 12 | −1,8% | +7,3% / 1,9% | 2,8% |
| C1 breakout_projected_ok=1 | +6,2% / 10,5% / 64 | +5,3% | +0,8% / 9,1% | - |
| C1 + skip_near_resistance=1 | **+12,7%** / 10,1% / 64 | **+8,3%** | +4,3% / 7,7% | 17,5% |
| C2 buy_order_sessions=2–3, limit_fill_touch | = mặc định | = | = | 12/12 lệnh khớp |
| C3 trail_pct=8 | +0,9% | −1,8% | +2,7% | - |
| C4 stop_min_pct=3 | +5,7% / 4,5% / 12 | −1,8% | +7,6% / **1,7%** | 2,8% |
| C4 stop_atr_mult=2.0 | −1,2% / 2 lệnh | | | - |
| C5 confidence_sell_min=0.65 | = mặc định | | | |
| C6 flow_weight=0.15 | +7,2% / 4,4% / 13 | −1,8% | **+9,4%** / 2,2% | - |
| stop_min 3 + flow 0.15 | +7,8% / 4,4% / 13 | −1,8% | +9,9% / 2,1% | 3,4% |
| skip_near + risk 2.0 + stop_min 3 | +7,7% / 8,9% / 25 | −2,0% | **+10,1%** / 5,6% | 8,4% |
| min_rr_ratio=1.2, dist_threshold=6, dist_min_drop=0.5, confidence 0.65/0.70 | xấu hơn mặc định | | | |

Chẩn đoán: trên 25.249 cặp (mã, phiên) engine chỉ ra **14 BUY**; WATCH/NO_TRADE vì confidence chưa đạt (5.962), regime risk-off/thiếu VNINDEX (2.821), tín hiệu mâu thuẫn (1.906); cổng R:R chỉ chặn 829 và "không có kháng cự" 442. Tỷ trọng đầu tư trung bình 2,8% NAV: **engine là sổ gần như toàn tiền mặt**, lãi thấp là vì không đầu tư chứ không phải vì chọn sai. 12 tín hiệu BUY → 12 lệnh → 12 khớp: giả thuyết C2 (lệnh không khớp vì giá bỏ chạy) **sai**, nên C2 không đổi gì.

**Phòng thủ (VN100 hôm nay):**

| Phương án | Toàn bộ lãi / DD | Train lãi / DD | Test lãi / DD |
|---|---|---|---|
| Mặc định (MA200 tuần) | +32,5% / 12,1% | +12,9% / 9,1% | **+16,9%** / 12,3% |
| C7a kiểm tra hằng ngày, đệm 2% | +31,1% / 14,2% | +14,8% / **7,3%** | +14,2% / 13,9% |
| C7a hằng ngày, đệm 0% | +29,4% / 17,1% | +16,0% / 8,3% | +10,9% / 17,0% |
| C7b động lượng 0.5 | **+44,3%** / 13,2% | **+24,2%** / 10,6% | +14,1% / 13,8% |
| C7b động lượng 1.0 | +39,0% / 17,0% | +29,3% / 10,7% | +4,2% / 18,3% |
| C7a+b | +36,3% / 15,2% | +20,5% / 8,9% | +9,0% / 16,4% |

### E.4 Quyết định
- **Đổi mặc định một tham số:** `stop_min_pct = 3` (khoảng stop không sát hơn 3% giá; trước đây 1,5 × ATR có thể chỉ 2,2% - nằm trong biên độ một phiên). Trên mọi tổ hợp thử, sàn 3% không làm xấu đi và giảm sụt giảm; mức 3% thấp hơn biên độ HOSE 7% nên không đổi bản chất chiến lược. Sổ ảo/lệnh thật từ v14 dùng mức này; `quant_params.json` cũ không có khoá này sẽ lấy mặc định mới.
- **Không đổi mặc định** các tham số còn lại, nhưng tất cả đã nằm trong `TUNABLE` để hiệu chỉnh đêm thử trên dữ liệu mới:
  - `flow_weight` 0,15 cho kết quả test tốt nhất (+9,4%, thắng 73%) nhưng dữ liệu dòng tiền chỉ có từ 2024 (train không đổi) → bằng chứng mới một cửa sổ, để calibrate quyết định.
  - `skip_near_resistance=1 + breakout_projected_ok=1` là cách duy nhất làm engine **đầu tư thật** (17,5% NAV, 64 lệnh, train +8,3% khi VNINDEX −15,8%) nhưng test chỉ +4,3% so với +7,3% của mặc định và sụt giảm 10%. Nếu anh muốn engine giao dịch nhiều hơn, đây là bộ nên bật thử trên sổ ảo (`quant_params.json`), không bật cho tài khoản thật ngay.
  - Phòng thủ với `fb_momentum_weight=0.5`: train vượt trội (+24,2% so với +12,9%) nhưng test kém hơn (+14,1% so với +16,9%) và rổ VN100 hôm nay thiên lệch sống sót → **giữ bộ đã kiểm định 15/10**; để ở `FACTOR_FIXED` cho lần kiểm định điểm-theo-thời-gian sau.
  - Trail stop, ngưỡng SELL riêng, R:R 1,2, nới ngày phân phối: không giúp hoặc xấu hơn.
- **Hệ quả quan trọng cho mục tiêu lợi nhuận:** trên 2022–2025 Phòng thủ (+32,5%, DD 12%) là sổ duy nhất thắng VNINDEX (+15,2%) và cũng đứng vững khi VNINDEX −15,8%; Quant Engine giữ tiền mặt gần như toàn thời gian. Chiến lược mặc định cho tín hiệu thật (`SIGNAL_STRATEGY=factor`) là đúng; engine nên xem như bộ lọc tham khảo, không phải nguồn lợi nhuận, trừ khi bật bộ "đầu tư thật" ở trên và theo dõi vài tháng trên sổ ảo.

## D. Những gì đã kiểm tra và không thấy lỗi

Luật bước giá/trần sàn (đúng từng mức giá), T+2 theo lịch có ngày lễ, khớp lệnh sổ ảo (gap qua stop, trần/sàn trắng bên, nến lỗi), chạy bù không trùng, ghi file nguyên tử, không nhìn trước tương lai trong `simulator`/`factor`/`momentum`/`ledger` (quyết định trên nến D, khớp từ D+1), mật khẩu web so sánh an toàn, CSRF, giới hạn 64 KB, chữ ký Zoom, chỉ id được phép mới ra lệnh Telegram, HTML báo cáo escape đầy đủ, vòng gọi tool có giới hạn, LLM trả sai định dạng không làm sập, toán SMA20/MACD của ước tính đổi chiều.

**Kết luận:** v14 an toàn để upload (18/18 bộ đạt, mã bản dựng đã đóng dấu). Mục B đã sửa (trừ B9), mục C đã cài và backtest (mục E): chỉ `stop_min_pct=3` được đổi mặc định; các tham số còn lại để hiệu chỉnh đêm hoặc bật tay trên sổ ảo trước khi dùng cho tiền thật.
