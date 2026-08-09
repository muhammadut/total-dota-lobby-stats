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

  * every starter on a side belongs to the SAME team, three of them at
    minimum, and the remaining slots hold only registered stand-ins
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


def lobby_aliases() -> list:
    return json.loads(LOBBY.read_text(encoding="utf-8")).get("aliases", [])


def stamp_teams(matches: list, idx: dict, spare: set) -> int:
    """
    Write `radiant_team_id` / `dire_team_id` onto each match that lacks them.

    WHY HISTORY IS FROZEN AND NOT RECOMPUTED
    ----------------------------------------
    Team attribution used to be derived from teams.json every time the site
    was exported. That is correct only for as long as nobody ever changes
    team. On 2026-08-08 a fifth team was added and Rogue Agent moved from
    Team 4 to Team 5 -- which would have turned five already-played Team 4
    line-ups into "a mix of teams" and quietly dropped five real results
    from the standings, months after they were played.

    A recorded game's teams are a fact about the night it was played. So
    they are resolved ONCE, at ingest, against the roster in force then,
    and stored. The live roster still gates what may ENTER the ledger --
    a new game must look like a league game today -- but it no longer
    rewrites what is already in it.
    """
    n = 0
    for m in matches:
        if m.get("radiant_team_id") and m.get("dire_team_id"):
            continue
        rad, _, _ = LR.side_team(m, "radiant", idx, spare)
        dire, _, _ = LR.side_team(m, "dire", idx, spare)
        if rad is None or dire is None:
            print(f"  ! {_p(m['source_ref'])}: sides do not resolve against the "
                  f"CURRENT roster, so its teams cannot be frozen. Fix the "
                  f"roster before it is lost.")
            continue
        m["radiant_team_id"] = rad
        m["dire_team_id"] = dire
        n += 1
    return n


def freeze_teams() -> int:
    payload = load_ledger()
    teams = json.loads(TEAMS.read_text(encoding="utf-8"))
    lobby = json.loads(LOBBY.read_text(encoding="utf-8"))
    al = payload.get("aliases", []) + lobby.get("aliases", [])
    idx = LR.team_index(teams, al)
    spare = LR.stand_ins(teams, al)
    already = sum(1 for m in payload["matches"] if m.get("radiant_team_id"))
    n = stamp_teams(payload["matches"], idx, spare)
    if n:
        save_ledger(payload)
    print(f"\n  {already} match(es) already carried their teams, {n} stamped now.")
    for m in payload["matches"]:
        print(f"    {_p(m['source_ref']):<36} Team {m.get('radiant_team_id')} vs "
              f"Team {m.get('dire_team_id')}")
    return 0


# Never patchable. name and side are identity; kills, deaths and assists
# are what the checksum chain is computed from. A screenshot that shows a
# different number for one of these is a different reading of the match,
# and belongs in a re-ingest with a human looking at it -- not in a patch
# that says it is only filling in blanks.
FROZEN_PLAYER_FIELDS = ("name", "side", "kills", "deaths", "assists")


def apply_patch(target: dict, patch: dict, overwrite: bool) -> tuple:
    """
    Merge `patch` into `target`. Returns (changes, errors).

    Built for the common case: a screenshot arrives showing the columns
    that were cut off the first one, and the null cells need filling. So
    filling a null is always allowed, and CHANGING a value that is already
    recorded is refused unless --overwrite is passed. Without that
    asymmetry an "amend" is indistinguishable from a silent rewrite, which
    is the one thing this ledger cannot have.
    """
    changes, errs = [], []
    ref = target["source_ref"]

    for k, v in patch.items():
        if k in ("source_ref", "players"):
            continue
        old = target.get(k)
        if old == v:
            continue
        if old is not None and not overwrite:
            errs.append(f"{ref}: {k} is already {old!r}; refusing to change it "
                        f"to {v!r} without --overwrite.")
            continue
        target[k] = v
        changes.append(f"{k}: {old!r} -> {v!r}")

    by_name = {p["name"]: p for p in target["players"]}
    for pp in patch.get("players", []):
        name = pp.get("name")
        if name not in by_name:
            errs.append(f"{ref}: no player called {name!r} in this match.")
            continue
        row = by_name[name]
        filled = []
        for k, v in pp.items():
            if k == "name":
                continue
            if k in FROZEN_PLAYER_FIELDS:
                if row.get(k) != v:
                    errs.append(f"{ref}: {name}: {k} is checksummed or identity "
                                f"and cannot be amended ({row.get(k)!r} -> {v!r}).")
                continue
            old = row.get(k)
            if old == v:
                continue
            if old is not None and not overwrite:
                errs.append(f"{ref}: {name}: {k} is already {old!r}; refusing to "
                            f"change it to {v!r} without --overwrite.")
                continue
            row[k] = v
            filled.append(k)
        if filled:
            changes.append(f"{name}: filled {', '.join(filled)}")
    return changes, errs


