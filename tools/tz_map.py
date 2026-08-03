"""
Timezone alias resolver + wall-clock -> UTC conversion.

Players type things like "PKT", "ET", "AST", "UTC+5", "+3", "Karachi",
"Riyadh". This module maps all of them to canonical IANA names
("Asia/Karachi", "America/New_York", "Asia/Riyadh") so `find_slot.py`
can do DST-correct math without caring what abbreviation someone typed.

Uses only zoneinfo (Python 3.9+ stdlib). No external tz database
package needed on Windows because Python 3.12 ships with tzdata bundled.

    python tools/tz_map.py resolve PKT       -> Asia/Karachi
    python tools/tz_map.py convert Karachi Sat 21:00 2026-11-01
"""

import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Loose abbreviation -> IANA. Deliberately conservative: only add an
# abbreviation here if we know which region uses it and that DST is
# handled correctly by the IANA name we point to. Adding "CST" would
# be dangerous -- it could mean US Central (America/Chicago) OR China
# Standard Time (Asia/Shanghai) OR Cuba Standard Time. When in doubt,
# require the region name.
ALIASES = {
    # Pakistan (no DST)
    "pkt":      "Asia/Karachi",
    "pakistan": "Asia/Karachi",
    "karachi":  "Asia/Karachi",
    "lahore":   "Asia/Karachi",
    "islamabad": "Asia/Karachi",

    # US Eastern (DST)
    "et":            "America/New_York",
    "est":           "America/New_York",
    "edt":           "America/New_York",
    "eastern":       "America/New_York",
    "eastern time":  "America/New_York",
    "new york":      "America/New_York",
    "nyc":           "America/New_York",
    "toronto":       "America/Toronto",  # same offset as NYC, safe default

    # US Pacific
    "pt":            "America/Los_Angeles",
    "pst":           "America/Los_Angeles",
    "pdt":           "America/Los_Angeles",
    "pacific":       "America/Los_Angeles",
    "pacific time":  "America/Los_Angeles",
    "la":            "America/Los_Angeles",

    # US Central / Mountain (in case a Team 4 player lives there)
    "ct":            "America/Chicago",
    "central":       "America/Chicago",
    "central time":  "America/Chicago",
    "chicago":       "America/Chicago",
    "mt":            "America/Denver",
    "mountain":      "America/Denver",
    "mountain time": "America/Denver",
    "mst":           "America/Phoenix",  # no DST; safer default for MT lookalikes

    # Saudi Arabia (no DST)
    "ast":      "Asia/Riyadh",         # Arabia Standard Time (not Atlantic)
    "arabia":   "Asia/Riyadh",
    "saudi":    "Asia/Riyadh",
    "riyadh":   "Asia/Riyadh",
    "jeddah":   "Asia/Riyadh",
    "mecca":    "Asia/Riyadh",

    # Central Europe (DST)
    "cet":      "Europe/Berlin",
    "cest":     "Europe/Berlin",
    "berlin":   "Europe/Berlin",
    "paris":    "Europe/Paris",
    "amsterdam": "Europe/Amsterdam",

    # British / Western Europe
    "gmt":      "Europe/London",
    "bst":      "Europe/London",
    "london":   "Europe/London",
    "uk":       "Europe/London",

    # India (no DST)
    "ist":      "Asia/Kolkata",        # India, not Israel
    "india":    "Asia/Kolkata",
    "delhi":    "Asia/Kolkata",
    "mumbai":   "Asia/Kolkata",

    # UAE (no DST)
    "gst":      "Asia/Dubai",
    "dubai":    "Asia/Dubai",
    "uae":      "Asia/Dubai",

    # Turkey (no DST)
    "trt":      "Europe/Istanbul",
    "turkey":   "Europe/Istanbul",
    "istanbul": "Europe/Istanbul",

    # Egypt (no DST)
    "eet":      "Africa/Cairo",
    "cairo":    "Africa/Cairo",
    "egypt":    "Africa/Cairo",

    # Plain UTC
    "utc":      "UTC",
    "gmt+0":    "UTC",
    "zulu":     "UTC",
    "z":        "UTC",
}

# Fixed offsets -- less DST-safe than named regions, but honest about
# what was declared. "UTC+5" means +5 forever, no seasonal shift.
_OFFSET_RE = re.compile(r"^(?:utc|gmt)?([+-])(\d{1,2})(?::(\d{2}))?$", re.I)


def resolve(name: str) -> str:
    """
    Return an IANA timezone name for a user-typed alias.

    Raises ValueError on unknown input rather than falling back silently
    to UTC -- a wrong timezone silently applied would put a match on the
    wrong day for that player, which is exactly the failure this system
    is designed to catch loudly, not swallow.
    """
    if not name or not name.strip():
        raise ValueError("empty timezone name")

    key = name.strip().lower()
    if key in ALIASES:
        return ALIASES[key]

    # Very common in chat: "GMT time", "BST time", "PKT time", "IST timezone".
    # Try with those trailing words stripped. This is much better UX than
    # forcing users to know the exact abbreviation.
    for suffix in (" time", " timezone", " zone", " tz"):
        if key.endswith(suffix):
            stripped = key[:-len(suffix)].strip()
            if stripped in ALIASES:
                return ALIASES[stripped]
            # Also try suffix-stripped as a fixed offset ("GMT+5 time")
            m = _OFFSET_RE.match(stripped.replace(" ", ""))
            if m:
                return _offset_to_iana(m, stripped)

    # Try as-is: user might have typed a full IANA name.
    try:
        ZoneInfo(name.strip())
        return name.strip()
    except ZoneInfoNotFoundError:
        pass

    # Try as a fixed offset like "+5", "UTC-4", "GMT+3:30".
    m = _OFFSET_RE.match(key.replace(" ", ""))
    if m:
        return _offset_to_iana(m, name)

    raise ValueError(
        f"unknown timezone {name!r} -- known aliases include "
        "PKT, ET, PT, AST, CET, GMT, UTC+5. Full IANA names also work "
        "(e.g. Asia/Karachi, America/New_York).")


