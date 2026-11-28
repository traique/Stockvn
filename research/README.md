# Bộ kiểm định point-in-time (Phòng thủ, Động lượng)

Mục đích: con số hiệu quả của chiến lược **Phòng thủ** (sổ mặc định cho tín hiệu thật) và **Động lượng** chạy lại
được từ repo này, **không thiên lệch sống sót**. Trước đây các con số trong `tradingagents/trading/factor.py` và
`momentum.py` (15,6%/năm 2013–10/2026…) đến từ một bộ kiểm định bên ngoài không có trong repo (kế hoạch đợt đo
29/11/2026, PR 6).

Thư mục này **không nằm trong gói Botkeep** (builder chỉ đóng gói `tradingagents/`, `vnbot/`, `altcoin/bot/`) và
bot không gọi tới nó. Dữ liệu tải về nằm ở `research/out/` (không commit).

## Chạy lại

Từ gốc repo, cần mạng (nguồn công khai, không khoá), khoảng một giờ cho lần tải đầu:

```bash
python -m research.pit.fetch                 # tải: VNINDEX, thành phần HOSE từng phiên, giá VCI từng mã
python -m research.pit.evaluate              # bảng kết quả → màn hình + research/out/ket_qua_<ngày>.md/.json
python -m research.pit.evaluate --variants --write research/KET_QUA.md
python tests/run_all.py research_pit         # test offline của bộ kiểm định (không cần mạng)
```

`fetch` chạy lại được giữa chừng: tháng / mã đã có thì bỏ qua (`--refresh` để tải lại), `--as-of YYYY-MM-DD`
để cố định ngày dữ liệu.

## Dữ liệu và luật

| Phần | Nguồn | Point-in-time thế nào |
|---|---|---|
| Lịch phiên, VNINDEX | DNSE, nến ngày từ 06/2011 | Đủ 200 phiên cho MA200 và 252 phiên cho điểm xếp hạng trước 2013 |
| Thành phần HOSE | VNDirect `stock_prices?q=floor:HOSE~date:D`, **mỗi phiên** từ 02/01/2013 | Sàn của chính ngày D. Đã kiểm: 15/03/2013 có ITA, KSA, HVG, PVF (nay đã rời HOSE), không có FLC, SHB (lúc đó ở HNX) |
| Giá, thanh khoản, vốn hoá | VCI `price-history` (cùng nguồn `history_of` của bot khi chạy thật) | Giá chưa điều chỉnh và số cổ phiếu **của ngày đó**; giá đã điều chỉnh tới ngày tải cho khớp lệnh |
| Sàn trước 2013 / sàn của nến | Biên độ trần VCI: trước 15/01/2013 HOSE 5%, HNX 7%, UPCoM 10%; từ đó 7%, 10%, 15% | Chỉ dùng khi VNDirect chưa có (trước 2013) và cho bước giá của nến |

- **Luật chọn rổ là luật của bot**: `factor.liquid_pool` → `factor.cap_universe` (100 mã HOSE thanh khoản nhất
  theo trung vị giá trị khớp 60 phiên, ≥ 250 phiên, giá ≥ 5.000đ; rồi 50 mã vốn hoá lớn nhất). Chỉ "ảnh chụp"
  từng mã (`factor.snapshot`) được tính dạng mảng cho nhanh; test chứng minh hai cách cho cùng kết quả.
- **Chạy bằng code của bot**: Phòng thủ là `factor.run` với `universe=` rổ point-in-time; Động lượng dùng
  `momentum.decide` / `momentum.plan` của bot trên rổ VN30 giả lập (30 hoặc 50 mã vốn hoá lớn, dựng lại cuối mỗi
  tháng, dùng cho tháng sau). Ledger của bot: phí mua 0,15%, phí bán 0,15% + thuế 0,1%, trượt giá mỗi chiều, khớp
  giá mở cửa phiên sau, T+2, lô 100, bước giá, phiên trắng bên không khớp; tiền mặt không lãi.
- **Giai đoạn** (cố định từ 15/10/2026): TRAIN 2013–2020, VALID 2021–2023, TEST 2024 → ngày dữ liệu, mỗi giai đoạn
  chạy riêng với 1 tỉ đồng; ALL là một lần chạy liền. CAGR theo ngày lịch, sụt tối đa trên NAV cuối phiên, Sharpe
  từ lợi nhuận ngày × √252.
- **Đo thiên lệch sống sót**: cùng luật nhưng danh sách mã HOSE là danh sách **hôm nay** cho mọi phiên – cách các
  backtest cũ đã làm.

## Giới hạn

