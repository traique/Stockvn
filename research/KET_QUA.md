Dữ liệu tới **2026-11-27**. Mỗi ô: CAGR / sụt tối đa / Sharpe (sau phí, thuế, trượt giá).

| Sổ | Rổ | TRAIN | VALID | TEST | ALL |
|---|---|---|---|---|---|
| VNINDEX | chỉ số giá | 13,1% / -45,3% / 0,82 | 0,8% / -40,3% / 0,14 | 14,0% / -18,1% / 0,82 | 10,5% / -45,3% / 0,65 |
| Phòng thủ | point-in-time | 18,9% / -23,0% / 1,44 | 14,8% / -9,7% / 1,13 | 7,7% / -20,0% / 0,54 | 16,0% / -23,0% / 1,17 |
| Phòng thủ | danh sách HOSE hôm nay (thiên lệch) | 18,5% / -24,1% / 1,40 | 13,1% / -12,5% / 1,00 | 8,7% / -20,1% / 0,59 | 15,7% / -24,1% / 1,13 |
| Động lượng | VN30 giả lập (30 vốn hoá lớn) point-in-time | 5,4% / -40,1% / 0,37 | -1,3% / -46,3% / 0,04 | 8,0% / -33,5% / 0,46 | 5,1% / -46,3% / 0,35 |
| Động lượng | 50 vốn hoá lớn point-in-time | 8,8% / -44,3% / 0,51 | 14,9% / -45,5% / 0,68 | 9,3% / -34,8% / 0,51 | 10,3% / -45,6% / 0,55 |
| Phòng thủ | point-in-time, lọc MA200 hằng ngày, đệm 2% | 17,3% / -26,1% / 1,35 | 15,7% / -9,3% / 1,19 | 8,1% / -17,5% / 0,55 | 15,4% / -26,1% / 1,13 |
| Phòng thủ | point-in-time, thêm động lượng 6 tháng ×0,5 | 21,0% / -24,8% / 1,44 | 11,2% / -22,3% / 0,84 | 9,4% / -19,5% / 0,62 | 16,6% / -24,8% / 1,13 |

Giai đoạn: TRAIN 2013-01-02 → 2020-12-31; VALID 2021-01-04 → 2023-12-29; TEST 2024-01-02 → 2026-11-27; ALL 2013-01-02 → 2026-11-27.
Mã từng có mặt trên HOSE từ 2013: 533; VCI không có dữ liệu: 19 (AGD, ALP, BCI, BHS, CLP, GTN, KTB, NHS, NHW, PHT, PME, PTK, PVF, SEC, TAC…).
Mã từng vào rổ Phòng thủ point-in-time (cuối mỗi tháng): 147, trong đó 9 mã nay không còn trên HOSE (CAV, FLC, HBC, HNG, HVG, ITA, PXS, ROS, SII).
