"""
Generate the season fixture list -- who plays whom, on which night, in which slot.

    python tools/make_fixtures.py --dry-run     # print the table, write nothing
    python tools/make_fixtures.py               # write data/fixtures.json
    python tools/make_fixtures.py --first 2026-08-07 --days Fri,Sat --meetings 3

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
team plays once and can watch the other. THREE nights is therefore one
complete cycle, after which every pair has met exactly once.

That is why the season length is expressed as `--meetings` -- how many
times each pair should meet -- and never as a raw night count. Nights are
`3 x meetings` by construction, so a part-finished cycle (which would
leave some pairs having met once more than others) cannot be expressed.

WHICH PAIR TAKES 3 AM, AND WHY IT CANNOT ALWAYS BE EVEN
-------------------------------------------------------
Slot 2 starts at 3 AM Pakistan time, which is a real cost, so the pair
that takes it is chosen greedily to keep the running totals level.

With `k` cycles, each split is used k times, and a little algebra says all
four late-slot counts must share the same parity:

    T1 + T2 = 2a + 2k     (a = how often split 0 sent pair (1,2) late)

so T2, T3 and T4 all have the same parity as T1. The counts sum to 6k, so
a perfectly even share is 1.5k -- an integer only when k is EVEN. With an
odd number of cycles a perfect split would need two teams on 1.5k-0.5 and
two on 1.5k+0.5, which is mixed parity and therefore impossible. The best
attainable is a gap of two nights.

So the check below is parity-aware: it demands a gap of 0 for an even
number of cycles and refuses anything above 2 for an odd one, and it says
out loud which team drew the short straw rather than burying it. If that
matters, use an even `--meetings`.

The league runs continuously to the end of the season -- no playoffs, no
final.
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

# Fri 7 Aug 2026 is the night the season actually started -- the first two
# series were played on it before the schedule was rewritten to match.
FIRST_NIGHT = date(2026, 8, 7)
NIGHT_DAYS = ("Fri", "Sat")
MEETINGS = 3                             # times each pair meets -> 9 nights
SEASON_END = date(2026, 12, 31)          # from data/teams.json's season block
SLOT_START_HOURS = {1: 23, 2: 27}        # 11 PM, and 3 AM the next morning
SLOT_LENGTH_H = 3


def ampm(dt: datetime) -> str:
    """3:00 AM, not 03:00 -- the league reads these, not a machine."""
    return dt.strftime("%I:%M %p").lstrip("0")


def night_dates(first: date, days: tuple, count: int) -> list[date]:
    """The first `count` playing nights on or after `first`."""
    out, d = [], first
    while len(out) < count:
        if d.strftime("%a") in days:
            out.append(d)
        d += timedelta(days=1)
        if d > SEASON_END + timedelta(days=7):
            sys.exit(f"  cannot fit {count} nights on {'/'.join(days)} "
                     f"before the season ends ({SEASON_END}).")
    return out


def late_pair_order(n: int, late: Counter):
    """
    Order this night's two pairings so the SECOND one takes the 3 AM slot,
    picking whichever choice keeps the running late-slot totals flattest.

    Scored by the sorted-descending tuple of resulting counts, so the
    choice that lowers the worst-off team wins. Checked against a
    brute-force search over every night for 3, 6, 9, 12 and 42 nights --
    this greedy reaches the optimum in each.
    """
    best = None
    for flip in (False, True):
        pairs = list(SPLITS[n % 3])
        if flip:
            pairs.reverse()
        trial = Counter(late)
        trial[pairs[1][0]] += 1
        trial[pairs[1][1]] += 1
        key = tuple(sorted(trial.values(), reverse=True))
        if best is None or key < best[0]:
            best = (key, pairs, trial)
    return best[1], best[2]


def build(first: date = FIRST_NIGHT, days: tuple = NIGHT_DAYS,
          meetings: int = MEETINGS) -> dict:
    weeks, late_count, meetings_seen = [], Counter({1: 0, 2: 0, 3: 0, 4: 0}), Counter()
    played = Counter()
    nights = night_dates(first, days, 3 * meetings)

    for n, night in enumerate(nights):
        wk = (night - first).days // 7 + 1
        pairs, late_count = late_pair_order(n, late_count)

        series = []
        for slot, (a, b) in enumerate(pairs, start=1):
            start = (datetime.combine(night, datetime.min.time(), PKT)
                     + timedelta(hours=SLOT_START_HOURS[slot]))
            end = start + timedelta(hours=SLOT_LENGTH_H)
            played[a] += 1
            played[b] += 1
            meetings_seen[(a, b)] += 1
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
                        "week_of": (first + timedelta(weeks=wk - 1)).isoformat(),
                        "nights": []}
            weeks.append(wk_entry)
        wk_entry["nights"].append({"date": night.isoformat(),
                                   "day": night.strftime("%a"),
                                   "series": series})

    # --- the checks this script exists for --------------------------------
    problems = []
    if len(set(meetings_seen.values())) != 1:
        problems.append(f"pairs do not meet equally often: {dict(meetings_seen)}")
    if len(set(played.values())) != 1:
        problems.append(f"teams do not play equally often: {dict(played)}")
    # Parity (see the module docstring): an even number of cycles can and
    # must divide the 3 AM slot exactly; an odd number provably cannot do
    # better than a gap of two.
    gap = max(late_count.values()) - min(late_count.values())
    allowed = 0 if meetings % 2 == 0 else 2
    if gap > allowed:
        problems.append(f"late slot is not shared evenly (gap {gap}, "
                        f"max {allowed} for {meetings} meetings): "
                        f"{dict(late_count)}")
    if problems:
        for p in problems:
            print(f"  REFUSING TO WRITE: {p}", file=sys.stderr)
        sys.exit(1)

    return {
        "_comment": [
            "Generated by tools/make_fixtures.py -- do not edit by hand.",
            "Re-run the tool instead; it re-checks that every pair meets the",
            "same number of times and that the 3 AM slot is shared as evenly",
            "as the arithmetic allows.",
            "",
            "A series is one best-of-three between two teams in one slot.",
            "`games` is filled in as results arrive; `score` is series score.",
            "Results themselves live in data/series_results.json, NOT here --",
            "this file is rebuilt from scratch on every run.",
        ],
        "season": {"id": "2026-fall",
                   "first_night": nights[0].isoformat(),
                   "last_night": nights[-1].isoformat(),
                   "nights": len(nights),
                   "night_days": list(days),
                   "meetings_per_pair": meetings,
                   "reference_zone": "Asia/Karachi"},
        "slots": [
            {"n": 1, "label": "Slot 1", "pkt_window": "11:00 PM - 2:00 AM"},
            {"n": 2, "label": "Slot 2", "pkt_window": "3:00 AM - 6:00 AM"},
        ],
        "totals": {
            "series_per_team": played[1],
            "late_slots_per_team": dict(sorted(late_count.items())),
            "meetings_per_pair": next(iter(meetings_seen.values())),
        },
        "weeks": weeks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--first", default=FIRST_NIGHT.isoformat(),
                    help=f"first playing night, YYYY-MM-DD (default {FIRST_NIGHT})")
    ap.add_argument("--days", default=",".join(NIGHT_DAYS),
                    help=f"playing nights, e.g. Fri,Sat (default "
                         f"{','.join(NIGHT_DAYS)})")
    ap.add_argument("--meetings", type=int, default=MEETINGS,
                    help=f"how many times each pair meets (default {MEETINGS}). "
                         f"Nights = 3 x this, so a cycle is never left half "
                         f"finished. An EVEN number also divides the 3 AM slot "
                         f"exactly.")
    args = ap.parse_args()

    first = date.fromisoformat(args.first)
    days = tuple(d.strip().title()[:3] for d in args.days.split(",") if d.strip())
    if first.strftime("%a") not in days:
        sys.exit(f"  --first {first} is a {first:%A}, which is not in --days "
                 f"{'/'.join(days)}.")
    if args.meetings < 1:
        sys.exit("  --meetings must be at least 1.")

    data = build(first, days, args.meetings)
    t, s_ = data["totals"], data["season"]
    late = t["late_slots_per_team"]
    print(f"  {s_['nights']} nights across {len(data['weeks'])} weeks "
          f"({s_['first_night']} -> {s_['last_night']}) on "
          f"{'/'.join(s_['night_days'])}")
    print(f"  each team plays {t['series_per_team']} best-of-threes · "
          f"every pair meets {t['meetings_per_pair']}x · "
          f"late slot per team: "
          + ", ".join(f"T{k}:{v}" for k, v in late.items()))
    if max(late.values()) - min(late.values()):
        light = [k for k, v in late.items() if v == min(late.values())]
        print(f"  NOTE: {args.meetings} meetings is an odd number of cycles, so "
              f"the 3 AM slot cannot divide evenly — "
              f"Team {', '.join(map(str, light))} draws it "
              f"{max(late.values()) - min(late.values())} fewer times. Use an "
              f"even --meetings for an exact split.")

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
