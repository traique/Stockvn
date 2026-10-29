"""Đánh giá Kronos trên các tín hiệu bóng đã có kết quả của bot.
Mỗi tín hiệu: lấy 400 nến 15m ĐÃ ĐÓNG trước thời điểm tín hiệu, cho Kronos sinh N đường giá 16 nến (4h),
tính xác suất chạm +1R trước SL và kỳ vọng di chuyển (R), rồi so với kết quả thật (outcome_r).

KHÔNG chạy trên Botkeep – chạy trên máy tính / VPS (cần ~1.2 GB thư viện). Xem tools/README.md.

    python tools/kronos_eval.py --kronos ../Kronos --data okxbot-all-YYYYMMDD-HHMM.json --out res_mini.json \
        --model NeoQuasar/Kronos-mini --tok NeoQuasar/Kronos-Tokenizer-2k --ctx 2048
    python tools/kronos_analyze.py res_mini.json

--data: file JSON "sao lưu toàn bộ" tải từ dashboard (thẻ Tải dữ liệu).
--shard i/k: chia việc cho k tiến trình chạy song song (mỗi tiến trình 1 luồng CPU), rồi gộp các file --out lại."""
import sys, json, time, math, argparse
ap = argparse.ArgumentParser()
ap.add_argument("--kronos", required=True)
ap.add_argument("--data", required=True)
ap.add_argument("--model", default="NeoQuasar/Kronos-small")
ap.add_argument("--tok", default="NeoQuasar/Kronos-Tokenizer-base")
ap.add_argument("--ctx", type=int, default=512)
ap.add_argument("--paths", type=int, default=24)
ap.add_argument("--horizon", type=int, default=16)
ap.add_argument("--lookback", type=int, default=400)
ap.add_argument("--out", required=True)
ap.add_argument("--shard", default="0/1")            # i/k: chỉ chạy tín hiệu thứ j với j % k == i
a = ap.parse_args()
sys.path.insert(0, a.kronos)

import numpy as np, pandas as pd, torch, ccxt
from model import Kronos, KronosTokenizer
from model.kronos import auto_regressive_inference, calc_time_stamps

torch.set_num_threads(1)            # gần với máy chủ nhỏ (Botkeep 1.5 CPU, chạy tiến trình riêng 1 luồng)
import resource
torch.manual_seed(0)
tok = KronosTokenizer.from_pretrained(a.tok).eval()
mdl = Kronos.from_pretrained(a.model).eval()
ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
ex.session.trust_env = True

d = json.load(open(a.data))
sigs = [s for s in d["signals"] if s.get("outcome") not in (None, "PENDING", "NODATA") and s.get("outcome_r") is not None]
_i, _k = map(int, a.shard.split("/"))
sigs = [x for j, x in enumerate(sigs) if j % _k == _i]
print("tín hiệu có kết quả:", len(sigs), flush=True)
TF = 900_000


def candles_before(sym, ts):
    end = int(ts * 1000) // TF * TF            # nến đang hình thành lúc tín hiệu bắt đầu ở `end`
    since = end - a.lookback * TF
    out = []
    while since < end:
        rows = ex.fetch_ohlcv(sym, "15m", since=since, limit=300)
        rows = [r for r in rows if r[0] < end]
        if not rows:
            break
        out += [r for r in rows if not out or r[0] > out[-1][0]]
        since = rows[-1][0] + TF
        if len(rows) < 2:
            break
    return out[-a.lookback:], end


res = []
for k, s in enumerate(sigs):
    try:
        rows, end = candles_before(s["symbol"], s["ts"])
        if len(rows) < 200:
            print("thiếu nến", s["symbol"]); continue
        arr = np.array([[r[1], r[2], r[3], r[4], r[5], r[5] * (r[1] + r[2] + r[3] + r[4]) / 4] for r in rows], dtype=np.float32)
        mu, sd = arr.mean(0), arr.std(0)
        x = np.clip((arr - mu) / (sd + 1e-5), -5, 5)
        xts = pd.Series(pd.to_datetime([r[0] for r in rows], unit="ms"))
        yts = pd.Series(pd.to_datetime([end + i * TF for i in range(a.horizon)], unit="ms"))
        xs = calc_time_stamps(xts).values.astype(np.float32)
        ys = calc_time_stamps(yts).values.astype(np.float32)
        N = a.paths
        X = torch.from_numpy(np.repeat(x[None], N, 0))
        XS = torch.from_numpy(np.repeat(xs[None], N, 0))
        YS = torch.from_numpy(np.repeat(ys[None], N, 0))
        t0 = time.time()
        with torch.no_grad():
            p = auto_regressive_inference(tok, mdl, X, XS, YS, a.ctx, a.horizon, 5, 1.0, 0, 0.9, 1, False)
        dt_ = time.time() - t0
        p = np.asarray(p)[:, -a.horizon:, :] * (sd + 1e-5) + mu      # (N, H, 6)
        side, entry, sl = s["side"], float(s["entry"]), float(s["sl"])
        risk = abs(entry - sl); sg = 1 if side == "long" else -1
        hit, mv = [], []
        for path in p:
            o = 0
            for h_, l_ in zip(path[:, 1], path[:, 2]):
                fav = (h_ - entry) * sg if side == "long" else (entry - l_)
                adv = (entry - l_) if side == "long" else (h_ - entry)
                if adv >= risk: o = -1; break
                if fav >= risk: o = 1; break
            hit.append(o)
            mv.append(sg * (path[-1, 3] - entry) / risk)
        hit = np.array(hit)
        r = dict(sid=s["sid"], symbol=s["symbol"], side=side, source=s["source"], score=s["score"],
                 outcome=s["outcome"], outcome_r=s["outcome_r"], p_tp1=float((hit == 1).mean()),
                 p_sl=float((hit == -1).mean()), exp_move_r=float(np.mean(mv)), secs=round(dt_, 2))
        res.append(r)
        print(f"{k+1}/{len(sigs)} {s['symbol']:<18} {side:<5} out={s['outcome_r']:+.2f}R  p_tp1={r['p_tp1']:.2f} "
              f"p_sl={r['p_sl']:.2f} move={r['exp_move_r']:+.2f}R  {dt_:.1f}s", flush=True)
    except Exception as e:
        print("lỗi", s["symbol"], str(e)[:120], flush=True)
json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)
print("xong", len(res), "| RSS đỉnh MB:", round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
