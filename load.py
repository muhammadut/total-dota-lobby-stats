"""
Build / refresh dota_stats.db from data/matches.json.

Idempotent: every match carries a `source_ref`, so re-running this after
adding a match to the JSON updates in place instead of duplicating.

    python load.py
"""

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "dota_stats.db"
SCHEMA = ROOT / "schema.sql"
DATA = ROOT / "data" / "matches.json"

# Trailing clan tag as Dota renders it: "HURR [PK_]" -> ("HURR", "PK_")
CLAN_RE = re.compile(r"^(.*?)\s*\[([^\[\]]+)\]\s*$")


def split_name(display_name: str):
    m = CLAN_RE.match(display_name)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return display_name.strip(), None


def get_player_id(cur, display_name: str) -> int:
    base, clan = split_name(display_name)
    cur.execute("SELECT id FROM players WHERE display_name = ?", (display_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO players (display_name, base_name, clan_tag) VALUES (?, ?, ?)",
        (display_name, base, clan),
    )
    return cur.lastrowid


def validate(match: dict) -> list[str]:
    """Sanity checks that catch transcription slips before they reach the DB."""
    problems = []
    ref = match["source_ref"]
    players = match["players"]

    if len(players) != 10:
        problems.append(f"{ref}: expected 10 players, found {len(players)}")

    # The real invariant, verified across every match transcribed so far:
    #
    #     sum(team kills)  <=  team score  <=  sum(enemy deaths)
    #
    # Left gap : a hero killed by a TOWER credits the owning team's score
    #            but no player gets the kill.
    # Right gap: a hero killed by neutrals / Roshan / itself is a death that
    #            credits nobody, so it never reaches any team's score.
    # Usually all three are equal. A violation of the ORDERING, however, is
    # arithmetically impossible and means a digit was transcribed wrong.
    other = {"radiant": "dire", "dire": "radiant"}
    tally = {}
    for side in ("radiant", "dire"):
        team = [p for p in players if p["side"] == side]
        if len(team) != 5:
            problems.append(f"{ref}: {side} has {len(team)} players, expected 5")
        tally[side] = {
            "kills": sum(p["kills"] for p in team),
            "deaths": sum(p["deaths"] for p in team),
            "score": match.get(f"{side}_score"),
        }

    for side in ("radiant", "dire"):
        t, e = tally[side], tally[other[side]]
        if t["score"] is None:
            continue
        broken = False
        if t["kills"] > t["score"]:
            broken = True
            problems.append(
                f"{ref}: ERROR {side} player kills ({t['kills']}) EXCEED the team "
                f"score ({t['score']}) -- impossible, a kill digit is wrong")
        if t["score"] > e["deaths"]:
            broken = True
            problems.append(
                f"{ref}: ERROR {side} score ({t['score']}) EXCEEDS {other[side]} "
                f"deaths ({e['deaths']}) -- impossible, a death digit is wrong")
        # Only describe the gaps once the ordering is known to hold. On broken
        # input the arithmetic yields negatives and the note would cheerfully
        # report "-40 tower-credited kills ... Legal." next to its own error.
        if broken:
            continue
        gap_l, gap_r = t["score"] - t["kills"], e["deaths"] - t["score"]
        if gap_l or gap_r:
            problems.append(
                f"{ref}: note -- {side} kills {t['kills']} <= score {t['score']} "
                f"<= {other[side]} deaths {e['deaths']}; {gap_l} tower-credited "
                f"kill(s), {gap_r} death(s) to neutrals/Roshan/self. Legal.")

    if match["winning_side"] not in ("radiant", "dire"):
        problems.append(f"{ref}: winning_side must be 'radiant' or 'dire'")

    names = [p["name"] for p in players]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        problems.append(f"{ref}: duplicate player names in one match: {dupes}")

    return problems


def migrate(cur) -> None:
    """
    Add columns introduced after a database was first created. schema.sql uses
    CREATE TABLE IF NOT EXISTS, which is a no-op on an existing table, so new
    columns need an explicit ALTER or they would silently never appear.
    """
    have = {r[1] for r in cur.execute("PRAGMA table_info(match_players)")}
    for col, typ in (("bounty_runes", "INTEGER"),
                     ("outposts", "INTEGER"),
                     ("damage_received_raw", "INTEGER"),
                     ("damage_reduced_pct", "REAL")):
        if col not in have:
            cur.execute(f"ALTER TABLE match_players ADD COLUMN {col} {typ}")
            print(f"  [migrate] added match_players.{col}")


def fingerprint(match: dict) -> str:
    """
    Content hash of the roster + K/D/A. Two uploads of the SAME game produce
    the same fingerprint even under different source_refs -- which matters
    now that SCOREBOARD-tab captures arrive without a match id to dedupe on.
    """
    rows = sorted(f"{p['name']}|{p['kills']}|{p['deaths']}|{p['assists']}"
                  for p in match["players"])
    return hashlib.sha1("||".join(rows).encode("utf-8")).hexdigest()[:12]


def apply_aliases(cur, aliases: list[dict]) -> None:
    """
    Point each alias row at its canonical player. The JSON is authoritative,
    so every pointer is cleared first -- deleting a line from the file and
    re-running is all it takes to undo a merge.
    """
    cur.execute("UPDATE players SET merged_into = NULL")
    if not aliases:
        return

    ids = dict(cur.execute("SELECT display_name, id FROM players").fetchall())
    canon_names = {a["canonical"] for a in aliases}
    applied = 0
    claimed: set = set()          # alias names already pointed somewhere
    # canonical id -> every player id folded into it so far, itself included.
    groups: dict = {}

    for a in aliases:
        c_name, a_name = a["canonical"], a["alias"]
        missing = [n for n in (c_name, a_name) if n not in ids]
        if missing:
            print(f"  [alias] SKIP {c_name!r} <- {a_name!r}: "
                  f"no player named {missing[0]!r} in the data")
            continue

        # A merge chain (A -> B -> C) would break the single-level
        # resolution in v_player, silently dropping a player's games.
        if a_name in canon_names:
            print(f"  [alias] REFUSED {c_name!r} <- {a_name!r}: "
                  f"{a_name!r} is itself a canonical name (merge chain)")
            continue

        # The same alias pointed at two canonicals: last write would win
        # silently, revoking an earlier confirmed merge and moving both
        # players' records.
        if a_name in claimed:
            print(f"  [alias] REFUSED {c_name!r} <- {a_name!r}: "
                  f"{a_name!r} is already merged elsewhere -- remove one row")
            continue

        cid, aid = ids[c_name], ids[a_name]
        group = groups.setdefault(cid, {cid})

        # TRANSITIVE co-occurrence. Checking only (canonical, alias) is not
        # enough: with C<-A already applied, C<-B passes that test even when
        # A and B played each other, and C then holds two rows in that match
        # -- a phantom game and a phantom win, permanently, in silence.
        marks = ",".join("?" * len(group))
        clash = cur.execute(f"""
            SELECT COUNT(*) FROM match_players x
            JOIN match_players y ON x.match_id = y.match_id
            WHERE x.player_id = ? AND y.player_id IN ({marks})
        """, (aid, *group)).fetchone()[0]
        if clash:
            print(f"  [alias] REFUSED {c_name!r} <- {a_name!r}: {a_name!r} shares "
                  f"{clash} match(es) with {c_name!r} or someone already merged "
                  f"into it -- they cannot be one person")
            continue

        cur.execute("UPDATE players SET merged_into = ? WHERE id = ?", (cid, aid))
        group.add(aid)
        claimed.add(a_name)
        applied += 1

    print(f"  [alias] {applied}/{len(aliases)} merges applied")


def main() -> int:
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    matches = payload["matches"]

    problems = [p for m in matches for p in validate(m)]

    # Same game uploaded twice under different source_refs?
    seen: dict[str, str] = {}
    for m in matches:
        fp = fingerprint(m)
        if fp in seen:
            problems.append(
                f"{m['source_ref']}: ERROR identical roster and K/D/A to "
                f"{seen[fp]!r} -- almost certainly the same match uploaded "
                f"twice. Remove one, or confirm they really are two games.")
        seen[fp] = m["source_ref"]

    # source_ref is the upsert key, so two entries sharing one silently
    # collapse into a single row and a match disappears without a word.
    refs: dict[str, int] = {}
    for m in matches:
        refs[m["source_ref"]] = refs.get(m["source_ref"], 0) + 1
    for ref, n in refs.items():
        if n > 1:
            problems.append(f"{ref}: ERROR appears {n} times in matches.json -- "
                            f"source_ref must be unique or a match is lost.")

    for p in problems:
        print(f"  [check] {p}")

    # Refuse to build from data known to be broken. This used to print the
    # error and load anyway, so the one guard against counting a match twice
    # could not actually stop anything -- and the caller, which keys on the
    # exit code, was told everything went fine.
    fatal = [p for p in problems if "ERROR" in p]
    if fatal:
        print(f"\n  REFUSED — {len(fatal)} fatal problem(s); the database was "
              f"not rebuilt. Fix data/matches.json and re-run.")
        return 1

    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    cur = con.cursor()
    migrate(cur)

    # Matches deleted from the JSON must disappear from the database too.
    # Without this the loader only ever upserts, so removing a duplicate
    # from matches.json is a no-op against an existing DB and the phantom
    # win survives every rebuild -- while a fresh clone reports a different
    # number. The published site and the owner's local answer then disagree.
    keep = [m["source_ref"] for m in matches]
    marks = ",".join("?" * len(keep)) or "''"
    cur.execute(f"DELETE FROM matches WHERE source_ref NOT IN ({marks})", keep)
    if cur.rowcount:
        print(f"  [clean] dropped {cur.rowcount} match(es) no longer in matches.json")

    for m in matches:
        cur.execute(
            """
            INSERT INTO matches (source_ref, dota_match_id, played_on, played_at,
                                 duration_seconds, game_mode, radiant_team_name,
                                 dire_team_name, radiant_score, dire_score,
                                 winning_side, result_confidence, notes)
            VALUES (:source_ref, :dota_match_id, :played_on, :played_at,
                    :duration_seconds, :game_mode, :radiant_team_name,
                    :dire_team_name, :radiant_score, :dire_score,
                    :winning_side, :result_confidence, :notes)
            ON CONFLICT(source_ref) DO UPDATE SET
                dota_match_id     = excluded.dota_match_id,
                played_on         = excluded.played_on,
                played_at         = excluded.played_at,
                duration_seconds  = excluded.duration_seconds,
                game_mode         = excluded.game_mode,
                radiant_team_name = excluded.radiant_team_name,
                dire_team_name    = excluded.dire_team_name,
                radiant_score     = excluded.radiant_score,
                dire_score        = excluded.dire_score,
                winning_side      = excluded.winning_side,
                result_confidence = excluded.result_confidence,
                notes             = excluded.notes
            """,
            {k: m.get(k) for k in (
                "source_ref", "dota_match_id", "played_on", "played_at",
                "duration_seconds", "game_mode", "radiant_team_name",
                "dire_team_name", "radiant_score", "dire_score",
                "winning_side", "result_confidence", "notes")},
        )

        cur.execute("SELECT id FROM matches WHERE source_ref = ?", (m["source_ref"],))
        match_id = cur.fetchone()[0]

        # Rewrite the roster wholesale so edits to the JSON always win.
        cur.execute("DELETE FROM match_players WHERE match_id = ?", (match_id,))
        for p in m["players"]:
            cur.execute(
                """
                INSERT INTO match_players
                    (match_id, player_id, side, hero, hero_level,
                     kills, deaths, assists, net_worth, last_hits, denies,
                     gpm, xpm, hero_damage, building_damage, healing,
                     bounty_runes, outposts, damage_received_raw,
                     damage_reduced_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (match_id, get_player_id(cur, p["name"]), p["side"],
                 p.get("hero"), p.get("hero_level"),
                 p["kills"], p["deaths"], p["assists"], p.get("net_worth"),
                 p.get("last_hits"), p.get("denies"), p.get("gpm"), p.get("xpm"),
                 p.get("hero_damage"), p.get("building_damage"), p.get("healing"),
                 p.get("bounty_runes"), p.get("outposts"),
                 p.get("damage_received_raw"), p.get("damage_reduced_pct")),
            )

    # A corrected name in the JSON leaves the old spelling behind as a player
    # row with zero appearances. Harmless in reports (the views are driven by
    # match_players) but it pollutes the alias detector, so sweep it up.
    cur.execute("""
        DELETE FROM players WHERE id NOT IN (SELECT player_id FROM match_players)
          AND id NOT IN (SELECT merged_into FROM players WHERE merged_into IS NOT NULL)
    """)
    if cur.rowcount:
        print(f"  [clean] removed {cur.rowcount} player row(s) with no appearances")

    apply_aliases(cur, payload.get("aliases", []))
    con.commit()

    n_m = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    n_p = cur.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    n_r = cur.execute("SELECT COUNT(*) FROM match_players").fetchone()[0]
    con.close()

    print(f"\n  {DB_PATH.name}: {n_m} matches, {n_p} players, {n_r} player-appearances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
