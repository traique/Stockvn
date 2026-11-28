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

## Kết quả

Xem [`KET_QUA.md`](KET_QUA.md) (sinh bởi `python -m research.pit.evaluate --variants --write research/KET_QUA.md`).
