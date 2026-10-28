# Chẩn đoán 28/10/2026: vì sao chưa có lệnh MUA

Dữ liệu thật (DNSE/VCI) tới phiên 27/10/2026, chạy bằng chính code của bot (`policy.evaluate_policy`, `engine.build_inputs`).
Chạy lại: `python docs/chandoan_2026-10-28/chandoan.py 250 vn100` (cần mạng, khoảng 5 phút), hoặc trên bot:
`/vikhongmua dunglai 250 vn100` rồi `/vikhongmua`.

| Số đo | Kết quả |
|---|---|
| Regime VNINDEX, 250 phiên (24/10/2025 - 27/10/2026) | risk-off 213, risk-on 20, trung tính 17 |
| Lý do risk-off | mọi phiên risk-off đều có ≥ 4 ngày phân phối/25 phiên; 116 phiên chỉ vì lý do đó |
| Phiên 27/10 | 9 ngày phân phối (ngưỡng 4); VNINDEX 1.761,87 dưới MA200 (1.796,41) từ 24/9 và dưới MA100 (1.783,30) |
| Phòng thủ / Động lượng | giữ tiền mặt (lọc MA200 kiểm tra cuối tuần / MA100) |
| VN100, 20 phiên gần nhất (2.000 cặp mã-phiên) | MUA 0; cổng đầu tiên: xu hướng giảm 1.157, độ tin cậy (kèm risk-off) 575, mâu thuẫn 162, chưa rõ 81, thị trường 25 |
| Cũng 20 phiên đó nếu thị trường trung tính | MUA 0: 600 cặp xu hướng tăng trượt độ tin cậy 405, R:R 159, phá đỉnh chưa đo được R:R 33, phiên rủi ro 3 |
| VN100, 250 phiên (24.750 cặp) | MUA 15; MUA nếu thị trường trung tính thêm 22 |
| 15 MUA thật | 10 phiên sau -3,4% (VNINDEX -3,5 điểm %), theo kế hoạch 14 khớp, 13 chạm cắt lỗ, -0,90R |
| 22 MUA bị chặn vì thị trường | 10 phiên sau -4,4%, thắng 27%; theo kế hoạch 19/22 chạm cắt lỗ, -0,64R |
| Sàn cắt lỗ 3% (23/10) | gần như không liên quan: bỏ nó, 250 phiên thêm 2 MUA |

