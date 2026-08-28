"""
Export dota_stats.db to docs/data.js for the static dashboard.

Exports MATCHES ONLY — standings and hero records are aggregated in the
browser. That is deliberate: the year filter has to recompute every
average and win rate from scratch, so shipping pre-aggregated totals
would mean shipping one set per year and keeping them in sync.

Written as a JS assignment rather than .json so the page works from
file:// as well as GitHub Pages — no fetch, no CORS, no local server.

    python export_web.py
"""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "dota_stats.db"
OUT = ROOT / "docs" / "data.js"
TEAMS = ROOT / "data" / "teams.json"
SCHEDULING = ROOT / "data" / "scheduling.json"
PLAYERS_TZ = ROOT / "data" / "players_tz.json"
FIXTURES = ROOT / "data" / "fixtures.json"
# Results are kept OUT of fixtures.json on purpose: make_fixtures.py
# regenerates that file from scratch and would erase the season.
SERIES_RESULTS = ROOT / "data" / "series_results.json"
# The league match ledger. Deliberately NOT part of dota_stats.db -- see
# tools/league_ingest.py. Lobby statistics are computed from the database
# and therefore cannot include a league game.
LEAGUE_MATCHES = ROOT / "data" / "league_matches.json"
# The SHORT format under consideration. Hand-authored, unlike fixtures.json:
# it holds only the pools and the best-of, and build_mini() derives every
# match, box, placing and count on the page from them.
MINI = ROOT / "data" / "mini_tournament.json"

