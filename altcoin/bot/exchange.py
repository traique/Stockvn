"""Kết nối OKX qua ccxt – dữ liệu thị trường (public) + giao dịch (private)."""
import logging
import os
import time
import ccxt
from .config import cfg

log = logging.getLogger("exchange")

# v3.3: OKX ghi loại tài sản gốc của mỗi hợp đồng ở trường instCategory (thông tin công khai /public/instruments, ccxt để ở
# market["info"]): "1" = crypto, "3" = cổ phiếu (SKHY, SOXS, UNITREE, TSLA…), "4" = hàng hoá (CL, BZ, XAU…) – kiểm tra
# trực tiếp 02/11/2026. instFamily (SKHY-USDT…) không phân biệt được. Chỉ quét hợp đồng crypto; danh sách tên EXCLUDE
# vẫn giữ làm lớp lọc thêm (BTC/ETH, stablecoin, mã OKX chưa ghi instCategory).
CRYPTO_CATEGORIES = ("1",)


class OKX:
    def __init__(self):
        params = {
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
        if cfg.OKX_API_KEY:
            params.update(apiKey=cfg.OKX_API_KEY, secret=cfg.OKX_API_SECRET, password=cfg.OKX_PASSPHRASE)
        self.ex = ccxt.okx(params)
        if os.getenv("HTTPS_PROXY") or os.getenv("REQUESTS_CA_BUNDLE"):
            # tôn trọng proxy / CA bundle của hệ thống (ccxt mặc định bỏ qua)
            self.ex.session.trust_env = True
        if cfg.OKX_DEMO:
            self.ex.set_sandbox_mode(True)
        self._markets = None
        self._markets_ts = 0
        self.last_filtered = set()     # mã bị loại vì instCategory không phải crypto ở lượt lấy danh sách gần nhất
        self._no_category = set()      # mã OKX không ghi instCategory (chỉ lọc theo tên) – báo một lần

    # ------------------------------------------------------------ public
    def markets(self, force=False):
        if force or self._markets is None or time.time() - self._markets_ts > 3600:
            self._markets = self.ex.load_markets(True)
            self._markets_ts = time.time()
        return self._markets

    def market(self, symbol):
        return self.markets()[symbol]

    @staticmethod
    def is_crypto(m):
        """Hợp đồng có tài sản gốc là crypto theo instCategory của OKX. OKX không ghi trường này → coi là crypto (lọc
        theo tên EXCLUDE vẫn áp dụng) để danh sách quét không bị rỗng khi sàn đổi định dạng."""
        cat = str((m.get("info") or {}).get("instCategory") or "").strip()
        return not cat or cat in CRYPTO_CATEGORIES

    def top_altcoins(self, n=25):
        """Lấy n cặp USDT-SWAP crypto có volume 24h (USD) cao nhất: loại hợp đồng cổ phiếu / hàng hoá theo instCategory
        của OKX (v3.3 – bản cũ chỉ lọc theo tên, 9/50 mã trong danh sách là cổ phiếu / dầu), rồi BTC/ETH/stable theo tên."""
        mk = self.markets()
        tickers = None
        for attempt in range(3):
            try:
                tickers = self.ex.fetch_tickers(params={"instType": "SWAP"})
                break
            except Exception as e:                      # mạng chập chờn: thử lại, rồi dùng danh sách lần trước
                log.warning("OKX tickers lỗi mạng (lần %d/3): %s", attempt + 1, str(e)[:120])
                time.sleep(2 * (attempt + 1))
        if tickers is None:
            if getattr(self, "_top_cache", None):
                log.warning("Dùng tạm danh sách coin của lượt quét trước")
                return self._top_cache[:n]
            raise RuntimeError("không lấy được danh sách coin từ OKX (mạng)")
        rows, filtered, no_cat = [], set(), set()
        for sym, t in tickers.items():
            m = mk.get(sym)
            if not m or not m.get("swap") or m.get("quote") != "USDT" or not m.get("active", True):
                continue
            if not self.is_crypto(m):
                filtered.add(m["base"])
                continue
            if not str((m.get("info") or {}).get("instCategory") or "").strip():
                no_cat.add(m["base"])
            if m["base"].upper() in cfg.EXCLUDE:
                continue
            info = t.get("info", {})
            try:
                vol_usd = float(info.get("volCcy24h", 0)) * float(t.get("last") or 0)
            except (TypeError, ValueError):
                vol_usd = 0
            if not vol_usd and t.get("quoteVolume"):
                vol_usd = float(t["quoteVolume"])
            if vol_usd <= 0:
                continue
            rows.append((sym, vol_usd, t.get("percentage") or 0))
        rows.sort(key=lambda r: r[1], reverse=True)
        if filtered != self.last_filtered:
            log.info("Loại %d hợp đồng không phải crypto (cổ phiếu / hàng hoá theo instCategory của OKX): %s",
                     len(filtered), ", ".join(sorted(filtered)[:40]) + (" …" if len(filtered) > 40 else ""))
        if no_cat - self._no_category:
            log.warning("OKX không ghi instCategory cho %d mã (chỉ lọc theo tên EXCLUDE): %s", len(no_cat - self._no_category),
                        ", ".join(sorted(no_cat - self._no_category)[:20]))
            self._no_category |= no_cat
        self.last_filtered = filtered
        self._top_cache = rows
        return rows[:n]

    def ohlcv(self, symbol, timeframe, limit=200):
        return self.ex.fetch_ohlcv(symbol, timeframe, limit=limit)

    def ohlcv_since(self, symbol, timeframe, since_s, max_bars=2000):
        """Nến [ts_ms, o, h, l, c, v] mở cửa từ since_s (giây) tới hiện tại, tăng dần (gồm nến đang chạy ở cuối).
        OKX trả tối đa 300 nến/lần → tự lật trang (bot tắt vài giờ vẫn đọc đủ nến 1m của lệnh đang mở)."""
        step = self.ex.parse_timeframe(timeframe) * 1000
        cur, out = int(since_s * 1000), []
        while len(out) < max_bars:
            rows = [r for r in self.ex.fetch_ohlcv(symbol, timeframe, since=cur, limit=300) if r[0] >= cur]
            if not rows:
                break
            out += rows
            cur = int(rows[-1][0]) + step
            if cur > time.time() * 1000:
                break
        return out

    def price(self, symbol):
        t = self.ex.fetch_ticker(symbol)
        return float(t["last"])

    def prices(self, symbols):
        if not symbols:
            return {}
        t = self.ex.fetch_tickers(symbols)
        return {s: float(v["last"]) for s, v in t.items() if v.get("last")}

    def funding_rate(self, symbol):
        try:
            fr = self.ex.fetch_funding_rate(symbol)
            return float(fr.get("fundingRate") or 0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------ private
    def balance_usdt(self):
        b = self.ex.fetch_balance(params={"type": "swap"})
        usdt = b.get("USDT", {})
        return float(usdt.get("total") or 0), float(usdt.get("free") or 0)

    def set_leverage(self, symbol, lev):
        try:
            self.ex.set_leverage(lev, symbol, params={"mgnMode": "isolated"})
        except Exception as e:
            log.warning("set_leverage %s: %s", symbol, e)

    def contracts_for_notional(self, symbol, notional_usdt, price):
        """Đổi giá trị USDT -> số hợp đồng hợp lệ theo lot size của OKX."""
        m = self.market(symbol)
        ct_val = float(m.get("contractSize") or 1)
        lot = float(m["limits"]["amount"].get("min") or 1)
        step = float(m.get("precision", {}).get("amount") or lot)
        raw = notional_usdt / (price * ct_val)
        contracts = int(raw / step) * step
        if contracts < lot:
            return 0.0, lot * ct_val * price  # trả về min notional cần có
        return float(self.ex.amount_to_precision(symbol, contracts)), 0.0

    def open_position(self, symbol, side, contracts, sl, tp):
        """Mở lệnh market + SL/TP (OCO) trên OKX. side: long|short."""
        self.set_leverage(symbol, cfg.LEVERAGE)
        order_side = "buy" if side == "long" else "sell"
        params = {
            "tdMode": "isolated",
            "posSide": "net",
            "attachAlgoOrds": [{
                "tpTriggerPx": self.ex.price_to_precision(symbol, tp),
                "tpOrdPx": "-1",
                "slTriggerPx": self.ex.price_to_precision(symbol, sl),
                "slOrdPx": "-1",
                # v3.3: kích hoạt theo giá last – cùng loại giá bot dùng cho TP1/trailing, paper và mô phỏng bóng
                "tpTriggerPxType": "last",
                "slTriggerPxType": "last",
            }],
        }
        order = self.ex.create_order(symbol, "market", order_side, contracts, params=params)
        time.sleep(1)
        try:
            o = self.ex.fetch_order(order["id"], symbol)
            fill = float(o.get("average") or o.get("price") or 0)
        except Exception:
            fill = 0.0
        return order["id"], fill

    def close_position(self, symbol, side, contracts):
        """Đóng vị thế bằng lệnh market reduceOnly rồi huỷ MỌI lệnh SL/TP còn treo của mã (v3.3: cả loại oco – SL/TP gắn
        kèm lệnh mở và lệnh dời SL đều là oco, bản cũ chỉ huỷ conditional nên để lại lệnh reduce-only mồ côi có thể đóng
        nhầm vị thế mới cùng mã). Lệnh đóng lỗi → ném lỗi, KHÔNG huỷ SL (vị thế còn mở vẫn được bảo vệ)."""
        order_side = "sell" if side == "long" else "buy"
        params = {"tdMode": "isolated", "posSide": "net", "reduceOnly": True}
        o = self.ex.create_order(symbol, "market", order_side, contracts, params=params)
        try:
            algos = algo_orders(self.ex, symbol)
        except Exception as e:
            log.warning("đọc SL/TP còn treo %s: %s", symbol, e)
            algos = []
        for a in algos:
            try:
                self.ex.cancel_order(a["id"], symbol, params={"stop": True})
            except Exception as e:
                log.warning("huỷ SL/TP %s %s: %s", symbol, a.get("id"), e)
        return o

    def close_fill_price(self, symbol, since_ts, side, exclude=()):
        """Giá khớp trung bình của các lệnh ĐÓNG vị thế (sell với long, buy với short) kể từ since_ts.
        Dùng khi sàn tự khớp SL/TP để ghi PnL thật thay vì ước lượng. None nếu không lấy được.
        exclude: mã lệnh bot tự đóng một phần (cắt bớt sau khi khớp, chốt TP1) – PnL các phần đó đã ghi ở realized,
        không được tính lẫn vào giá đóng phần còn lại."""
        try:
            rows = self.ex.fetch_my_trades(symbol, since=int(since_ts * 1000), limit=50)
        except Exception as e:
            log.warning("fetch_my_trades %s: %s", symbol, e)
            return None
        want = "sell" if side == "long" else "buy"
        skip = {str(x) for x in exclude or ()}
        fills = [(float(x.get("price") or 0), float(x.get("amount") or 0)) for x in rows
                 if x.get("side") == want and str(x.get("order") or "") not in skip]
        fills = [(p, a) for p, a in fills if p > 0 and a > 0]
        qty = sum(a for _, a in fills)
        return sum(p * a for p, a in fills) / qty if qty else None

    def fetch_positions(self):
        """Vị thế đang mở. v3.3: lỗi mạng/API → NÉM lỗi (bản cũ trả [] – bot tưởng sàn đã đóng mọi lệnh, ghi đóng
        bằng giá ước lượng rồi thôi quản lý vị thế thật và mở thêm lệnh vượt trần rủi ro)."""
        try:
            pos = self.ex.fetch_positions(params={"instType": "SWAP"})
        except Exception as e:
            log.warning("fetch_positions: %s", e)
            raise
        return [p for p in pos if float(p.get("contracts") or 0) > 0]


def algo_orders(ex, symbol):
    """Lệnh SL/TP đang treo của `symbol` (ex: đối tượng ccxt okx): cả loại conditional (một mức) lẫn oco (SL + TP,
    gồm SL/TP gắn kèm lệnh mở). Dùng chung cho close_position và LiveEngine._replace_algo."""
    seen, out = set(), []
    for kind in ("conditional", "oco"):
        for a in ex.fetch_open_orders(symbol, params={"ordType": kind}) or []:
            if a.get("id") not in seen:
                seen.add(a.get("id"))
                out.append(a)
    return out
