"""Sổ Grid spot chạy ảo – v3.5 (Phần B của kế hoạch 29/11/2026).

Giả lập grid spot KHÔNG đòn bẩy như grid spot của OKX trên từng cặp (GRID_PAIRS, mặc định BTC-USDT và ETH-USDT), mỗi cặp một
sổ riêng (GRID_CAPITAL USDT ảo), tham số cố định trong đợt đo. Chạy trong cùng tiến trình với bot futures, trên một luồng
riêng (GridRunner), KHÔNG dùng chung vốn với sổ futures, và CHỈ dùng API công khai (nến 1D, nến 1m) – không bao giờ gọi API đặt
lệnh, kể cả khi MODE=live (test_grid.py khẳng định).

Luật (GridBook – thuần, không mạng, dùng lại trong test và backtest):
  * Mở vòng: vùng giá = đáy / đỉnh của GRID_RANGE_DAYS nến ngày ĐÃ ĐÓNG; chia GRID_LEVELS mức (geometric: tỉ lệ bằng nhau;
    arithmetic: khoảng bằng nhau). Vốn chia đều cho GRID_LEVELS − 1 "ô" (giữa hai mức kề nhau). Ô có mức dưới THẤP hơn giá
    hiện tại: đặt lệnh mua ảo ở mức dưới; ô có mức dưới CAO hơn hoặc bằng giá: mua sẵn lượng coin của ô đó ở giá hiện tại
    (phí taker) và đặt lệnh bán ở mức trên – như OKX làm lúc tạo grid. Ghi giá bắt đầu để tính HODL.
  * Mỗi nến 1m ĐÃ ĐÓNG: một mức chỉ khớp khi giá ĐI QUA nó (đoạn giá đi từ trên xuống dưới mức mua, từ dưới lên trên mức bán),
    chạm sát không khớp. Nến chạm nhiều mức: xử lý theo đường giá mở → (thấp → cao nếu nến tăng, cao → thấp nếu nến giảm) → đóng;
    mỗi mức tối đa một lần mỗi nến. Khớp mua ở mức i → đặt bán ở mức i+1 (lượng coin vừa mua); khớp bán ở mức i+1 → đặt mua lại
    ở mức i; lãi đã khoá của vòng mua–bán = lượng × (mức i+1 − mức i) − phí hai chiều (maker).
  * Dừng: giá đóng nến 1m ra ngoài vùng quá GRID_STOP_OUT_PCT → vòng dừng, giữ nguyên coin / USDT, huỷ lệnh ảo, ghi sự kiện và
    báo một tin. KHÔNG tự đặt lại vùng: vòng mới chỉ mở khi GRID_ROUND trên panel lớn hơn số vòng đã chạy (người quyết định).
  * Mất kết nối: lúc chạy lại lấy bù mọi nến 1m thiếu kể từ nến đã xử lý cuối (last_bar) rồi xử lý tuần tự – kết quả như chạy
    liên tục (test).
Chỉ số: lãi grid đã khoá, lãi/lỗ tồn kho (giá trị sổ − vốn − lãi đã khoá), giá trị sổ so với HODL (mua 100% coin ở giá bắt đầu,
phí taker) và so với giữ 100% USDT, sụt tối đa (theo nến 1m), số vòng mua–bán mỗi ngày, tỉ lệ nến có giá đóng trong vùng.
"""
import logging
import threading
import time
from .config import cfg

log = logging.getLogger("grid")
CLOSED_LAG = 5


def grid_levels(low, high, n, spacing="geometric"):
    """n mức giá từ low tới high (cả hai đầu)."""
    n = max(2, int(n))
    if spacing == "arithmetic":
        step = (high - low) / (n - 1)
        return [low + step * i for i in range(n)]
    ratio = (high / low) ** (1.0 / (n - 1))
    return [low * ratio ** i for i in range(n)]


