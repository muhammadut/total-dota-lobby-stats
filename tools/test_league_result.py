"""
Test the series-association rules in league_result.py.

    python tools/test_league_result.py

Same style as test_league_commands.py: no framework, just PASS/FAIL per
case so coverage is visible at a glance.

UNLIKE that file, this one does NOT back up and restore real state. It
redirects the module's MATCHES and RESULTS paths at a temp directory
instead, so data/matches.json -- the year's ledger -- is never writable
while tests run. teams.json and fixtures.json stay pointed at the real
files because nothing here writes them, and testing against the real
rosters is the point: a lineup that passes here is a lineup that would
pass in production.
"""

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import league_result as LR

TMP = Path(tempfile.mkdtemp(prefix="league_result_test_"))
LR.MATCHES = TMP / "matches.json"
LR.RESULTS = TMP / "series_results.json"

TEAMS = json.loads(LR.TEAMS.read_text(encoding="utf-8"))
FIXTURES = json.loads(LR.FIXTURES.read_text(encoding="utf-8"))

# The first night's two series, READ FROM the schedule rather than named.
# These were once hardcoded as W1-SAT-S1/W1-SAT-S2; the season moved to
# Friday nights and every case broke on an id that no longer existed. The
# structure is what the tests actually depend on -- night one, slot one and
# slot two, which is always two different pairs of teams -- so derive it.
SID1, SID2 = [s["id"] for _, _, s in LR.all_series(FIXTURES)][:2]

PASS = FAIL = 0


def check(name, cond, actual=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"    PASS  {name}")
    else:
        FAIL += 1
        print(f"    FAIL  {name}")
        if actual is not None:
            print(f"          got: {actual}")


def group(title):
    print(f"\n  {title}\n  " + "-" * (len(title) + 2))


# ── builders ────────────────────────────────────────────────────────────

def roster(team_id, n=5):
    """First n non-stand_in players of a team, canonical names."""
    t = next(x for x in TEAMS["teams"] if x["id"] == team_id)
    return [r["name"] for r in t["roster"] if r["role"] != "stand_in"][:n]


def series_by_id(sid):
    for _, _, s in LR.all_series(FIXTURES):
        if s["id"] == sid:
            return s
    raise KeyError(sid)


def local_str(utc_dt):
    """A UTC instant expressed in the machine clock, as matches.json stores it."""
    return utc_dt.astimezone(LR.LOCAL).strftime("%Y-%m-%d %H:%M")


def make_match(ref, radiant, dire, winning_side="radiant", when=None):
    players = ([{"name": n, "side": "radiant", "kills": 1, "deaths": 1, "assists": 1}
                for n in radiant] +
               [{"name": n, "side": "dire", "kills": 1, "deaths": 1, "assists": 1}
                for n in dire])
    return {"source_ref": ref, "played_on": (when or "2026-08-08")[:10],
            "played_at": when, "radiant_score": 30, "dire_score": 20,
            "winning_side": winning_side, "players": players}


def write_matches(matches, aliases=None):
    LR.MATCHES.write_text(json.dumps(
        {"aliases": aliases or [], "matches": matches}, ensure_ascii=False),
        encoding="utf-8")


def write_results(results):
    LR.RESULTS.write_text(json.dumps({"results": results}, ensure_ascii=False),
                          encoding="utf-8")


def at(sid, offset_h=0.5):
    """A local-clock timestamp inside series `sid`'s window."""
    s = series_by_id(sid)
    return local_str(datetime.fromisoformat(s["start_utc"]) + timedelta(hours=offset_h))


# ── cases ───────────────────────────────────────────────────────────────

