"""Phân tích: dự báo Kronos có liên quan tới kết quả thật (outcome_r) của tín hiệu không?

    python tools/kronos_analyze.py res_mini.json [res_small_0.json res_small_1.json ...]

Nhiều file (các shard) được gộp lại. Chỉ dùng thư viện chuẩn – chạy được ở bất kỳ đâu."""
import json, sys, random, math

res = [r for f in sys.argv[1:] for r in json.load(open(f))]
n = len(res)
y = [r["outcome_r"] for r in res]


def rank(v):
    idx = sorted(range(len(v)), key=lambda i: v[i])
    rk = [0.0] * len(v)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
            j += 1
        for k in range(i, j + 1):
            rk[idx[k]] = (i + j) / 2
        i = j + 1
    return rk


def pearson(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a)); sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    return sum((x - ma) * (z - mb) for x, z in zip(a, b)) / (sa * sb) if sa and sb else 0


def spearman(a, b):
    return pearson(rank(a), rank(b))


def perm_p(a, b, stat, iters=5000):
    random.seed(0)
    obs = stat(a, b)
    bb = list(b); c = 0
    for _ in range(iters):
        random.shuffle(bb)
        if abs(stat(a, bb)) >= abs(obs):
            c += 1
    return obs, (c + 1) / (iters + 1)


def agg(rows):
    v = [r["outcome_r"] for r in rows]
    if not v:
        return "n=0"
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0
    t = m / (sd / math.sqrt(len(v))) if sd else 0
    return f"n={len(v):>2}  kỳ vọng {m:+.3f}R  thắng {100 * sum(1 for x in v if x > 0) / len(v):4.0f}%  tổng {sum(v):+6.2f}R  t={t:+.2f}"


print(f"Số tín hiệu: {n} | thời gian TB/tín hiệu: {sum(r['secs'] for r in res) / n:.1f}s\n")
print("Toàn bộ:            ", agg(res))
for name, f in [("p_tp1 - p_sl", lambda r: r["p_tp1"] - r["p_sl"]), ("p_tp1", lambda r: r["p_tp1"]),
                ("exp_move_r", lambda r: r["exp_move_r"]), ("điểm KT (so sánh)", lambda r: r["score"])]:
    x = [f(r) for r in res]
    rho, p = perm_p(x, y, spearman)
    print(f"Spearman({name:<17}, kết quả) = {rho:+.3f}   p = {p:.3f}")

print("\nChia nhóm theo edge = p_tp1 - p_sl:")
key = lambda r: r["p_tp1"] - r["p_sl"]
s = sorted(res, key=key)
q = n // 3
print("  1/3 thấp nhất      ", agg(s[:q]), f"(edge ≤ {key(s[q - 1]):+.2f})")
print("  1/3 giữa           ", agg(s[q:2 * q]))
print("  1/3 cao nhất       ", agg(s[2 * q:]), f"(edge ≥ {key(s[2 * q]):+.2f})")
print("  edge > 0 (Kronos ủng hộ) ", agg([r for r in res if key(r) > 0]))
print("  edge ≤ 0 (Kronos phản đối)", agg([r for r in res if key(r) <= 0]))

# hiệu 2 nhóm edge>0 vs ≤0 có ý nghĩa? kiểm định hoán vị
A = [r["outcome_r"] for r in res if key(r) > 0]; B = [r["outcome_r"] for r in res if key(r) <= 0]
if A and B:
    obs = sum(A) / len(A) - sum(B) / len(B)
    allv = A + B; random.seed(1); c = 0
    for _ in range(5000):
        random.shuffle(allv)
        d = sum(allv[:len(A)]) / len(A) - sum(allv[len(A):]) / len(B)
        if abs(d) >= abs(obs):
            c += 1
    print(f"  Chênh lệch ủng hộ – phản đối: {obs:+.3f}R, p = {(c + 1) / 5001:.3f}")

print("\nTheo hướng lệnh:")
for side in ("long", "short"):
    sub = [r for r in res if r["side"] == side]
    if len(sub) > 5:
        rho, p = perm_p([key(r) for r in sub], [r["outcome_r"] for r in sub], spearman, 2000)
        print(f"  {side:<5} {agg(sub)} | Spearman edge {rho:+.3f} p={p:.3f}")

print("\nSo với AI (chỉ các tín hiệu đã hỏi AI):")
ai = [r for r in res if r["source"] in ("taken", "ai_rejected")]
print("  AI duyệt           ", agg([r for r in ai if r["source"] == "taken"]))
print("  AI từ chối         ", agg([r for r in ai if r["source"] == "ai_rejected"]))
print("  Kronos ủng hộ      ", agg([r for r in ai if key(r) > 0]))
print("  Kronos phản đối    ", agg([r for r in ai if key(r) <= 0]))

# hiệu chuẩn: p_tp1 dự báo vs tỉ lệ thực tế chạm TP1 (outcome_r > 0 xấp xỉ)
print("\nHiệu chuẩn (p_tp1 dự báo vs tỉ lệ thắng thật):")
for lo, hi in ((0, .35), (.35, .5), (.5, .65), (.65, 1.01)):
    sub = [r for r in res if lo <= r["p_tp1"] < hi]
    if sub:
        print(f"  p_tp1 {lo:.2f}–{min(hi, 1):.2f}: {agg(sub)}")