def _offset_to_iana(match, original: str) -> str:
    """Turn an _OFFSET_RE match into an IANA Etc/GMT+/- zone."""
    sign = 1 if match.group(1) == "+" else -1
    hh = int(match.group(2))
    mm = int(match.group(3) or 0)
    # IANA has "Etc/GMT-5" for UTC+5 (the sign is inverted by convention).
    if mm == 0 and 0 <= hh <= 14:
        iana = f"Etc/GMT{'-' if sign > 0 else '+'}{hh}"
        try:
            ZoneInfo(iana)
            return iana
        except ZoneInfoNotFoundError:
            pass
    # Half-hour offsets (India +5:30) have no Etc/GMT alias; ask the user.
    raise ValueError(
        f"cannot map fixed offset {original!r} to an IANA zone -- "
        "declare a region instead, e.g. Asia/Kolkata for +5:30")


def to_utc_from_date(zone: str, ymd: str, hhmm: str) -> datetime:
    """
    Convert a specific local date + time in `zone` to a UTC datetime.

    `ymd`  : "YYYY-MM-DD" in the LOCAL calendar of the given zone
    `hhmm` : "HH:MM" 24-hour. "24:00" becomes next-day 00:00 (used to
             express "midnight" as an end time without ambiguity).

    Applies the CURRENT DST rule for that zone on that date -- e.g.
    Nov 1, 2026 in America/New_York picks up the fall-back transition
    automatically.
    """
    hh, mm = hhmm.split(":")
    hh, mm = int(hh), int(mm)
    from datetime import timedelta as _td
    add_day = 0
    if hh == 24:
        hh, add_day = 0, 1
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"HH:MM out of range: {hhmm}")

    d = datetime.fromisoformat(ymd).date() + _td(days=add_day)
    tz = ZoneInfo(resolve(zone))
    local = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
    return local.astimezone(ZoneInfo("UTC"))


def to_utc(zone: str, day: str, hhmm: str, week_of: str | None = None) -> datetime:
    """
    Convert wall-clock (day + HH:MM) in `zone` to a UTC datetime.

    `day`     : one of Mon, Tue, Wed, Thu, Fri, Sat, Sun
    `hhmm`    : "HH:MM" 24-hour, 00:00 through 24:00 (24:00 == next-day 00:00)
    `week_of` : YYYY-MM-DD anchoring the week (defaults to next occurrence
                of the named weekday from today)

    Returns a UTC-aware datetime. DST is applied correctly for the given
    zone AT THAT MOMENT -- e.g. Nov 1 in America/New_York honours the
    fall-back transition.
    """
    days = {"mon":0, "tue":1, "wed":2, "thu":3, "fri":4, "sat":5, "sun":6}
    dnum = days.get(day.strip().lower()[:3])
    if dnum is None:
        raise ValueError(f"unknown day {day!r}, expected Mon..Sun")

    # Handle 24:00 as next-day 00:00.
    hh, mm = hhmm.split(":")
    hh, mm = int(hh), int(mm)
    add_day = 0
    if hh == 24:
        hh, add_day = 0, 1
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"HH:MM out of range: {hhmm}")

    anchor = (datetime.fromisoformat(week_of).date()
              if week_of else datetime.now().date())
    delta_days = (dnum - anchor.weekday()) % 7
    target_date = anchor + timedelta(days=delta_days + add_day)

    tz = ZoneInfo(resolve(zone))
    local = datetime(target_date.year, target_date.month, target_date.day,
                     hh, mm, tzinfo=tz)
    return local.astimezone(ZoneInfo("UTC"))


def render_utc_in(dt_utc: datetime, zones: list[str]) -> list[tuple[str, str]]:
    """
    Given a UTC datetime, return [(zone_label, "HH:MM day")] for each zone.

    Used to print `Sat 15:00 UTC == 20:00 PKT == 11:00 ET == ...` in
    confirmation messages.
    """
    out = []
    for z in zones:
        try:
            iana = resolve(z)
        except ValueError:
            iana = z
        local = dt_utc.astimezone(ZoneInfo(iana))
        out.append((z, local.strftime("%a %H:%M")))
    return out


# ── Quick CLI so this file can be sanity-checked in isolation ──
def _cli() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    cmd = sys.argv[1]
    if cmd == "resolve":
        for name in sys.argv[2:]:
            try:
                print(f"  {name!r:>16}  ->  {resolve(name)}")
            except ValueError as e:
                print(f"  {name!r:>16}  ->  ERROR  {e}")
        return 0
    if cmd == "convert":
        if len(sys.argv) < 5:
            print("usage: convert ZONE DAY HH:MM [WEEK_OF]")
            return 2
        zone, day, hhmm = sys.argv[2], sys.argv[3], sys.argv[4]
        week_of = sys.argv[5] if len(sys.argv) > 5 else None
        utc = to_utc(zone, day, hhmm, week_of)
        print(f"  {day} {hhmm} {zone}  ->  {utc.isoformat()}  ({utc.strftime('%a %H:%M UTC')})")
        for label, when in render_utc_in(
                utc, ["Asia/Karachi", "America/New_York", "Asia/Riyadh", "Europe/Berlin"]):
            print(f"    {label:<20} {when}")
        return 0
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
