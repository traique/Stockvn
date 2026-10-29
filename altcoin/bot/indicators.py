"""Chỉ báo kỹ thuật – Python thuần, không cần pandas/numpy (tiết kiệm ~150 MB ổ đĩa trên host nhỏ).

Frame: dict các cột -> list số; truy cập 1 nến bằng frame.row(i) (i âm = đếm từ cuối).
"""
from types import SimpleNamespace
import math

NAN = float("nan")


class Frame:
    def __init__(self, ohlcv):
        self.ts = [float(c[0]) for c in ohlcv]
        self.open = [float(c[1]) for c in ohlcv]
        self.high = [float(c[2]) for c in ohlcv]
        self.low = [float(c[3]) for c in ohlcv]
        self.close = [float(c[4]) for c in ohlcv]
        self.volume = [float(c[5]) for c in ohlcv]
        self.n = len(self.close)

    def __len__(self):
        return self.n

    def row(self, i):
        if i < 0:
            i += self.n
        return SimpleNamespace(**{k: v[i] for k, v in self.__dict__.items() if isinstance(v, list)})

    def rows(self, start, stop=None):
        idx = range(*slice(start, stop).indices(self.n))
        return [self.row(i) for i in idx]


# ---------------------------------------------------------------- phép tính cơ bản
def ema(s, n):
    k = 2 / (n + 1)
    out, prev = [], None
    for x in s:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def rma(s, n):
    """Wilder smoothing (alpha = 1/n) – dùng cho RSI, ATR, ADX."""
    a = 1 / n
    out, prev = [], None
    for x in s:
        prev = x if prev is None else x * a + prev * (1 - a)
        out.append(prev)
    return out


def rolling(s, n, fn):
    out = []
    for i in range(len(s)):
        if i + 1 < n:
            out.append(NAN)
        else:
            out.append(fn(s[i + 1 - n:i + 1]))
    return out


def sma(s, n):
    return rolling(s, n, lambda w: sum(w) / len(w))


def stdev(s, n):
    def sd(w):
        m = sum(w) / len(w)
        return math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)) if len(w) > 1 else 0.0
    return rolling(s, n, sd)


def rsi(close, n=14):
    ups, dns = [0.0], [0.0]
    for i in range(1, len(close)):
        d = close[i] - close[i - 1]
        ups.append(max(d, 0.0)); dns.append(max(-d, 0.0))
    au, ad = rma(ups, n), rma(dns, n)
    out = []
    for u, d in zip(au, ad):
        out.append(50.0 if d == 0 and u == 0 else (100.0 if d == 0 else 100 - 100 / (1 + u / d)))
    return out


def true_range(f):
    tr = [f.high[0] - f.low[0]]
    for i in range(1, f.n):
        tr.append(max(f.high[i] - f.low[i], abs(f.high[i] - f.close[i - 1]), abs(f.low[i] - f.close[i - 1])))
    return tr


def atr(f, n=14):
    return rma(true_range(f), n)


def macd(close, fast=12, slow=26, sig=9):
    ef, es = ema(close, fast), ema(close, slow)
    m = [a - b for a, b in zip(ef, es)]
    s = ema(m, sig)
    return m, s, [a - b for a, b in zip(m, s)]


def adx(f, n=14):
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, f.n):
        up = f.high[i] - f.high[i - 1]
        dn = f.low[i - 1] - f.low[i]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
    atr_n = rma(true_range(f), n)
    pdi = [100 * a / b if b else 0.0 for a, b in zip(rma(plus_dm, n), atr_n)]
    mdi = [100 * a / b if b else 0.0 for a, b in zip(rma(minus_dm, n), atr_n)]
    dx = [100 * abs(p - m) / (p + m) if (p + m) else 0.0 for p, m in zip(pdi, mdi)]
    return rma(dx, n), pdi, mdi


def vwap(f, n=48):
    tp = [(h + l + c) / 3 for h, l, c in zip(f.high, f.low, f.close)]
    pv = [t * v for t, v in zip(tp, f.volume)]
    out = []
    for i in range(f.n):
        if i + 1 < n:
            out.append(NAN); continue
        v = sum(f.volume[i + 1 - n:i + 1])
        out.append(sum(pv[i + 1 - n:i + 1]) / v if v else f.close[i])
    return out


# ---------------------------------------------------------------- gắn đủ chỉ báo
def enrich(f):
    c = f.close
    f.ema9, f.ema21, f.ema50, f.ema200 = ema(c, 9), ema(c, 21), ema(c, 50), ema(c, 200)
    f.rsi = rsi(c)
    f.atr = atr(f)
    f.macd, f.macd_sig, f.macd_hist = macd(c)
    mid, sd = sma(c, 20), stdev(c, 20)
    f.bb_mid = mid
    f.bb_up = [m + 2 * s if not math.isnan(m) else NAN for m, s in zip(mid, sd)]
    f.bb_lo = [m - 2 * s if not math.isnan(m) else NAN for m, s in zip(mid, sd)]
    vol_ma = sma(f.volume, 20)
    f.vol_ma = vol_ma
    f.vol_ratio = [v / m if (m and not math.isnan(m)) else NAN for v, m in zip(f.volume, vol_ma)]
    f.atr_pct = [a / p * 100 if p else 0.0 for a, p in zip(f.atr, c)]
    f.hh20 = rolling(f.high, 20, max)
    f.ll20 = rolling(f.low, 20, min)
    f.adx, f.di_plus, f.di_minus = adx(f)
    f.vwap = vwap(f)
    f.close_pos, f.body_pct = [], []
    for o, h, l, cl in zip(f.open, f.high, f.low, c):
        rng = h - l
        f.close_pos.append((cl - l) / rng if rng else 0.5)
        f.body_pct.append(abs(cl - o) / rng if rng else 0.0)
    return f


def to_frame(ohlcv):
    return Frame(ohlcv)
