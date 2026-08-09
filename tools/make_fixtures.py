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
other match. With five teams that leaves one team off each night -- the
BYE -- and with four it leaves nobody.

`--meetings` is how many times each pair should meet ACROSS THE SEASON.
It is not an instruction to run another rotation: nights already played
count towards it, and what gets generated is the remainder.

That is why this does not cycle a fixed rotation any more. It used to
repeat the circle method, which only works while every pair owes the same
number of games. After weekend one, four pairs owed one more meeting and
six owed two, and Team 5 -- who did not exist yet -- owed more than
anyone and could never take a bye. `plan_nights` searches instead.

NIGHTS THAT HAVE BEEN PLAYED ARE NEVER REGENERATED
---------------------------------------------------
`--carry` (on by default) copies forward, verbatim, every night whose
series already has a recorded result, and generates only from the first
playing night after it. Without this, adding the fifth team would have
rewritten weekend one -- which was played by four teams -- and orphaned
eleven real games.

Those nights still COUNT: their pairings go towards `--meetings` and
their 3 AM slots seed the balance. The fairness checks below are about
the whole season, because that is what the promise on the site is about.

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
MEETINGS = 2                             # times each pair meets
SEASON_END = date(2026, 12, 31)          # from data/teams.json's season block
SLOT_START_HOURS = {1: 23, 2: 27}        # 11 PM, and 3 AM the next morning
SLOT_LENGTH_H = 3
SLOTS = 2                                # two series a night


def ampm(dt: datetime) -> str:
    """3:00 AM, not 03:00 -- the league reads these, not a machine."""
    return dt.strftime("%I:%M %p").lstrip("0")


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


def _tally(night_pairs: list, picks, teams: int, seed: Counter) -> Counter:
    late = Counter({t: 0 for t in range(1, teams + 1)})
    late.update(seed)
    for pairs, pick in zip(night_pairs, picks):
        for t in pairs[pick]:
            late[t] += 1
    return late


