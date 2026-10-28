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
mã) cùng chặn. Năm qua những lệnh MUA bị bộ lọc thị trường chặn còn lỗ nhiều hơn lệnh được cho qua, nên nới cổng lúc này không có
cơ sở. Từ v1.13.0 bot ghi mọi quyết định và đo kết quả bóng hằng ngày để quyết định bằng số đo (README, mục "Vì sao chưa có MUA &
kho dữ liệu"). Giới hạn: rổ VN100 hôm nay (thiên lệch sống sót), giá điều chỉnh tới hôm nay, không có tin tức và dòng tiền trong
lần dựng lại; mẫu MUA nhỏ (15 và 22).