Kết luận: không có lỗi; ba lớp lọc (thị trường risk-off, MA200/MA100 của hai chiến lược xoay vòng, cổng độ tin cậy và R:R của từng
mã) cùng chặn. Năm qua 22 lệnh MUA bị bộ lọc thị trường chặn cũng lỗ (19 chạm cắt lỗ, cộng -14R nếu cho qua): bộ lọc chặn đúng,
nên nới cổng lúc này không có cơ sở. Vì sao chính các lệnh MUA lỗ: mục **Bổ sung** bên dưới. Từ v1.13.0 bot ghi mọi quyết định và đo kết quả bóng hằng ngày để quyết định bằng số đo (README, mục "Vì sao chưa có MUA &
kho dữ liệu"). Giới hạn: rổ VN100 hôm nay (thiên lệch sống sót), giá điều chỉnh tới hôm nay, không có tin tức và dòng tiền trong
lần dựng lại; mẫu MUA nhỏ (15 và 22).

## Bổ sung: vì sao các lệnh MUA lỗ

`python docs/chandoan_2026-10-28/vi_sao_lo.py 900` (cần mạng, khoảng 15 phút): dựng lại engine cho VN100 + VN30 ở 900 phiên
(16/03/2023 - 27/10/2026, không dòng tiền); mỗi lệnh MUA đi theo kế hoạch như sổ ảo (lệnh giới hạn ở cận trên vùng mua một
phiên, bán từ T+2, cắt lỗ hoặc mục tiêu, tối đa 30 phiên, R sau phí). "Năm cuối" là 250 phiên từ 24/10/2025. Số lệnh: chốt lời /
cắt lỗ / hết hạn.

| Số đo | 16/03/2023 - 23/10/2025 | Năm cuối |
|---|---|---|
| MUA thật | 31 lệnh khớp: 13 / 15 / 3, TB +0,26R | 14 lệnh: 1 / 13 / 0, TB -0,90R |
| MUA bị chặn vì thị trường | 59 lệnh: 16 / 37 / 6, TB -0,17R | 22 lệnh: 3 / 19 / 0, TB -0,64R |
| Setup breakout (thật + bị chặn) | 33 lệnh: 4 / 26 / 3, TB -0,61R | 13 lệnh: 1 / 12 / 0, TB -0,86R |
| Setup pullback (thật + bị chặn) | 57 lệnh: 25 / 26 / 6, TB +0,32R | 23 lệnh: 3 / 20 / 0, TB -0,67R |
| Cũng các lệnh đó nếu giữ nguyên, không cắt lỗ, 20 phiên | breakout -0,5%, pullback +0,7% | breakout -7,5%, pullback -4,3% |
| Sau khi cắt lỗ, 20 phiên sau tín hiệu | giá TB -3,8% so với giá mua; 6/52 lệnh tới mục tiêu sau đó | -7,3%; 3/32 |
| Mọi mã xu hướng tăng phá kênh 20 phiên, giá 20 phiên sau | engine MUA (34 lần) -0,4%; bị loại vì không còn kháng cự phía trên (2.545 lần) +3,1% | engine MUA (14) -7,8%; không còn kháng cự (407) -1,5% |

1. **Năm cuối là năm "chỉ số tăng, cổ phiếu giảm":** VNINDEX +4,7% nhưng mã VN100 trung vị -10,7%, 32/98 mã tăng; VIC +119% kéo
   chỉ số. Mua gần như setup nào cũng lỗ, và giữ nguyên không cắt lỗ còn lỗ nặng hơn: cắt lỗ đã làm đúng việc.
2. **Nhiều lệnh chạm cắt lỗ là do cấu trúc kế hoạch:** cắt lỗ trung vị -5,7% (1,6 ATR), mục tiêu +10,7%; không có lợi thế gì thì
   khoảng 65% lệnh chạm cắt lỗ trước, hoà vốn cần thắng khoảng 38%. Trước năm cuối MUA thật thắng 13/31 (+0,26R, t +1,0: chưa đủ
   để gọi là lợi thế); năm cuối 1/14.
3. **Lệnh bị chặn cũng lỗ nghĩa là bộ lọc thị trường chặn đúng:** cả 900 phiên, lệnh bị chặn TB -0,29R, lệnh được cho qua -0,10R.
4. **Điểm yếu có thật ở cả hai giai đoạn: breakout mà engine MUA.** Cổng R:R đo tới kháng cự gần nhất nên engine chỉ MUA mã phá
   kênh 20 phiên mà còn nằm dưới một đỉnh cũ đủ xa; mã phá đỉnh hẳn bị loại (cổng "không còn kháng cự"), trong khi chính nhóm này
   tăng tốt hơn trong 2023-2025. Pullback là setup duy nhất có lãi khi thị trường bình thường.
5. **Độ tin cậy không phải xác suất thắng:** mã xu hướng tăng có độ tin cậy (nếu thị trường trung tính) 0,75-0,85 hay từ 0,85
   không hơn VNINDEX sau 10 phiên (+0,03%, +0,01%); nhóm 0,55-0,65 còn hơn (+0,36%).

Không nới cổng độ tin cậy hay thị trường để có MUA. Ứng viên đáng thử trên sổ ảo: bỏ MUA breakout còn kháng cự phía trên, giữ
pullback; từ v1.14.0 có **công tắc MUA breakout** (trang `/bot`, mặc định bật như cũ), mã bị tắt vẫn được chấm kết quả bóng ở nhóm
riêng (README chính, mục Bot giao dịch). Lưu ý backtest 23/10 (`BAO_CAO_RA_SOAT_2026-10-23.md`, mục E): cho MUA mã phá đỉnh với mục tiêu
2R (`breakout_projected_ok=1`) tốt hơn ở đoạn train nhưng kém hơn ở đoạn test, nên đó không phải cách sửa miễn phí. Giới hạn: rổ hôm
nay (thiên lệch sống sót), giá điều chỉnh, không tin tức/dòng tiền; mẫu nhỏ và dồn cụm (15 MUA thật năm cuối nằm trong 13 phiên của
hai đợt 31/12 - 20/01 và 17/04 - 18/05; các mã cùng phiên không độc lập), t chỉ để tham khảo.
