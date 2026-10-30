"""Assumptions of the valuation card (``vn_valuation``), set by hand per valuation profile.

They are judgement, not measurements: the card prints them next to every number they move, and a
sensitivity line for Ke. Nothing here is a Quant Engine parameter (``quant.params``), nothing is
calibrated, and nothing of the engine, policy or simulator reads this module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Assumptions:
    ke: float        # cost of equity, a year
    lam: float       # λ: the share of the excess return (ROE − Ke) lost each year
    g: float         # long-run growth of the book
    margin: float    # safety margin: eab = Base × (1 − margin)


# Banks: Ke 12% = the 10-year government bond yield (about 3-4% in 2025-2026) plus an equity risk
# premium of about 8-9% for Vietnam, the range VN brokers commonly use for banks (12-14%). Not checked
# live; the operator overrides it with VALUATION_KE_BANK (in %). The CTG profile PDF used 7,94%, which
# moves its Base about 8,5% higher (CTG 28/10/2026: 29.984 against 27.643). λ 0,4, g 0 and the 13,7%
# margin are the PDF's.
# Every other profile: Ke 13% = the banks' 12% plus one point - no deposit franchise or capital rules
# holding the return up, earnings more cyclical (steel, real estate, brokers), the middle of the 12-15%
# VN brokers commonly use for listed companies (not checked live); overridden with VALUATION_KE_DEFAULT.
# λ, g and the margin stay the PDF's: one model for every stock, its weak spots said by the reliability.
_BANK = Assumptions(ke=0.12, lam=0.4, g=0.0, margin=0.137)
_OTHER = Assumptions(ke=0.13, lam=0.4, g=0.0, margin=0.137)
PROFILES = {"banking": _BANK, "insurance": _OTHER, "securities": _OTHER, "realestate": _OTHER, "default": _OTHER}
ENV_KE = {"banking": "VALUATION_KE_BANK", **{k: "VALUATION_KE_DEFAULT" for k in PROFILES if k != "banking"}}
SENSITIVITY_STEP = 0.02     # the card's sensitivity line: Base at Ke − 2, Ke and Ke + 2 points
KE_BOUNDS = (0.04, 0.30)

# The card's profile of a stock: the sector map's key (first match in this order), else the VCI
# listing's ICB code (icbCode2, checked live 30/10/2026: CTG 8300, BVH 8500, VHM 8600, SSI 8700).
PROFILE_ORDER = ("banking", "insurance", "securities", "realestate")
ICB_PROFILE = {"8300": "banking", "8500": "insurance", "8700": "securities", "8600": "realestate"}
TECH_ICB = "9500"           # technology (FPT): little on the books, the "default" profile's reliability says so
PROFILE_LABELS = {"banking": "ngân hàng", "insurance": "bảo hiểm", "securities": "chứng khoán",
                  "realestate": "bất động sản", "default": "doanh nghiệp"}

# How far the book anchors the value, per profile: (level, why). Every profile but the banks drops to "thấp"
# after a year's ROE under −Ke, the "default" one also for technology, a sustained ROE far above Ke, fast
# growth (vn_valuation._reliability).
RELIABILITY = {
    "banking": ("cao", "sổ sách (tài sản tài chính, ghi gần giá thị trường) là neo giá trị của ngân hàng"),
    "insurance": ("cao", "sổ sách chủ yếu là danh mục đầu tư tài chính: neo giá trị của công ty bảo hiểm"),
    "securities": ("cao", "sổ sách chủ yếu là cho vay ký quỹ và tài sản tài chính; ROE theo thanh khoản thị "
                          "trường nên Bear và Bull cách xa"),
    "realestate": ("thấp", "quỹ đất và dự án ghi theo giá gốc, lợi nhuận theo đợt bàn giao nên ROE năm lên xuống "
                           "mạnh: sổ sách không phản ánh giá trị dự án"),
    "default": ("trung bình", "giá trị doanh nghiệp nằm ở dòng tiền tương lai nhiều hơn ở sổ sách; mô hình giả định "
                              "lợi thế phai dần (λ 0,4) và không tăng trưởng (g 0)"),
}
HIGH_ROE = 0.20             # "default": a sustained ROE this high - the excess return may fade slower than λ says
HIGH_GROWTH = 0.20          # "default": the parent equity growing this fast a year over three years - g 0 is wrong


def sensitivity_ke(ke: float) -> tuple[float, ...]:
    """The Ke values of the sensitivity line, around the Ke in use."""
    return tuple(round(ke + step, 6) for step in (-SENSITIVITY_STEP, 0.0, SENSITIVITY_STEP))


def assumptions(profile_key: str) -> Assumptions | None:
    """The profile's assumptions, Ke overridden by its environment variable (in %, e.g. 13 or 12,5)."""
    base = PROFILES.get(profile_key)
    env = ENV_KE.get(profile_key)
    raw = (os.environ.get(env) or "").strip().rstrip("%").replace(",", ".") if env else ""
    if base is None or not raw:
        return base
    try:
        ke = float(raw) / 100
    except ValueError:
        ke = None
    if ke is None or not KE_BOUNDS[0] <= ke <= KE_BOUNDS[1]:
        logger.warning("%s=%r ignored: Ke in %% from %g to %g", env, raw, KE_BOUNDS[0] * 100, KE_BOUNDS[1] * 100)
        return base
    return replace(base, ke=ke)
