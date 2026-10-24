# Backtest 24/10/2026 - ba ý Wyckoff/VSA từ file AmiBroker

Nguồn ý tưởng: file `Wyckoff_VSA_Master.afl` (chỉ báo AmiBroker). File gốc chọn vùng giá, Spring và kênh giá bằng cả những nến **sau** điểm được đánh dấu, nên nhãn cũ trên biểu đồ tự đổi khi có nến mới và không backtest được. Ở đây ba ý được viết lại để mỗi phiên chỉ dùng nến **đến chính phiên đó** (`wyckoff.py`; `test_wyckoff.py` kiểm tra rằng nến sau không bao giờ làm đổi kết quả của phiên trước).

**Kết luận: không ý nào tốt hơn cách bot đang làm, nên không bật ý nào.** Code nghiên cứu nằm trong thư mục này, không có trong gói của bot.

## Dữ liệu và cách đo

- **39 mã:** 13 mã anh từng giao dịch và VN30. Nến ngày (giá đã điều chỉnh quyền, có khối lượng) từ 10/2021 đến 23/10/2026; tín hiệu công khai của sieutinhieu.vn 08/2024–10/2026 (582 lần MUA).
- **Vùng giá của một phiên:** 60 phiên trước nó (thử lại với 120 phiên). Hỗ trợ là đáy thấp nhất, kháng cự là đỉnh cao nhất. Vị trí trong kênh: 0% ở hỗ trợ, 100% ở kháng cự.
- **Sự kiện:** ngưỡng đặt trước theo sách, không dò cho khớp dữ liệu.
  - **Spring:** vùng rộng không quá 40%; giá thấp nhất thủng hỗ trợ nhưng đóng cửa trở lại trên hỗ trợ.
  - **Test cạn cung:** phiên giảm, khối lượng ≤ 0,75 lần trung bình 20 phiên, biên độ hẹp, giá ở 30% dưới của kênh.
  - **SOW:** nến giảm đóng cửa dưới hỗ trợ, khối lượng ≥ 1,25 lần trung bình.
- **Giai đoạn:** học 01/2022–06/2024, kiểm tra 07/2024–10/2026. Kết quả chỉ được tính khi đúng ở cả hai giai đoạn.
- **Điều kiện giao dịch:** T+2, phí mua 0,15%, phí bán 0,15% + thuế 0,1%.
- **Độ tin cậy:** "khoảng 90%" là khoảng tin cậy bootstrap của phần chênh lệch. Khoảng có chứa 0 nghĩa là không phân biệt được với may rủi.

Chi tiết từng con số: `wyckoff.txt`.

## Ý 1 - Vị trí trong kênh làm bộ lọc MUA

Câu hỏi: có nên bỏ các lệnh MUA khi giá đã ở 70–100% kênh (sát trần mà chưa vượt) không?

| Nguồn tín hiệu MUA | Giai đoạn | 70–100% kênh | Các vùng khác | Bỏ lệnh 70–100% thì |
|---|---|---|---|---|
| Quy tắc TB20+MACD (bot ước tính trang) | học 2022–24 | −0,19%/lệnh | +0,67% | tốt hơn nhưng không chắc: [−0,30; +2,04] |
| Quy tắc TB20+MACD | kiểm tra 2024–26 | **+3,08%** | +0,28% | **xấu đi rõ:** [−4,95; −0,69] |
| Siêu tín hiệu, thoát R4 | 2024–26 | **+3,46%** | +0,45% | **xấu đi rõ:** [−5,44; −0,57] |

- Kết quả **đảo chiều giữa hai giai đoạn**. Trong 2024–2026, mua khi giá đã ở cao trong kênh lại là nhóm tốt nhất. Ngược lại, mua sát hỗ trợ (dưới 35% kênh) là nhóm kém nhất: −0,52%/lệnh với tín hiệu của trang, −1,72%/lệnh trên 13 mã của anh.
- Kết luận: **không thêm bộ lọc này.** Đi ngược lại (chỉ mua khi giá đã cao) cũng không nên, vì giai đoạn học cho kết quả ngược lại.
- Quant Engine chỉ phát 14 lệnh MUA trong 3,5 năm trên VN30, quá ít để đo, nên ý này chưa kiểm tra được trên Engine.

