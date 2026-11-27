"""Hỏi đáp: the companion. A message in plain Vietnamese ("FPT có nên mua không?", "danh mục của
anh sao rồi?", "hôm nay thị trường thế nào?") or ``/hoi ...`` on Telegram and Zoom is answered
from what the bot knows, with one AI request - not the command list.

The code gathers the facts first, the AI only words them:

- the session (date, phase or why the market is closed, VN-Index now);
- every stock named (at most ``MAX_CODES``): its price now (vnbot.quote), the Quant Engine's
  decision of the day (``engine.analyze``, seconds, no AI), the bot's valuation card
  (vnbot.dinhgia), the Tài sản holding of it (quantity, cost, P&L, cut-loss, target) and the
  site's signal when SIEUTINHIEU is on;
- the Tài sản portfolio when the question is about it (or names no stock): the overview of ``/ts``;
- the alerts the bot sent today (vnbot.intraday) and the watchlist.

One request to the agents' provider chain (9Router -> Groq -> ...), within the day's AI budget;
the last ``MEMORY_TURNS`` exchanges of the chat go with it (kept ``MEMORY_DAYS`` days in
``data/hoidap/``), so "còn HPG thì sao?" follows "FPT có nên mua không?". The AI may only use
the figures given, says when it does not know, and never gives an order: the trading decision
of the bot stays the Quant Engine's (``/nhanh``), the discipline rules stay vnbot.kyluat's.
Without a provider, or the budget spent, the answer is the facts alone, said so - never the
command list. ``HOIDAP=0`` turns the assistant off (free text then shows the command list again).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from vnbot.jobs import write_text_atomic

logger = logging.getLogger(__name__)

MAX_CODES = 3
MAX_QUESTION = 600
MEMORY_TURNS = 6
MEMORY_DAYS = 2
MAX_ANSWER = 1800
SUMMARY_CHARS = 900           # the Quant Engine's block of one stock in the prompt
PORTFOLIO_CHARS = 1600
ALERTS_SHOWN = 6
_lock = threading.Lock()

SYSTEM = (
    "Bạn là người đồng hành đầu tư của một nhà đầu tư cá nhân trên thị trường chứng khoán Việt Nam "
    "(HOSE/HNX/UPCoM). Bạn nói chuyện thân tình, gọi người dùng là 'anh', trả lời ngắn gọn để đọc trên điện "
    "thoại (tối đa khoảng 10 dòng, văn xuôi hoặc gạch đầu dòng, không markdown, không bảng).\n"
    "Nguyên tắc bắt buộc:\n"
    "1. Chỉ dùng số liệu trong phần BỐI CẢNH bên dưới. Không bịa giá, chỉ số, tin tức hay sự kiện; thiếu gì thì nói "
    "thẳng là bot chưa có số liệu đó và gợi ý lệnh để lấy (/soi MÃ, /dinhgia MÃ, /phantich MÃ, /ts).\n"
    "2. Quyết định giao dịch của bot là của Quant Engine (hành động, vùng mua, stop, mục tiêu trong bối cảnh); bạn "
    "diễn giải và cân nhắc, không tự đặt mức giá mới, không hứa chắc chắn, không nói 'sẽ tăng/giảm'.\n"
    "3. Luật thị trường Việt Nam: T+2 (mua phiên T, bán sớm nhất phiên chiều T+2), biên độ HOSE ±7% HNX ±10% "
    "UPCoM ±15%, lô 100, phí và thuế bán ~0,4% một vòng. Nhắc khi liên quan.\n"
    "4. Kỷ luật trước lợi nhuận: nhắc cắt lỗ đã đặt, không bình quân giá xuống khi đang lỗ, không mua đuổi, một mã "
    "không quá 30% tài sản, rủi ro một lệnh ~2% tài sản. Khi anh đang lỗ hay lo lắng, nói rõ ràng và điềm tĩnh, "
    "không trách móc.\n"
    "5. Kết thúc bằng MỘT việc cụ thể anh có thể làm tiếp (một lệnh của bot hoặc một mức giá cần theo dõi), và "
    "một câu ngắn rằng đây là tham khảo, không phải lệnh đặt.\n"
    "Nếu câu hỏi không liên quan đến đầu tư/chứng khoán, trả lời ngắn và kéo về việc đầu tư của anh."
)

PORTFOLIO_WORDS = ("danh mục", "danh muc", "tài sản", "tai san", "đang giữ", "dang giu", "đang lỗ", "dang lo",
                   "đang lãi", "dang lai", "lỗ", "lãi", "tiền mặt", "tien mat", "nên bán gì", "nen ban gi",
                   "nên mua gì", "nen mua gi", "của anh", "cua anh", "của tôi", "cua toi", "portfolio", "nav",
                   "cắt lỗ", "cat lo", "sổ", "so sánh", "tổng")
MARKET_WORDS = ("thị trường", "thi truong", "vnindex", "vn-index", "vni", "vn30", "hôm nay", "hom nay",
                "phiên", "phien", "xu hướng", "xu huong")
# ASCII three-letter Vietnamese words that are not stock codes
_STOP = {"MUA", "BAN", "CAO", "KHI", "NAM", "BAO", "SAO", "DAU", "HAY", "THI", "VOI", "CHO", "CON", "NAY", "HOM",
         "TOI", "ANH", "GIA", "LAI", "LOI", "VAO", "VAN", "CAI", "CUA", "NEN", "CHI", "MOI", "LUC", "GIU", "BOT",
         "AI", "NHU", "THE", "NAO", "DEN", "TAI", "SAN", "DEM", "QUA", "LON", "NHO", "TAM", "XIN", "CAM", "DAN",
         "TIN", "TOT", "XAU", "BAY", "GIO", "HOI", "DAY", "KIA", "SAU", "NHA", "DOI", "MAI", "RUI", "RO", "HET",
         "CAN", "PHI", "THU", "TRA", "LAM", "VNI", "HNX", "GIO", "MAY", "RAT", "KHA"}
_WORD = re.compile(r"[A-Za-z0-9]{3}")
_SPLIT = re.compile(r"[\s,;:.!?()\[\]\"'/+&]+")


def enabled() -> bool:
    return (os.environ.get("HOIDAP") or "1").strip().lower() not in ("0", "off", "false", "no")


def is_question(text: str) -> bool:
    """A message the assistant takes: not a lone unknown command ("/xyz"), at least two words
    or a sentence (Zoom adds a "/" to every message: it is dropped first)."""
    body = (text or "").strip()
    slash = body.startswith("/")
    words = body[1:].split() if slash else body.split()
    return len(words) >= 2 or (len(words) == 1 and not slash and len(words[0]) >= 12)


def codes_in(text: str, known: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The stock codes a question names, in order: a three-character word typed in capitals
    ("FPT"), or in any case when it is a code the bot follows (``known``: watchlist, holdings),
    or any three-letter word that is not a common Vietnamese one (checked against the price
    source later: an unknown code is dropped from the context)."""
    from tradingagents.dataflows.symbols import normalize_symbol
    from tradingagents.market import vn_rules

    known_set = {k.upper() for k in known}
    out: list[str] = []
    for raw in _SPLIT.split(text or ""):
        word = raw.strip()
        if not word:
            continue
        code = normalize_symbol(word)
        if vn_rules.is_index(code):
            continue
        if not _WORD.fullmatch(code) or not any(ch.isalpha() for ch in code):
            continue
        if code in known_set or word.isupper() or code not in _STOP:
            if code not in out:
                out.append(code)
    return out[:MAX_CODES]


