"""
Attach an already-ingested match to its scheduled league series.

A league game takes the SAME path as any other screenshot -- parsed by the
model, checked by `ingest.py` against the kills/score/deaths chain, written
to data/matches.json. This tool does the one extra thing a league game
needs: it says *which best-of-three that game belongs to*, and which team
won it.

    python tools/league_result.py --ref discord-123456789        # inspect
    python tools/league_result.py --ref discord-123456789 --apply
    python tools/league_result.py --ref X --series W1-SAT-S1 --apply
    python tools/league_result.py --list
    python tools/league_result.py --unlink discord-123456789 --apply

WHY THE RESULTS ARE NOT WRITTEN INTO fixtures.json
--------------------------------------------------
`make_fixtures.py` rebuilds data/fixtures.json from scratch every run --
each series is recreated with `"games": []`. Writing results there means
the next schedule regeneration silently erases the season. So results live
here, in data/series_results.json, keyed by series id, and `export_web.py`
merges the two at export time. The schedule stays regenerable; the results
stay safe.

WHY ASSOCIATION IS CHECKED AND NEVER GUESSED
--------------------------------------------
Team records used to be *inferred*: any match after the season start where
three players on one side shared a team got booked as a league result.
These people play inhouse together every night, so a casual pub game whose
sides happened to line up became a permanent win -- Team 3 read 1-0 before
a league game had been played. Every rule below exists to make that class
of mistake impossible:

  * all five players on a side must belong to the SAME team
  * the two sides must be two DIFFERENT teams
  * those two teams must actually have a fixture against each other
  * the match's clock must fall in that fixture's night
  * ambiguity is refused and reported, never resolved by picking one

A game that fails any of these is not a league game. That is the correct
answer, not an error to work around.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The LEAGUE ledger, not the lobby one. A match must be in
# data/league_matches.json before it can be attached to a series -- which
# also means a lobby game can never be attached, because it is not there.
MATCHES  = ROOT / "data" / "league_matches.json"
# Read only for the alias table: a league screenshot can carry a spelling
# that was merged in the lobby ledger, and that mapping is worth reusing.
LOBBY    = ROOT / "data" / "matches.json"
TEAMS    = ROOT / "data" / "teams.json"
FIXTURES = ROOT / "data" / "fixtures.json"
RESULTS  = ROOT / "data" / "series_results.json"

# played_at is stored in the clock of the machine that ran the ingest (see
# discord_pull.local_time), not UTC. Converting it needs that offset, so we
# read it from the running machine rather than assuming one.
LOCAL = datetime.now().astimezone().tzinfo

# A scheduled slot is 3 hours, but a best-of-three that starts late and
# goes the distance can run past it. Widen the acceptance window rather
# than refuse a genuine result for being 20 minutes late -- the roster
# check is what actually identifies the fixture; the clock only separates
# two fixtures between the SAME two teams, which are weeks apart.
EARLY = timedelta(hours=2)
LATE  = timedelta(hours=6)


def _p(s: str) -> str:
    """Console-safe: Windows terminals are not always UTF-8."""
    enc = sys.stdout.encoding or "utf-8"
    return str(s).encode(enc, "replace").decode(enc)


# ── loading ─────────────────────────────────────────────────────────────

def load_json(p: Path, default=None):
    if not p.exists():
        if default is not None:
            return default
        sys.exit(f"  {p.relative_to(ROOT)} not found.")
    return json.loads(p.read_text(encoding="utf-8"))


def load_results() -> dict:
    return load_json(RESULTS, default={"results": {}})


def save_results(data: dict) -> None:
    data["_comment"] = [
        "League series results: which match belongs to which best-of-three.",
        "",
        "Written by tools/league_result.py, read by export_web.py. Kept",
        "SEPARATE from data/fixtures.json on purpose -- make_fixtures.py",
        "regenerates that file from scratch and would erase every result.",
        "",
        "  results[series_id].games[] -- source_ref, winner (team id), game_no",
        "",
        "`winner` is a TEAM ID, not a side. Which side a team drafted on is a",
        "property of the individual game and lives in matches.json.",
    ]
    ordered = {"_comment": data["_comment"], "results": data.get("results", {})}
    RESULTS.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")


# ── identity: player -> team ────────────────────────────────────────────

def team_index(teams: dict, aliases: list) -> dict:
    """
    Build {lowercased name -> team_id} from canonical names, `aka` nicks,
    and the merge table in matches.json.

    The alias table matters: a screenshot can carry a misread spelling that
    has since been merged into a canonical player. Without it, a legitimate
    league game would look like it contained a stranger and be refused.
    """
    idx, canon_team = {}, {}
    for t in teams["teams"]:
        for r in t["roster"]:
            canon_team[r["name"]] = t["id"]
            idx[r["name"].lower()] = t["id"]
            for a in r.get("aka", []):
                idx.setdefault(a.lower(), t["id"])
    # alias -> canonical -> team
    for a in aliases:
        tid = canon_team.get(a["canonical"])
        if tid:
            idx.setdefault(a["alias"].lower(), tid)
    return idx


def side_team(match: dict, side: str, idx: dict):
    """
    Return (team_id, [unknown_names]) for one side of a match.

    team_id is None unless EVERY player on that side maps to the same team.
    """
    names = [p["name"] for p in match["players"] if p["side"] == side]
    tids, unknown = set(), []
    for n in names:
        t = idx.get(n.lower())
        if t is None:
            unknown.append(n)
        else:
            tids.add(t)
    return (tids.pop() if len(tids) == 1 and not unknown else None), unknown, names


# ── fixtures ────────────────────────────────────────────────────────────

def all_series(fixtures: dict):
    for wk in fixtures.get("weeks", []):
        for night in wk.get("nights", []):
            for s in night.get("series", []):
                yield wk, night, s


def match_when_utc(match: dict):
    """Best UTC estimate of when the match was played, or None."""
    at = match.get("played_at")
    if at:
        try:
            return datetime.fromisoformat(at).replace(tzinfo=LOCAL).astimezone(timezone.utc)
        except ValueError:
            pass
    on = match.get("played_on")
    if on:
        # Date only: treat as the whole local day, anchored at noon so a
        # +/-12h zone error cannot push it into the wrong calendar day.
        return datetime.fromisoformat(on + "T12:00").replace(
            tzinfo=LOCAL).astimezone(timezone.utc)
    return None


def candidates(fixtures: dict, ta: int, tb: int, when):
    """Series between these two teams, narrowed by the clock when possible."""
    pair = {ta, tb}
    same = [(wk, n, s) for wk, n, s in all_series(fixtures)
            if set(s.get("teams") or []) == pair]
    if when is None:
        return same, same
    timed = []
    for wk, n, s in same:
        start = datetime.fromisoformat(s["start_utc"])
        end = datetime.fromisoformat(s["end_utc"])
        if start - EARLY <= when <= end + LATE:
            timed.append((wk, n, s))
    return same, timed


# ── the check ───────────────────────────────────────────────────────────

def resolve(ref: str, explicit_series: str | None):
    """
    Work out which series `ref` belongs to. Returns (info, errors).
    `info` is populated as far as it got, so the caller can explain.
    """
    payload  = load_json(MATCHES)
    teams    = load_json(TEAMS)
    fixtures = load_json(FIXTURES)
    results  = load_results()

    errs = []
    info = {"ref": ref}

    match = next((m for m in payload["matches"] if m.get("source_ref") == ref), None)
    if not match:
        return info, [f"no match with source_ref {ref!r} in "
                      f"data/league_matches.json. Ingest it first with "
                      f"tools/league_ingest.py --from <file>."]
    info["match"] = match

    # Already attached somewhere?
    for sid, entry in results.get("results", {}).items():
        for g in entry.get("games", []):
            if g["source_ref"] == ref:
                return info, [f"{ref} is already recorded as game {g['game_no']} "
                              f"of series {sid} (winner: Team {g['winner']}). "
                              f"Use --unlink first if that is wrong."]

    lobby_aliases = (json.loads(LOBBY.read_text(encoding="utf-8")).get("aliases", [])
                     if LOBBY.exists() else [])
    idx = team_index(teams, payload.get("aliases", []) + lobby_aliases)
    rad, rad_unknown, rad_names = side_team(match, "radiant", idx)
    dire, dire_unknown, dire_names = side_team(match, "dire", idx)
    info.update({"radiant_team": rad, "dire_team": dire,
                 "radiant_names": rad_names, "dire_names": dire_names})

    for label, tid, unknown, names in (("Radiant", rad, rad_unknown, rad_names),
                                       ("Dire", dire, dire_unknown, dire_names)):
        if unknown:
            errs.append(f"{label} contains player(s) on no league roster: "
                        + ", ".join(sorted(unknown)))
        elif tid is None:
            spread = sorted({idx[n.lower()] for n in names})
            errs.append(f"{label} is a mix of teams {spread} — a league game has "
                        f"one team per side. This looks like an inhouse game.")
    if rad is not None and rad == dire:
        errs.append(f"both sides map to Team {rad} — a team cannot play itself.")
    if errs:
        return info, errs

    win_side = match.get("winning_side")
    winner = rad if win_side == "radiant" else dire if win_side == "dire" else None
    if winner is None:
        return info, [f"winning_side is {win_side!r}; expected 'radiant' or 'dire'."]
    info["winner"] = winner

    when = match_when_utc(match)
    info["when_utc"] = when
    same, timed = candidates(fixtures, rad, dire, when)

    if explicit_series:
        hit = [(wk, n, s) for wk, n, s in all_series(fixtures)
               if s["id"].upper() == explicit_series.upper()]
        if not hit:
            return info, [f"no series with id {explicit_series!r}."]
        wk, n, s = hit[0]
        if set(s["teams"]) != {rad, dire}:
            return info, [f"series {s['id']} is Team {s['teams'][0]} vs "
                          f"Team {s['teams'][1]}, but this match is Team {rad} vs "
                          f"Team {dire}."]
        chosen = (wk, n, s)
    elif not same:
        return info, [f"Team {rad} and Team {dire} have no fixture against each "
                      f"other anywhere in the season."]
    elif len(timed) == 1:
        chosen = timed[0]
    elif not timed:
        opts = ", ".join(s["id"] for _, _, s in same)
        return info, [f"Team {rad} vs Team {dire} is scheduled {len(same)} time(s), "
                      f"but none of those nights contains "
                      f"{when.strftime('%Y-%m-%d %H:%M UTC') if when else 'this match'}. "
                      f"Pass --series explicitly if this is a replay. Options: {opts}"]
    else:
        opts = ", ".join(s["id"] for _, _, s in timed)
        return info, [f"{len(timed)} fixtures between Team {rad} and Team {dire} "
                      f"overlap this match's time — pass --series. Options: {opts}"]

    wk, night, s = chosen
    info.update({"week": wk["week"], "night": night, "series": s})

    # Series-level guards.
    entry = results.get("results", {}).get(s["id"], {"games": []})
    games = entry.get("games", [])
    best_of = s.get("best_of", 3)
    need = best_of // 2 + 1

    if len(games) >= best_of:
        errs.append(f"series {s['id']} already has all {best_of} games recorded.")
    wins = {}
    for g in games:
        wins[g["winner"]] = wins.get(g["winner"], 0) + 1
    if wins and max(wins.values()) >= need:
        done = max(wins, key=wins.get)
        errs.append(f"series {s['id']} is already decided — Team {done} has "
                    f"{wins[done]} win(s) in a best-of-{best_of}. "
                    f"A further game cannot belong to it.")
    info["existing"] = games
    info["game_no"] = len(games) + 1
    return info, errs


# ── reporting ───────────────────────────────────────────────────────────

def team_name(teams: dict, tid: int) -> str:
    return next((t["name"] for t in teams["teams"] if t["id"] == tid), f"Team {tid}")


def describe(info: dict) -> None:
    teams = load_json(TEAMS)
    m = info.get("match")
    if m:
        print(f"  match      {_p(info['ref'])}")
        print(f"             {m.get('played_at') or m.get('played_on') or 'undated'} local"
              + (f"   ({info['when_utc']:%a %d %b %H:%M} UTC)" if info.get("when_utc") else ""))
        print(f"             {m.get('radiant_score')}-{m.get('dire_score')}, "
              f"{m.get('winning_side')} win")
    if info.get("radiant_team"):
        print(f"  radiant    Team {info['radiant_team']} "
              f"({_p(team_name(teams, info['radiant_team']))})")
        print(f"             {_p(', '.join(info['radiant_names']))}")
    if info.get("dire_team"):
        print(f"  dire       Team {info['dire_team']} "
              f"({_p(team_name(teams, info['dire_team']))})")
        print(f"             {_p(', '.join(info['dire_names']))}")
    s = info.get("series")
    if s:
        print(f"  series     {s['id']}   week {info['week']}, "
              f"{info['night']['day']} {info['night']['date']}, "
              f"slot {s['slot']} ({s['pkt_window']} PKT)")
        print(f"  game       {info['game_no']} of {s.get('best_of', 3)}"
              + (f"   ({len(info['existing'])} already recorded)"
                 if info.get("existing") else ""))
    if info.get("winner"):
        print(f"  WINNER     Team {info['winner']} "
              f"({_p(team_name(teams, info['winner']))})")


def show_list() -> int:
    fixtures = load_json(FIXTURES)
    teams = load_json(TEAMS)
    results = load_results().get("results", {})
    if not results:
        print("\n  No series results recorded yet.\n"
              "  Pull league screenshots with:  python tools/discord_pull.py --source league")
        return 0
    by_id = {s["id"]: (wk, n, s) for wk, n, s in all_series(fixtures)}
    print(f"\n  {len(results)} series with results:\n")
    for sid in sorted(results, key=lambda k: (by_id[k][0]["week"] if k in by_id else 999, k)):
        games = results[sid].get("games", [])
        if sid not in by_id:
            print(f"  {sid:<14} ! not in the current schedule ({len(games)} game(s))")
            continue
        wk, n, s = by_id[sid]
        a, b = s["teams"]
        sc = [sum(1 for g in games if g["winner"] == a),
              sum(1 for g in games if g["winner"] == b)]
        print(f"  {sid:<14} {n['day']} {n['date']}  "
              f"{_p(team_name(teams, a))} {sc[0]}-{sc[1]} {_p(team_name(teams, b))}"
              f"   ({len(games)}/{s.get('best_of',3)} games)")
        for g in games:
            print(f"       game {g['game_no']}  {_p(g['source_ref']):<32} "
                  f"-> Team {g['winner']}")
    return 0


# ── mutation ────────────────────────────────────────────────────────────

def do_unlink(ref: str, apply: bool) -> int:
    data = load_results()
    for sid, entry in data.get("results", {}).items():
        games = entry.get("games", [])
        hit = [g for g in games if g["source_ref"] == ref]
        if not hit:
            continue
        print(f"\n  {ref} is game {hit[0]['game_no']} of {sid}.")
        if not apply:
            print("  dry run — nothing written. Re-run with --apply.")
            return 0
        entry["games"] = [g for g in games if g["source_ref"] != ref]
        for i, g in enumerate(sorted(entry["games"], key=lambda x: x["game_no"]), 1):
            g["game_no"] = i          # renumber so the series stays 1..N
        if not entry["games"]:
            del data["results"][sid]
        save_results(data)
        print(f"  removed. {RESULTS.relative_to(ROOT)} updated — "
              f"re-run export_web.py to refresh the site.")
        return 0
    print(f"\n  {ref} is not attached to any series.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", help="source_ref of an already-ingested match")
    ap.add_argument("--series", help="series id, e.g. W1-SAT-S1 (skips inference)")
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    ap.add_argument("--list", action="store_true", help="show every recorded series")
    ap.add_argument("--unlink", help="detach a source_ref from its series")
    args = ap.parse_args()

    if args.list:
        return show_list()
    if args.unlink:
        return do_unlink(args.unlink, args.apply)
    if not args.ref:
        ap.error("one of --ref, --list or --unlink is required")

    info, errs = resolve(args.ref, args.series)
    print()
    describe(info)
    if errs:
        print("\n  REFUSED — nothing was written:")
        for e in errs:
            print(f"    x {_p(e)}")
        return 1

    if not args.apply:
        print("\n  dry run — nothing written. Re-run with --apply.")
        return 0

    data = load_results()
    sid = info["series"]["id"]
    entry = data.setdefault("results", {}).setdefault(sid, {"games": []})
    entry["games"].append({
        "source_ref": args.ref,
        "winner":     info["winner"],
        "game_no":    info["game_no"],
        "linked_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    entry["games"].sort(key=lambda g: g["game_no"])
    save_results(data)
    print(f"\n  recorded game {info['game_no']} of {sid} "
          f"-> Team {info['winner']}")
    print(f"  wrote {RESULTS.relative_to(ROOT)}")
    print("  Next: python export_web.py   (the site does not change until you do)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
