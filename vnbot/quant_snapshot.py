"""The thresholds frozen for a measurement: a snapshot of ``quant_params.json`` taken at the
start (the first trading run after the deploy, or again by hand on the page /bot), compared
on every run with the set in force.

Only the CALIBRATABLE fields are compared (``params.TUNABLE`` and ``MOMENTUM_TUNABLE``): the
hand switches (``breakout_buy``) and the defensive book's fixed fields are not, so turning the
breakout switch is not "a parameter moved during the measurement". A change is logged, said
once on Telegram (per set of values) and shown on /bot; whether ``AUTO_CALIBRATE`` is on is
shown next to it. State: ``data/results/quant_params_snapshot.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FILE = "quant_params_snapshot.json"


def watched() -> tuple[str, ...]:
    from tradingagents.quant import params as qp

    return tuple(qp.TUNABLE) + tuple(qp.MOMENTUM_TUNABLE)


def values(params=None) -> dict:
    """The watched fields of ``params`` (the set in force by default), {name: value}."""
    from tradingagents.quant import params as qp

    params = params or qp.active()
    return {k: getattr(params, k) for k in watched()}


def fingerprint(vals: dict) -> str:
    """7 hex characters of the watched values (for the summary sheet and the "said once")."""
    body = json.dumps({k: vals[k] for k in sorted(vals)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:7]


def path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "results" / FILE


def load(data_dir: Path | str) -> dict | None:
    try:
        data = json.loads(path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("params"), dict) else None


def _save(data_dir: Path | str, state: dict) -> None:
    from vnbot.jobs import write_text_atomic

    write_text_atomic(path(data_dir), json.dumps(state, ensure_ascii=False, indent=2))


def take(data_dir: Path | str, *, day: str = "", note: str = "", params=None) -> dict:
    """Freeze the set in force as the measurement's reference."""
    vals = values(params)
    state = {"taken_at": datetime.now().isoformat(timespec="seconds"), "day": day, "note": note,
             "params": vals, "fingerprint": fingerprint(vals), "warned": ""}
    _save(data_dir, state)
    logger.info("Quant params snapshot taken (%s)%s", state["fingerprint"], f": {note}" if note else "")
    return state


def ensure(data_dir: Path | str, day: str = "") -> dict:
    """The snapshot, taken now when there is none (the first run after the deploy)."""
    return load(data_dir) or take(data_dir, day=day, note="lần chạy đầu sau khi cập nhật")


def changes(data_dir: Path | str, params=None) -> list[tuple[str, object, object]]:
    """(name, frozen, now) of the watched fields that moved since the snapshot; [] without one."""
    state = load(data_dir)
    if state is None:
        return []
    now = values(params)
    return [(k, state["params"].get(k), now[k]) for k in watched() if k in state["params"] and state["params"][k] != now[k]]


def warning(data_dir: Path | str, *, auto_calibrate: bool | None = None, params=None) -> str:
    """The Telegram line for a set of values not warned about yet ("" otherwise); records it."""
    state = load(data_dir)
    diff = changes(data_dir, params)
    if state is None or not diff:
        return ""
    fp = fingerprint(values(params))
    if state.get("warned") == fp:
        return ""
    state["warned"] = fp
    _save(data_dir, state)
    moved = ", ".join(f"{k} {a}→{b}" for k, a, b in diff)
    return (f"⚠️ Tham số Quant đổi giữa đợt đo (ảnh chụp {state.get('day') or state['taken_at'][:10]}): {moved}. "
            + ("AUTO_CALIBRATE đang bật: hiệu chỉnh đêm đã đổi tham số - đặt AUTO_CALIBRATE=0 nếu đang đo. "
               if auto_calibrate else "")
            + "Muốn lấy bộ hiện hành làm mốc mới: nút «Chụp lại tham số» trên trang /bot.")


def status(data_dir: Path | str, *, auto_calibrate: bool | None = None) -> dict:
    """What /bot and the summary sheet show."""
    state = load(data_dir)
    diff = changes(data_dir)
    return {"taken": bool(state), "day": (state or {}).get("day") or "", "taken_at": (state or {}).get("taken_at") or "",
            "fingerprint": (state or {}).get("fingerprint") or "", "now": fingerprint(values()),
            "changed": [{"name": k, "frozen": a, "now": b} for k, a, b in diff],
            "auto_calibrate": auto_calibrate}
