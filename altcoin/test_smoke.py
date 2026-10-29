"""Smoke test: quét thật dữ liệu OKX public, không cần API key, không đặt lệnh."""
import os, logging
os.environ.setdefault("MODE", "paper")
os.environ.setdefault("AI_ENABLED", "false")
os.environ.setdefault("DATA_DIR", "/tmp/okx-bot-test")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
from bot.exchange import OKX
from bot.strategy import analyze
from bot.config import cfg
from bot.risk import position_notional

okx = OKX()
coins = okx.top_altcoins(8)
print("Top coin:", [(c[0], round(c[1] / 1e6, 1)) for c in coins])
for sym, vol, chg in coins:
    ltf = okx.ohlcv(sym, cfg.TIMEFRAME, 220)
    htf = okx.ohlcv(sym, cfg.HTF_TIMEFRAME, 120)
    sig = analyze(sym, ltf, htf, change24h=float(chg or 0))
    if sig:
        notional = position_notional(10, sig.price, sig.sl)
        ct, min_needed = okx.contracts_for_notional(sym, notional, sig.price)
        print(f"{sym}: {sig.side} score={sig.score} rr={sig.rr:.2f} entry={sig.price:.6g} sl={sig.sl:.6g} tp={sig.tp:.6g} "
              f"notional={notional:.2f} contracts={ct} min_needed={min_needed:.2f} | {sig.reasons}")
    else:
        print(f"{sym}: không có tín hiệu")
