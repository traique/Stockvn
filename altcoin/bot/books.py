"""Sổ (book) và so sánh sổ – v3.5.

Sổ chính ``main`` là sổ cũ (paper hoặc live). Sổ ``noai`` là một PaperEngine thứ hai chạy song song trong cùng tiến trình:
nhận đúng danh sách ứng viên đã qua cổng, đạt MIN_SCORE và đã xếp theo điểm mà sổ chính nhận (main.Bot.scan), vào lệnh
KHÔNG hỏi AI, hệ số rủi ro theo AI = 1,0×, cùng cách tính cỡ lệnh / phí / trượt giá / cách thoát như sổ chính (cùng
BaseEngine). Hai sổ cùng thị trường, cùng thời điểm, cùng giá last lúc quyết định (engine.stamp ghi một lần trên tín hiệu),
nên khác biệt chỉ đến từ AI. Sổ noai không bao giờ gọi AI, không gửi tin từng lệnh, có cầu dao drawdown riêng
(MAX_DRAWDOWN), và luôn là PaperEngine kể cả khi MODE=live.

So sánh (compare): mỗi sổ – số lệnh, R trung bình với sai số chuẩn GOM CỤM THEO NGÀY (learner._tstat), tỉ lệ thắng, vốn,
sụt tối đa trong đợt đo, tổng phí và phí / lãi gộp. Và hiệu THEO CẶP TÍN HIỆU: mỗi lệnh noai có sid (dòng signals của tín
hiệu); lệnh main cùng sid cho R_main, không có (AI từ chối, hoặc không vào được) thì R_main = 0 → diff = R_noai − R_main,
sai số theo ngày. Cùng tín hiệu nên nhiễu thị trường triệt tiêu – mẫu 30 lệnh đủ thấy khác biệt lớn hơn nhiều so với so
hai trung bình. R của lệnh = PnL / (giá trị lệnh ban đầu × khoảng cách SL ban đầu / giá vào), như trang Tự học và tổng kết.
"""
import time
from .config import cfg
from .storage import MAIN

NOAI = "noai"
BOOK_NAMES = {MAIN: "Sổ chính (có AI)", NOAI: "Sổ không AI"}
# "AI" của sổ noai: cùng hướng tín hiệu, không độ tự tin → risk.quality_mult = 1,0 (không 0,85× / 1,2× / 0,5×)
NOAI_AI = {"decision": None, "confidence": None, "model": "noai", "reason": "sổ không AI – vào theo tín hiệu kỹ thuật"}


def noai_ai(sig):
    return dict(NOAI_AI, decision=sig.side.upper())


def trade_r(t):
    """R của một lệnh đã đóng (None khi thiếu dữ liệu): PnL / rủi ro theo khoảng cách SL ban đầu."""
    try:
        entry = float(t.get("entry") or 0)
        sl0 = t.get("sl0") if t.get("sl0") is not None else t.get("sl")
        n0 = float(t.get("notional0") or t.get("notional") or 0)
        if not entry or sl0 is None or not n0:
            return None
        risk = n0 * abs(entry - float(sl0)) / entry
        return float(t.get("pnl") or 0) / risk if risk else None
    except (TypeError, ValueError):
        return None


def max_drawdown(curve):
    """Sụt tối đa (%) từ đỉnh của đường vốn [(ts, equity)]."""
    peak, dd = None, 0.0
    for _, eq in curve:
        eq = float(eq)
        peak = eq if peak is None or eq > peak else peak
        if peak:
            dd = max(dd, (peak - eq) / peak)
    return round(100 * dd, 2)


def _book_stats(store, book, since, equity_now, start_equity):
    from .learner import _tstat, _day
    rows = store.closed_trades(book, since)
    rs, days = [], []
    for t in rows:
        r = trade_r(t)
        if r is not None:
            rs.append(r)
            days.append(_day(t.get("closed_at") or t.get("opened_at")))
    se, tt = _tstat(rs, days) if len(rs) >= 2 else (0.0, 0.0)
    wins = sum(1 for t in rows if (t.get("pnl") or 0) > 0)
    fees = sum(float(t.get("fee") or 0) for t in rows)
    gross = sum(float(t.get("pnl") or 0) + float(t.get("fee") or 0) for t in rows if (float(t.get("pnl") or 0) + float(t.get("fee") or 0)) > 0)
    curve = store.equity_since(since, book)
    return {
        "name": BOOK_NAMES.get(book, book), "n": len(rows), "open": len(store.open_trades(book)),
        "mean_r": round(sum(rs) / len(rs), 3) if rs else None, "se": round(se, 3), "t": round(tt, 2),
        "days": len(set(days)), "winrate": round(100 * wins / len(rows), 1) if rows else None,
        "equity": round(float(equity_now), 4) if equity_now is not None else None,
        "start_equity": round(float(start_equity), 4) if start_equity else None,
        "ret_pct": round(100 * (float(equity_now) - float(start_equity)) / float(start_equity), 2)
        if equity_now is not None and start_equity else None,
        "max_dd_pct": max_drawdown(curve), "fees": round(fees, 4), "gross_profit": round(gross, 4),
        "fee_share": round(100 * fees / gross, 1) if gross > 0 else None,
        "pnl": round(sum(float(t.get("pnl") or 0) for t in rows), 4),
    }