def price_range(daily, days):
    """(đáy, đỉnh) của `days` nến ngày đã đóng cuối cùng; daily: [ts_ms, o, h, l, c, v] cũ → mới, GỒM nến đang chạy ở cuối."""
    closed = daily[:-1][-days:] if len(daily) > 1 else []
    if not closed:
        raise ValueError("không đủ nến ngày để tính vùng giá")
    return min(float(c[3]) for c in closed), max(float(c[2]) for c in closed)


def spot_symbol(pair):
    """'BTC-USDT' → 'BTC/USDT' (mã spot của ccxt)."""
    return pair.replace("-", "/").replace(":USDT", "")


class GridBook:
    """Một sổ grid thuần: trạng thái là dict (lưu JSON vào grid_runs.state)."""

    def __init__(self, state):
        self.s = state

    @classmethod
    def start(cls, low, high, levels, spacing, capital, price, ts, maker_fee, taker_fee):
        lv = grid_levels(low, high, levels, spacing)
        n_cells = len(lv) - 1
        q = capital / n_cells                     # USDT mỗi ô
        usdt, coin, fee, orders = float(capital), 0.0, 0.0, []
        for i in range(n_cells):
            lo, hi = lv[i], lv[i + 1]
            if lo < price:
                orders.append({"i": i, "side": "buy", "price": lo, "amount": q / lo})
            else:
                amt = q / lo                      # coin của ô này mua sẵn ở giá hiện tại (rẻ hơn mức dưới của ô)
                cost = amt * price
                f = cost * taker_fee
                usdt -= cost + f
                fee += f
                coin += amt
                orders.append({"i": i, "side": "sell", "price": hi, "amount": amt, "buy": price})
        st = {"levels": lv, "q": q, "usdt": usdt, "coin": coin, "fees": fee, "realized": 0.0, "rounds": 0,
              "orders": orders, "capital": float(capital), "start_price": float(price), "started_at": float(ts),
              "maker_fee": maker_fee, "taker_fee": taker_fee, "last_bar": None, "bars": 0, "bars_in": 0,
              "peak": float(capital), "max_dd": 0.0, "price": float(price), "stopped": None, "low": low, "high": high,
              "hodl_coin": float(capital) * (1 - taker_fee) / float(price)}
        return cls(st)

    # ---------- giá trị
    def value(self, price=None):
        p = self.s["price"] if price is None else price
        return self.s["usdt"] + self.s["coin"] * p

    def hodl(self, price=None):
        p = self.s["price"] if price is None else price
        return self.s["hodl_coin"] * p

    def metrics(self):
        s = self.s
        v = self.value()
        days = max((s["bars"] or 0) / 1440.0, 1e-9)
        return {"value": round(v, 4), "usdt": round(s["usdt"], 4), "coin": s["coin"], "price": s["price"],
                "hodl": round(self.hodl(), 4), "cash": s["capital"], "realized": round(s["realized"], 4),
                "inventory_pnl": round(v - s["capital"] - s["realized"], 4), "fees": round(s["fees"], 4),
                "ret_pct": round(100 * (v / s["capital"] - 1), 3), "hodl_pct": round(100 * (self.hodl() / s["capital"] - 1), 3),
                "vs_hodl_pct": round(100 * (v - self.hodl()) / s["capital"], 3), "max_dd_pct": round(100 * s["max_dd"], 3),
                "rounds": s["rounds"], "rounds_per_day": round(s["rounds"] / days, 2) if s["bars"] else 0,
                "in_range_pct": round(100 * s["bars_in"] / s["bars"], 1) if s["bars"] else None,
                "bars": s["bars"], "stopped": s["stopped"], "low": s["low"], "high": s["high"], "levels": len(s["levels"])}

    # ---------- khớp
    def _fill(self, o, ts, fills):
        s, lv = self.s, self.s["levels"]
        fee = s["maker_fee"]
        if o["side"] == "buy":
            cost = o["amount"] * o["price"]
            f = cost * fee
            s["usdt"] -= cost + f
            s["coin"] += o["amount"]
            s["fees"] += f
            fills.append((ts, o["i"], o["price"], "buy", o["amount"], f, 0.0))
            return {"i": o["i"], "side": "sell", "price": lv[o["i"] + 1], "amount": o["amount"], "buy": o["price"], "buy_fee": f}
        proceeds = o["amount"] * o["price"]
        f = proceeds * fee
        s["usdt"] += proceeds - f
        s["coin"] -= o["amount"]
        if abs(s["coin"]) < 1e-12:
            s["coin"] = 0.0
        s["fees"] += f
        profit = o["amount"] * (o["price"] - o["buy"]) - f - float(o.get("buy_fee") or 0)
        s["realized"] += profit
        s["rounds"] += 1
        fills.append((ts, o["i"] + 1, o["price"], "sell", o["amount"], f, profit))
        return {"i": o["i"], "side": "buy", "price": lv[o["i"]], "amount": s["q"] / lv[o["i"]]}

    def _segment(self, a, b, ts, done, fills):
        """Giá đi từ a tới b: khớp mọi lệnh có mức nằm HẲN giữa (đi qua), theo thứ tự gặp; mỗi mức một lần mỗi nến."""
        s = self.s
        if a == b:
            return
        down = b < a
        cands = [o for o in s["orders"]
                 if (o["side"] == "buy" if down else o["side"] == "sell")
                 and (min(a, b) < o["price"] < max(a, b)) and (o["i"], o["side"]) not in done]
        cands.sort(key=lambda o: o["price"], reverse=down)
        for o in cands:
            done.add((o["i"], o["side"]))
            s["orders"].remove(o)
            s["orders"].append(self._fill(o, ts, fills))

    def step(self, bar):
        """Một nến 1m đã đóng [ts_ms, o, h, l, c, v]. Trả [(ts, level, price, side, amount, fee, profit)] các lần khớp;
        đặt s['stopped'] = lý do khi phá vùng."""
        s = self.s
        if s["stopped"]:
            return []
        ts = float(bar[0]) / 1000
        o, h, l, c = (float(x) for x in bar[1:5])
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        done, fills = set(), []
        for a, b in zip(path, path[1:]):
            self._segment(a, b, ts, done, fills)
        s["price"], s["last_bar"], s["bars"] = c, ts, s["bars"] + 1
        if s["low"] <= c <= s["high"]:
            s["bars_in"] += 1
        v = self.value()
        s["peak"] = max(s["peak"], v)
        if s["peak"]:
            s["max_dd"] = max(s["max_dd"], (s["peak"] - v) / s["peak"])
        pct = cfg.GRID_STOP_OUT_PCT
        if c > s["high"] * (1 + pct):
            s["stopped"] = f"giá đóng {c:.6g} vượt đỉnh vùng {s['high']:.6g} quá {pct * 100:g}%"
        elif c < s["low"] * (1 - pct):
            s["stopped"] = f"giá đóng {c:.6g} thủng đáy vùng {s['low']:.6g} quá {pct * 100:g}%"
        if s["stopped"]:
            s["orders"] = []          # huỷ lệnh ảo, giữ nguyên coin / USDT
        return fills