def check(new: list, existing: list) -> list:
    """Every reason this batch must not be written. Empty list == good."""
    errs = []
    teams = json.loads(TEAMS.read_text(encoding="utf-8"))
    lobby = json.loads(LOBBY.read_text(encoding="utf-8"))
    idx = LR.team_index(teams, lobby.get("aliases", []))
    spare = LR.stand_ins(teams, lobby.get("aliases", []))

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
        rad, rad_unknown, _ = LR.side_team(m, "radiant", idx, spare)
        dire, dire_unknown, _ = LR.side_team(m, "dire", idx, spare)
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
    spare = LR.stand_ins(teams, lobby.get("aliases", []))
    print(f"\n  {len(ms)} league match(es):\n")
    for m in ms:
        rad, _, _ = LR.side_team(m, "radiant", idx, spare)
        dire, _, _ = LR.side_team(m, "dire", idx, spare)
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
    ap.add_argument("--amend", action="store_true",
                    help="merge the patch in --from into matches that already "
                         "exist, matched on source_ref. Players are matched by "
                         "name and only their NULL columns are filled -- the "
                         "usual case is a second screenshot showing the columns "
                         "the first one cut off.")
    ap.add_argument("--overwrite", action="store_true",
                    help="with --amend, allow a value that is already recorded "
                         "to be replaced. Off by default: an amend that can "
                         "silently rewrite a verified number is not an amend.")
    ap.add_argument("--list", action="store_true", help="show the league ledger")
    ap.add_argument("--freeze-teams", action="store_true",
                    help="stamp radiant_team_id/dire_team_id onto any match "
                         "that lacks them, using the roster in force NOW. Run "
                         "this BEFORE a roster reshuffle: it is what stops a "
                         "transfer rewriting games already played.")
    ap.add_argument("--alias", metavar="ALIAS=CANONICAL",
                    help="record a rename inside the league, e.g. "
                         "--alias 'Boostmode [Mn5tR]=Beast Mode [Mn5tR]'. Applied "
                         "at export, never to the stored rows, so the ledger keeps "
                         "the name that was actually on the screenshot.")
    args = ap.parse_args()

    if args.list:
        return show_list()

    if args.freeze_teams:
        return freeze_teams()

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

    if args.amend:
        by_ref = {m["source_ref"]: m for m in existing}
        touched, errs = [], []
        for patch in new:
            ref = patch.get("source_ref")
            if ref not in by_ref:
                errs.append(f"no match with source_ref {ref!r} in the league ledger.")
                continue
            changes, e = apply_patch(by_ref[ref], patch, args.overwrite)
            errs += e
            touched.append((ref, changes))
        # Re-validate every amended match exactly as a new one would be,
        # against all the others. Same reasoning as ingest.py: a patch can
        # touch scores and rosters, so a weaker check here would let a match
        # be edited into a copy of another, or a winner flipped.
        if not errs:
            for ref, _ in touched:
                others = [m for m in existing if m["source_ref"] != ref]
                errs += check([by_ref[ref]], others)
        if errs:
            print("\n  REFUSED — nothing was written:")
            for e in errs:
                print(f"    x {_p(e)}")
            return 1
        print(f"\n  amending {len(touched)} league match(es)")
        for ref, changes in touched:
            print(f"    {_p(ref)}")
            for c in changes:
                print(f"      {_p(c)}")
        if args.dry_run:
            print("\n  dry run — nothing written.")
            return 0
        save_ledger(payload)
        print(f"\n  wrote {LEAGUE.relative_to(ROOT)}")
        return 0 if run([sys.executable, "export_web.py"]) else 3

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

    # Freeze each new match's teams at ingest, against the roster in force
    # today. See stamp_teams for why this is stored and not recomputed.
    teams_now = json.loads(TEAMS.read_text(encoding="utf-8"))
    al = payload.get("aliases", []) + lobby_aliases()
    stamp_teams(new, LR.team_index(teams_now, al), LR.stand_ins(teams_now, al))

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
