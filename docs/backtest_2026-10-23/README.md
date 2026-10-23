# Backtest 23/10/2026 - các phương án mục C

Dữ liệu: DNSE/VCI tải ngày chạy (nguồn công khai, giá đã điều chỉnh tới hôm nay), 900 phiên
2022-03-17 → 2025-10-24. Quant Engine: rổ VN30 hôm nay (30 mã). Phòng thủ: VN100 hôm nay (98 mã).
**Cả hai rổ là thành phần hiện tại, nên có thiên lệch sống sót**: con số tuyệt đối lạc quan,
chỉ dùng để so sánh các phương án với nhau trên cùng dữ liệu. Train = 2/3 đầu (tới 13/08/2024),
Test = 1/3 cuối. Điểm = lãi % − 0,5 × sụt giảm tối đa % (đúng công thức của hiệu chỉnh đêm).

- `build_dataset.py`: tải dữ liệu (chạy từ gốc repo, cần mạng; ghi vào `out/`).
- `run_variants.py engine|factor`: các phương án đợt 1 → `engine_variants.txt`, `factor_variants.txt`.
- `diagnostics.py`: phân bố action, cổng chặn, lệnh đặt/khớp, tỷ trọng đầu tư, đợt 2 → `engine_diagnostics.txt`.

Kết luận và bảng tóm tắt: `BAO_CAO_RA_SOAT_2026-10-23.md`, mục E.