# Path for importing tools.find_slot
sys.path.insert(0, str(ROOT / "tools"))


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
        SELECT id, source_ref, dota_match_id, played_on, played_at,
               duration_seconds,
               game_mode, radiant_team_name, dire_team_name,
               radiant_score, dire_score, winning_side, result_confidence, notes
        FROM matches
        ORDER BY COALESCE(CAST(dota_match_id AS INTEGER), 9999999999)
    """)

    parts = rows(cur, """
        SELECT mp.match_id, vp.player_name AS name, mp.side, mp.hero,
               mp.hero_level AS lvl, mp.kills AS k, mp.deaths AS d,
               mp.assists AS a, mp.net_worth AS net, mp.last_hits AS lh,
               mp.denies AS dn, mp.gpm, mp.xpm, mp.hero_damage AS hdmg,
               mp.building_damage AS bdmg, mp.healing AS heal,
               CASE WHEN mp.side = m.winning_side THEN 1 ELSE 0 END AS won
        FROM match_players mp
        JOIN matches m ON m.id = mp.match_id
        JOIN v_player vp ON vp.raw_id = mp.player_id
    """)

    by_match = {}
    for p in parts:
        by_match.setdefault(p.pop("match_id"), []).append(p)

    def strip_side(r):
        return {k: v for k, v in r.items() if k != "side"}

    for i, m in enumerate(matches):
        roster = sorted(by_match.get(m["id"], []), key=lambda r: -(r["net"] or 0))
        m["radiant"] = [strip_side(r) for r in roster if r["side"] == "radiant"]
        m["dire"]    = [strip_side(r) for r in roster if r["side"] == "dire"]
        m["seq"] = i + 1
        m["year"] = (m["played_on"] or "")[:4]
        m.pop("id", None)

    aliases = rows(cur, """
        SELECT p.display_name AS alias, c.display_name AS canonical
        FROM players p JOIN players c ON c.id = p.merged_into
    """)

    years = sorted({m["year"] for m in matches if m["year"]}, reverse=True)

    # League roster. Optional — if data/teams.json is absent, the Teams
    # tab shows an empty state rather than crashing. Comments (keys prefixed
    # with _) are stripped so they never travel to the browser.
    league = None
    if TEAMS.exists():
        raw = json.loads(TEAMS.read_text(encoding="utf-8"))
        league = {k: v for k, v in raw.items() if not k.startswith("_")}

    # Coord block: confirmed upcoming matches + open scheduling rounds with
    # their currently-best slots. The slot ranking runs HERE (Python) rather
    # than in the browser because zoneinfo does DST math correctly and the
    # browser would need heavy tzdata polyfills otherwise.
    coord = build_coord(league)
    fixtures = build_fixtures(league)
    tournament = build_tournament(aliases)
    mini = build_mini(league, fixtures)

    payload = {
        "meta": {
            "years": years,
            "aliases": aliases,
            "generated": None,   # stamped by the caller if ever needed
        },
        "matches": matches,
        "league": league,
        "coord": coord,
        "fixtures": fixtures,
        "tournament": tournament,
        "mini": mini,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "/* Generated by export_web.py -- do not edit by hand. */\n"
        "window.LOBBY = " + json.dumps(payload, ensure_ascii=False,
                                       separators=(",", ":")) + ";\n",
        encoding="utf-8")

    kb = OUT.stat().st_size / 1024
    print(f"  wrote {OUT.relative_to(ROOT)}  ({kb:.1f} KB)")
    print(f"  {len(matches)} matches | years: {', '.join(years)} | "
          f"{len(parts)} player-games | {len(aliases)} merges")

    sync_hero_slugs(cur)
    stamp_assets()
    return 0


def build_tournament(aliases: list) -> dict | None:
    """
    Emit LOBBY.tournament: the league's OWN match ledger, reshaped to the
    same {radiant:[], dire:[]} form the lobby matches use so the browser
    can draw them with the identical scoreboard component.

    This reads data/league_matches.json directly. Those rows are never in
    dota_stats.db, which is the whole guarantee: every lobby statistic is
    computed from the database, so none of them can include a league game.

    Each match is stamped with the two team ids at export time, using the
    same roster resolution the association tool uses. Doing it here rather
    than in the browser means the page never has to know about `aka`
    nicknames or the merge table.
    """
    if not LEAGUE_MATCHES.exists():
        return None
    sys.path.insert(0, str(ROOT / "tools"))
    import league_result as LR

    payload = json.loads(LEAGUE_MATCHES.read_text(encoding="utf-8"))
    teams = json.loads(TEAMS.read_text(encoding="utf-8"))
    league_aliases = payload.get("aliases", [])
    idx = LR.team_index(teams, league_aliases + aliases)
    spare = LR.stand_ins(teams, league_aliases + aliases)

    # Renames inside the league are applied HERE, on the way out, exactly
    # as load.py applies the lobby merge table on the way into the
    # database. Without it a player who renames mid-series (Beast Mode ->
    # Boostmode, same clan tag, same roster slot) silently becomes two
    # people on the leaderboard, each with a fraction of the record.
    canon = {a["alias"]: a["canonical"] for a in league_aliases}

    # Column names match the lobby export exactly -- board() in app.js is
    # shared, and a second naming scheme would mean a second component.
    FIELDS = [("hero", "hero"), ("hero_level", "lvl"), ("kills", "k"),
              ("deaths", "d"), ("assists", "a"), ("net_worth", "net"),
              ("last_hits", "lh"), ("denies", "dn"), ("gpm", "gpm"),
              ("xpm", "xpm"), ("hero_damage", "hdmg"),
              ("building_damage", "bdmg"), ("healing", "heal")]

    out, unresolved = [], []
    for i, m in enumerate(payload.get("matches", [])):
        # A recorded game carries the teams it was actually played by --
        # frozen at ingest (league_ingest.stamp_teams). Only fall back to
        # live resolution for a row written before that existed, because
        # recomputing means a transfer months later silently rewrites, or
        # deletes, a result from a night the player was on the other team.
        rad, dire = m.get("radiant_team_id"), m.get("dire_team_id")
        if rad is None or dire is None:
            rad, rad_unknown, _ = LR.side_team(m, "radiant", idx, spare)
            dire, dire_unknown, _ = LR.side_team(m, "dire", idx, spare)
            if rad is None or dire is None:
                unresolved.append((m["source_ref"],
                                   sorted(rad_unknown + dire_unknown)))

        def roster(side, tid):
            rows = []
            for p in m["players"]:
                if p["side"] != side:
                    continue
                r = {"name": canon.get(p["name"], p["name"]),
                     "won": 1 if m["winning_side"] == side else 0}
                for src, dst in FIELDS:
                    r[dst] = p.get(src)
                rows.append(r)
            return sorted(rows, key=lambda r: -(r["net"] or 0))

        e = {k: v for k, v in m.items() if k != "players"}
        e["seq"] = i + 1
        e["year"] = (m.get("played_on") or "")[:4]
        e["radiant"] = roster("radiant", rad)
        e["dire"] = roster("dire", dire)
        e["radiant_team_id"] = rad
        e["dire_team_id"] = dire
        e["winner_team_id"] = rad if m["winning_side"] == "radiant" else dire
        out.append(e)

    for ref, who in unresolved:
        print(f"  ! league match {ref} has player(s) on no roster: "
              + ", ".join(who))
        print("    Its teams cannot be identified, so it will not count "
              "towards team records.")
    if out:
        print(f"  tournament: {len(out)} league match(es) exported "
              f"(separate ledger, not in the database)")
    return {"matches": out}


def build_coord(league: dict | None) -> dict | None:
    """
    Emit LOBBY.coord: upcoming confirmed matches + open scheduling rounds
    with their currently top-ranked slots.

    Runs find_slot for each open round so the browser doesn't have to
    replicate the DST-safe UTC math. Failure of any single round to
    compute is caught and reported inline -- the tab shows what it can
    rather than blanking out.
    """
    if not SCHEDULING.exists() or not league:
        return None

    import find_slot   # imported lazily; only needed when scheduling data exists

    sched   = json.loads(SCHEDULING.read_text(encoding="utf-8"))
    teams   = json.loads(TEAMS.read_text(encoding="utf-8"))
    tz_data = (json.loads(PLAYERS_TZ.read_text(encoding="utf-8"))
               if PLAYERS_TZ.exists() else {"players": {}})

    team_names = {t["id"]: t["name"] for t in teams["teams"]}

    open_rounds_out = []
    for r in sched.get("rounds", []):
        if r.get("status") != "collecting":
            continue
        entry = {
            "round_id":     r["round_id"],
            "match_up":     r["match_up"],
            "match_up_names": [team_names.get(tid, f"Team {tid}") for tid in r["match_up"]],
            "week_of":      r.get("week_of"),
            "opened_at":    r.get("opened_at"),
            "opened_by":    r.get("opened_by"),
            "note":         r.get("note"),
            "respondents":  [e["player"] for e in r.get("availability", [])],
            "roster_size":  sum(len(t["roster"]) for t in teams["teams"] if t["id"] in r["match_up"]),
            "slots":        [],
            "warnings":     [],
        }
        try:
            rosters = find_slot.team_rosters(teams, r["match_up"])
            grid    = find_slot.compute_availability(r, tz_data)
            slots   = find_slot.rank_slots(grid, rosters, top=3)
            entry["warnings"] = find_slot.warnings(r, rosters, tz_data)
            # Serialise slots (find_slot.slots_as_json handles this)
            payload = find_slot.slots_as_json(r, slots, rosters)
            entry["slots"] = payload["slots"]
        except Exception as e:
            entry["warnings"].append(f"slot ranking failed: {e}")
        open_rounds_out.append(entry)

    return {
        "upcoming":    sched.get("upcoming", []),
        "open_rounds": open_rounds_out,
    }


# Display name -> Valve's internal hero name, where the two differ.
HERO_SPECIAL = {
    "Anti-Mage": "antimage", "Centaur Warrunner": "centaur",
    "Clockwerk": "rattletrap", "Doom": "doom_bringer", "Io": "wisp",
    "Lifestealer": "life_stealer", "Magnus": "magnataur",
    "Nature's Prophet": "furion", "Necrophos": "necrolyte",
    "Outworld Destroyer": "obsidian_destroyer", "Queen of Pain": "queenofpain",
    "Shadow Fiend": "nevermore", "Timbersaw": "shredder",
    "Treant Protector": "treant", "Underlord": "abyssal_underlord",
    "Vengeful Spirit": "vengefulspirit", "Windranger": "windrunner",
    "Wraith King": "skeleton_king", "Zeus": "zuus",
}
CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes"


# The zones the league actually spans, from the captains' roster sheet.
# Fixed rather than derived from players_tz.json, because the people in the
# most awkward zones (Malaysia especially) are exactly the ones who have not
# registered yet -- deriving it would quietly drop the worst-affected row.
# Anything anyone HAS registered gets unioned in, so it stays truthful.
LEAGUE_ZONES = [
    ("Pakistan", "Asia/Karachi"),
    ("Saudi", "Asia/Riyadh"),
    ("Sweden", "Europe/Stockholm"),
    ("UK", "Europe/London"),
    ("US East", "America/New_York"),
    ("Malaysia", "Asia/Kuala_Lumpur"),
]


def build_fixtures(league: dict | None = None) -> dict | None:
    """
    Emit LOBBY.fixtures: the season schedule with each series pre-rendered
    into every league timezone.

    The conversion happens HERE for the same reason build_coord's does --
    zoneinfo gets DST right and the browser would need a tzdata polyfill to
    match it. Times are formatted 12-hour on the way out because that is how
    the league reads them; a 24-hour string is one mental conversion away
    from a player showing up twelve hours late.
    """
    if not FIXTURES.exists():
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo

    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    results = (json.loads(SERIES_RESULTS.read_text(encoding="utf-8")).get("results", {})
               if SERIES_RESULTS.exists() else {})

    zones = list(LEAGUE_ZONES)
    if PLAYERS_TZ.exists():
        known = set(json.loads(PLAYERS_TZ.read_text(encoding="utf-8"))
                    .get("players", {}).values())
        have = {z for _, z in zones}
        for z in sorted(known - have):
            zones.append((z.split("/")[-1].replace("_", " "), z))

    def fmt(dt):
        return dt.strftime("%a %I:%M %p").replace(" 0", " ")

    played = 0
    for wk in data.get("weeks", []):
        for night in wk.get("nights", []):
            for s in night.get("series", []):
                start = datetime.fromisoformat(s["start_utc"])
                end = datetime.fromisoformat(s["end_utc"])
                s["local"] = [
                    {"label": label,
                     "window": f"{fmt(start.astimezone(ZoneInfo(z)))} - "
                               f"{fmt(end.astimezone(ZoneInfo(z)))}"}
                    for label, z in zones
                ]

                # Merge in recorded results. The schedule and the results
                # are separate files precisely so make_fixtures.py can be
                # re-run without erasing a season -- they only ever meet
                # here, on the way to the browser.
                games = sorted((results.get(s["id"], {}) or {}).get("games", []),
                               key=lambda g: g.get("game_no", 0))
                s["games"] = games
                if games and not s.get("teams"):
                    # The final's teams are decided by the results, not the
                    # schedule: whoever actually turned up. Fill them in on
                    # the way out so the browser can draw the fixture.
                    s["teams"] = sorted({g["winner"] for g in games} |
                                        {g.get("loser") for g in games
                                         if g.get("loser")})
                if games and s.get("teams") and len(s["teams"]) == 2:
                    played += 1
                    a, b = s["teams"]
                    s["score"] = [sum(1 for g in games if g["winner"] == a),
                                  sum(1 for g in games if g["winner"] == b)]
                    need = s.get("best_of", 3) // 2 + 1
                    s["status"] = ("final" if max(s["score"]) >= need
                                   else "playing")
                else:
                    s["score"] = [0, 0]

    # A result pointing at a series id the schedule no longer contains is
    # invisible on the site and would be a silently-dropped game. Say so.
    known = {s["id"] for wk in data.get("weeks", [])
             for n in wk.get("nights", []) for s in n.get("series", [])}
    orphans = sorted(set(results) - known)
    if orphans:
        print(f"  ! {len(orphans)} recorded series not in the schedule: "
              + ", ".join(orphans))
        print("    They will NOT appear on the site. Re-check make_fixtures.py "
              "or tools/league_result.py --list")
    if played:
        print(f"  fixtures: {played} series with results merged")

    data["progress"] = season_progress(data, league)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def build_mini(league: dict | None, fixtures: dict | None = None) -> dict | None:
    """
    Emit LOBBY.mini: EVERY mini tournament, oldest first, plus which one
    the tab should open on. Each is two pools of three followed by a
    four-team double-elimination playoff.

    A season that cannot be drawn is skipped with a message and the other
    seasons still render -- one bad config should not take the tab down
    with it.

    data/mini_tournament.json holds only what cannot be worked out: which
    teams are in which pool, how many advance, the best-of at each stage,
    and the tie-break ladder. Everything the page shows is derived here --
    the six pool matches, the four playoff boxes, which box feeds which,
    the final placings, the game and night counts, and the comparison
    against the full season. Same reasoning as app.js::fxCopy: prose and
    counts typed by hand have no checksum, and the schedule copy went
    false the first time the schedule was regenerated.

    Nothing on this page is a result. The mini tournament is a proposal;
    if it is ever played, its games go through tools/league_ingest.py into
    the league ledger exactly as any other league game.

    A configuration this function cannot draw is REFUSED with a message,
    never drawn approximately -- the bracket below is specific to two
    pools with two advancing from each, and a half-right bracket is worse
    than no bracket.
    """
    if not MINI.exists():
        return None
    raw = json.loads(MINI.read_text(encoding="utf-8"))

    seasons = []
    for entry in raw.get("seasons") or []:
        built = build_mini_season(entry, league, fixtures)
        if built is None:
            print(f"  ! season {entry.get('id')} was refused; the rest of the "
                  f"tab is still drawn")
            continue
        seasons.append(built)
    if not seasons:
        return None

    ids = [s["id"] for s in seasons]
    if len(set(ids)) != len(ids):
        print(f"  ! mini tournament not drawn: duplicate season ids {ids}")
        return None

    current = raw.get("current")
    if current not in ids:
        current = ids[-1]          # newest, which is what it always means
    return {"seasons": seasons, "current": current}


def build_mini_season(cfg: dict, league: dict | None,
                      fixtures: dict | None) -> dict | None:
    """
    One mini tournament. See build_mini for the contract; everything here
    is scoped to a single season, ids included -- Season 2's team 1 is
    not Season 1's team 1, and nothing crosses between them.
    """
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}

    pools_in = cfg.get("pools") or []
    advance = cfg.get("advance_per_pool", 2)

    label = cfg.get("name") or f"season {cfg.get('id')}"

    def refuse(why):
        print(f"  ! {label} not drawn: {why}")
        print("    Fix data/mini_tournament.json and re-run export_web.py.")
        return None

    if len(pools_in) != 2 or advance != 2:
        return refuse(f"the bracket is built for 2 pools with 2 advancing "
                      f"from each; got {len(pools_in)} pool(s), "
                      f"{advance} advancing")

    seen = {}
    for pool in pools_in:
        for tid in pool["teams"]:
            if tid in seen:
                return refuse(f"team {tid} is in pool {seen[tid]} and pool "
                              f"{pool['id']} at once")
            seen[tid] = pool["id"]
        if len(pool["teams"]) <= advance:
            return refuse(f"pool {pool['id']} has {len(pool['teams'])} teams "
                          f"and {advance} advance -- nothing is decided")

    bo = cfg.get("best_of") or {}
    bo_pool = bo.get("pool", 1)
    bo_play = bo.get("playoff", 3)
    bo_final = bo.get("final", bo_play)

    # Names.
    #
    # A season either names its own teams inline or resolves ids against
    # data/teams.json. Inline wins where both exist.
    #
    # `provisional` means the ROSTER is not settled -- not "absent from
    # teams.json". Season 2's six teams are all new and none of them is in
    # teams.json, and drawing all six as unconfirmed would say something
    # false. A team that names its own five is settled; one carrying
    # placeholders is not, and says so with a dashed outline everywhere.
    own = {t["id"]: t for t in cfg.get("teams") or []}
    real = {t["id"]: t["name"] for t in (league or {}).get("teams", [])}
    prov = {t["id"]: t for t in cfg.get("provisional_teams", [])}
    for tid in sorted(t for t in seen
                      if t not in own and t not in real and t not in prov):
        print(f"  ! {label}: team {tid} is in none of the season's own "
              f"teams, data/teams.json or provisional_teams -- it will "
              f"show as 'Team {tid}' with no players")

    # The same person on two rosters in the same cup -- the one thing that
    # cannot be true of a tournament. Warned rather than refused, because
    # this league genuinely shares stand-ins and a slot written "A / B" is
    # a single string that cannot collide by accident. UT moving from Team
    # Toxic to CPX's Return is exactly the edit this would have caught had
    # Trollmitsu not replaced him in the same breath.
    where = {}
    for tid, t in own.items():
        for r in t.get("roster") or []:
            n = (r.get("name") or "").strip().lower()
            if n:
                where.setdefault(n, []).append(tid)
    for n, tids in sorted(where.items()):
        if len(tids) > 1:
            print(f"  ! {label}: {n!r} is on {len(tids)} rosters -- "
                  + ", ".join(str(own[i].get("name") or i) for i in tids))

    def team(tid):
        o = own.get(tid)
        if o:
            roster = o.get("roster") or []
            named = [r for r in roster if r.get("name")]
            explicit = o.get("provisional")
            return {
                "id": tid,
                "name": o.get("name") or f"Team {tid}",
                "provisional": (explicit if explicit is not None
                                else not named),
                "players": [r["name"] for r in named],
                "roster": roster,
                "unfilled": len(roster) - len(named),
                "note": o.get("note"),
            }
        p = prov.get(tid) or {}
        return {
            "id": tid,
            "name": real.get(tid) or p.get("name") or f"Team {tid}",
            "provisional": tid not in real,
            "players": p.get("players", []),
            "roster": [],
            "unfilled": p.get("unfilled", 0),
            "note": p.get("note"),
        }

    # Pools. A round robin inside a pool of n is every unordered pair, in
    # the order the teams are listed -- which is the order the draft was
    # written in, so the page reads back the way it was drawn.
    pools, pool_matches = [], []
    for pool in pools_in:
        ids = list(pool["teams"])
        ms = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ms.append({"id": f"MINI-{pool['id']}-{a}v{b}",
                           "pool": pool["id"], "teams": [a, b],
                           "best_of": bo_pool})
        pool_matches += ms
        pools.append({
            "id": pool["id"],
            "label": pool.get("label") or f"Pool {pool['id']}",
            "teams": [team(t) for t in ids],
            "matches": ms,
            "advance": advance,
            "out": len(ids) - advance,
        })

    # Teams placed by hand rather than drawn. Checked against the pools
    # so the page cannot name a move that did not happen, or miss one that
    # did: the team must exist and must actually be in the pool claimed.
    placed = []
    for e in cfg.get("placed_by_hand") or []:
        tid, pid = e.get("team"), e.get("pool")
        pool = next((p for p in pools if p["id"] == pid), None)
        if pool is None:
            return refuse(f"placed_by_hand says team {tid} was put in pool "
                          f"{pid!r}, which is not a pool in this season")
        if tid not in [t["id"] for t in pool["teams"]]:
            return refuse(f"placed_by_hand says team {tid} was put in pool "
                          f"{pid}, but team {tid} is not in pool {pid}")
        placed.append({"team": tid, "name": team(tid)["name"],
                       "pool": pid, "from": e.get("from"),
                       "on": e.get("on"), "why": e.get("why")})

    # Results.
    #
    # A result here is REPORTED, not read off a scoreboard: it names a
    # winner and nothing else. Everywhere else in this project a result
    # arrives as a screenshot, is checksummed against
    # `team kills <= team score <= enemy deaths` and lands in a ledger with
    # every player's line behind it. These have none of that, so a result
    # without a `source_ref` is carried to the browser flagged `reported`
    # and the page says so out loud. Add a `source_ref` naming a game in
    # data/league_matches.json and the flag clears.
    #
    # What IS checked: the match exists, the winner actually played in it,
    # and no match has two results. Those are the ways a typo becomes a
    # wrong team in the bracket.
    all_matches = {m["id"]: m for pool in pools for m in pool["matches"]}
    seen_res = {}
    for r in cfg.get("results") or []:
        mid = r.get("match")
        if mid not in all_matches:
            return refuse(f"a result names {mid!r}, which is not a match in "
                          f"any pool")
        if mid in seen_res:
            return refuse(f"two results recorded for {mid}")
        pair = all_matches[mid]["teams"]
        if r.get("winner") not in pair:
            return refuse(f"the result for {mid} says team {r.get('winner')} "
                          f"won, but that match is team {pair[0]} v team "
                          f"{pair[1]}")
        seen_res[mid] = r

    # Matches being played RIGHT NOW. A third state, and the file could not
    # express it before: `results` carries a winner, so a game that has
    # started but not finished had to masquerade as "still to play" -- which
    # is the page saying nothing is happening while three games are live.
    #
    # It is deliberately NOT a result. Nothing about a standing, a pool
    # order or a bracket slot moves because a game is in progress; the only
    # thing that changes is what the page says is happening.
    playing = []
    for mid in cfg.get("in_progress") or []:
        if mid not in all_matches:
            return refuse(f"in_progress names {mid!r}, which is not a match "
                          f"in any pool")
        if mid in playing:
            return refuse(f"{mid} is listed twice in in_progress")
        # The contradiction that matters: a game cannot be under way and
        # already won. Left unchecked, a stale in_progress entry would sit
        # there claiming a finished match is still being played.
        if mid in seen_res:
            return refuse(f"{mid} is in in_progress but already has a "
                          f"result -- remove it from one of the two")
        playing.append(mid)

    for mid, m in all_matches.items():
        r = seen_res.get(mid)
        m["winner"] = r["winner"] if r else None
        m["loser"] = ([t for t in m["teams"] if t != r["winner"]][0]
                      if r else None)
        m["source_ref"] = (r or {}).get("source_ref")
        # No screenshot behind it. The page prints this; do not drop it.
        m["reported"] = bool(r) and not m["source_ref"]
        m["playing"] = mid in playing

    # Tie-break results. A tie-break only EXISTS where the group stage
    # left teams level, so its fixtures are generated, not configured --
    # and a recorded result that matches none of them is refused below
    # rather than silently ignored, which is how a result disappears.
    tb = {}
    for r in cfg.get("tie_break_results") or []:
        mid = r.get("match")
        if mid in tb:
            return refuse(f"two tie-break results recorded for {mid}")
        tb[mid] = r.get("winner")

    for pool in pools:
        pool_standings(pool, tb)

    tb_ids = {m["id"]: m for pool in pools
              for grp in pool["tie_breaks"] for m in grp["matches"]}
    for mid, w in tb.items():
        if mid not in tb_ids:
            return refuse(f"a tie-break result names {mid!r}, which is not a "
                          f"tie-break any pool needs. Tie-breaks that exist: "
                          + (", ".join(sorted(tb_ids)) or "none"))
        if w not in tb_ids[mid]["teams"]:
            return refuse(f"the tie-break result for {mid} says team {w} won, "
                          f"but that game is team {tb_ids[mid]['teams'][0]} v "
                          f"team {tb_ids[mid]['teams'][1]}")

    A, B = pools[0]["id"], pools[1]["id"]

    # The four playoff boxes. `col`/`row` is the grid the browser draws
    # them on; `feeds` is what fills each of the two slots in a box; and
    # `links` says which box feeds which slot of which other box -- the
    # lines. All of it is data so the drawing code holds no bracket
    # knowledge of its own that could fall out of step with this.
    nodes = [
        {"id": "MINI-UB", "round": "Upper match", "best_of": bo_play,
         "col": 1, "row": 1,
         "feeds": [{"kind": "pool", "pool": A, "rank": 1, "label": "Winner"},
                   {"kind": "pool", "pool": B, "rank": 1, "label": "Winner"}],
         "stakes": "Winner goes straight to the grand final. The loser drops "
                   "to the lower final and still has a life left.",
         "knockout": False},
        {"id": "MINI-ELIM", "round": "Elimination match", "best_of": bo_play,
         "col": 1, "row": 2,
         "feeds": [{"kind": "pool", "pool": A, "rank": 2, "label": "Runner-up"},
                   {"kind": "pool", "pool": B, "rank": 2, "label": "Runner-up"}],
         "stakes": "Loser is knocked out, in 4th.",
         "knockout": True},
        {"id": "MINI-LF", "round": "Lower final", "best_of": bo_play,
         "col": 2, "row": 2,
         "feeds": [{"kind": "node", "node": "MINI-UB", "side": "loser",
                    "label": "Loser"},
                   {"kind": "node", "node": "MINI-ELIM", "side": "winner",
                    "label": "Winner"}],
         "stakes": "Loser is knocked out, in 3rd.",
         "knockout": True},
        {"id": "MINI-GF", "round": "Grand final", "best_of": bo_final,
         "col": 3, "row": "center",
         "feeds": [{"kind": "node", "node": "MINI-UB", "side": "winner",
                    "label": "Winner"},
                   {"kind": "node", "node": "MINI-LF", "side": "winner",
                    "label": "Winner"}],
         "stakes": "Whoever wins it wins the whole thing.",
         "knockout": True, "final": True},
    ]
    links = [
        {"from": "MINI-UB",   "to": "MINI-GF", "slot": 0, "kind": "win"},
        {"from": "MINI-UB",   "to": "MINI-LF", "slot": 0, "kind": "loss"},
        {"from": "MINI-ELIM", "to": "MINI-LF", "slot": 1, "kind": "win"},
        {"from": "MINI-LF",   "to": "MINI-GF", "slot": 1, "kind": "win"},
    ]
    # A decided pool answers "winner of Pool A" and "runner-up of Pool A".
    # An UNdecided one does not, and is left alone -- a pool where two
    # teams are level on the tie-break is exactly where guessing would put
    # the wrong team in the bracket and nobody would notice until the
    # night it was played.
    pool_by_id = {pool["id"]: pool for pool in pools}
    for n in nodes:
        for f in n["feeds"]:
            f["team"] = None
            if f["kind"] != "pool":
                continue
            pool = pool_by_id.get(f["pool"])
            if not pool or not pool["decided"]:
                continue
            hit = [t for t in pool["standings"] if t["rank"] == f["rank"]]
            if hit:
                f["team"] = hit[0]["id"]

    by_id = {n["id"]: n for n in nodes}
    for ln in links:                    # a typo here would draw a lie
        assert ln["from"] in by_id and ln["to"] in by_id, ln
        assert 0 <= ln["slot"] < len(by_id[ln["to"]]["feeds"]), ln

    # The bracket always seats four, so places 1-4 are fixed. Everything
    # below that is however many teams the pools leave behind, which moves
    # the moment a pool changes size -- this line read "5th & 6th, third in
    # a pool" and went false the day season 2 went from pools of three to
    # pools of four. Derive it, and check it against the team count.
    def _ord(n):
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1:'st',2:'nd',3:'rd'}.get(n % 10, 'th')}"

    missed = sorted({p for pool in pools_in
                     for p in range(advance + 1, len(pool["teams"]) + 1)})
    out_n = sum(len(pool["teams"]) - advance for pool in pools_in)

    placings = [
        {"place": "1st", "from": "Wins the grand final"},
        {"place": "2nd", "from": "Loses the grand final"},
        {"place": "3rd", "from": "Loses the lower final"},
        {"place": "4th", "from": "Loses the elimination match"},
    ]
    if out_n:
        lo, hi = 5, 4 + out_n
        band = (_ord(lo) if lo == hi else
                f"{_ord(lo)} & {_ord(hi)}" if hi == lo + 1 else
                f"{_ord(lo)}\u2013{_ord(hi)}")
        # Words, not numerals, for the pool places -- the original copy read
        # "Third in a pool" and this line is prose, not a table cell.
        WORD = {1: "first", 2: "second", 3: "third", 4: "fourth",
                5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth"}
        where = " or ".join(WORD.get(p, _ord(p)) for p in missed)
        placings.append({
            "place": band,
            "from": f"{where[0].upper()}{where[1:]} in a pool \u2014 out before "
                    f"the bracket starts",
        })

    # Every team must land in exactly one band, or the page is describing a
    # tournament with a different number of teams than it just drew.
    assert 4 + out_n == len(seen), (
        f"placings cover {4 + out_n} teams but the season has {len(seen)}")

    # Two matches a night, the way the season already runs. Matches are
    # played in the order above and none starts before the ones it depends
    # on have finished, so filling nights in order is enough: every box's
    # feeders always land in an earlier slot than the box itself.
    slots = len((fixtures or {}).get("slots") or []) or 2
    nights, used = 0, slots
    for _ in pool_matches + nodes:
        if used >= slots:
            nights += 1
            used = 0
        used += 1

    def span(count, best_of):
        return (count * (best_of // 2 + 1), count * best_of)

    p_lo, p_hi = span(len(pool_matches), bo_pool)
    k_lo, k_hi = span(3, bo_play)
    f_lo, f_hi = span(1, bo_final)

    played = sum(1 for m in all_matches.values() if m["winner"])
    hearsay = sum(1 for m in all_matches.values() if m["reported"])

    # Tie-break games are EXTRA -- they are not part of the ten the format
    # promises, so they are counted apart from it rather than quietly
    # inflating "10 matches over 5 nights".
    tb_all = [m for pool in pools for g in pool["tie_breaks"]
              for m in g["matches"]]
    tb_done = [m for m in tb_all if m["winner"]]
    hearsay += len(tb_done)

    totals = {
        "played": played,
        "playing": len(playing),
        "reported": hearsay,
        "tie_break_matches": len(tb_all),
        "tie_break_played": len(tb_done),
        "pool_matches": len(pool_matches),
        "playoff_matches": len(nodes),
        "matches": len(pool_matches) + len(nodes),
        "games_min": p_lo + k_lo + f_lo,
        "games_max": p_hi + k_hi + f_hi,
        "nights": nights,
        "slots_per_night": slots,
        "teams": len(seen),
    }

    # What "shorter" actually means, measured against the season on the
    # Schedule tab rather than asserted. Regenerate the season and this
    # moves with it.
    season = None
    if fixtures and fixtures.get("weeks"):
        s_nights = s_series = s_lo = s_hi = 0
        for wk in fixtures["weeks"]:
            for night in wk.get("nights", []):
                s_nights += 1
                for s in night.get("series", []):
                    s_series += 1
                    b = s.get("best_of", 3)
                    s_lo += b // 2 + 1
                    s_hi += b
        season = {"nights": s_nights, "matches": s_series,
                  "games_min": s_lo, "games_max": s_hi}

    return {
        "id": cfg.get("id"),
        "status": cfg.get("status", "proposal"),
        "name": cfg.get("name", "Mini Cup"),
        "note": cfg.get("note"),
        # Every seed the pools were drawn with, in order. A season that adds
        # teams later draws again, and the page has to name BOTH -- printing
        # only the first would describe a draw that does not account for two
        # of the eight teams, and prose has no checksum to catch that.
        "draw_seeds": [s for s in (cfg.get("draw_seed"),
                                   cfg.get("draw_seed_newcomers")) if s],
        "draw_seed": cfg.get("draw_seed"),
        # Teams the seeds do NOT account for, because somebody moved them.
        # Without this the page goes on advertising a reproducible draw
        # that no longer reproduces the pools -- a fairness claim that has
        # quietly gone false, which is the worst kind to leave standing.
        "placed_by_hand": placed,
        "pools": pools,
        "bracket": nodes,
        "links": links,
        "placings": placings,
        "tie_breaks": cfg.get("tie_breaks", []),
        "best_of": {"pool": bo_pool, "playoff": bo_play, "final": bo_final},
        "totals": totals,
        "season": season,
    }


def _split_level(group: list, beat: dict) -> list:
    """
    Order a set of teams that are level, using only the results among
    those teams. Returns a list of sub-groups, best first; a sub-group
    with more than one member is genuinely inseparable.

    In a group of three each pair has met, so the counts come out either
    all different (someone beat both, someone beat one, someone beat
    none) or all equal, which is the circle: A beat B, B beat C, C beat
    A. There is no third shape, and the circle is the one that has to be
    refused.
    """
    if len(group) == 1:
        return [group]
    inner = {i: len(beat[i] & set(group)) for i in group}
    if len(set(inner.values())) == 1:
        return [group]
    return [[i for i in group if inner[i] == c]
            for c in sorted(set(inner.values()), reverse=True)]


def pool_standings(pool: dict, tb: dict | None = None) -> None:
    """
    Work out a pool's table, and its finishing order where the results
    actually settle one.

    Teams are grouped by wins. A group of more than one is split by the
    results among ONLY those teams. If that still does not separate them
    the tied teams REPLAY -- a single-game round robin between just them,
    whose fixtures are generated here and whose results arrive in
    `tie_break_results`. Until that replay is both complete and decisive
    the group stays UNDECIDED and the pool's bracket slots stay empty.

    Three teams playing one game each can all finish 1-1 with head to
    head running in a circle, and then nothing on the sheet tells them
    apart. Inventing an order at that point would silently send the wrong
    team into the bracket. A replay can circle too, and that is handled
    the same way: say so, decide nothing.

    Sets `standings`, `complete`, `decided` and `tie_breaks` in place.
    """
    tb = tb or {}
    ids = [t["id"] for t in pool["teams"]]
    rec = {i: {"id": i, "played": 0, "won": 0, "lost": 0} for i in ids}
    beat = {i: set() for i in ids}          # who each team has beaten

    for m in pool["matches"]:
        if m["winner"] is None:
            continue
        w, l = m["winner"], m["loser"]
        rec[w]["played"] += 1
        rec[w]["won"] += 1
        rec[l]["played"] += 1
        rec[l]["lost"] += 1
        beat[w].add(l)

    pool["complete"] = all(m["winner"] is not None for m in pool["matches"])

    # Pass one: the group stage on its own.
    groups = []
    if pool["complete"]:
        for wins in sorted({rec[i]["won"] for i in ids}, reverse=True):
            groups += _split_level([i for i in ids if rec[i]["won"] == wins],
                                   beat)

    # Pass two: anything still level replays.
    order, tie_breaks = [], []
    for g in groups:
        if len(g) == 1:
            order.append(g)
            continue

        fixtures, tb_beat, tb_rec = [], {i: set() for i in g}, {
            i: {"id": i, "won": 0, "lost": 0} for i in g}
        for x, a in enumerate(g):
            for b in g[x + 1:]:
                mid = f"TB-{pool['id']}-{a}v{b}"
                w = tb.get(mid)
                lose = None
                if w in (a, b):
                    lose = b if w == a else a
                    tb_beat[w].add(lose)
                    tb_rec[w]["won"] += 1
                    tb_rec[lose]["lost"] += 1
                else:
                    w = None
                fixtures.append({"id": mid, "teams": [a, b], "winner": w,
                                 "loser": lose, "reported": w is not None})

        done = all(f["winner"] is not None for f in fixtures)
        split = _split_level(g, tb_beat) if done else [g]
        resolved = done and all(len(x) == 1 for x in split)
        order += split if resolved else [g]

        tie_breaks.append({
            "pool": pool["id"], "teams": g, "matches": fixtures,
            "complete": done, "resolved": resolved,
            "standings": [tb_rec[i] for i in
                          (sum(split, []) if resolved else g)],
        })

    pool["tie_breaks"] = tie_breaks
    pool["decided"] = pool["complete"] and all(len(g) == 1 for g in order)

    rank = 1
    for group in order:
        for i in group:
            rec[i]["rank"] = rank
            rec[i]["tied"] = len(group) > 1
            rec[i]["outcome"] = ("tied" if len(group) > 1 else
                                 "advances" if rank <= pool["advance"] else
                                 "out")
        rank += len(group)

    pool["standings"] = sorted(
        rec.values(),
        key=lambda r: (r.get("rank", 99), -r["won"], ids.index(r["id"])))


def season_progress(data: dict, league: dict | None) -> dict | None:
    """
    Who still owes whom a series.

    This is the schedule that actually survives contact with the league.
    A pre-assigned "Team 1 v Team 2, Saturday, late slot" is a guess about
    a night three weeks away, and it has been wrong every way it can be --
    teams played a week early, swapped slots, finished a best-of-three on
    a different night than it started. What never changes is the debt:
    every pair owes N meetings, and each one played knocks one off. The
    season is over when every counter reaches zero, whenever those games
    actually happened.

    The final is excluded on purpose. It has no pair to owe -- its two
    teams are whoever tops the round robin.
    """
    if not league:
        return None
    target = (data.get("season") or {}).get("meetings_per_pair")
    if not target:
        return None

    names = {t["id"]: t["name"] for t in league.get("teams", [])}
    ids = sorted(names)

    done = Counter()      # frozenset({a, b}) -> completed series
    live = Counter()      # started but not decided
    for wk in data.get("weeks", []):
        for night in wk.get("nights", []):
            for s in night.get("series", []):
                if s.get("decided_by") or s["id"] == "FINAL":
                    continue
                pair = s.get("teams") or []
                if len(pair) != 2:
                    continue
                key = frozenset(pair)
                if s.get("status") == "final":
                    done[key] += 1
                elif s.get("status") == "playing":
                    live[key] += 1

    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            key = frozenset((a, b))
            pairs.append({"a": a, "b": b, "played": done[key],
                          "playing": live[key],
                          "remaining": max(0, target - done[key])})

    teams = []
    for a in ids:
        vs = [{"id": p["b"] if p["a"] == a else p["a"],
               "played": p["played"], "playing": p["playing"],
               "remaining": p["remaining"]}
              for p in pairs if a in (p["a"], p["b"])]
        teams.append({"id": a, "name": names[a],
                      "played": sum(v["played"] for v in vs),
                      "remaining": sum(v["remaining"] for v in vs),
                      "vs": sorted(vs, key=lambda v: v["id"])})

    return {
        "target": target,
        "total": len(pairs) * target,
        "played": sum(p["played"] for p in pairs),
        "playing": sum(p["playing"] for p in pairs),
        "remaining": sum(p["remaining"] for p in pairs),
        "pairs": pairs,
        "teams": teams,
    }


def sync_hero_slugs(cur) -> None:
    """
    Keep docs/heroes.js covering every hero in the database.

    Only heroes NOT already mapped are looked up, so this stays fast and
    works offline once the map is warm. A hero with no entry simply renders
    without art -- it must never break the page or block a deploy.
    """
    import json as _json
    import re
    import urllib.request

    out = ROOT / "docs" / "heroes.js"
    have = {}
    if out.exists():
        m = re.search(r"window\.HERO_SLUG\s*=\s*(\{.*?\});", out.read_text(encoding="utf-8"), re.S)
        if m:
            have = _json.loads(m.group(1))

    heroes = [r[0] for r in cur.execute(
        "SELECT DISTINCT hero FROM match_players WHERE hero IS NOT NULL")]
    missing = [h for h in heroes if h not in have]
    if not missing:
        print(f"  hero art: {len(have)} mapped, none new")
        return

    added, failed = 0, []
    for h in missing:
        slug = HERO_SPECIAL.get(h) or re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_")
        try:
            req = urllib.request.Request(f"{CDN}/{slug}.png", method="HEAD",
                                         headers={"User-Agent": "Mozilla/5.0"})
            ok = urllib.request.urlopen(req, timeout=15).status == 200
        except Exception:
            ok = False
        if ok:
            have[h] = slug
            added += 1
        else:
            failed.append((h, slug))

    out.write_text(
        "/* Display name -> Valve internal hero slug. Every entry HEAD-verified\n"
        "   against the Dota 2 CDN. Regenerated by export_web.py. */\n"
        "window.HERO_SLUG = " + _json.dumps(have, ensure_ascii=False, indent=0,
                                            sort_keys=True) + ";\n",
        encoding="utf-8")
    print(f"  hero art: {len(have)} mapped (+{added} new)")
    for h, s in failed:
        print(f"    ! no image for {h!r} (tried {s!r}) -- it will render without art")


def stamp_assets() -> None:
    """
    Rewrite index.html's asset links as `app.js?v=<hash>`.

    Without this, a returning visitor can end up holding a CACHED copy of
    one file and a fresh copy of another -- e.g. the previous app.js with
    the current data.js. The two disagree about the shape of the data and
    the page renders blank, which is exactly what happened on the first
    redesign deploy. Hashing the content means any change to any asset
    produces new URLs, so the browser can never mix versions.
    """
    import hashlib
    import re

    docs = ROOT / "docs"
    assets = ["style.css", "app.js", "data.js", "heroes.js"]
    h = hashlib.sha1()
    for a in assets:
        p = docs / a
        if p.exists():
            h.update(p.read_bytes())
    ver = h.hexdigest()[:8]

    idx = docs / "index.html"
    html = idx.read_text(encoding="utf-8")
    before = html
    for a in assets:
        html = re.sub(r'(href|src)="' + re.escape(a) + r'(\?v=[0-9a-f]+)?"',
                      lambda m: f'{m.group(1)}="{a}?v={ver}"', html)
    if html != before:
        idx.write_text(html, encoding="utf-8")
    print(f"  stamped assets ?v={ver}")


if __name__ == "__main__":
    sys.exit(main())