class GridRunner:
    """Luồng riêng: mỗi phút đọc nến 1m đã đóng của từng cặp (lấy bù nến thiếu), chạy GridBook, ghi DB, báo khi dừng."""

    def __init__(self, okx, store, notify, pairs=None, active=None):
        """active: hàm trả True khi instance này được chạy grid (leader) – nhiều instance chung DB không xử lý nến hai lần."""
        self.okx, self.store, self.notify = okx, store, notify
        self.active = active or (lambda: True)
        self.pairs = list(pairs if pairs is not None else cfg.GRID_PAIRS)
        self.books = {}            # pair -> (run_id, GridBook)
        self.last_equity = {}      # pair -> ts của dòng grid_equity gần nhất
        self._stop = threading.Event()

    # ---------- dữ liệu công khai
    def _ohlcv(self, sym, tf, since_s=None, limit=200):
        if since_s is None:
            return self.okx.ex.fetch_ohlcv(sym, tf, limit=limit)
        return self.okx.ohlcv_since(sym, tf, since_s, max_bars=20000)

    def _last_price(self, sym):
        """Giá last của cặp spot (fetch_ticker – API công khai)."""
        return float(self.okx.price(sym))

    # ---------- vòng
    def _open_round(self, pair, now):
        sym = spot_symbol(pair)
        daily = self._ohlcv(sym, "1d", limit=cfg.GRID_RANGE_DAYS + 3)
        low, high = price_range(daily, cfg.GRID_RANGE_DAYS)
        price = self._last_price(sym)
        book = GridBook.start(low, high, cfg.GRID_LEVELS, cfg.GRID_SPACING, cfg.GRID_CAPITAL, price,
                              int(now // 60) * 60, cfg.GRID_MAKER_FEE, cfg.GRID_TAKER_FEE)
        book.s["last_bar"] = int(now // 60) * 60 - 60     # nến từ phút này trở đi mới xét
        rid = self.store.grid_add_run(pair, cfg.GRID_ROUND, low, high, cfg.GRID_LEVELS, cfg.GRID_SPACING, cfg.GRID_CAPITAL,
                                      price, now, book.s)
        self.books[pair] = (rid, book)
        log.info("Grid %s vòng %d mở: vùng %.6g–%.6g (%d ngày), %d mức %s, vốn %.0f USDT, giá %.6g, mua sẵn %.6g coin",
                 pair, cfg.GRID_ROUND, low, high, cfg.GRID_RANGE_DAYS, cfg.GRID_LEVELS, cfg.GRID_SPACING, cfg.GRID_CAPITAL,
                 price, book.s["coin"])
        self.notify(f"🪙 Grid {pair} vòng {cfg.GRID_ROUND} mở: vùng {low:.6g}–{high:.6g}, {cfg.GRID_LEVELS} mức, "
                    f"vốn {cfg.GRID_CAPITAL:.0f} USDT ảo, giá bắt đầu {price:.6g}")
        return rid, book

    def _load(self, pair, now):
        """Vòng đang chạy của cặp (từ DB), hoặc mở vòng mới khi chưa có / vòng trước đã dừng mà GRID_ROUND đã tăng."""
        if pair in self.books:
            return self.books[pair]
        run = self.store.grid_open_run(pair)
        if run:
            import json
            book = GridBook(json.loads(run["state"]))
            self.books[pair] = (run["id"], book)
            return self.books[pair]
        last = self.store.grid_runs(pair, 1)
        if last and int(last[0].get("round") or 1) >= cfg.GRID_ROUND:
            return None            # vòng đã dừng – chờ người tăng GRID_ROUND
        return self._open_round(pair, now)

    def tick(self, now=None):
        """Một lượt cho mọi cặp: lấy nến 1m đã đóng kể từ nến cuối, xử lý tuần tự, ghi khớp / vốn, lưu trạng thái."""
        now = time.time() if now is None else now
        for pair in self.pairs:
            try:
                self._tick_pair(pair, now)
            except Exception as e:
                log.warning("Grid %s: %s", pair, str(e)[:200])

    def _tick_pair(self, pair, now):
        got = self._load(pair, now)
        if not got:
            return
        rid, book = got
        if book.s["stopped"]:
            return
        sym = spot_symbol(pair)
        since = float(book.s["last_bar"] or book.s["started_at"]) + 60
        bars = [b for b in self._ohlcv(sym, "1m", since_s=since) if b[0] / 1000 >= since and b[0] / 1000 + 60 + CLOSED_LAG <= now]
        if not bars:
            return
        fills = []
        for b in bars:
            fills += [(ts, rid, pair, lvl, price, side, amt, fee, profit) for ts, lvl, price, side, amt, fee, profit in book.step(b)]
            ts = b[0] / 1000
            if int(ts) % 900 == 0 or book.s["stopped"]:
                m = book.metrics()
                self.store.grid_add_equity(ts, rid, pair, m["usdt"], m["coin"], m["price"], m["value"], m["hodl"], m["realized"])
                self.last_equity[pair] = ts
            if book.s["stopped"]:
                break
        if fills:
            self.store.grid_add_fills(fills)
            log.info("Grid %s: %d lần khớp, lãi đã khoá %.4f USDT, giá trị %.4f (HODL %.4f)", pair, len(fills),
                     book.s["realized"], book.value(), book.hodl())
        if book.s["stopped"]:
            self.store.grid_save_run(rid, book.s, ended_at=float(book.s["last_bar"]), reason=book.s["stopped"])
            m = book.metrics()
            log.warning("Grid %s dừng: %s – giữ %.6g coin + %.4f USDT (giá trị %.4f, HODL %.4f)", pair, book.s["stopped"],
                        m["coin"], m["usdt"], m["value"], m["hodl"])
            self.notify(f"⏹ Grid {pair} dừng: {book.s['stopped']}. Giữ nguyên {m['coin']:.6g} coin + {m['usdt']:.2f} USDT "
                        f"(giá trị {m['value']:.2f}, HODL {m['hodl']:.2f}, lãi đã khoá {m['realized']:+.2f}). "
                        f"Không tự đặt lại vùng: muốn vòng mới, tăng GRID_ROUND trên panel rồi khởi động lại.")
        else:
            self.store.grid_save_run(rid, book.s)

    # ---------- trạng thái cho web / lệnh
    def snapshot(self):
        out = []
        for pair in self.pairs:
            got = self.books.get(pair)
            if not got:
                last = self.store.grid_runs(pair, 1)
                if last:
                    import json
                    got = (last[0]["id"], GridBook(json.loads(last[0]["state"])))
            if not got:
                out.append({"pair": pair, "state": "chưa mở"})
                continue
            rid, book = got
            m = book.metrics()
            m.update({"pair": pair, "run_id": rid, "state": "đã dừng" if m["stopped"] else "đang chạy",
                      "started_at": book.s["started_at"], "last_bar": book.s["last_bar"],
                      "orders": len(book.s["orders"]), "curve": [(r["ts"], r["value"], r["hodl"]) for r in self.store.grid_equity(rid)]})
            out.append(m)
        return out

    def text(self):
        snaps = self.snapshot()
        if not snaps:
            return "Grid: không có cặp nào (GRID_PAIRS trống)."
        lines = ["🪙 GRID SPOT (chạy ảo, chỉ đọc)"]
        for m in snaps:
            if m.get("state") == "chưa mở":
                lines.append(f"• {m['pair']}: chưa mở vòng")
                continue
            lines.append(f"• {m['pair']} – {m['state']}{' (' + m['stopped'] + ')' if m.get('stopped') else ''}: "
                         f"vùng {m['low']:.6g}–{m['high']:.6g}, {m['levels']} mức\n"
                         f"  giá trị {m['value']:.2f} USDT ({m['ret_pct']:+.2f}%) | HODL {m['hodl']:.2f} ({m['hodl_pct']:+.2f}%) "
                         f"| so HODL {m['vs_hodl_pct']:+.2f} điểm %\n"
                         f"  lãi đã khoá {m['realized']:+.4f} | tồn kho {m['inventory_pnl']:+.4f} | phí {m['fees']:.4f} "
                         f"| sụt tối đa {m['max_dd_pct']:.2f}% | {m['rounds']} vòng ({m['rounds_per_day']}/ngày) "
                         f"| trong vùng {m['in_range_pct'] if m['in_range_pct'] is not None else '—'}%")
        return "\n".join(lines)

    # ---------- luồng
    def start(self):
        if not cfg.GRID_ENABLED or not self.pairs:
            log.info("Grid: tắt (GRID_ENABLED=%s, cặp %s)", cfg.GRID_ENABLED, ",".join(self.pairs) or "-")
            return None
        t = threading.Thread(target=self._loop, daemon=True, name="grid")
        t.start()
        return t

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self.active():
                    self.tick()
            except Exception as e:
                log.warning("Grid loop: %s", str(e)[:200])
            # đợi tới sau giây thứ CLOSED_LAG + 2 của phút kế
            now = time.time()
            wait = 60 - (now % 60) + CLOSED_LAG + 2
            self._stop.wait(wait)

    def stop(self):
        self._stop.set()
