"""
Export dota_stats.db to docs/data.js for the static dashboard.

Written as a JS assignment rather than .json so the page works from file://
as well as from GitHub Pages -- no fetch, no CORS, no local web server.

    python export_web.py
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "dota_stats.db"
OUT = ROOT / "docs" / "data.js"


def rows(cur, sql, args=()):
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    if not DB.exists():
        sys.exit("dota_stats.db not found -- run `python load.py` first.")
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # Match ids are globally sequential, so they are the authoritative
    # chronology. The one match with no id sorts last.
    matches = rows(cur, """
        SELECT id, dota_match_id, played_on, played_at, duration_seconds,
               game_mode, radiant_team_name, dire_team_name,
               radiant_score, dire_score, winning_side, result_confidence, notes
        FROM matches
        ORDER BY COALESCE(CAST(dota_match_id AS INTEGER), 9999999999)
    """)

    parts = rows(cur, """
        SELECT mp.match_id, vp.player_name, mp.side, mp.hero, mp.hero_level,
               mp.kills, mp.deaths, mp.assists, mp.net_worth, mp.last_hits,
               mp.denies, mp.gpm, mp.xpm, mp.hero_damage, mp.building_damage,
               mp.healing, mp.bounty_runes, mp.outposts, mp.damage_received_raw,
               mp.damage_reduced_pct,
               CASE WHEN mp.side = m.winning_side THEN 1 ELSE 0 END AS won
        FROM match_players mp
        JOIN matches m ON m.id = mp.match_id
        JOIN v_player vp ON vp.raw_id = mp.player_id
    """)

    by_match = {}
    for p in parts:
        by_match.setdefault(p["match_id"], []).append(p)

    for i, m in enumerate(matches):
        roster = sorted(by_match.get(m["id"], []),
                        key=lambda r: -(r["net_worth"] or 0))
        m["radiant"] = [r for r in roster if r["side"] == "radiant"]
        m["dire"] = [r for r in roster if r["side"] == "dire"]
        m["seq"] = i + 1
        for r in roster:
            r.pop("match_id", None)
            r.pop("side", None)

    players = rows(cur, """
        SELECT player_name AS name, games, wins, losses, win_pct,
               kills, deaths, assists, kda
        FROM v_player_record
        ORDER BY games DESC, win_pct DESC, player_name
    """)

    # Per-player extras the headline view doesn't carry.
    extra = {r["player_name"]: r for r in rows(cur, """
        SELECT vp.player_name,
               ROUND(AVG(mp.gpm))       AS avg_gpm,
               ROUND(AVG(mp.xpm))       AS avg_xpm,
               ROUND(AVG(mp.net_worth)) AS avg_net,
               MAX(mp.net_worth)        AS best_net,
               SUM(mp.last_hits)        AS last_hits,
               SUM(mp.hero_damage)      AS hero_damage,
               SUM(mp.healing)          AS healing
        FROM match_players mp JOIN v_player vp ON vp.raw_id = mp.player_id
        GROUP BY vp.player_name
    """)}

    hist = {}
    for m in matches:
        for r in m["radiant"] + m["dire"]:
            hist.setdefault(r["player_name"], []).append({
                "seq": m["seq"], "dotaId": m["dota_match_id"],
                "played": m["played_at"] or m["played_on"],
                "won": r["won"], "hero": r["hero"],
                "k": r["kills"], "d": r["deaths"], "a": r["assists"],
                "net": r["net_worth"], "gpm": r["gpm"],
            })

    for p in players:
        e = extra.get(p["name"], {})
        p.update({k: e.get(k) for k in
                  ("avg_gpm", "avg_xpm", "avg_net", "best_net",
                   "last_hits", "hero_damage", "healing")})
        p["history"] = hist.get(p["name"], [])
        picks = {}
        for h in p["history"]:
            d = picks.setdefault(h["hero"], {"hero": h["hero"], "picks": 0, "wins": 0})
            d["picks"] += 1
            d["wins"] += h["won"]
        p["heroes"] = sorted(picks.values(), key=lambda d: (-d["picks"], d["hero"]))

    heroes = rows(cur, """
        SELECT hero, COUNT(*) AS picks, SUM(won) AS wins,
               COUNT(*) - SUM(won) AS losses,
               SUM(kills) AS kills, SUM(deaths) AS deaths, SUM(assists) AS assists,
               GROUP_CONCAT(DISTINCT player_name) AS players
        FROM v_results WHERE hero IS NOT NULL
        GROUP BY hero ORDER BY picks DESC, wins DESC, hero
    """)
    for h in heroes:
        h["players"] = (h["players"] or "").split(",")

    aliases = rows(cur, """
        SELECT p.display_name AS alias, c.display_name AS canonical
        FROM players p JOIN players c ON c.id = p.merged_into
    """)

    stat = cur.execute("""
        SELECT COUNT(*), SUM(kills), SUM(deaths), SUM(assists), SUM(net_worth)
        FROM match_players
    """).fetchone()
    longest = cur.execute("""
        SELECT dota_match_id, duration_seconds FROM matches
        WHERE duration_seconds IS NOT NULL
        ORDER BY duration_seconds DESC LIMIT 1
    """).fetchone()

    payload = {
        "meta": {
            "matches": len(matches),
            "players": len(players),
            "appearances": stat[0],
            "totalKills": stat[1],
            "totalNetWorth": stat[4],
            "sessionFrom": min(m["played_on"] for m in matches),
            "sessionTo": max(m["played_on"] for m in matches),
            "longestMatch": {"id": longest[0], "seconds": longest[1]},
            "aliases": aliases,
        },
        "players": players,
        "matches": matches,
        "heroes": heroes,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(
        "/* Generated by export_web.py -- do not edit by hand. */\n"
        "window.LOBBY = " + body + ";\n", encoding="utf-8")

    kb = OUT.stat().st_size / 1024
    print(f"  wrote {OUT.relative_to(ROOT)}  ({kb:.1f} KB)")
    print(f"  {len(matches)} matches | {len(players)} players | "
          f"{len(heroes)} heroes | {stat[0]} appearances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
