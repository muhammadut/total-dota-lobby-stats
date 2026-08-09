"""
Generate the season fixture list -- who plays whom, on which night, in which slot.

    python tools/make_fixtures.py --dry-run     # print the table, write nothing
    python tools/make_fixtures.py               # write data/fixtures.json
    python tools/make_fixtures.py --teams 5 --meetings 3 --days Fri,Sat

WHY THIS IS GENERATED AND NOT HAND-WRITTEN
------------------------------------------
The fixture list is the one part of the league where a quiet mistake is
invisible: nobody notices that Team 2 drew the 3 AM slot four weekends
running, or that two teams only ever met twice, until the season is over.
Generating it means both properties can be *checked* -- and this script
refuses to write a schedule that fails either check.

THE SHAPE
---------
Two best-of-three series a night, one per slot, so a team can watch the
other match. Pairings come from the standard circle method:

    4 teams -> 3 rounds per cycle, everybody plays every night
    5 teams -> 5 rounds per cycle, ONE TEAM HAS A BYE each night

That bye is the whole difference the fifth team makes. With four teams
the two matches used up all four; with five, four play and one is off.
Each team therefore sits out exactly once per cycle, and a cycle is five
nights rather than three.

Season length is expressed as `--meetings` -- how many times each pair
should meet -- never as a raw night count. Nights are
`meetings x rounds_per_cycle`, so a part-finished cycle (which would
leave some pairs having met once more than others) cannot be expressed.

NIGHTS THAT HAVE BEEN PLAYED ARE NEVER REGENERATED
---------------------------------------------------
`--carry` (on by default) copies forward, verbatim, every night whose
series already has a recorded result, and generates only from the first
playing night after it. Without this, adding the fifth team would have
rewritten weekend one -- which was played by four teams, in pairings a
five-team round-robin cannot even express -- and orphaned eleven real
games. The fairness checks below therefore apply to the GENERATED part
of the season; carried nights are history and are not up for inspection.

WHICH PAIR TAKES 3 AM
---------------------
Slot 2 starts at 3 AM Pakistan time, which is a real cost, so which pair
takes it is solved across the WHOLE season at once (`late_plan`), not
night by night. A greedy pass shipped first and refused to write: for
five teams it produced 7/7/4/6/6 when 6/6/6/6/6 exists. A perfectly even
share is `2 x nights / teams`; when that is not a whole number no exact
split is possible and the check allows a gap of two. It always prints
who drew the short straw.
"""

import argparse
import itertools
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fixtures.json"
RESULTS = ROOT / "data" / "series_results.json"

PKT = ZoneInfo("Asia/Karachi")          # the league's reference clock, no DST
UTC = ZoneInfo("UTC")

# Fri 7 Aug 2026 is the night the season actually started.
FIRST_NIGHT = date(2026, 8, 7)
NIGHT_DAYS = ("Fri", "Sat")
TEAM_COUNT = 5
MEETINGS = 3                             # times each pair meets
SEASON_END = date(2026, 12, 31)          # from data/teams.json's season block
SLOT_START_HOURS = {1: 23, 2: 27}        # 11 PM, and 3 AM the next morning
SLOT_LENGTH_H = 3
SLOTS = 2                                # two series a night


def ampm(dt: datetime) -> str:
    """3:00 AM, not 03:00 -- the league reads these, not a machine."""
    return dt.strftime("%I:%M %p").lstrip("0")


