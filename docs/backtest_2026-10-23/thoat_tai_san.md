# Backtest 23/10/2026 - quy tắc bán cho danh mục Tài sản

Câu hỏi: với các mã mua theo tín hiệu MUA của sieutinhieu.vn, bán theo cảnh báo nào thì tốt nhất?

## Dữ liệu và cách làm

- **Tín hiệu:** lịch sử công khai của trang cho từng mã (30 tín hiệu gần nhất mỗi mã, 08/2024 → 10/2026), lấy ngày 23/10/2026 bằng đúng cách bot vẫn hỏi (tự xưng tên, giãn 1,5 giây). 39 mã: 13 mã anh từng giao dịch (POW, HDG, VRE, CTG, TPB, CII, DIG, CTS, SHS, FPT, NVB, SBS, GEX) và VN30. Tổng 1.170 tín hiệu, 582 lần MUA.
- **Giá:** nến ngày DNSE/VCI (đã điều chỉnh quyền, cùng cơ sở với giá tín hiệu của trang).
- **Vào lệnh:** giá mở cửa phiên sau ngày trang báo MUA. **T+2:** không bán trước chiều phiên thứ 2 sau ngày mua; lệnh bán ATO rơi vào trước đó thì dời sang giá đóng cửa chiều T+2. **Phí:** mua 0,15%, bán 0,15% + thuế 0,1% (để so các quy tắc cho công bằng; sổ Tài sản không tự trừ phí).
- **Bốn quy tắc bán:**

| | Quy tắc | Bán khi | Giá bán |
|---|---|---|---|
| R1 | 🔴 Trang BÁN | trang chốt BÁN (sau 15:10) | mở cửa phiên sau (ATO) |
| R2 | 🟠 Ước tính đổi chiều | giá thấp nhất trong phiên xuống dưới mức đổi chiều bot tính (TB20 + MACD); trang BÁN trước thì như R1 | đóng cửa hôm đó (ATC) |
| R3 | ⛔ Chỉ thủng cắt lỗ | giá thấp nhất chạm mức cắt lỗ trong kế hoạch MUA của trang; bỏ qua tín hiệu BÁN | min(giá mở cửa, mức cắt lỗ) |
| R4 | ⛔ hoặc 🔴 (bot đang báo) | cái nào đến trước | như R3 / R1 |
| R5 | lai | một nửa theo R4, một nửa giữ tới R3 | |

Mỗi lần MUA của trang là một giao dịch độc lập (cùng điểm vào cho mọi quy tắc). Giao dịch chưa có điểm bán tới 23/10/2026 tính theo giá đóng cửa ngày đó.

## Kết quả (lãi/lỗ mỗi giao dịch, đã trừ phí)

**39 mã, 582 giao dịch**

| Quy tắc | TB | Trung vị | Thắng | Lỗ TB | Lỗ quá 10% | Lỗ lớn nhất | Giữ TB (phiên) |
|---|---|---|---|---|---|---|---|
| R1 🔴 | +1,24% | −1,73% | 35,9% | −4,60% | 31 | −21,2% | 17 |
| R2 🟠 | +1,08% | −1,61% | 36,1% | −4,14% | 21 | −20,0% | 13 |
| R3 ⛔ | +12,35% | −6,51% | 22,5% | −6,97% | 29 | −16,4% | 88 |
| **R4 ⛔/🔴** | **+1,14%** | −2,24% | 33,5% | **−4,26%** | **5** | **−16,4%** | 16 |
| R5 lai | +6,75% | −4,39% | 27,3% | −5,09% | 7 | −16,4% | 52 |

**13 mã của anh, 195 giao dịch**

| Quy tắc | TB | Trung vị | Thắng | Lỗ quá 10% | Lỗ lớn nhất |
|---|---|---|---|---|---|
| R1 🔴 | +0,52% | −2,28% | 36,4% | 20 | −18,6% |
| R2 🟠 | +0,34% | −1,71% | 35,9% | 9 | −20,0% |
| R3 ⛔ | −2,03% | −6,61% | 15,4% | 10 | −16,4% |
| **R4 ⛔/🔴** | **+0,62%** | −3,03% | 33,3% | **3** | **−16,4%** |
| R5 lai | −0,70% | −4,55% | 22,6% | 4 | −16,4% |

**Chỉ tín hiệu phát trực tiếp** (trang chạy thật từ 08/2026, chưa tạo lại; 51 giao dịch, phần lớn chưa đóng): R4 −0,33%, R1 −0,36%, R2 −0,66%, R3 −0,86%. Mẫu còn nhỏ, chỉ để tham khảo.

## Kết luận

1. **Giữ cách bot đang báo (⛔ hoặc 🔴, R4).** Lãi trung bình ngang chờ trang BÁN (R1), nhưng số lệnh lỗ quá 10% giảm từ 31 xuống 5 (từ 20 xuống 3 trên mã của anh) và lệnh lỗ nặng nhất từ −21% còn −16%. Trên 13 mã của anh, R4 là quy tắc tốt nhất.
2. **🟠 Sớm không nên là lệnh bán.** Bán ở ATC khi bot ước tính đổi chiều chỉ tốt hơn chờ trang chốt trong 262/547 lần (48%), trung bình kém hơn một chút; lợi ích duy nhất là giữ ngắn hơn 4 phiên. Từ v1.1.0 cảnh báo 🟠 ghi "theo dõi, chờ trang chốt sau 15:10; chỉ bán sớm khi sát mức cắt lỗ".
3. **Chỉ giữ tới khi thủng cắt lỗ (R3) không hợp với danh mục của anh.** Con số trung bình +12% đến từ vài xu hướng rất lớn trong năm 2025 (VIC từ 20 nghìn lên 236 nghìn, VHM, BSR: các lần MUA chồng nhau trên cùng một xu hướng được đếm nhiều lần). Trung vị −6,5%, chỉ thắng 22,5%, giữ trung bình 88 phiên; trên 13 mã của anh thì lỗ (−2%). Phương án lai (R5) cũng thua R4 trên mã của anh.
4. **Lưu ý:** lịch sử trước 08/2026 của trang được trang **tạo lại hàng loạt** (có thể thuận lợi hơn thực tế); giá đã điều chỉnh quyền; rổ VN30 là thành phần hiện tại (thiên lệch sống sót). Các con số chỉ dùng để so các quy tắc với nhau. Kết quả thật của từng cảnh báo được bot chấm dần (giá sau 5 và 10 phiên, trang `/taisan` và báo cáo thứ Sáu).

## Chạy lại

Từ gốc repo, cần mạng (lần đầu tải tín hiệu và giá vào `out/`, khoảng 1-2 phút):

```bash
python docs/backtest_2026-10-23/exit_rules.py        > docs/backtest_2026-10-23/exit_rules.txt
python docs/backtest_2026-10-23/exit_rules_indep.py  > docs/backtest_2026-10-23/exit_rules_indep.txt
```

`exit_rules.py`: mỗi mã một vị thế một lúc (lần MUA đến khi đang giữ thì bỏ qua). `exit_rules_indep.py`: mỗi lần MUA là một giao dịch độc lập (bảng ở trên).
