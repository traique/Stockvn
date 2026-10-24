"""The Vietnamese lunar date (âm lịch) of a solar date, for the date pill in the header.

The astronomical method of Hồ Ngọc Đức (new moons and the sun's longitude, UTC+7),
the one Vietnamese calendars use: month 11 holds the winter solstice, a year with 13
months repeats the first month without a major solar term. Standard library only.
"""

from __future__ import annotations

import math
from datetime import date

TZ = 7.0
_DR = math.pi / 180


def _jd(d: int, m: int, y: int) -> int:
    """Julian day number of a Gregorian date."""
    a = (14 - m) // 12
    yy = y + 4800 - a
    mm = m + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045


def _new_moon(k: int) -> float:
    """Julian day (UT) of the k-th new moon after 1900-01-01."""
    t = k / 1236.85
    t2, t3 = t * t, t * t * t
    jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * t2 - 0.000000155 * t3
    jd1 += 0.00033 * math.sin((166.56 + 132.87 * t - 0.009173 * t2) * _DR)
    m = 359.2242 + 29.10535608 * k - 0.0000333 * t2 - 0.00000347 * t3
    mpr = 306.0253 + 385.81691806 * k + 0.0107306 * t2 + 0.00001236 * t3
    f = 21.2964 + 390.67050646 * k - 0.0016528 * t2 - 0.00000239 * t3
    c1 = (0.1734 - 0.000393 * t) * math.sin(m * _DR) + 0.0021 * math.sin(2 * _DR * m)
    c1 += -0.4068 * math.sin(mpr * _DR) + 0.0161 * math.sin(_DR * 2 * mpr) - 0.0004 * math.sin(_DR * 3 * mpr)
    c1 += 0.0104 * math.sin(_DR * 2 * f) - 0.0051 * math.sin(_DR * (m + mpr))
    c1 += -0.0074 * math.sin(_DR * (m - mpr)) + 0.0004 * math.sin(_DR * (2 * f + m))
    c1 += -0.0004 * math.sin(_DR * (2 * f - m)) - 0.0006 * math.sin(_DR * (2 * f + mpr))
    c1 += 0.0010 * math.sin(_DR * (2 * f - mpr)) + 0.0005 * math.sin(_DR * (2 * mpr + m))
    if t < -11:
        delta = 0.001 + 0.000839 * t + 0.0002261 * t2 - 0.00000845 * t3 - 0.000000081 * t * t3
    else:
        delta = -0.000278 + 0.000265 * t + 0.000262 * t2
    return jd1 + c1 - delta


def _new_moon_day(k: int) -> int:
    return math.floor(_new_moon(k) + 0.5 + TZ / 24)


def _sun_sector(day_number: int) -> int:
    """The sun's longitude at the start of ``day_number`` (local), in 30° sectors 0-11."""
    t = (day_number - 0.5 - TZ / 24 - 2451545.0) / 36525
    t2 = t * t
    m = 357.52910 + 35999.05030 * t - 0.0001559 * t2 - 0.00000048 * t * t2
    l0 = 280.46645 + 36000.76983 * t + 0.0003032 * t2
    dl = (1.914600 - 0.004817 * t - 0.000014 * t2) * math.sin(_DR * m)
    dl += (0.019993 - 0.000101 * t) * math.sin(_DR * 2 * m) + 0.000290 * math.sin(_DR * 3 * m)
    lon = (l0 + dl) * _DR
    lon -= 2 * math.pi * math.floor(lon / (2 * math.pi))
    return math.floor(lon / math.pi * 6)


def _month11(year: int) -> int:
    """Julian day of the first day of lunar month 11 of ``year``."""
    k = math.floor((_jd(31, 12, year) - 2415021) / 29.530588853)
    nm = _new_moon_day(k)
    return _new_moon_day(k - 1) if _sun_sector(nm) >= 9 else nm


def _leap_offset(a11: int) -> int:
    k = math.floor((a11 - 2415021.076998695) / 29.530588853 + 0.5)
    i, arc = 1, _sun_sector(_new_moon_day(k + 1))
    while True:
        last = arc
        i += 1
        arc = _sun_sector(_new_moon_day(k + i))
        if arc == last or i >= 14:
            return i - 1


def lunar(day: date) -> tuple[int, int, int, bool]:
    """(day, month, year, leap month) of the lunar date of ``day``."""
    dn = _jd(day.day, day.month, day.year)
    k = math.floor((dn - 2415021.076998695) / 29.530588853)
    start = _new_moon_day(k + 1)
    if start > dn:
        start = _new_moon_day(k)
    a11 = b11 = _month11(day.year)
    if a11 >= start:
        year = day.year
        a11 = _month11(day.year - 1)
    else:
        year = day.year + 1
        b11 = _month11(day.year + 1)
    lday = dn - start + 1
    diff = math.floor((start - a11) / 29)
    leap = False
    month = diff + 11
    if b11 - a11 > 365:
        leap_diff = _leap_offset(a11)
        if diff >= leap_diff:
            month = diff + 10
            leap = diff == leap_diff
    if month > 12:
        month -= 12
    if month >= 11 and diff < 4:
        year -= 1
    return lday, month, year, leap


def short(day: date) -> str:
    """"15/09 ÂL" (an "n" after the month in a leap month: "01/06n ÂL")."""
    d, m, _, leap = lunar(day)
    return f"{d:02d}/{m:02d}{'n' if leap else ''} ÂL"