def rounds(n: int) -> list:
    """
    One full cycle of the circle method: every pair of `n` teams exactly
    once, as a list of rounds, each round a list of (a, b) team ids.

    An odd `n` gets a dummy opponent whose pairing is the bye, so a round
    for five teams yields two real matches and one team with the night
    off. Rounds per cycle is `n - 1` for even n and `n` for odd.
    """
    ids = list(range(1, n + 1)) + ([None] if n % 2 else [])
    m = len(ids)
    out = []
    for _ in range(m - 1):
        pairs = [(ids[i], ids[m - 1 - i]) for i in range(m // 2)]
        out.append([p for p in pairs if p[0] is not None and p[1] is not None])
        ids = [ids[0]] + [ids[-1]] + ids[1:-1]        # fix the first, rotate
    return out


def playing_nights(first: date, days: tuple, count: int) -> list:
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


# Above this many nights the exhaustive search stops being instant, and a
# greedy pass takes over. 2**18 combinations is well under a second.
LATE_EXACT_MAX = 18


def _tally(cycle: list, picks, teams: int) -> Counter:
    late = Counter({t: 0 for t in range(1, teams + 1)})
    for i, pick in enumerate(picks):
        for t in cycle[i % len(cycle)][pick]:
            late[t] += 1
    return late


def late_plan(cycle: list, n_nights: int, teams: int) -> list:
    """
    Decide, for every night, WHICH pairing takes the 3 AM slot.

    Searched over the whole season at once, not night by night. A greedy
    pass shipped first and was wrong: for five teams over fifteen nights it
    produced 7/7/4/6/6 and the fairness check refused to write. An
    exhaustive search finds 6/6/6/6/6 -- a perfectly even split existed and
    the greedy simply could not see it, because the pairing that balances
    tonight can strand a team three weeks later.

    Scored on the sorted-descending tuple of resulting counts, so the
    arrangement that lowers the worst-off team wins.
    """
    n_choices = len(cycle[0])
    if n_nights <= LATE_EXACT_MAX:
        best = None
        for picks in itertools.product(range(n_choices), repeat=n_nights):
            key = tuple(sorted(_tally(cycle, picks, teams).values(), reverse=True))
            if best is None or key < best[0]:
                best = (key, list(picks))
        return best[1]

    # Long season: fall back to greedy, which is exact for four teams and
    # good enough beyond the reach of the search.
    picks, late = [], Counter({t: 0 for t in range(1, teams + 1)})
    for n in range(n_nights):
        best = None
        for c in range(n_choices):
            trial = Counter(late)
            for t in cycle[n % len(cycle)][c]:
                trial[t] += 1
            key = tuple(sorted(trial.values(), reverse=True))
            if best is None or key < best[0]:
                best = (key, c, trial)
        picks.append(best[1])
        late = best[2]
    return picks


def carried(results: dict):
    """
    Nights already played, copied forward verbatim.

    A night is history the moment one of its series has a recorded game.
    Regenerating it would rewrite what happened -- and after a roster
    change it may not even be expressible by the new rotation. Returns
    (weeks, last_night_date).
    """
    if not OUT.exists() or not results:
        return [], None
    old = json.loads(OUT.read_text(encoding="utf-8"))
    played, last = [], None
    for wk in old.get("weeks", []):
        nights = [n for n in wk.get("nights", [])
                  if any(s["id"] in results for s in n.get("series", []))]
        if not nights:
            continue
        for n in nights:
            for s in n["series"]:
                s["status"] = "scheduled"
                s["score"] = [0, 0]
                s["games"] = []           # results are merged at export, not here
            last = max(last or n["date"], n["date"])
        played.append({**wk, "nights": nights})
    return played, (date.fromisoformat(last) if last else None)


def build(first: date = FIRST_NIGHT, days: tuple = NIGHT_DAYS,
          teams: int = TEAM_COUNT, meetings: int = MEETINGS,
          carry: bool = True) -> dict:
    results = (json.loads(RESULTS.read_text(encoding="utf-8")).get("results", {})
               if RESULTS.exists() else {})
    weeks, last_played = carried(results) if carry else ([], None)

    cycle = rounds(teams)
    if any(len(r) != SLOTS for r in cycle):
        sys.exit(f"  {teams} teams gives {len(cycle[0])} match(es) a night, but "
                 f"there are {SLOTS} slots. Only 4 or 5 teams fit.")

    start = first
    if last_played:
        start = last_played + timedelta(days=1)
    nights = playing_nights(start, days, len(cycle) * meetings)

    picks = late_plan(cycle, len(nights), teams)
    late_count = _tally(cycle, picks, teams)
    played, met, byes = Counter(), Counter(), Counter()

    for n, night in enumerate(nights):
        wk = (night - first).days // 7 + 1
        # The chosen pairing goes LAST, which is slot 2 -- the 3 AM one.
        night_pairs = list(cycle[n % len(cycle)])
        late_pair = night_pairs.pop(picks[n])
        pairs = night_pairs + [late_pair]
        off = set(range(1, teams + 1)) - {t for p in pairs for t in p}
        for t in off:
            byes[t] += 1

        series = []
        for slot, (a, b) in enumerate(pairs, start=1):
            begin = (datetime.combine(night, datetime.min.time(), PKT)
                     + timedelta(hours=SLOT_START_HOURS[slot]))
            end = begin + timedelta(hours=SLOT_LENGTH_H)
            played[a] += 1
            played[b] += 1
            met[tuple(sorted((a, b)))] += 1
            series.append({
                "id": f"W{wk}-{night:%a}-S{slot}".upper(),
                "slot": slot,
                "teams": [a, b],
                "best_of": 3,
                "start_utc": begin.astimezone(UTC).isoformat(),
                "end_utc": end.astimezone(UTC).isoformat(),
                "pkt_window": f"{ampm(begin)} - {ampm(end)}",
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
        entry = {"date": night.isoformat(), "day": night.strftime("%a"),
                 "series": series}
        if off:
            entry["bye"] = sorted(off)
        wk_entry["nights"].append(entry)

    weeks.sort(key=lambda w: w["week"])
    for w in weeks:
        w["nights"].sort(key=lambda n: n["date"])

    # --- the checks this script exists for --------------------------------
    # They cover the GENERATED nights only. Carried nights were played
    # under whatever rules were in force then and are not up for review.
    problems = []
    if len(set(met.values())) != 1:
        problems.append(f"pairs do not meet equally often: {dict(met)}")
    if len(set(played.values())) != 1:
        problems.append(f"teams do not play equally often: {dict(played)}")
    if byes and len(set(byes.values())) != 1:
        problems.append(f"byes are not shared equally: {dict(byes)}")
    gap = max(late_count.values()) - min(late_count.values())
    ideal = 2 * len(nights) / teams
    allowed = 1 if float(ideal).is_integer() else 2
    if gap > allowed:
        problems.append(f"late slot is not shared evenly (gap {gap}, max "
                        f"{allowed} for {len(nights)} nights over {teams} "
                        f"teams): {dict(late_count)}")
    if problems:
        for p in problems:
            print(f"  REFUSING TO WRITE: {p}", file=sys.stderr)
        sys.exit(1)

    all_nights = [n for w in weeks for n in w["nights"]]
    return {
        "_comment": [
            "Generated by tools/make_fixtures.py -- do not edit by hand.",
            "Re-run the tool instead; it re-checks that every pair meets the",
            "same number of times, that the byes are shared equally, and that",
            "the 3 AM slot is split as evenly as the arithmetic allows.",
            "",
            "A series is one best-of-three between two teams in one slot.",
            "`bye` on a night names the team not playing -- with five teams",
            "and two slots, one team is off every night.",
            "",
            "Results live in data/series_results.json, NOT here: this file is",
            "rebuilt from scratch on every run. Nights that already have a",
            "recorded result are carried forward verbatim rather than",
            "regenerated, so a roster reshuffle cannot rewrite the past.",
        ],
        "season": {"id": "2026-fall",
                   "first_night": all_nights[0]["date"],
                   "last_night": all_nights[-1]["date"],
                   "nights": len(all_nights),
                   "night_days": list(days),
                   "teams": teams,
                   "meetings_per_pair": meetings,
                   "carried_nights": len(all_nights) - len(nights),
                   "reference_zone": "Asia/Karachi"},
        "slots": [
            {"n": 1, "label": "Slot 1", "pkt_window": "11:00 PM - 2:00 AM"},
            {"n": 2, "label": "Slot 2", "pkt_window": "3:00 AM - 6:00 AM"},
        ],
        "totals": {
            "series_per_team": played[1],
            "late_slots_per_team": dict(sorted(late_count.items())),
            "meetings_per_pair": next(iter(met.values())),
            "byes_per_team": dict(sorted(byes.items())) if byes else {},
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
    ap.add_argument("--teams", type=int, default=TEAM_COUNT,
                    help=f"how many teams (default {TEAM_COUNT}). 5 gives one "
                         f"bye a night; 4 gives none.")
    ap.add_argument("--meetings", type=int, default=MEETINGS,
                    help=f"how many times each pair meets (default {MEETINGS}). "
                         f"Nights = this x rounds per cycle, so a cycle is "
                         f"never left half finished.")
    ap.add_argument("--no-carry", action="store_true",
                    help="regenerate EVERY night, including ones already "
                         "played. Orphans their recorded results -- only ever "
                         "correct before the season starts.")
    args = ap.parse_args()

    first = date.fromisoformat(args.first)
    days = tuple(d.strip().title()[:3] for d in args.days.split(",") if d.strip())
    if first.strftime("%a") not in days:
        sys.exit(f"  --first {first} is a {first:%A}, which is not in --days "
                 f"{'/'.join(days)}.")
    if args.meetings < 1:
        sys.exit("  --meetings must be at least 1.")

    data = build(first, days, args.teams, args.meetings, not args.no_carry)
    t, s_ = data["totals"], data["season"]
    late = t["late_slots_per_team"]
    print(f"  {s_['teams']} teams · {s_['nights']} nights across "
          f"{len(data['weeks'])} weeks ({s_['first_night']} -> "
          f"{s_['last_night']}) on {'/'.join(s_['night_days'])}")
    if s_["carried_nights"]:
        print(f"  {s_['carried_nights']} night(s) already played were carried "
              f"forward untouched; the rest is newly generated.")
    print(f"  each team plays {t['series_per_team']} new best-of-threes · "
          f"every pair meets {t['meetings_per_pair']}x · "
          + (f"byes {list(t['byes_per_team'].values())[0]} each · "
             if t["byes_per_team"] else "")
          + "late slot: "
          + ", ".join(f"T{k}:{v}" for k, v in late.items()))
    if max(late.values()) - min(late.values()):
        light = [k for k, v in late.items() if v == min(late.values())]
        print(f"  NOTE: the 3 AM slot cannot divide evenly here — "
              f"Team {', '.join(map(str, light))} draws it "
              f"{max(late.values()) - min(late.values())} fewer times.")

    for w in data["weeks"]:
        print(f"\n  Week {w['week']}")
        for night in w["nights"]:
            d = date.fromisoformat(night["date"])
            for s in night["series"]:
                print(f"    {d:%a %d %b}  slot {s['slot']}  "
                      f"{s['pkt_window']:<20} "
                      f"Team {s['teams'][0]} vs Team {s['teams'][1]}")
            if night.get("bye"):
                print(f"    {d:%a %d %b}  bye        "
                      + ", ".join(f"Team {b}" for b in night["bye"]))

    if args.dry_run:
        print("\n  dry run - nothing written.")
        return 0
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
