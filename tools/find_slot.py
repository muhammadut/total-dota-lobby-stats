"""
Slot finder for a scheduling round.

Reads data/scheduling.json + data/teams.json + data/players_tz.json,
finds the top-N UTC time windows in which the most players from BOTH
teams in a round are available, and renders them in Karachi, New York,
Riyadh, Berlin.

    python tools/find_slot.py                 # first open round, top 3 slots
    python tools/find_slot.py --round R1
    python tools/find_slot.py --round R1 --top 5
    python tools/find_slot.py --format discord    # markdown-ready reply
    python tools/find_slot.py --format json       # machine-readable

Design decisions:

* **Bucket size is 30 minutes.** Small enough to catch a player who
  says "sat 20:30-22:30", large enough that the report is short. If we
  ever need finer resolution, change one constant.

* **A "slot" is the earliest bucket in a contiguous run of buckets at
  the same count level.** So a 4-hour window where 8/10 are available
  becomes ONE slot in the ranking, not 8 separate rows.

* **Ties are broken by (a) count desc, (b) earliest UTC, (c) longest
  run.** So on true ties we prefer the earlier slot -- easier to plan
  around than "sometime late Sunday".

* **DST is applied live at conversion.** The availability entry stores
  wall-clock intent ("Sat 20:00 in Asia/Karachi"), and the UTC math
  runs through zoneinfo which knows the current DST rule. A round that
  straddles Nov 1 stays correct because each entry is re-converted, not
  cached.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from tz_map import resolve, to_utc  # noqa: E402

ROOT = Path(__file__).parent.parent
SCHEDULING = ROOT / "data" / "scheduling.json"
TEAMS      = ROOT / "data" / "teams.json"
PLAYERS_TZ = ROOT / "data" / "players_tz.json"

# 30 min. If we ever need finer, change this and every "slot count" and
# "duration" number below adapts. Fewer than 30 min becomes noisy in the
# rendered output; more than 60 min misses "sat 21:30" style entries.
BUCKET_MIN = 30

# Timezones we always render alongside UTC when reporting a slot. Chosen
# to cover the 4 team regions in a single glance -- if a team relocates,
# add its zone here.
DISPLAY_ZONES = [
    ("Karachi",  "Asia/Karachi"),
    ("New York", "America/New_York"),
    ("Riyadh",   "Asia/Riyadh"),
    ("Berlin",   "Europe/Berlin"),
]


# ── Loading ────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path.name} not found. Run from the repo root, or check the file exists.")
    return json.loads(path.read_text(encoding="utf-8"))


def load_state():
    """Load all three JSON files. No mutation."""
    sched   = _load_json(SCHEDULING)
    teams   = _load_json(TEAMS)
    tz_data = _load_json(PLAYERS_TZ)
    return sched, teams, tz_data


def find_round(sched: dict, round_id: str | None):
    """
    Return the requested round, or the first open one if unspecified.
    Round IDs match case-insensitively -- players type "r1", "R1", "R0-Test".
    """
    rounds = sched.get("rounds", [])
    if round_id:
        want = round_id.strip().lower()
        for r in rounds:
            if r["round_id"].lower() == want:
                return r
        raise LookupError(f"round {round_id!r} not found. Open rounds: "
                          + ", ".join(r["round_id"] for r in rounds))
    open_rounds = [r for r in rounds if r.get("status") == "collecting"]
    if not open_rounds:
        raise LookupError("no open rounds. Use `!schedule Team X vs Team Y` in "
                          "#dota-league-2026 to open one.")
    return open_rounds[0]


def team_rosters(teams: dict, match_up: list[int]) -> dict[int, list[str]]:
    """
    Return {team_id: [player_name, ...]} for the two teams in a match-up.
    Includes stand-in -- the finder is generous about who might play.
    """
    out = {}
    for t in teams["teams"]:
        if t["id"] in match_up:
            out[t["id"]] = [r["name"] for r in t["roster"]]
    missing = [tid for tid in match_up if tid not in out]
    if missing:
        raise LookupError(f"unknown team_id(s) in match_up: {missing}")
    return out


# ── Bucket math ────────────────────────────────────────────────────────

def _iter_buckets(start_utc: datetime, end_utc: datetime):
    """Yield 30-min UTC bucket start times in [start_utc, end_utc)."""
    cur = start_utc
    step = timedelta(minutes=BUCKET_MIN)
    while cur < end_utc:
        yield cur
        cur += step


def buckets_for_window(zone: str, window: dict, week_of: str | None = None) -> list[datetime]:
    """
    Convert an availability window into a list of UTC bucket-start datetimes.

    Two supported window shapes:

      {"date": "YYYY-MM-DD",     "start_local": "HH:MM", "end_local": "HH:MM"}
      {"day":  "Sat"|"Sun"|...,  "start_local": "HH:MM", "end_local": "HH:MM"}

    Date-based windows are the new (2026-08-02+) format; day-of-week
    windows are kept for backward compatibility with pre-Aug archive data
    (and need `week_of` to resolve to an absolute date).
    """
    # Snap start_local down and end_local up to a bucket boundary.
    def snap_down(hhmm: str) -> str:
        hh, mm = hhmm.split(":")
        hh, mm = int(hh), int(mm)
        mm = (mm // BUCKET_MIN) * BUCKET_MIN
        return f"{hh:02d}:{mm:02d}"

    def snap_up(hhmm: str) -> str:
        hh, mm = hhmm.split(":")
        hh, mm = int(hh), int(mm)
        rem = mm % BUCKET_MIN
        if rem:
            mm += (BUCKET_MIN - rem)
        if mm >= 60:
            hh += mm // 60; mm %= 60
        if hh > 24:
            raise ValueError(f"end time {hhmm} spills past 24:00")
        return f"{hh:02d}:{mm:02d}"

    start_snap = snap_down(window["start_local"])
    end_snap   = snap_up(window["end_local"])

    if "date" in window:
        from tz_map import to_utc_from_date
        start_utc = to_utc_from_date(zone, window["date"], start_snap)
        end_utc   = to_utc_from_date(zone, window["date"], end_snap)
    elif "day" in window:
        if not week_of:
            return []                # legacy row missing an anchor; skip
        start_utc = to_utc(zone, window["day"], start_snap, week_of)
        end_utc   = to_utc(zone, window["day"], end_snap,   week_of)
    else:
        return []                    # malformed row; skip silently

    if end_utc <= start_utc:
        return []
    return list(_iter_buckets(start_utc, end_utc))


# ── Finder ─────────────────────────────────────────────────────────────

def compute_availability(availability: dict, tz_data: dict,
                         week_of: str | None = None) -> dict[datetime, set[str]]:
    """
    Return {bucket_start_utc: {player_name, ...}} for every 30-min UTC
    bucket that at least one player has declared available.

    `availability` is the new week-based shape:
        {player_name: {"windows": [...], "declared_in_tz": "...", ...}}
    (the old round-based list-of-entries shape is auto-detected for
    backward compat with any lingering archive rows).

    `week_of` is only consulted for legacy day-of-week windows.
    """
    grid: dict[datetime, set[str]] = {}

    if isinstance(availability, list):
        # Legacy shape: [{player, windows, ...}, ...]
        iterable = ((e["player"], e) for e in availability)
    else:
        iterable = availability.items()

    for name, entry in iterable:
        zone = entry.get("declared_in_tz") or tz_data["players"].get(name)
        if not zone:
            continue     # will be surfaced in warnings()
        for w in entry.get("windows", []):
            try:
                buckets = buckets_for_window(zone, w, week_of)
            except (ValueError, KeyError):
                continue
            for b in buckets:
                grid.setdefault(b, set()).add(name)
    return grid


def team_readiness(availability: dict, teams: dict) -> dict:
    """
    Return {team_id: {"team_name": str, "responded": [names],
                       "missing": [names], "pct": 0..1}} for every team.
    """
    responded = set(availability.keys()) if isinstance(availability, dict) else \
                {e["player"] for e in availability}
    out = {}
    for t in teams.get("teams", []):
        roster = [r["name"] for r in t.get("roster", [])]
        r_hit = [n for n in roster if n in responded]
        r_miss = [n for n in roster if n not in responded]
        out[t["id"]] = {
            "team_name": t["name"],
            "responded": r_hit,
            "missing":   r_miss,
            "pct":       (len(r_hit) / len(roster)) if roster else 0.0,
        }
    return out


def ready_pairs(readiness: dict, threshold: float = 1.0) -> list[tuple[int, int]]:
    """
    All (team_a, team_b) pairs where BOTH teams are at or above `threshold`
    readiness. Used to decide when to auto-propose match slots.
    """
    ready = sorted(tid for tid, r in readiness.items() if r["pct"] >= threshold)
    return [(a, b) for i, a in enumerate(ready) for b in ready[i + 1:]]


def warnings(availability, rosters: dict[int, list[str]],
             tz_data: dict) -> list[str]:
    """
    Human-readable notes about who's missing data. Accepts both new
    (dict) and legacy (list) availability shapes.
    """
    out = []
    all_players = {p for names in rosters.values() for p in names}

    if isinstance(availability, dict):
        declared = set(availability.keys())
        iterable = availability.items()
    else:
        declared = {e["player"] for e in availability}
        iterable = ((e["player"], e) for e in availability)

    missing_avail = sorted(all_players - declared)
    if missing_avail:
        out.append(f"{len(missing_avail)} player(s) have not posted availability: "
                   + ", ".join(missing_avail))

    missing_tz = []
    for name, entry in iterable:
        if not entry.get("declared_in_tz") and name not in tz_data["players"]:
            missing_tz.append(name)
    if missing_tz:
        out.append(f"{len(missing_tz)} player(s) posted availability without a "
                   f"declared timezone and none in players_tz.json: "
                   + ", ".join(missing_tz))
    return out


def rank_slots(grid: dict[datetime, set[str]], rosters: dict[int, list[str]],
               top: int = 3) -> list[dict]:
    """
    Collapse the bucket grid into ranked "slots".

    A slot represents an EARLIEST bucket in a contiguous run of buckets
    where the *same set of players* was available. So a 4-hour window
    with 8 people continuously available produces ONE slot entry, tagged
    with duration_min = 240.

    Order: player-count desc, then bucket-start asc, then duration desc.
    Return the top N.
    """
    if not grid:
        return []

    # Sort buckets chronologically. Then walk forward, grouping consecutive
    # buckets that share the same available-set into one slot.
    buckets = sorted(grid.keys())
    slots = []
    cur = None
    for b in buckets:
        who = grid[b]
        if cur is None:
            cur = {"start_utc": b, "end_utc": b + timedelta(minutes=BUCKET_MIN),
                   "available": who}
            continue
        # Contiguous AND same players -> extend. Otherwise flush + start new.
        if b == cur["end_utc"] and who == cur["available"]:
            cur["end_utc"] = b + timedelta(minutes=BUCKET_MIN)
        else:
            slots.append(cur)
            cur = {"start_utc": b, "end_utc": b + timedelta(minutes=BUCKET_MIN),
                   "available": who}
    if cur is not None:
        slots.append(cur)

    # Annotate each slot with per-team available counts.
    for s in slots:
        s["duration_min"] = int((s["end_utc"] - s["start_utc"]).total_seconds() / 60)
        s["per_team"] = {tid: sum(1 for p in names if p in s["available"])
                          for tid, names in rosters.items()}
        s["total"]    = sum(s["per_team"].values())
        s["missing"]  = {tid: sorted(set(names) - s["available"])
                          for tid, names in rosters.items()}

    # Rank: prefer slots where BOTH teams have at least 3 available (a
    # game is playable with a stand-in filling in). Within that, sort by
    # total, earliest start, longest duration.
    def playable(s):
        return all(n >= 3 for n in s["per_team"].values())

    slots.sort(key=lambda s: (
        not playable(s),                 # playable first
        -s["total"],                     # then most-available
        s["start_utc"],                  # then earliest
        -s["duration_min"],              # then longest
    ))
    return slots[:top]


# ── Rendering ──────────────────────────────────────────────────────────

def render_slot_text(idx: int, slot: dict, teams: dict, rosters: dict) -> str:
    """One slot -> a multiline plain-text block."""
    tids = sorted(rosters.keys())
    team_names = {t["id"]: t["name"] for t in teams["teams"]}
    hdr = (f"[{idx}] {slot['start_utc'].strftime('%a %d %b · %H:%M UTC')}  "
           f"(window: {slot['duration_min']} min)  "
           f"{slot['total']}/{sum(len(n) for n in rosters.values())} available")

    tz_lines = []
    for label, iana in DISPLAY_ZONES:
        local = slot["start_utc"].astimezone(ZoneInfo(iana))
        tz_lines.append(f"    {label:<8}  {local.strftime('%a %H:%M')}")

    team_lines = []
    for tid in tids:
        team_lines.append(
            f"    {team_names[tid]:<8}  {slot['per_team'][tid]}/{len(rosters[tid])} "
            + (f" missing: {', '.join(slot['missing'][tid])}"
               if slot['missing'][tid] else ""))

    return "\n".join([hdr, "  Times:"] + tz_lines + ["  Teams:"] + team_lines)


def render_report(round_info: dict, teams: dict, rosters: dict,
                  slots: list[dict], warns: list[str]) -> str:
    team_names = {t["id"]: t["name"] for t in teams["teams"]}
    mu = round_info["match_up"]
    header = (f"Round {round_info['round_id']}: "
              f"{team_names[mu[0]]} vs {team_names[mu[1]]}  "
              f"(week of {round_info.get('week_of', 'unspecified')})")

    if not slots:
        body = "No overlapping windows yet."
    else:
        body = "\n\n".join(render_slot_text(i + 1, s, teams, rosters)
                            for i, s in enumerate(slots))

    footer = ""
    if warns:
        footer = "\n\nNotes:\n" + "\n".join(f"  - {w}" for w in warns)

    return header + "\n\n" + body + footer


def slots_as_json(match_up, week_of, slots, rosters, round_id: str | None = None):
    """
    Machine-readable version of the slot list. `match_up` and `week_of`
    are passed directly (no longer read from a round object).
    """
    payload = {
        "match_up":     match_up,
        "week_of":      week_of,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "slots": [{
            "start_utc":    s["start_utc"].isoformat(),
            "end_utc":      s["end_utc"].isoformat(),
            "duration_min": s["duration_min"],
            "total":        s["total"],
            "per_team":     s["per_team"],
            "missing":      s["missing"],
            "renderings": [
                {"zone_label": label,
                 "iana": iana,
                 "local": s["start_utc"].astimezone(ZoneInfo(iana)).isoformat()}
                for label, iana in DISPLAY_ZONES
            ],
        } for s in slots]
    }
    if round_id:
        payload["round_id"] = round_id
    return payload


# ── CLI ────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    p.add_argument("--round", help="round_id (default: first open round)")
    p.add_argument("--top", type=int, default=3, help="top N slots to return (default 3)")
    p.add_argument("--format", choices=("text", "discord", "json"), default="text")
    args = p.parse_args()

    sched, teams, tz_data = load_state()
    round_info = find_round(sched, args.round)
    rosters    = team_rosters(teams, round_info["match_up"])
    grid       = compute_availability(round_info, tz_data)
    slots      = rank_slots(grid, rosters, top=args.top)
    warns      = warnings(round_info, rosters, tz_data)

    if args.format == "json":
        print(json.dumps(slots_as_json(round_info, slots, rosters),
                         indent=2, default=str))
    else:
        print(render_report(round_info, teams, rosters, slots, warns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