def _paired(store, since):
    """Hiệu R theo cặp tín hiệu: với mỗi lệnh noai đã đóng (có sid), R_main của lệnh main cùng sid (0 nếu main không vào)."""
    from .learner import _tstat, _day
    noai = [t for t in store.closed_trades(NOAI, since) if t.get("sid")]
    main_by_sid = {t["sid"]: t for t in store.closed_trades(MAIN, since) if t.get("sid")}
    diffs, days, matched, main_open = [], [], 0, 0
    for t in noai:
        r = trade_r(t)
        if r is None:
            continue
        m = main_by_sid.get(t["sid"])
        if m is None:
            if store.trade_by_sid(t["sid"], MAIN):     # main còn mở → chưa so được
                main_open += 1
                continue
            rm = 0.0
        else:
            rm = trade_r(m) or 0.0
            matched += 1
        diffs.append(r - rm)
        days.append(_day(t.get("closed_at") or t.get("opened_at")))
    se, tt = _tstat(diffs, days) if len(diffs) >= 2 else (0.0, 0.0)
    main_only = sum(1 for sid in main_by_sid if not any(t.get("sid") == sid for t in noai))
    return {"n": len(diffs), "matched": matched, "main_only": main_only, "pending": main_open,
            "diff": round(sum(diffs) / len(diffs), 3) if diffs else None, "se": round(se, 3), "t": round(tt, 2),
            "days": len(set(days))}


def compare(store, period, equities, start_equities, halted=None):
    """period: kv "period" ({ts, equity}); equities / start_equities: {book: USDT}; halted: {book: bool}."""
    since = float((period or {}).get("ts") or 0)
    books = {}
    for b in (MAIN, NOAI):
        if b not in equities:
            continue
        books[b] = _book_stats(store, b, since, equities.get(b), start_equities.get(b))
        books[b]["halted"] = bool((halted or {}).get(b))
    out = {"since": since, "days": round((time.time() - since) / 86400, 1) if since else 0,
           "min_trades": 30, "books": books}
    if NOAI in books:
        out["paired"] = _paired(store, since)
    return out


def _r(v):
    return "—" if v is None else f"{v:+.2f}R"


def compare_text(c):
    """Văn bản /alt compare."""
    lines = [f"⚖️ SO SÁNH SỔ · đợt đo {c['days']:g} ngày (từ {time.strftime('%d/%m', time.localtime(c['since']))})"
             if c.get("since") else "⚖️ SO SÁNH SỔ"]
    for b, s in c["books"].items():
        se = f" ± {s['se']:.2f}" if s["n"] >= 2 else ""
        lines.append(f"• {s['name']}{' – ĐÃ DỪNG (cầu dao)' if s.get('halted') else ''}: {s['n']} lệnh"
                     + (f" ({s['open']} đang mở)" if s["open"] else "") + f" | R TB {_r(s['mean_r'])}{se} ({s['days']} ngày)"
                     + (f" | thắng {s['winrate']}%" if s["winrate"] is not None else "")
                     + (f"\n  vốn {s['equity']:.4f} USDT ({s['ret_pct']:+.1f}%)" if s["ret_pct"] is not None else "")
                     + f" | sụt tối đa {s['max_dd_pct']}% | phí {s['fees']:.4f}"
                     + (f" = {s['fee_share']}% lãi gộp" if s["fee_share"] is not None else ""))
    p = c.get("paired")
    if p:
        if p["n"]:
            lines.append(f"Theo cặp tín hiệu ({p['n']} lệnh noai, {p['matched']} có lệnh main cùng tín hiệu, "
                         f"{p['days']} ngày): noai − main = {p['diff']:+.2f} ± {p['se']:.2f}R/lệnh (t {p['t']:+.1f})"
                         + (f"; {p['pending']} cặp chờ main đóng" if p["pending"] else "")
                         + (f"; {p['main_only']} lệnh main không có lệnh noai" if p["main_only"] else ""))
        else:
            lines.append("Theo cặp tín hiệu: chưa có lệnh noai nào đóng.")
        n_min = c.get("min_trades", 30)
        ns = [s["n"] for s in c["books"].values()]
        if min(ns) < n_min:
            lines.append(f"Chưa đủ dữ liệu: cần ≥ {n_min} lệnh mỗi sổ và ≥ 3 tuần trước khi kết luận.")
        elif p["n"] >= 2 and p["se"]:
            if p["diff"] >= 0 or p["t"] > -2:
                lines.append("noai ngang hoặc tốt hơn main trong sai số → AI không thêm giá trị vào quyết định vào lệnh.")
            else:
                lines.append("main tốt hơn noai hơn 2 sai số chuẩn → AI đang giúp.")
    return "\n".join(lines)