## Ý 2 - Spring và test cạn cung làm điểm mua

**a) Mua riêng lẻ.** Vào lệnh ở giá mở cửa phiên sau, cắt lỗ dưới đáy Spring (test cạn cung: 2% dưới hỗ trợ), giữ 20 phiên. Đối chứng là mua bất kỳ phiên nào ở đáy kênh với cùng cách cắt lỗ.

| | Học 2022–24 | Kiểm tra 2024–26 | Kênh 120 phiên |
|---|---|---|---|
| Đối chứng: mua ở đáy kênh | −0,48% | −0,81% | −0,01% |
| Spring | **−1,85%** (kém hơn rõ) | −0,64% (như nhau) | **−1,28%** (kém hơn rõ) |
| Test cạn cung | −1,00% (như nhau) | −1,03% (như nhau) | −0,22% (như nhau) |

Spring thắng chỉ 23–32% số lần và kém hơn mua bừa ở đáy kênh. Khối lượng thấp hay cao đều không cứu được.

**b) Vào sớm hơn Siêu tín hiệu.** Cách làm, chỉ dùng điều biết được lúc đó:
- mua ở Spring hoặc test cạn cung khi trang đang ở trạng thái BÁN;
- nếu trang chuyển MUA trong 15 phiên thì giữ như lệnh của trang (R4);
- nếu không thì bán sau 15 phiên, hoặc cắt lỗ trước đó.

| | Chỉ theo trang (R4) | Thêm vào sớm |
|---|---|---|
| 39 mã | 582 lệnh, +1,14%/lệnh, tổng **+666** điểm % | 733 lệnh, +0,68%/lệnh, tổng **+495** điểm % |
| 13 mã của anh | 195 lệnh, +0,62%/lệnh, tổng **+121** | 258 lệnh, −0,02%/lệnh, tổng **−6** |

- Trên 39 mã, cứ 295 lần vào sớm thì 144 lần trang MUA thật (mua được rẻ hơn); 151 lần còn lại trang không MUA hoặc bị cắt lỗ trước. Phần lỗ ở các lần sai lớn hơn phần lợi ở các lần đúng.
- **Lưu ý về cách đo:** lần chạy đầu chỉ xét các Spring mà sau đó trang **có** MUA, và ra kết quả "vào sớm +2 điểm %/lệnh". Con số đó sai vì dùng thông tin tương lai; bảng trên là phép đo đúng.

## Ý 3 - SOW làm cảnh báo bán

Giá sau SOW, tính từ giá mở cửa phiên sau, so với giá sau một phiên bất kỳ:

| | Sau 10 phiên: SOW | Mọi phiên | Sau 20 phiên: SOW | Mọi phiên |
|---|---|---|---|---|
| Học 2022–24 | +0,96% (thấp hơn 45% số lần) | −0,09% | +1,75% | +0,06% |
| Kiểm tra 2024–26 | **+1,90%** (thấp hơn 37% số lần) | +0,43% | **+4,33%** | +1,06% |
| Kênh 120 phiên | **+2,27%** | +0,25% | **+4,00%** | +0,84% |

- Sau một SOW, giá thường **hồi lên** chứ không giảm tiếp: đó là phiên bán tháo cạn kiệt, không phải tín hiệu yếu. Bán theo SOW nghĩa là bán gần đáy.
- Thêm SOW làm điểm bán vào các lệnh đang giữ: kết quả không đổi, vì cắt lỗ ⛔ và tín hiệu BÁN 🔴 của trang gần như luôn đến trước. SOW chỉ làm đổi 0–5 lệnh trên 195–702 lệnh.
- Kết luận: **không thêm cảnh báo SOW.** Giữ quy tắc đã kiểm định ở backtest 23/10: bán khi ⛔ hoặc 🔴.

## Chạy lại

Từ gốc repo, cần mạng ở bước đầu:

```bash
python docs/backtest_2026-10-24/build_data.py        # tải nến kèm khối lượng và tín hiệu của trang vào out/
python docs/backtest_2026-10-24/test_wyckoff.py      # kiểm tra không nhìn trước
python docs/backtest_2026-10-24/wyckoff_backtest.py > docs/backtest_2026-10-24/wyckoff.txt
```