def main():
    write_results({})

    # SID1 is night one, slot one; SID2 is the other pair, same night.
    s1 = series_by_id(SID1)
    t_a, t_b = s1["teams"]
    print(f"\n  using {s1['id']}: Team {t_a} vs Team {t_b} "
          f"({s1['start_utc']} .. {s1['end_utc']})")

    group("happy path")
    write_matches([make_match("g1", roster(t_a), roster(t_b), "radiant", at(SID1))])
    info, errs = LR.resolve("g1", None)
    check("no errors", not errs, errs)
    check(f"picked {SID1}", info.get("series", {}).get("id") == SID1,
          info.get("series", {}).get("id"))
    check(f"winner is Team {t_a}", info.get("winner") == t_a, info.get("winner"))
    check("game_no 1", info.get("game_no") == 1, info.get("game_no"))

    group("dire win flips the winner")
    write_matches([make_match("g1", roster(t_a), roster(t_b), "dire", at(SID1))])
    info, errs = LR.resolve("g1", None)
    check(f"winner is Team {t_b}", info.get("winner") == t_b, info.get("winner"))

    group("sides swapped still resolves to the same series")
    write_matches([make_match("g1", roster(t_b), roster(t_a), "radiant", at(SID1))])
    info, errs = LR.resolve("g1", None)
    check("same series", info.get("series", {}).get("id") == SID1, errs)
    check(f"winner is Team {t_b} (drafted radiant)", info.get("winner") == t_b,
          info.get("winner"))

    group("refusals — the whole point")
    mixed = roster(t_a)[:3] + roster(t_b)[:2]
    write_matches([make_match("g1", mixed, roster(t_b), "radiant", at(SID1))])
    _, errs = LR.resolve("g1", None)
    check("mixed side refused", any("mix of teams" in e for e in errs), errs)

    write_matches([make_match("g1", roster(t_a)[:4] + ["Some Rando"], roster(t_b),
                              "radiant", at(SID1))])
    _, errs = LR.resolve("g1", None)
    check("stranger refused", any("no league roster" in e for e in errs), errs)

    write_matches([make_match("g1", roster(t_a), roster(t_a), "radiant", at(SID1))])
    _, errs = LR.resolve("g1", None)
    check("team vs itself refused", any("cannot play itself" in e for e in errs), errs)

    write_matches([make_match("g1", roster(t_a), roster(t_b), "radiant", at(SID1))])
    _, errs = LR.resolve("g1", SID2)
    check("explicit series with wrong teams refused",
          any("but this match is" in e for e in errs), errs)
    _, errs = LR.resolve("g1", "NOPE-99")
    check("unknown series id refused", any("no series with id" in e for e in errs), errs)
    _, errs = LR.resolve("does-not-exist", None)
    check("unknown source_ref refused", any("no match with source_ref" in e for e in errs), errs)

    group("clock disambiguates repeat fixtures")
    same, _ = LR.candidates(FIXTURES, t_a, t_b, None)
    print(f"    (Team {t_a} vs Team {t_b} is scheduled {len(same)}x this season)")
    write_matches([make_match("g1", roster(t_a), roster(t_b), "radiant",
                              "2026-06-01 12:00")])
    _, errs = LR.resolve("g1", None)
    check("match outside every window refused",
          any("none of those nights" in e for e in errs), errs)
    check("refusal lists the options", any(SID1 in e for e in errs), errs)

    # A time inside a LATER meeting of the same pair must pick that one.
    later = [s["id"] for _, _, s in LR.all_series(FIXTURES)
             if set(s["teams"]) == {t_a, t_b}][1]
    write_matches([make_match("g1", roster(t_a), roster(t_b), "radiant", at(later))])
    info, errs = LR.resolve("g1", None)
    check(f"second meeting resolves to {later}",
          info.get("series", {}).get("id") == later, info.get("series", {}).get("id"))

    group("series-level guards")
    write_matches([make_match("g1", roster(t_a), roster(t_b), "radiant", at(SID1))])
    write_results({SID1: {"games": [
        {"source_ref": "g1", "winner": t_a, "game_no": 1}]}})
    _, errs = LR.resolve("g1", None)
    check("already-linked ref refused", any("already recorded as game" in e for e in errs), errs)

    write_matches([make_match("g2", roster(t_a), roster(t_b), "radiant", at(SID1))])
    _, errs = LR.resolve("g2", None)
    check("second game allowed", not errs, errs)
    info, _ = LR.resolve("g2", None)
    check("game_no 2", info.get("game_no") == 2, info.get("game_no"))

    write_results({SID1: {"games": [
        {"source_ref": "gA", "winner": t_a, "game_no": 1},
        {"source_ref": "gB", "winner": t_a, "game_no": 2}]}})
    _, errs = LR.resolve("g2", None)
    check("decided series refuses a third game",
          any("already decided" in e for e in errs), errs)

    write_results({SID1: {"games": [
        {"source_ref": "gA", "winner": t_a, "game_no": 1},
        {"source_ref": "gB", "winner": t_b, "game_no": 2},
        {"source_ref": "gC", "winner": t_a, "game_no": 3}]}})
    _, errs = LR.resolve("g2", None)
    check("full series refuses a fourth game",
          any("all 3 games" in e for e in errs), errs)

    group("identity — aka nicks and merged aliases")
    write_results({})
    t = next(x for x in TEAMS["teams"] if x["id"] == t_a)
    nicks = [(r.get("aka") or [r["name"]])[0] for r in t["roster"]
             if r["role"] != "stand_in"][:5]
    write_matches([make_match("g1", nicks, roster(t_b), "radiant", at(SID1))])
    info, errs = LR.resolve("g1", None)
    check("aka nicks map to the team", not errs, errs)

    canon = roster(t_a)[0]
    write_matches(
        [make_match("g1", ["MisreadName"] + roster(t_a)[1:], roster(t_b),
                    "radiant", at(SID1))],
        aliases=[{"alias": "MisreadName", "canonical": canon}])
    info, errs = LR.resolve("g1", None)
    check("merged alias maps to the team", not errs, errs)

    group("stand-ins count for their team")
    t = next(x for x in TEAMS["teams"] if x["id"] == t_a)
    sub = next(r["name"] for r in t["roster"] if r["role"] == "stand_in")
    write_matches([make_match("g1", roster(t_a)[:4] + [sub], roster(t_b),
                              "radiant", at(SID1))])
    _, errs = LR.resolve("g1", None)
    check("stand-in accepted", not errs, errs)

    # A stand-in listed on ANOTHER team's sheet must still count for the
    # side they actually played on. Scarface [FUBU] sits on Team 1 and
    # filled a Team 3 slot the next night; the original all-five rule
    # called that "a mix of teams" and refused a real league result.
    other = next((r["name"] for x in TEAMS["teams"] if x["id"] != t_a
                  for r in x["roster"] if r["role"] == "stand_in"), None)
    write_matches([make_match("g1", roster(t_a)[:4] + [other], roster(t_b),
                              "radiant", at(SID1))])
    info, errs = LR.resolve("g1", None)
    check("stand-in from another team's sheet accepted", not errs, errs)
    check(f"...and the side is still Team {t_a}",
          info.get("radiant_team") == t_a, info.get("radiant_team"))

    # ...but the relaxation has a floor. Two starters plus three floaters
    # is not that team fielding a side, and must not resolve.
    subs = [r["name"] for x in TEAMS["teams"] for r in x["roster"]
            if r["role"] == "stand_in"][:3]
    write_matches([make_match("g1", roster(t_a)[:2] + subs, roster(t_b),
                              "radiant", at(SID1))])
    _, errs = LR.resolve("g1", None)
    check("fewer than 3 starters refused",
          any("mix of teams" in e or "one team per side" in e for e in errs), errs)

    print(f"\n  {PASS} passed, {FAIL} failed\n")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