- VCI không có dữ liệu cho một số mã đã sáp nhập sớm (ví dụ PVF năm 2013): chúng không vào được rổ vì không có số
  cổ phiếu để xếp vốn hoá – như bot thật khi VCI không trả số cổ phiếu. Danh sách nằm trong báo cáo kết quả.
- Giá đã điều chỉnh tới ngày tải; VNINDEX là chỉ số giá (không cổ tức) nên so sánh có lợi cho chiến lược khoảng
  1,5–2,5 điểm %/năm.
- Phiên tạm ngừng không có nến (khối lượng 0): không xếp hạng, lệnh chờ phiên sau.
- Kết quả quá khứ không hứa hẹn tương lai. TEST đã được xem; mọi thay đổi tham số chỉ nên quyết theo TRAIN + VALID.

## Kết quả (dữ liệu tới 27/11/2026)

Bảng đầy đủ: [`KET_QUA.md`](KET_QUA.md) (sinh bởi `python -m research.pit.evaluate --variants --write research/KET_QUA.md`).
CAGR / sụt tối đa / Sharpe, sau phí, thuế, trượt giá:

| | TRAIN 2013–2020 | VALID 2021–2023 | TEST 2024–11/2026 | ALL 2013–11/2026 |
|---|---|---|---|---|
| **Phòng thủ, rổ point-in-time** | 18,9% / −23,0% / 1,44 | 14,8% / −9,7% / 1,13 | 7,7% / −20,0% / 0,54 | **16,0% / −23,0% / 1,17** |
| Kiểm định ngoài 15/10/2026 (để đối chiếu) | 18,1% / −25,9% / 1,37 | 13,9% / −12,0% / 0,99 | 8,1% / −20,8% / 0,55 | 15,6% / −25,9% / 1,11 |
| Phòng thủ, danh sách HOSE hôm nay (thiên lệch) | 18,5% / −24,1% | 13,1% / −12,5% | 8,7% / −20,1% | 15,7% / −24,1% |
| VNINDEX (chỉ số giá) | 13,1% / −45,3% | 0,8% / −40,3% | 14,0% / −18,1% | 10,5% / −45,3% |
| Động lượng, VN30 giả lập point-in-time | 5,4% / −40,1% | −1,3% / −46,3% | 8,0% / −33,5% | 5,1% / −46,3% |
| Động lượng, 50 vốn hoá lớn point-in-time | 8,8% / −44,3% | 14,9% / −45,5% | 9,3% / −34,8% | 10,3% / −45,6% |

Đọc kết quả:

- **Con số của Phòng thủ nay chạy lại được từ repo** và khớp bộ kiểm định ngoài trong 1 điểm %/năm ở mọi giai đoạn
  (khác nguồn: 533 mã HOSE từ 2013 theo VNDirect so với 568 mã của bộ cũ; 19 mã VCI không có dữ liệu). Kết luận cũ
  giữ nguyên: lợi thế là **rủi ro** (sụt tối đa −23% so với −45% của VNINDEX, Sharpe 1,17 so với 0,65), không phải
  lợi nhuận trong pha tăng – TEST 2024–2026 thua VNINDEX (7,7% so với 14,0%).
- **Thiên lệch sống sót nhỏ với luật này** (+0,3 điểm %/năm trên ALL, ngược dấu ở VALID): rổ 50 vốn hoá lớn nhất gần
  như toàn mã còn sống; 147 mã từng vào rổ, chỉ 9 nay rời HOSE (CAV, FLC, HBC, HNG, HVG, ITA, PXS, ROS, SII).
- **Động lượng** trên rổ point-in-time đúng như đã ghi trong `momentum.py`: 5,1%/năm với sụt −46% – thua VNINDEX; rổ
  50 mã khá hơn (10,3%) nhưng sụt tối đa vẫn −46%. Không có lý do đổi sổ mặc định.
- **Hai phương án còn treo từ 23/10** (`--variants`): lọc MA200 hằng ngày đệm 2% – TRAIN kém hơn (17,3%), VALID/TEST
  tương đương, sụt TEST nhẹ hơn (−17,5%); thêm động lượng 6 tháng ×0,5 – TRAIN hơn (21,0%) nhưng VALID kém rõ (11,2%,
  sụt −22,3% so với −9,7%). Không phương án nào tốt hơn ở cả TRAIN lẫn VALID → **giữ bộ `FACTOR_FIXED`** như đã quyết.
- TEST đã được nhìn (nhiều lần, từ 15/10). Mọi quyết định tham số sau này chỉ dựa vào TRAIN và VALID; TEST chỉ để báo
  cáo. Kết quả quá khứ không hứa hẹn tương lai.
