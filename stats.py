"""
Report on dota_stats.db.

    python stats.py              # win/loss record for every player
    python stats.py log          # match log, newest first
    python stats.py player UT    # one player's match-by-match history
    python stats.py aliases      # suspected duplicate identities
"""

import sqlite3
import sys
from pathlib import Path
from unicodedata import east_asian_width

DB_PATH = Path(__file__).parent / "dota_stats.db"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def dwidth(s: str) -> int:
    """Display width in terminal columns. CJK glyphs occupy two columns each,
    so len() under-counts them and knocks every later column out of line."""
    return sum(2 if east_asian_width(c) in ("W", "F") else 1 for c in s)


def pad(s: str, width: int, align: str) -> str:
    gap = " " * max(0, width - dwidth(s))
    if align == ">":
        return gap + s
    if align == "^":
        left = len(gap) // 2
        return " " * left + s + " " * (len(gap) - left)
    return s + gap


def table(rows, headers, aligns=None):
    """Minimal fixed-width table renderer -- no third-party dependency."""
    if not rows:
        print("  (no rows)")
        return
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(dwidth(h), *(dwidth(r[i]) for r in cells))
              for i, h in enumerate(headers)]
    aligns = aligns or ["<"] * len(headers)

    print("  " + "  ".join(pad(h, w, a) for h, a, w in zip(headers, aligns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in cells:
        print("  " + "  ".join(pad(v, w, a) for v, a, w in zip(r, aligns, widths)))


def connect():
    if not DB_PATH.exists():
        sys.exit("dota_stats.db not found -- run `python load.py` first.")
    return sqlite3.connect(DB_PATH)


def cmd_record(con, order="games"):
    # NOTE: the NULL test must wrap printf, not the other way round --
    # SQLite's printf('%.2f', NULL) yields the string '0.00', so COALESCE
    # around it never fires and "undefined" silently becomes "zero".
    rows = con.execute("""
        SELECT player_name, games, wins, losses,
               printf('%.1f%%', win_pct),
               CASE WHEN wl_ratio IS NULL THEN 'inf'
                    ELSE printf('%.2f', wl_ratio) END,
               kills || '/' || deaths || '/' || assists,
               CASE WHEN kda IS NULL THEN 'inf'
                    ELSE printf('%.2f', kda) END
        FROM v_player_record
        ORDER BY """ + ("games DESC, win_pct DESC, player_name"
                        if order == "games" else
                        "win_pct DESC, games DESC, player_name") + """
    """).fetchall()
    print("\nWIN / LOSS RECORD\n")
    table(rows,
          ["PLAYER", "GP", "W", "L", "WIN%", "W/L", "K/D/A", "KDA"],
          ["<", ">", ">", ">", ">", ">", ">", ">"])
    print("\n  'inf' = undefined: W/L for a player who has never lost,")
    print("          KDA for a player who has never died.")
    if order == "games":
        print("  Sorted by games played -- the rows with the most evidence first.")
        print("  Use `python stats.py record winrate` to rank by win rate instead.")
    else:
        print("  Ranked by win rate -- treat 1-2 game records as noise, not skill.")


def cmd_log(con):
    rows = con.execute("""
        SELECT COALESCE(dota_match_id, '(no id)'), played,
               COALESCE(game_mode, '?'), COALESCE(duration, '?'),
               radiant, radiant_score || ' - ' || dire_score, dire,
               UPPER(winning_side),
               CASE result_confidence WHEN 'inferred' THEN '~' ELSE '' END
        FROM v_match_log
    """).fetchall()
    print("\nMATCH LOG\n")
    table(rows,
          ["MATCH ID", "PLAYED", "MODE", "DUR", "RADIANT", "SCORE", "DIRE", "WON", "?"],
          ["<", "<", "<", ">", "<", "^", "<", "<", "<"])
    print("\n  ? column: '~' = winner inferred, not stated outright by the game.")
    check_chronology(con)


def check_chronology(con):
    """
    Dota match ids are globally sequential, so ordering by id is authoritative.
    Timestamps are NOT comparable across clients -- they are whatever local
    clock and timezone that machine was set to. Where the two orderings
    disagree, the timestamp is the thing to distrust.
    """
    rows = con.execute("""
        SELECT dota_match_id, COALESCE(played_at, played_on)
        FROM matches WHERE dota_match_id IS NOT NULL AND played_at IS NOT NULL
    """).fetchall()
    if len(rows) < 2:
        return

    by_id = [r[0] for r in sorted(rows, key=lambda r: int(r[0]))]
    by_time = [r[0] for r in sorted(rows, key=lambda r: r[1])]
    if by_id == by_time:
        return

    print("\n  ! CHRONOLOGY CONFLICT -- match id order disagrees with timestamp order.")
    print("    Match ids are globally sequential, so this order is the true one:")
    for mid in by_id:
        stamp = next(r[1] for r in rows if r[0] == mid)
        flag = "  <-- out of place by timestamp" if by_time.index(mid) != by_id.index(mid) else ""
        print(f"      {mid}  reads {stamp}{flag}")
    print("    Cause: timestamps came from different players' clients"
          " (differing locale/timezone).")


def cmd_player(con, name):
    rows = con.execute("""
        SELECT played, COALESCE(dota_match_id, '(no id)'), side, result,
               COALESCE(hero, '-'), kda, net_worth
        FROM v_player_match_log
        WHERE player_name LIKE ?
        ORDER BY played
    """, (f"%{name}%",)).fetchall()
    if not rows:
        sys.exit(f"No player matching '{name}'.")

    summary = con.execute("""
        SELECT player_name, games, wins, losses, printf('%.1f%%', win_pct)
        FROM v_player_record WHERE player_name LIKE ?
    """, (f"%{name}%",)).fetchall()

    for s in summary:
        print(f"\n{s[0]}  --  {s[1]} games, {s[2]}W {s[3]}L, {s[4]} win rate\n")
    table(rows,
          ["PLAYED", "MATCH ID", "SIDE", "RESULT", "HERO", "K/D/A", "NET WORTH"],
          ["<", "<", "<", "<", "<", ">", ">"])


def cmd_heroes(con, who=None):
    """Hero pick counts and win rates. Optionally scoped to one player."""
    where, args = "", []
    if who:
        where, args = "WHERE player_name LIKE ?", [f"%{who}%"]
    rows = con.execute(f"""
        SELECT hero, COUNT(*) AS picks, SUM(won) AS wins,
               COUNT(*) - SUM(won) AS losses,
               printf('%.0f%%', 100.0 * SUM(won) / COUNT(*)),
               SUM(kills) || '/' || SUM(deaths) || '/' || SUM(assists),
               GROUP_CONCAT(DISTINCT player_name)
        FROM v_results {where}
        GROUP BY hero ORDER BY picks DESC, wins DESC, hero
    """, args).fetchall()
    title = f"HERO REPORT -- {who}" if who else "HERO REPORT -- all players"
    print(f"\n{title}\n")
    table([r for r in rows if r[1] > 1] or rows,
          ["HERO", "PICKS", "W", "L", "WIN%", "K/D/A", "PLAYED BY"],
          ["<", ">", ">", ">", ">", ">", "<"])
    once = [r[0] for r in rows if r[1] == 1]
    if once and not who:
        print(f"\n  picked once ({len(once)}): " + ", ".join(sorted(once)))


def cmd_match(con, key):
    """Full scoreboard for one match. Columns the source didn't have show '-'."""
    m = con.execute("""
        SELECT id, COALESCE(dota_match_id,'(no id)'), COALESCE(played_at, played_on,'?'),
               COALESCE(game_mode,'?'), duration_seconds, winning_side,
               COALESCE(radiant_team_name,'Radiant'), radiant_score,
               dire_score, COALESCE(dire_team_name,'Dire')
        FROM matches WHERE dota_match_id = ? OR source_ref = ? OR id = ?
    """, (key, key, key if key.isdigit() else -1)).fetchone()
    if not m:
        sys.exit(f"No match matching '{key}'. Try `python stats.py log`.")

    dur = f"{m[4]//60}:{m[4]%60:02d}" if m[4] else "?"
    print(f"\nMatch {m[1]}  --  {m[2]}  --  {m[3]}  --  {dur}")
    print(f"{m[6]} {m[7]} - {m[8]} {m[9]}   ->  {m[5].upper()} WIN\n")

    for side in ("radiant", "dire"):
        rows = con.execute("""
            SELECT vp.player_name, COALESCE(mp.hero,'-'),
                   COALESCE(mp.hero_level,'-'),
                   mp.kills || '/' || mp.deaths || '/' || mp.assists,
                   COALESCE(mp.net_worth,'-'),
                   CASE WHEN mp.last_hits IS NULL THEN '-'
                        ELSE mp.last_hits || '/' || COALESCE(mp.denies,0) END,
                   COALESCE(mp.gpm,'-'), COALESCE(mp.xpm,'-'),
                   COALESCE(mp.hero_damage,'-'), COALESCE(mp.building_damage,'-'),
                   COALESCE(mp.healing,'-'), COALESCE(mp.bounty_runes,'-'),
                   COALESCE(mp.outposts,'-'), COALESCE(mp.damage_received_raw,'-'),
                   CASE WHEN mp.damage_reduced_pct IS NULL THEN '-'
                        ELSE printf('%.1f%%', mp.damage_reduced_pct) END
            FROM match_players mp JOIN v_player vp ON vp.raw_id = mp.player_id
            WHERE mp.match_id = ? AND mp.side = ?
            ORDER BY mp.net_worth DESC
        """, (m[0], side)).fetchall()
        tag = "WINNER" if side == m[5] else "loser "
        print(f"  {side.upper()}  [{tag}]")
        table(rows,
              ["PLAYER", "HERO", "LVL", "K/D/A", "NET", "LH/DN", "GPM", "XPM",
               "HERO DMG", "BLDG", "HEAL", "BR", "OUT", "DMG TAKEN", "RED%"],
              ["<", "<", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">"])
        print()
    print("  '-' = the source screen did not expose that column"
          " (OVERVIEW tab shows only name / net worth / K-D-A).")


def cmd_aliases(con):
    """
    Flag names that may be the same human. Deliberately does NOT merge
    anything -- a wrong merge silently corrupts every win rate downstream.
    """
    import difflib

    players = con.execute(
        "SELECT id, display_name, base_name, clan_tag FROM players"
    ).fetchall()

    # Two players who appeared in the SAME match cannot be the same person.
    together = set()
    for a, b in con.execute("""
        SELECT x.player_id, y.player_id
        FROM match_players x JOIN match_players y
          ON x.match_id = y.match_id AND x.player_id < y.player_id
    """):
        together.add((a, b))

    hits = []
    for i, p in enumerate(players):
        for q in players[i + 1:]:
            if (min(p[0], q[0]), max(p[0], q[0])) in together:
                continue
            a, b = p[2].lower(), q[2].lower()
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            same_clan = p[3] and q[3] and p[3] == q[3]

            # Dota players habitually bolt suffixes onto their name --
            # "Stoic" becomes "Stoic (Mode: Sccc)(only solo)". A similarity
            # ratio scores that pair at ~29% because it is dominated by
            # length difference, so prefix containment needs its own signal.
            short, long = sorted((a, b), key=len)
            prefix = len(short) >= 3 and long.startswith(short)

            if prefix:
                why = "one name is a prefix of the other"
            elif same_clan and ratio > 0.3:
                why = "same clan tag"
            elif ratio > 0.75:
                why = "similar name"
            else:
                continue
            hits.append([p[1], q[1], f"{ratio:.0%}", why])

    hits.sort(key=lambda h: h[3])

    print("\nSUSPECTED DUPLICATE IDENTITIES\n")
    table(hits, ["NAME A", "NAME B", "SIMILARITY", "SIGNAL"])
    print("\n  Nothing is merged automatically. To confirm a merge, run:")
    print("    UPDATE players SET merged_into = (SELECT id FROM players")
    print("      WHERE display_name = '<canonical>') WHERE display_name = '<alias>';")
    print("  Players seen in the same match are excluded -- they cannot be one person.")


def main():
    con = connect()
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "record"

    if cmd == "record":
        cmd_record(con, argv[1] if len(argv) > 1 else "games")
    elif cmd == "log":
        cmd_log(con)
    elif cmd == "player":
        if len(argv) < 2:
            sys.exit("usage: python stats.py player <name>")
        cmd_player(con, " ".join(argv[1:]))
    elif cmd == "heroes":
        cmd_heroes(con, " ".join(argv[1:]) if len(argv) > 1 else None)
    elif cmd == "match":
        if len(argv) < 2:
            sys.exit("usage: python stats.py match <match id | source_ref>")
        cmd_match(con, argv[1])
    elif cmd == "aliases":
        cmd_aliases(con)
    else:
        sys.exit(__doc__)
    print()
    con.close()


if __name__ == "__main__":
    main()