def _wants(text: str, words) -> bool:
    low = (text or "").lower()
    return any(w in low for w in words)


# ─── memory ──────────────────────────────────────────────────────────────────


class Memory:
    """The last exchanges of one chat, on disk (``data/hoidap/<chat>.json``)."""

    def __init__(self, data_dir, chat_key) -> None:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(chat_key))[:60] or "chat"
        self.path = Path(data_dir) / "hoidap" / f"{safe}.json"

    def turns(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = data if isinstance(data, list) else []
        return [r for r in rows if isinstance(r, dict) and now - float(r.get("ts") or 0) < MEMORY_DAYS * 86400][-MEMORY_TURNS:]

    def add(self, question: str, answer: str, now: float | None = None) -> None:
        now = now or time.time()
        rows = self.turns(now) + [{"ts": now, "q": question[:MAX_QUESTION], "a": answer[:MAX_ANSWER]}]
        with _lock:
            write_text_atomic(self.path, json.dumps(rows[-MEMORY_TURNS:], ensure_ascii=False))

    def clear(self) -> None:
        with _lock:
            try:
                self.path.unlink()
            except OSError:
                pass


# ─── the facts ───────────────────────────────────────────────────────────────


def _vn(value, decimals: int = 0) -> str:
    from vnbot.quote import _vn as fmt

    return fmt(value, decimals)


def session_lines(now: datetime) -> list[str]:
    """The session now and VN-Index (one price request); the market closed says why."""
    from tradingagents.market import vn_rules
    from vnbot import quote

    day = now.strftime("%Y-%m-%d")
    phase = vn_rules.session_phase(now)
    names = {"pre_open": "trước giờ mở cửa", "ato": "phiên ATO", "continuous": "đang khớp lệnh liên tục",
             "lunch": "nghỉ trưa", "atc": "phiên ATC", "put_through": "sau ATC (giao dịch thoả thuận)",
             "after_close": "đã đóng cửa"}
    if phase == "closed":
        why = vn_rules.closure_reason(day) or "đóng cửa"
        state = f"thị trường nghỉ ({why}), phiên tới {vn_rules.next_session(day)[8:10]}/{vn_rules.next_session(day)[5:7]}"
    else:
        state = names.get(phase, phase)
    lines = [f"Thời điểm: {now:%H:%M %d/%m/%Y}, {state}."]
    try:
        q = quote.stock_quote("VNINDEX", now)
        change = (q["price"] - q["ref"]) if q.get("ref") else None
        pct = f" ({'+' if change > 0 else ''}{_vn(change / q['ref'] * 100, 2)}%)" if change is not None else ""
        when = "" if q["day"] == day else f" (phiên {q['day'][8:10]}/{q['day'][5:7]})"
        lines.append(f"VN-Index: {_vn(q['price'], 2)} điểm{pct}{when}.")
    except Exception as exc:  # noqa: BLE001 - the index is context, not the answer
        logger.info("Hỏi đáp: VN-Index: %s", exc)
        lines.append("VN-Index: chưa lấy được (nguồn giá không trả lời).")
    return lines


def stock_lines(code: str, now: datetime, *, account=None, data_dir=None) -> list[str] | None:
    """The facts of one stock; None when the price source does not know the code."""
    from tradingagents.quant import engine
    from vnbot import quote

    day = now.strftime("%Y-%m-%d")
    lines = []
    try:
        q = quote.stock_quote(code, now)
        lines += quote._stock_lines(q, day)
    except quote.Unknown:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info("Hỏi đáp: giá %s: %s", code, exc)
        lines.append(f"{code}: chưa lấy được giá (nguồn giá không trả lời).")
    try:
        result = engine.analyze(code, day)
        summary = (result.deterministic_summary or "").strip()
        lines.append("Quant Engine (quyết định của bot, trên nến đóng cửa, không gọi AI): "
                     + (summary[:SUMMARY_CHARS] + ("…" if len(summary) > SUMMARY_CHARS else "")))
    except Exception as exc:  # noqa: BLE001
        logger.info("Hỏi đáp: Quant %s: %s", code, exc)
        lines.append(f"Quant Engine: chưa tính được cho {code} ({type(exc).__name__}); gõ /nhanh {code} để thử lại.")
    if data_dir is not None:
        try:
            from vnbot import dinhgia

            row = dinhgia.Cards(data_dir).latest().get(code)
            if row and row.get("base") is not None:
                lines.append(f"Định giá bot tự tính (BCTC {row.get('period')}, tính sau phiên {dinhgia.ddmm(row.get('price_day') or '')}): "
                             f"vùng {row.get('zone')}, MOS {dinhgia.signed_pct(row.get('mos') or 0)}, Bear {dinhgia.price(row.get('bear'))} · "
                             f"Base {dinhgia.price(row.get('base'))} · Bull {dinhgia.price(row.get('bull'))}, tin cậy {row.get('reliability') or '—'}; "
                             "tham khảo, không vào quyết định.")
            elif row and row.get("note"):
                lines.append(f"Định giá bot: không có thẻ ({row['note']}).")
        except Exception as exc:  # noqa: BLE001
            logger.info("Hỏi đáp: định giá %s: %s", code, exc)
    if account is not None:
        try:
            lines += holding_lines(account, code, now)
        except Exception as exc:  # noqa: BLE001
            logger.info("Hỏi đáp: Tài sản %s: %s", code, exc)
    return lines


def holding_lines(account, code: str, now: datetime) -> list[str]:
    """The Tài sản holding of ``code`` (quantity, cost, P&L, cut-loss, target, the site's signal)."""
    from vnbot import taisan as ts

    book = account.book()
    h = book.holdings.get(code)
    if h is None:
        return [f"Tài sản: anh không giữ {code}."]
    s = account.summary(book=book)
    p = next((x for x in s["positions"] if x["symbol"] == code), None)
    out = f"Tài sản: anh đang giữ {code} {ts.vnd(h.qty)} cp, giá vốn {ts.vnd(h.avg)}"
    if p and p.get("pnl") is not None:
        weight = f"{p['weight']:.1f}".replace(".", ",")
        out += f", giá {ts.vnd(p['price'])}: {ts.signed(p['pnl'])} ({ts.pct(p['pnl_pct'])}), tỷ trọng {weight}%"
    lv = (account.levels() or {}).get(code) or {}
    if lv.get("stop"):
        out += f"; cắt lỗ {ts.vnd(lv['stop'])}" + (" (anh đặt)" if lv.get("source") == "owner" else "")
    else:
        out += "; CHƯA có mức cắt lỗ"
    if lv.get("target"):
        out += f"; mục tiêu {ts.vnd(lv['target'])}"
    day = now.strftime("%Y-%m-%d")
    pend = h.pending_lots(day)
    if pend:
        out += "; chưa về (T+2): " + ", ".join(f"{ts.vnd(q)} cp bán được từ chiều {ts.ddmm(d)}" for d, q in pend)
    try:
        sig = account.site_states([code], day).get(code)
    except Exception:  # noqa: BLE001
        sig = None
    if sig:
        out += f"; Siêu tín hiệu {'MUA' if sig.get('side') == 'BUY' else 'BÁN'} từ {ts.ddmm(sig.get('day', ''))}"
    return [out + "."]


def portfolio_lines(account) -> list[str]:
    text = account.overview_text()
    text = "\n".join(line for line in text.splitlines() if not line.startswith("Lệnh:"))
    return ["Tài sản (danh mục tiền thật của anh):", text[:PORTFOLIO_CHARS] + ("…" if len(text) > PORTFOLIO_CHARS else "")]


def alert_lines(monitor, day: str) -> list[str]:
    rows = monitor.today(day) if monitor is not None else []
    if not rows:
        return []
    shown = rows[-ALERTS_SHOWN:]
    return ["Cảnh báo bot đã gửi hôm nay:"] + [f"- {r.get('at', '')} {r.get('text', '')}"[:300] for r in shown]


def context(question: str, now: datetime, *, codes: list[str], account=None, data_dir=None, monitor=None,
            watchlist=()) -> tuple[str, list[str]]:
    """(the BỐI CẢNH block, the codes kept). Every source answers on its own; a failure is a line."""
    day = now.strftime("%Y-%m-%d")
    parts: list[str] = session_lines(now)
    kept: list[str] = []
    for code in codes:
        lines = stock_lines(code, now, account=account, data_dir=data_dir)
        if lines is None:
            parts.append(f"{code}: không có mã này trên HOSE/HNX/UPCoM (có thể là một từ thường).")
            continue
        kept.append(code)
        parts += [f"— {code} —"] + lines
    if account is not None and (not kept or _wants(question, PORTFOLIO_WORDS)):
        try:
            parts += portfolio_lines(account)
        except Exception as exc:  # noqa: BLE001
            logger.info("Hỏi đáp: Tài sản: %s", exc)
            parts.append("Tài sản: chưa đọc được danh mục.")
    try:
        parts += alert_lines(monitor, day)
    except Exception as exc:  # noqa: BLE001
        logger.info("Hỏi đáp: cảnh báo: %s", exc)
    if watchlist:
        parts.append("Watchlist: " + ", ".join(list(watchlist)[:30]) + ".")
    return "\n".join(parts), kept


# ─── the answer ──────────────────────────────────────────────────────────────


USAGE = ("Hỏi gì cũng được, nhắn tự nhiên là được: \"FPT có nên mua không?\", \"danh mục của anh sao rồi?\", "
         "\"hôm nay thị trường thế nào?\", hoặc /hoi câu hỏi. /hoi quen: xoá hội thoại đã nhớ.")
NOTE = "(Tham khảo, không phải lệnh đặt. Quyết định của bot: /nhanh MÃ · danh mục: /ts · lệnh: /trogiup)"


def facts_only(question: str, block: str, why: str) -> str:
    """The answer without the AI: the facts, and why the AI did not speak."""
    return f"🤝 {why} Đây là số liệu bot có cho câu hỏi của anh:\n{block}\n{NOTE}"


def ask_ai(question: str, block: str, turns: list[dict], llm) -> str:
    """One request: the system, the last exchanges, the question with the facts."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages = [SystemMessage(content=SYSTEM)]
    for turn in turns:
        messages += [HumanMessage(content=str(turn.get("q", ""))), AIMessage(content=str(turn.get("a", "")))]
    messages.append(HumanMessage(content=f"CÂU HỎI: {question}\n\nBỐI CẢNH (số liệu của bot):\n{block}"))
    reply = llm.invoke(messages)
    content = reply.content if isinstance(reply.content, str) else json.dumps(reply.content, ensure_ascii=False)
    text = re.sub(r"(\*\*|__|^#+\s*)", "", (content or "").strip(), flags=re.MULTILINE)
    if not text:
        raise ValueError("AI trả lời rỗng")
    return text[:MAX_ANSWER] + ("…" if len(text) > MAX_ANSWER else "")


def answer(question: str, chat_key, *, data_dir, llm_factory=None, account=None, monitor=None, watchlist=(),
           now: datetime | None = None, progress=None) -> str:
    """The reply to ``question`` in the chat ``chat_key``. ``llm_factory``: () -> the chat model, or
    raises/returns None when no provider is configured. ``progress(text)``: told when the facts
    take a few seconds (a stock named)."""
    from tradingagents.market import vn_rules

    question = re.sub(r"\s+", " ", (question or "").strip())[:MAX_QUESTION]
    if question.startswith("/"):
        question = question[1:].strip()
    if not question:
        return USAGE
    memory = Memory(data_dir, chat_key)
    if question.lower() in ("quen", "quên", "xoa", "xoá", "reset"):
        memory.clear()
        return "Đã quên hội thoại trước. Hỏi tiếp đi anh."
    now = (now or vn_rules.now_vn()).astimezone(vn_rules.VN_TZ)
    known = list(watchlist)
    if account is not None:
        try:
            known += list(account.book().holdings)
        except Exception:  # noqa: BLE001
            pass
    codes = codes_in(question, known)
    if codes and progress is not None:
        progress(f"⏳ Đang gom số liệu {', '.join(codes)} (giá, Quant Engine, định giá, danh mục)...")
    block, _ = context(question, now, codes=codes, account=account, data_dir=data_dir, monitor=monitor, watchlist=watchlist)
    turns = memory.turns()
    llm = None
    why = ""
    try:
        from tradingagents.llm_clients.fallback_client import configured_order

        llm = llm_factory() if llm_factory is not None and configured_order() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hỏi đáp: không tạo được AI: %s", exc)
    if llm is None:
        why = "Chưa cấu hình nhà cung cấp AI (ROUTER9/GROQ/OPENROUTER) nên bot chưa trò chuyện được."
    else:
        try:
            from tradingagents.llm_clients.fallback_client import budget_left

            left = budget_left()
        except Exception:  # noqa: BLE001
            left = None
        if left is not None and left < 1:
            why = "Hạn mức AI hôm nay đã hết."
    if not why:
        try:
            text = ask_ai(question, block, turns, llm)
        except Exception as exc:  # noqa: BLE001 - every provider resting, a bad answer: the facts still go out
            logger.warning("Hỏi đáp: AI lỗi: %s", exc)
            why = f"AI đang nghỉ ({type(exc).__name__}), bot trả lời bằng số liệu."
        else:
            memory.add(question, text)
            return text
    text = facts_only(question, block, why)
    memory.add(question, text[:400])
    return text
