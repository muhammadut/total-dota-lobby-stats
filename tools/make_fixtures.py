"""
Generate the season fixture list -- who plays whom, on which night, in which slot.

    python tools/make_fixtures.py --dry-run     # print the table, write nothing
    python tools/make_fixtures.py               # write data/fixtures.json

WHY THIS IS GENERATED AND NOT HAND-WRITTEN
------------------------------------------
The fixture list is the one part of the league where a quiet mistake is
invisible: nobody notices that Team 2 drew the 3 AM slot four weekends
running, or that two teams only ever met twice, until the season is over.
Generating it means both properties can be *checked* -- and this script
refuses to write a schedule that fails either check.

THE SHAPE
---------
Four teams split into two matches exactly three ways:

    (1v2, 3v4)      (1v3, 2v4)      (1v4, 2v3)

Each split is one night: two best-of-three series, one per slot, so every
team plays once and can watch the other. Cycling the three splits means
every pair meets equally often.

The league runs continuously to the end of the season -- no playoffs, no
final. Two nights a weekend (Saturday, Sunday), so the number of nights
is trimmed to a multiple of three: a part-finished cycle would leave some
pairs having met one more time than others, which is precisely the
unfairness this file exists to prevent.

Slot 2 starts at 3 AM Pakistan time, which is a real cost, so which pair
takes it flips every night. Over the season each team plays the late slot
as close to half the time as the arithmetic allows.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fixtures.json"

PKT = ZoneInfo("Asia/Karachi")          # the league's reference clock, no DST
UTC = ZoneInfo("UTC")

SPLITS = [[(1, 2), (3, 4)], [(1, 3), (2, 4)], [(1, 4), (2, 3)]]

FIRST_SATURDAY = date(2026, 8, 8)
SEASON_END = date(2026, 12, 31)          # from data/teams.json's season block
SLOT_START_HOURS = {1: 23, 2: 27}        # 11 PM, and 3 AM the next morning
SLOT_LENGTH_H = 3


def ampm(dt: datetime) -> str:
    """3:00 AM, not 03:00 -- the league reads these, not a machine."""
    return dt.strftime("%I:%M %p").lstrip("0")


def night_dates() -> list[date]:
    """
    Every Saturday and Sunday from the first weekend to the season end,
    trimmed to a multiple of three.

    The trim is the whole point: three nights is one complete cycle of the
    splits, so stopping part-way through a cycle would leave some pairs
    having played each other once more than others. Dropping up to two
    nights is a much smaller cost than an uneven season.
    """
    out, d = [], FIRST_SATURDAY
    while d <= SEASON_END:
        out.append(d)                                   # Saturday
        if d + timedelta(days=1) <= SEASON_END:
            out.append(d + timedelta(days=1))           # Sunday
        d += timedelta(weeks=1)
    return out[:len(out) - len(out) % 3]


def build() -> dict:
    weeks, late_count, meetings = [], Counter(), Counter()
    played = Counter()
    nights = night_dates()

    for n, night in enumerate(nights):
        wk = (night - FIRST_SATURDAY).days // 7 + 1
        pairs = list(SPLITS[n % 3])
        if n % 2:                        # flip who takes the 3 AM slot
            pairs.reverse()

        series = []
        for slot, (a, b) in enumerate(pairs, start=1):
            start = (datetime.combine(night, datetime.min.time(), PKT)
                     + timedelta(hours=SLOT_START_HOURS[slot]))
            end = start + timedelta(hours=SLOT_LENGTH_H)
            if slot == 2:
                late_count[a] += 1
                late_count[b] += 1
            played[a] += 1
            played[b] += 1
            meetings[(a, b)] += 1
            series.append({
                "id": f"W{wk}-{night:%a}-S{slot}".upper(),
                "slot": slot,
                "teams": [a, b],
                "best_of": 3,
                "start_utc": start.astimezone(UTC).isoformat(),
                "end_utc": end.astimezone(UTC).isoformat(),
                "pkt_window": f"{ampm(start)} - {ampm(end)}",
                "status": "scheduled",
                "score": [0, 0],
                "games": [],
            })

        wk_entry = next((w for w in weeks if w["week"] == wk), None)
        if wk_entry is None:
            wk_entry = {"week": wk, "phase": "Season",
                        "week_of": (FIRST_SATURDAY
                                    + timedelta(weeks=wk - 1)).isoformat(),
                        "nights": []}
            weeks.append(wk_entry)
        wk_entry["nights"].append({"date": night.isoformat(),
                                   "day": night.strftime("%a"),
                                   "series": series})

    # --- the checks this script exists for --------------------------------
    problems = []
    if len(set(meetings.values())) != 1:
        problems.append(f"pairs do not meet equally often: {dict(meetings)}")
    if len(set(played.values())) != 1:
        problems.append(f"teams do not play equally often: {dict(played)}")
    # The late slot cannot always divide perfectly -- with an odd number of
    # cycles one team draws it once more than another. Allow a gap of one,
    # refuse anything wider, and say so rather than hiding it.
    if max(late_count.values()) - min(late_count.values()) > 1:
        problems.append(f"late slot is not shared evenly: {dict(late_count)}")
    if problems:
        for p in problems:
            print(f"  REFUSING TO WRITE: {p}", file=sys.stderr)
        sys.exit(1)

    return {
        "_comment": [
            "Generated by tools/make_fixtures.py -- do not edit by hand.",
            "Re-run the tool instead; it re-checks that every pair meets the",
            "same number of times and that the 3 AM slot is shared evenly.",
            "",
            "A series is one best-of-three between two teams in one slot.",
            "`games` is filled in as results arrive; `score` is series score.",
        ],
        "season": {"id": "2026-fall",
                   "first_night": FIRST_SATURDAY.isoformat(),
                   "last_night": nights[-1].isoformat(),
                   "nights": len(nights),
                   "reference_zone": "Asia/Karachi"},
        "slots": [
            {"n": 1, "label": "Slot 1", "pkt_window": "11:00 PM - 2:00 AM"},
            {"n": 2, "label": "Slot 2", "pkt_window": "3:00 AM - 6:00 AM"},
        ],
        "totals": {
            "series_per_team": played[1],
            "late_slots_per_team": dict(sorted(late_count.items())),
            "meetings_per_pair": next(iter(meetings.values())),
        },
        "weeks": weeks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = build()
    t, s_ = data["totals"], data["season"]
    late = t["late_slots_per_team"]
    print(f"  {s_['nights']} nights across {len(data['weeks'])} weeks "
          f"({s_['first_night']} -> {s_['last_night']})")
    print(f"  each team plays {t['series_per_team']} best-of-threes · "
          f"every pair meets {t['meetings_per_pair']}x · "
          f"late slot per team: "
          + ", ".join(f"T{k}:{v}" for k, v in late.items()))

    for w in data["weeks"]:
        print(f"\n  Week {w['week']}")
        for night in w["nights"]:
            d = date.fromisoformat(night["date"])
            for s in night["series"]:
                print(f"    {d:%a %d %b}  slot {s['slot']}  "
                      f"{s['pkt_window']:<20} "
                      f"Team {s['teams'][0]} vs Team {s['teams'][1]}")

    if args.dry_run:
        print("\n  dry run - nothing written.")
        return 0
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
