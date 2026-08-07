"""
Ingest a league match into the LEAGUE ledger — separate from the lobby one.

    python tools/league_ingest.py --from g1.json --dry-run
    python tools/league_ingest.py --from g1.json
    python tools/league_ingest.py --list

WHY A SECOND LEDGER AND NOT A FLAG
-----------------------------------
League games and everyday inhouse games are counted for different things
and must never mix. A flag on a shared ledger would work right up until
one query forgot to apply it — and the whole point of this project is
that a wrong number must not survive quietly for twelve months.

So the league gets its own file, `data/league_matches.json`, and that
file NEVER enters `dota_stats.db`. The lobby database is built by
`load.py` from `data/matches.json` alone, so there is no query anywhere
that could accidentally include a league game in a lobby stat: the rows
are not there to be selected. Tournament numbers are computed in the
browser from the separately-exported league payload.

The cost is this file, which duplicates ingest.py's shape. The
*validation* is not duplicated — `load.validate` is imported, exactly as
ingest.py imports it, so the kills/score/deaths chain has one definition.

WHAT MAKES A MATCH ELIGIBLE
---------------------------
Being in this ledger means "this was a league game", so entry is gated on
it actually looking like one:

  * all five players on a side belong to the SAME team
  * the two sides are two DIFFERENT teams
  * it is not already in the lobby ledger (that would double-count a
    player's history across two systems)

A pub game cannot be filed here by accident, and a league game filed into
the lobby ledger by mistake is caught on the way in.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import load as loader                    # noqa: E402 -- one definition of the checks
from ingest import dump_matches, run     # noqa: E402 -- one definition of the writer
import league_result as LR               # noqa: E402 -- one definition of team identity

LEAGUE  = ROOT / "data" / "league_matches.json"
LOBBY   = ROOT / "data" / "matches.json"
TEAMS   = ROOT / "data" / "teams.json"

HEADER = [
    "LEAGUE match ledger for the 2026-fall season — SEPARATE from",
    "data/matches.json on purpose.",
    "",
    "This file NEVER enters dota_stats.db. load.py builds that database",
    "from data/matches.json alone, so no lobby statistic can include a",
    "league game: the rows simply are not there to be selected. Written",
    "by tools/league_ingest.py, read by export_web.py.",
    "",
    "Same match shape as the lobby ledger, and validated by the same",
    "imported load.validate — the kills/score/deaths chain has exactly",
    "one definition in this codebase.",
    "",
    "Which best-of-three each match belongs to lives in",
    "data/series_results.json, not here. A match can be recorded before",
    "the schedule is settled and attached afterwards.",
]


def _p(s):
    enc = sys.stdout.encoding or "utf-8"
    return str(s).encode(enc, "replace").decode(enc)


def load_ledger() -> dict:
    if not LEAGUE.exists():
        return {"_comment": HEADER, "matches": []}
    d = json.loads(LEAGUE.read_text(encoding="utf-8"))
    d.setdefault("matches", [])
    return d


def save_ledger(payload: dict) -> None:
    payload["_comment"] = HEADER
    LEAGUE.write_text(dump_matches(payload), encoding="utf-8")
    json.loads(LEAGUE.read_text(encoding="utf-8"))       # must still parse


def check(new: list, existing: list) -> list:
    """Every reason this batch must not be written. Empty list == good."""
    errs = []
    teams = json.loads(TEAMS.read_text(encoding="utf-8"))
    lobby = json.loads(LOBBY.read_text(encoding="utf-8"))
    idx = LR.team_index(teams, lobby.get("aliases", []))

    seen_ref = {m["source_ref"] for m in existing}
    seen_fp = {loader.fingerprint(m): m["source_ref"] for m in existing}
    lobby_refs = {m["source_ref"] for m in lobby["matches"]}
    lobby_fp = {loader.fingerprint(m): m["source_ref"] for m in lobby["matches"]}

    for m in new:
        for f in ("source_ref", "winning_side", "players"):
            if f not in m:
                errs.append(f"missing required field {f!r}")
        if errs:
            return errs
        ref = m["source_ref"]

        # Same arithmetic chain as the lobby ledger. Imported, not copied.
        for p in loader.validate(m):
            if "ERROR" in p or "expected" in p or "duplicate" in p:
                errs.append(p)
            else:
                print(f"  [check] {_p(p)}")

        # ── the league-specific gate ──
        rad, rad_unknown, _ = LR.side_team(m, "radiant", idx)
        dire, dire_unknown, _ = LR.side_team(m, "dire", idx)
        for label, tid, unknown in (("radiant", rad, rad_unknown),
                                    ("dire", dire, dire_unknown)):
            if unknown:
                errs.append(f"{ref}: {label} contains player(s) on no league "
                            f"roster: " + ", ".join(sorted(unknown))
                            + " — this is not a league match.")
            elif tid is None:
                errs.append(f"{ref}: {label} is a mix of teams — a league match "
                            f"has one team per side.")
        if rad is not None and rad == dire:
            errs.append(f"{ref}: both sides are Team {rad}.")

        if ref in seen_ref:
            errs.append(f"{ref}: already in the league ledger")
        seen_ref.add(ref)
        if ref in lobby_refs:
            errs.append(f"{ref}: already in the LOBBY ledger (data/matches.json). "
                        f"Remove it there first: tools/ingest.py --remove {ref}")

        fp = loader.fingerprint(m)
        if fp in seen_fp:
            errs.append(f"{ref}: identical roster and K/D/A to "
                        f"{seen_fp[fp]!r} — already recorded")
        if fp in lobby_fp:
            errs.append(f"{ref}: identical roster and K/D/A to "
                        f"{lobby_fp[fp]!r} in the LOBBY ledger — the same game "
                        f"must not be counted in both systems")
        seen_fp[fp] = ref

    return errs


def show_list() -> int:
    payload = load_ledger()
    ms = payload["matches"]
    if not ms:
        print("\n  League ledger is empty.")
        return 0
    teams = json.loads(TEAMS.read_text(encoding="utf-8"))
    lobby = json.loads(LOBBY.read_text(encoding="utf-8"))
    idx = LR.team_index(teams, lobby.get("aliases", []))
    print(f"\n  {len(ms)} league match(es):\n")
    for m in ms:
        rad, _, _ = LR.side_team(m, "radiant", idx)
        dire, _, _ = LR.side_team(m, "dire", idx)
        win = rad if m["winning_side"] == "radiant" else dire
        print(f"  {_p(m['source_ref']):<36} {m.get('played_on') or '????-??-??'}  "
              f"Team {rad} {m.get('radiant_score')}-{m.get('dire_score')} Team {dire}"
              f"   -> Team {win}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", help="JSON file; omit to read stdin")
    ap.add_argument("--dry-run", action="store_true", help="validate only")
    ap.add_argument("--list", action="store_true", help="show the league ledger")
    ap.add_argument("--alias", metavar="ALIAS=CANONICAL",
                    help="record a rename inside the league, e.g. "
                         "--alias 'Boostmode [Mn5tR]=Beast Mode [Mn5tR]'. Applied "
                         "at export, never to the stored rows, so the ledger keeps "
                         "the name that was actually on the screenshot.")
    args = ap.parse_args()

    if args.list:
        return show_list()

    if args.alias:
        if "=" not in args.alias:
            print("\n  REFUSED — expected ALIAS=CANONICAL")
            return 1
        alias, canonical = (s.strip() for s in args.alias.split("=", 1))
        payload = load_ledger()
        rosters = {r["name"] for t in json.loads(TEAMS.read_text(encoding="utf-8"))["teams"]
                   for r in t["roster"]}
        if canonical not in rosters:
            print(f"\n  REFUSED — {canonical!r} is on no league roster. The "
                  f"canonical side of a league alias must be a roster name.")
            return 1
        # An alias must never merge two people who appeared in the SAME
        # game -- that is two players, whatever the names look like. This
        # is the one precondition the lobby merge path also refuses to
        # skip, and for the same reason.
        for m in payload["matches"]:
            names = {p["name"] for p in m["players"]}
            if alias in names and canonical in names:
                print(f"\n  REFUSED — {alias!r} and {canonical!r} both played in "
                      f"{m['source_ref']}. Two names in one game are two people.")
                return 1
        al = payload.setdefault("aliases", [])
        if any(a["alias"] == alias for a in al):
            print(f"\n  {alias!r} is already recorded as an alias.")
            return 0
        al.append({"alias": alias, "canonical": canonical})
        save_ledger(payload)
        print(f"\n  recorded: {alias!r} -> {canonical!r}")
        print(f"  {len(al)} league alias(es). Re-run export_web.py to apply.")
        return 0

    raw = Path(args.src).read_text(encoding="utf-8") if args.src else sys.stdin.read()
    incoming = json.loads(raw)
    new = incoming if isinstance(incoming, list) else [incoming]

    payload = load_ledger()
    existing = payload["matches"]

    print(f"\n  validating {len(new)} league match(es) against "
          f"{len(existing)} already recorded")
    errs = check(new, existing)
    if errs:
        print("\n  REFUSED — nothing was written:")
        for e in errs:
            print(f"    x {_p(e)}")
        return 1

    for m in new:
        print(f"    ok {_p(m['source_ref'])}: {m.get('radiant_score')}-"
              f"{m.get('dire_score')}, {m['winning_side']} win")

    if args.dry_run:
        print("\n  dry run — nothing written.")
        return 0

    payload["matches"] = existing + new
    save_ledger(payload)
    print(f"\n  appended to {LEAGUE.relative_to(ROOT)} "
          f"({len(payload['matches'])} league match(es))")
    print("  NOT written to dota_stats.db — league games never enter the "
          "lobby database.")

    if not run([sys.executable, "export_web.py"]):
        return 3
    print("\n  Next: attach it to a series with tools/league_result.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