def late_plan(night_pairs: list, teams: int, seed: Counter) -> list:
    """
    Decide, for every night, WHICH pairing takes the 3 AM slot.

    Solved across the whole season at once, not night by night, and seeded
    with the late slots the already-played nights handed out -- otherwise
    balancing only the remainder leaves whoever drew 3 AM in week one
    permanently ahead.

    A greedy pass shipped first and was wrong: for five teams over fifteen
    nights it produced 7/7/4/6/6 and the fairness check refused to write,
    when 6/6/6/6/6 exists. The pairing that balances tonight can strand a
    team three weeks later, which is exactly what a greedy cannot see.

    Scored on the sorted-descending tuple of resulting counts, so the
    arrangement that lowers the worst-off team wins.
    """
    n = len(night_pairs)
    if n <= LATE_EXACT_MAX:
        best = None
        for picks in itertools.product(range(SLOTS), repeat=n):
            key = tuple(sorted(_tally(night_pairs, picks, teams, seed).values(),
                               reverse=True))
            if best is None or key < best[0]:
                best = (key, list(picks))
        return best[1]

    picks, late = [], Counter(seed)
    for pairs in night_pairs:
        best = None
        for c in range(SLOTS):
            trial = Counter(late)
            for t in pairs[c]:
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
    change it may not even be expressible by the new rotation.

    Also returns what those nights ALREADY CONTRIBUTE to the season: which
    pairs have met, and who has taken the 3 AM slot. Both count towards
    the season targets -- "every pair meets twice" means twice in total,
    not twice more -- so the generator schedules the remainder rather than
    a fresh full rotation.

    Returns (weeks, last_night_date, met, late).
    """
    met, late = Counter(), Counter()
    if not OUT.exists() or not results:
        return [], None, met, late
    old = json.loads(OUT.read_text(encoding="utf-8"))
    played, last = [], None
    for wk in old.get("weeks", []):
        nights = [n for n in wk.get("nights", [])
                  if any(s["id"] in results for s in n.get("series", []))]
        if not nights:
            continue
        for n in nights:
            for s in n["series"]:
                met[tuple(sorted(s["teams"]))] += 1
                if s["slot"] == max(SLOT_START_HOURS):
                    for t in s["teams"]:
                        late[t] += 1
                s["status"] = "scheduled"
                s["score"] = [0, 0]
                s["games"] = []           # results are merged at export, not here
            last = max(last or n["date"], n["date"])
        played.append({**wk, "nights": nights})
    return played, (date.fromisoformat(last) if last else None), met, late


def plan_nights(need: Counter, teams: int, byes: Counter, limit: int = 4000):
    """
    Lay the REMAINING series out over nights of `SLOTS` disjoint matches.

    The old generator repeated one clean cycle of the circle method, which
    only works when every pair owes the same number of games. It stops
    working the moment part of the season has been played: after weekend
    one, four pairs owed one more meeting and six owed two, and Team 5 --
    who did not exist yet -- owed more than anyone and could never take a
    bye. So this searches instead of cycling.

    Depth-first, most-constrained-first: the pair with the most games left
    is placed first, and among equal choices the team with the fewest byes
    so far is the one sent home. Prunes on the two facts that make a
    remainder infeasible -- a pair owing more games than there are nights
    left, and a team owing more games than there are nights left, since
    nobody plays twice in a night. Yields solutions lazily so the caller
    can keep looking for one whose 3 AM split is even.
    """
    total = sum(need.values())
    if total % SLOTS:
        sys.exit(f"  {total} series left does not divide into nights of "
                 f"{SLOTS}. Adjust --meetings.")
    n_nights = total // SLOTS
    found = 0

    def combos(remaining):
        """Every set of SLOTS disjoint pairs that still owe a game."""
        live = sorted([p for p, c in remaining.items() if c > 0],
                      key=lambda p: -remaining[p])
        for pick in itertools.combinations(live, SLOTS):
            used = [t for p in pick for t in p]
            if len(set(used)) == len(used):
                yield pick

    def per_team(remaining):
        c = Counter()
        for (a, b), n in remaining.items():
            c[a] += n
            c[b] += n
        return c

    def walk(remaining, byes_now, left, acc):
        nonlocal found
        if left == 0:
            found += 1
            yield list(acc)
            return
        if found >= limit:
            return
        if any(n > left for n in remaining.values()):
            return
        counts = per_team(remaining)
        if any(n > left for n in counts.values()):
            return
        cands = list(combos(remaining))
        # Send home whoever has sat out least; break ties by placing the
        # most-owed pairs first, which is what keeps the search shallow.
        cands.sort(key=lambda pick: (
            byes_now[next(iter(set(range(1, teams + 1))
                               - {t for p in pick for t in p}), 0)],
            -sum(remaining[p] for p in pick)))
        for pick in cands:
            off = set(range(1, teams + 1)) - {t for p in pick for t in p}
            nxt = Counter(remaining)
            for p in pick:
                nxt[p] -= 1
            nb = Counter(byes_now)
            for t in off:
                nb[t] += 1
            acc.append(pick)
            yield from walk(nxt, nb, left - 1, acc)
            acc.pop()

    yield from walk(Counter(need), Counter(byes), n_nights, [])


def build(first: date = FIRST_NIGHT, days: tuple = NIGHT_DAYS,
          teams: int = TEAM_COUNT, meetings: int = MEETINGS,
          carry: bool = True) -> dict:
    results = (json.loads(RESULTS.read_text(encoding="utf-8")).get("results", {})
               if RESULTS.exists() else {})
    weeks, last_played, done, late_seed = (carried(results) if carry
                                           else ([], None, Counter(), Counter()))

    # `--meetings` is a SEASON target, not an instruction to run another
    # rotation. Nights already played count towards it, so what is
    # generated is the remainder -- which is why this cannot be a cycle:
    # after weekend one four pairs owed one game and six owed two.
    need = Counter()
    for a, b in itertools.combinations(range(1, teams + 1), 2):
        short = meetings - done.get((a, b), 0)
        if short < 0:
            print(f"  ! Team {a} and Team {b} have already met "
                  f"{done[(a, b)]} time(s), more than the {meetings} asked "
                  f"for. Leaving them alone.", file=sys.stderr)
        if short > 0:
            need[(a, b)] = short

    start = first
    if last_played:
        start = last_played + timedelta(days=1)
    nights = playing_nights(start, days, sum(need.values()) // SLOTS)

    # Search for a layout whose 3 AM split is as even as the arithmetic
    # allows, rather than taking the first one that covers the fixtures.
    total_nights = len(nights) + sum(len(w["nights"]) for w in weeks)
    ideal = 2 * total_nights / teams
    allowed = 1 if float(ideal).is_integer() else 2
    best = None
    byes_seed = Counter({t: 0 for t in range(1, teams + 1)})
    for layout in plan_nights(need, teams, byes_seed):
        picks = late_plan(layout, teams, late_seed)
        tally = _tally(layout, picks, teams, late_seed)
        gap = max(tally.values()) - min(tally.values())
        if best is None or gap < best[0]:
            best = (gap, layout, picks, tally)
        if gap <= allowed:
            break
    if best is None:
        sys.exit(f"  no schedule covers {sum(need.values())} remaining series "
                 f"over {len(nights)} nights. Adjust --meetings.")
    _, layout, picks, late_count = best

    played, met = Counter(), Counter()
    # Seeded with every team so a side that never sits out reads 0
    # rather than vanishing from the totals.
    byes = Counter({t: 0 for t in range(1, teams + 1)})
    for pair, n in done.items():
        met[pair] += n

    for n, night in enumerate(nights):
        wk = (night - first).days // 7 + 1
        # The chosen pairing goes LAST, which is slot 2 -- the 3 AM one.
        night_pairs = list(layout[n])
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
    # They cover the WHOLE season -- carried nights included -- because
    # that is what the promise on the site is about. "Every pair meets
    # twice" has to be true of the season, not of the part of it that
    # happens to have been generated on this run.
    season_played = Counter()
    for (a, b), n in met.items():
        season_played[a] += n
        season_played[b] += n

    problems = []
    off_target = {p: n for p, n in met.items() if n != meetings}
    missing = [(a, b) for a, b in itertools.combinations(range(1, teams + 1), 2)
               if (a, b) not in met]
    if off_target or missing:
        problems.append(f"pairs do not all meet {meetings}x: "
                        f"{off_target or ''}{' never: ' + str(missing) if missing else ''}")
    if len(set(season_played.values())) != 1:
        problems.append(f"teams do not play equally often over the season: "
                        f"{dict(sorted(season_played.items()))}")
    gap = max(late_count.values()) - min(late_count.values())
    if gap > allowed:
        problems.append(f"late slot is not shared evenly (gap {gap}, max "
                        f"{allowed} for {total_nights} nights over {teams} "
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
            "series_per_team": season_played[1],
            "new_series_per_team": dict(sorted(played.items())),
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
    print(f"  every team plays {t['series_per_team']} best-of-threes over the "
          f"season · every pair meets {t['meetings_per_pair']}x")
    if s_["carried_nights"]:
        print("  newly scheduled per team: "
              + ", ".join(f"T{k}:{v}" for k, v in t["new_series_per_team"].items()))
    if t["byes_per_team"]:
        print("  byes: " + ", ".join(f"T{k}:{v}" for k, v in
                                     t["byes_per_team"].items()))
    print("  late slot (season): "
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
