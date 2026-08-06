"""
Read league scheduling commands from the #dota-league-2026 channel and act.

Its own watermark file so it never steps on discord_commands.py (which reads
the #lobby-stats channel). Uses the plain REST API over urllib -- same as
the rest of the bot code.

    python tools/discord_league.py            # process new commands, once
    python tools/discord_league.py --watch    # stay up and answer live
    python tools/discord_league.py --dry-run  # parse and reply-preview only
    python tools/discord_league.py --all      # ignore the watermark

Without --watch this is a batch job: it answers whatever was typed since
the last run and exits. Nothing is listening in between, so a command
typed while it is not running gets silence until somebody runs it again.
--watch is the fix -- it polls every few seconds and answers as people
type, and it is written to survive network drops rather than exit on the
first one.

Understood in the channel (case-insensitive):

    !help                                Show these commands.
    !status                              Standings link + current leader.

    !register @user as PlayerName        approver: map a Discord user to
                                         a player row in the ledger.
    !tz PlayerName TzName                approver: declare a player's
                                         timezone (PKT, ET, AST, CET,
                                         UTC+5, or IANA like Asia/Karachi).

    !schedule Team X vs Team Y           approver: open a scheduling round.
    !cancel R42                          approver: close an open round.
    !confirm R42 N                       approver: lock slot N of round R42.

    !avail Sat 20-23                     player: post availability
                                         (in your local timezone).
                                         Time format: HH-HH (24h) or "sat evening".
    !avail R42 Sat 20-23                 as above, for a specific round.
    !avail clear                         wipe your own availability in the
                                         (single) open round.

Anything !avail cannot parse is KEPT in data/avail_pending.json rather
than thrown back at the player -- see tools/avail_llm.py, which reads
the queue with a model and writes the result back through this file's
own do_avail(). No model runs in here.
    !find                                approver: rank top 3 slots for
                                         the first open round.
    !find R42                            approver: same, specific round.

All state lives in data/scheduling.json (round + availability + upcoming)
and data/discord_players.json (Discord user id -> player name). Both are
diffable JSON in git; nothing goes straight to the database.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from discord_pull import (api, api_raw, DiscordHTTPError,   # noqa: E402
                          config as lobby_config)
from discord_ask import approvers, post as post_msg       # noqa: E402
import find_slot                                          # noqa: E402
import tz_map                                             # noqa: E402

CONF = ROOT / "tools" / "discord.local.json"

SCHEDULING = ROOT / "data" / "scheduling.json"
PLAYERS_TZ = ROOT / "data" / "players_tz.json"
DISCORD_PLAYERS = ROOT / "data" / "discord_players.json"
TEAMS_FILE = ROOT / "data" / "teams.json"
AVAIL_PENDING = ROOT / "data" / "avail_pending.json"

MARK = ROOT / "data" / "discord_league_watermark.txt"


# ── Config: league channel id, on top of the shared token ─────────────

def league_channel() -> tuple[str, str]:
    """
    Return (bot_token, league_channel_id).

    Prefer env vars (LEAGUE_CHANNEL_ID, DISCORD_BOT_TOKEN) so a cloud
    runner can supply them as secrets. Local dev falls back to
    discord.local.json's channels.league.
    """
    import os
    tok = os.environ.get("DISCORD_BOT_TOKEN")
    chan = os.environ.get("DISCORD_LEAGUE_CHANNEL_ID") or os.environ.get("LEAGUE_CHANNEL_ID")
    if CONF.exists():
        c = json.loads(CONF.read_text(encoding="utf-8"))
        tok = tok or c.get("token")
        chan = chan or (c.get("channels") or {}).get("league")
    if not tok or not chan:
        sys.exit(
            "No league config. Add `channels.league` to tools/discord.local.json\n"
            'or set DISCORD_LEAGUE_CHANNEL_ID.')
    return tok, str(chan)


# ── JSON helpers ──────────────────────────────────────────────────────

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    """
    Atomic write. Truncate-and-write is what the rest of the codebase does,
    but for user-visible state that changes on every command, a partial
    write during a crash is a real risk -- write to a temp file, then
    os.replace() which is atomic on POSIX and Windows.
    """
    import os, tempfile
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def load_scheduling() -> dict:
    return load_json(SCHEDULING, default={"next_round_seq": 1,
                                          "rounds": [], "upcoming": [], "archive": []})


def load_discord_players() -> dict:
    return load_json(DISCORD_PLAYERS,
                     default={"_comment": ["Discord user_id -> player display_name."],
                              "players": {}})


def load_players_tz() -> dict:
    return load_json(PLAYERS_TZ, default={"players": {}})


def load_teams() -> dict:
    return load_json(TEAMS_FILE, default={"teams": []})


# ── The rewrite queue ─────────────────────────────────────────────────
#
# When the regex parser cannot read an !avail message, throwing an error
# at the player and forgetting the message is the worst of both worlds:
# they are told they are wrong, and the thing they typed is gone. Every
# rejection in this channel so far has been a missing feature, not user
# error -- `fri sat sun`, `Fri Sat 20-23`, `aug7 ... or ...`. Two of those
# players never re-posted, so their availability is simply absent from
# this week's numbers.
#
# So a failure is kept, not dropped. tools/avail_llm.py drains this queue
# with a model, which is allowed to rewrite the free text into the
# canonical grammar and NOTHING ELSE -- the string it produces still has
# to survive _parse_avail() and every guard in do_avail(). The model never
# produces windows, so it cannot invent a time that the deterministic
# parser would have rejected.

def load_pending() -> dict:
    return load_json(AVAIL_PENDING, default={
        "_comment": [
            "!avail messages the regex parser could not read.",
            "Drained by tools/avail_llm.py; see that file for the contract.",
        ],
        "pending": [], "resolved": []})


def enqueue_pending(raw: str, uname: str, uid: str, player: str | None,
                    err: str, msg_id: str | None) -> int:
    """
    Record an unparseable !avail. Returns the queue position shown to the
    user. Re-posting the same text does not stack up a second entry --
    people retype the identical line when they get no answer, and three
    copies of one request would read as three people waiting.
    """
    q = load_pending()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for e in q["pending"]:
        if e.get("discord_uid") == str(uid) and e.get("raw", "").strip() == raw.strip():
            e["at"] = now
            e["message_id"] = msg_id or e.get("message_id")
            save_json(AVAIL_PENDING, q)
            return e["id"]
    nid = 1 + max([e.get("id", 0) for e in q["pending"] + q["resolved"]] or [0])
    q["pending"].append({
        "id": nid, "at": now,
        "discord_uid": str(uid), "discord_name": uname,
        "player": player, "raw": raw.strip(),
        "parser_error": err, "message_id": msg_id,
    })
    save_json(AVAIL_PENDING, q)
    return nid


# ── Author -> player resolution ───────────────────────────────────────

def player_for_author(uid: str, uname: str, dp: dict) -> str | None:
    """
    Discord user id -> player display_name.

    Order:
      1. Explicit mapping in discord_players.json (from !register).
      2. Case-insensitive match of Discord username against a roster
         `name` or any `aka`. So a player whose Discord handle is "cpx"
         auto-resolves to "Mandark [<MC>]" without needing !register.
    """
    name = dp["players"].get(str(uid))
    if name:
        return name
    canonical, _ = resolve_to_roster(uname or "", load_teams())
    return canonical


# ── Command handlers ──────────────────────────────────────────────────

HELP = (
    "**League commands (in this channel only)**\n"
    "\n**Once per player:**\n"
    "`!register Nick Zone` — bind your Discord to a player name + timezone\n"
    "  e.g. `!register Cpx PKT`  ·  `!register Musa CET`\n"
    "\n**Every week — post your availability (in YOUR local time):**\n"
    "`!avail every day 9PM to 6AM` — same window all week, the easy one\n"
    "`!avail Aug 3 8PM to 10PM, Aug 4 9PM-11PM` — dates + times, comma-separated\n"
    "  Also OK: `Sat 8pm-10pm` · `Mon Wed Fri 8pm-11pm` · `Aug 5 20:00-22:00`\n"
    "  · `10PM to 2AM` (past midnight is fine) · `Aug 7 6pm-10pm or 12am-6am`\n"
    "`!avail clear` — remove your availability this week\n"
    "  If none of that fits, **just write it in plain English** — "
    "`!avail every night after 9 my time`. I'll keep it and come back to "
    "you with exactly what I understood.\n"
    "\n**Anyone:**\n"
    "`!who` — who has registered, and in which timezone\n"
    "`!readiness` — per-team % ready this week + who's missing\n"
    "`!find` — top slots for every pairing that has both teams ≥1 avail\n"
    "`!find 1 vs 3` — top 3 slots for a specific pair\n"
    "`!status` — site link + current state\n"
    "\n**Approvers:**\n"
    "`!confirm 1 vs 3 N` — lock slot N for that pair → posts to the site\n"
    "`!newweek` — archive last week and reset for the new one\n"
    "`!register @user Nick Zone` — bind on someone else's behalf\n"
    "`!tz PlayerName Zone` — set someone else's timezone\n"
    "\n**Zones:** `PKT`, `ET` (or `Eastern`), `PT`, `AST` (Saudi), `CET`, `GMT`, "
    "`IST` (India), `UTC+5`, or IANA (`Asia/Karachi`).\n"
    "\nWhen you post `!avail`, I'll ping the team members who haven't posted yet. "
    "When both teams in any pair reach 100%, I'll auto-suggest the top slots."
)


def do_help() -> str:
    return HELP


def do_status() -> str:
    """
    !status -- link + a short summary of this week's state.

    Uses the new week-based schema: no `rounds[]` anymore; reads
    availability, upcoming matches, and derives a one-line pulse per
    team from find_slot.team_readiness().
    """
    site = "https://muhammadut.github.io/total-dota-lobby-stats/"
    sched   = load_scheduling()
    teams   = load_teams()
    avail   = sched.get("availability", {})
    upcoming = sched.get("upcoming", [])
    week    = sched.get("week_of", "?")

    dp = load_discord_players().get("players", {})
    ptz = load_players_tz().get("players", {})
    roster_n = sum(len(t.get("roster", [])) for t in teams["teams"])
    setup_n = sum(1 for n in set(dp.values()) if ptz.get(n))

    parts = [f"**League status — week of {week}**"]
    parts.append(f"Site: {site}#teams")
    # Registration first: availability percentages are misleading while
    # most of the league still cannot post availability at all.
    parts.append(f"Registered with a timezone: **{setup_n}/{roster_n}**"
                 + ("  (`!who` for the list)" if setup_n < roster_n else ""))
    parts.append(f"Players who have posted availability this week: **{len(avail)}**")

    # An undrained rewrite queue is a person whose availability is missing
    # and who thinks they already posted it. That has to be visible from
    # inside the channel, not only to whoever runs the tools.
    waiting = load_pending().get("pending", [])
    if waiting:
        parts.append(f"⏳ Waiting on a closer read: **{len(waiting)}** "
                     f"({', '.join(sorted({e.get('player') or e.get('discord_name','?') for e in waiting}))})")

    r = find_slot.team_readiness(avail, teams)
    ready_pct = ", ".join(
        f"T{tid}:{int(round(info['pct']*100))}%"
        for tid, info in sorted(r.items()))
    parts.append(f"Team readiness: {ready_pct}")

    if upcoming:
        # Newest first
        m = sorted(upcoming,
                   key=lambda x: x.get("start_utc", ""),
                   reverse=True)[0]
        names = m.get("match_up_names", ["?", "?"])
        parts.append(f"Next confirmed match: **{names[0]} vs {names[1]}** at "
                     f"`{m.get('start_utc', '?')}`")
    else:
        parts.append("No confirmed upcoming match yet.")
    return "\n".join(parts)


def do_who() -> str:
    """
    !who -- who has registered, and in which timezone.

    !status and !readiness both answer "who has posted availability THIS
    WEEK", which resets every !newweek. Neither answers the prior
    question -- who has done the one-time setup at all -- so chasing
    stragglers meant reading two JSON files by hand. A player with no
    timezone cannot post availability that means anything, so this is
    the number that actually gates the league starting.
    """
    teams = load_teams()
    dp = load_discord_players().get("players", {})
    tz = load_players_tz().get("players", {})
    claimed = set(dp.values())

    total = done = zoned_only = 0
    lines = ["**Registration — who has set themselves up**"]
    for t in teams["teams"]:
        row = []
        for r in t.get("roster", []):
            total += 1
            nick = (r.get("aka") or [r["name"]])[0]
            zone = tz.get(r["name"])
            # Trailing element of the IANA name is the readable bit:
            # "Europe/London" -> "London". Nobody needs the continent.
            short = f" `{zone.split('/')[-1].replace('_', ' ')}`" if zone else ""
            if r["name"] in claimed and zone:
                done += 1
                row.append(f"✅{nick}{short}")
            elif zone:
                # Zone known from the captains' roster sheet, but this
                # player has never linked their Discord -- so the bot
                # cannot tell it is them typing, and they cannot post
                # availability. Showing a bare ⬜ hid that we already know
                # where they are, and made it look like more work than it is.
                zoned_only += 1
                row.append(f"⬜{nick}{short}")
            else:
                row.append(f"⬜{nick}")
        lines.append(f"**{t['name']}** " + " · ".join(row))

    pool = teams.get("open_pool") or []
    if pool:
        lines.append("**Open pool** " + " · ".join(
            f"{p if isinstance(p, str) else p.get('name', '?')}" for p in pool))

    lines.append(f"\n**{done} of {total}** fully set up.")
    if zoned_only:
        lines.append(
            f"{zoned_only} more already have a timezone from the roster sheet "
            f"but haven't linked their Discord — until they do, I can't tell "
            f"it's them typing, so they can't post availability.")
    if done < total:
        lines.append("Not set up yet? `!register YourNick YourZone` — "
                     "e.g. `!register Cpx PKT`. Zones: PKT, ET, PT, AST, "
                     "CET, GMT, IST, or `UTC+5`.")
    return "\n".join(lines)


def resolve_zone(text: str):
    """
    Resolve a timezone the way people write it, not the way a parser wants.

    tz_map.resolve wants one clean token. Someone typing their timezone
    into a chat box does not: `UK (GMT+1)` was rejected outright, which
    is a strange thing to tell a person who just told you exactly where
    they are, twice.

    Tries, in order: the string as given; the parenthetical removed
    (`UK (GMT+1)` -> `UK`); the parenthetical alone (`-> GMT+1`); then
    each bare word. Outside-the-brackets wins over inside deliberately --
    `UK` maps to Europe/London and follows DST, while `GMT+1` is a fixed
    offset that would be an hour wrong every winter.

    Returns an IANA name, or None if nothing resolved.
    """
    if not text:
        return None
    raw = text.strip()
    inner = re.findall(r"[\(\[]([^\)\]]+)[\)\]]", raw)
    outer = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", raw)

    seen, cands = set(), []
    for c in [raw, outer, *inner, *outer.split(), *[w for i in inner for w in i.split()]]:
        c = c.strip(" ,.;:-").strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            cands.append(c)

    for c in cands:
        try:
            z = tz_map.resolve(c)
            if z:
                return z
        except Exception:
            continue
    return None


def do_register(args: str, uname: str, uid: str, dp: dict, privileged: bool) -> str:
    """
    Two forms:

      !register Nick Zone                 -- register YOURSELF as Nick with tz
                                             (or someone else, if Nick is not
                                             already claimed by another user).
      !register @user Nick Zone           -- approver form: bind a specific
                                             Discord user to Nick + zone.
                                             Used when you're registering on
                                             behalf of someone who isn't in the
                                             channel yet, or fixing a mistake.

    Trust model: first-claim-wins. Once a Nick is bound to a Discord id,
    subsequent !register attempts from a different id are refused. Same
    id can re-register the same Nick to update the timezone.

    If Nick isn't on any team roster, they're added to `open_pool` in
    teams.json -- they show up on the Teams tab but don't count for a team
    until a captain picks them up.
    """
    txt = args.strip()
    if not txt:
        return ("Format: `!register Nick Zone` — e.g. `!register Cpx PKT`\n"
                "Zone shortcuts: PKT, ET (US East), PT (US West), AST (Saudi), "
                "CET (Europe), GMT (UK), IST (India), or `UTC+5`, or a full "
                "IANA name like `Asia/Karachi`.")

    # Detect `--new` / `--pool` flag: user insists they're not on any roster
    # and want to go straight to Open Pool. This is how we handle the
    # legitimate "I really am a new player" case after the suggestion prompt.
    force_new = False
    # Accept the em/en dashes too. Discord and phone keyboards autocorrect
    # "--" into "—", so the flag the bot itself just told someone to type
    # arrives mangled and is rejected. Soooze hit this twice in a row and
    # gave up; the bot was refusing its own instructions.
    m = re.search(r"\s+[-–—]{1,2}(?:new|pool)\s*$", txt, re.I)
    if m:
        force_new = True
        txt = txt[:m.start()].strip()

    # Approver override form: !register @user Nick Zone
    m = re.match(r"^\s*<@!?(\d+)>\s+(.+?)\s*$", txt)
    target_uid = uid
    target_uname = uname
    if m:
        if not privileged:
            return "Only approvers can register someone else via `@user`. Just do `!register Nick Zone` for yourself."
        target_uid = m.group(1)
        target_uname = f"user_{target_uid}"
        txt = m.group(2).strip()

    # Split remaining: everything but the last "phrase" is the nick,
    # last one/two tokens are the timezone. Try longest tz first (so
    # "!register Cpx Eastern time" works).
    parts = txt.split()
    if len(parts) < 2:
        return "Need a name AND a timezone. Try `!register Cpx PKT`."

    # Prefer a split where the NAME resolves to a roster player, not just
    # one where the tail happens to look like a timezone. "HURR UK (GMT+1)"
    # splits two ways that both leave a valid-looking zone -- ("HURR UK",
    # "(GMT+1)") and ("HURR", "UK (GMT+1)") -- and only the second has a
    # real player on the left. Checking the roster first picks it; without
    # that, the bot rejects a perfectly clear message by inventing a player
    # called "HURR UK".
    _teams_for_split = load_teams()
    for split_at in range(1, len(parts)):
        if resolve_to_roster(" ".join(parts[:split_at]), _teams_for_split)[0] \
           and resolve_zone(" ".join(parts[split_at:])):
            parts = [" ".join(parts[:split_at]), " ".join(parts[split_at:])]
            break

    iana = None
    nick = None
    for split_at in range(len(parts) - 1, 0, -1):
        name_str = " ".join(parts[:split_at])
        tz_str   = " ".join(parts[split_at:])
        try:
            iana = resolve_zone(tz_str)
            nick = name_str
            break
        except ValueError:
            continue
    if not iana:
        return (f"Couldn't recognise the timezone in `{txt}`. "
                f"Try `!register Cpx PKT` or `!register Cpx Asia/Karachi`.")

    # Resolve typed nick to a canonical roster name via name OR aka.
    # If it resolves, we bind to the CANONICAL name -- so 'Cpx' and
    # 'cpx22' and 'Mandark [<MC>]' all point at the same player row.
    teams = load_teams()
    canonical, on_team = resolve_to_roster(nick, teams)
    if canonical:
        nick = canonical
    elif not force_new:
        # Unknown nick, and user hasn't explicitly opted into Open Pool.
        # Show suggestions + the full team roster so they can pick correctly.
        # This catches the "HELL-ANGEL is really Musa" class of mistake --
        # human context is needed to map, and the bot should ask instead of
        # silently pooling someone who's actually on a team.
        return _register_suggest(nick, iana, teams)

    # First-claim-wins: if this Nick (canonical) is already bound to a
    # different Discord id, refuse. Same id re-registering IS an update.
    existing_uid = None
    for u, n in dp.get("players", {}).items():
        if n.lower() == nick.lower():
            existing_uid = u
            break
    if existing_uid and existing_uid != str(target_uid) and not privileged:
        return (f"`{nick}` is already registered by another Discord user. "
                f"If this is a mistake, ask an approver to override "
                f"(`!register @you {nick} {iana}`).")

    # Also refuse if THIS user has already claimed a DIFFERENT nick -- one
    # Discord id, one player identity. Otherwise a single user could bind
    # to Stoic on Monday and Cpx on Tuesday, and !avail would resolve them
    # to whichever last wrote.
    already_have = dp["players"].get(str(target_uid))
    if already_have and already_have.lower() != nick.lower() and not privileged:
        return (f"You're already registered as `{already_have}`. "
                f"If you meant to change identity, ask an approver.")

    # OK — write the binding + tz. If not on any team, add to open_pool.
    dp["players"][str(target_uid)] = nick
    save_json(DISCORD_PLAYERS, dp)

    ptz = load_players_tz()
    ptz["players"][nick] = iana
    save_json(PLAYERS_TZ, ptz)

    if on_team:
        return (f"✅ Registered **{nick}** on **{on_team['name']}** · timezone `{iana}`.\n"
                f"You can now post availability with `!avail Sat 20-23`.")

    # Not on a team — add to open_pool (idempotent).
    pool = teams.setdefault("open_pool", [])
    if not any(p.get("name", "").lower() == nick.lower() for p in pool):
        pool.append({"name": nick, "added_at": datetime.now(timezone.utc)
                                                .isoformat().replace("+00:00", "Z")})
        save_json(TEAMS_FILE, teams)
    return (f"✅ Registered **{nick}** (Open Pool) · timezone `{iana}`.\n"
            f"You're not on a fixed roster yet — a captain can pull you as a stand-in.")


def _register_suggest(typed_nick: str, iana: str, teams: dict) -> str:
    """
    Reply when a !register nick doesn't match any roster.

    Two-part message:
      1. Close-match suggestions (difflib fuzzy against every name/aka)
      2. Full grouped roster so the user can see all valid nicks

    Also tells them how to force Open Pool if they really ARE new
    (`!register Nick Zone --new`).
    """
    import difflib

    # Build the search space: every roster name + every aka. Keep a
    # reverse map so we can show the FRIENDLY form as the suggestion.
    candidates: list[str] = []
    friendly: dict[str, str] = {}     # searchable_form -> display_form
    for t in teams["teams"]:
        for r in t["roster"]:
            display = (r.get("aka") or [None])[0] or r["name"]
            candidates.append(r["name"])
            friendly[r["name"].lower()] = display
            for a in r.get("aka", []):
                candidates.append(a)
                friendly[a.lower()] = display

    close = difflib.get_close_matches(
        typed_nick.lower(),
        [c.lower() for c in candidates],
        n=3, cutoff=0.6)
    # De-duplicate suggestions (multiple aka can map to same friendly name).
    seen = set()
    suggestions = []
    for c in close:
        f = friendly.get(c, c)
        if f not in seen:
            suggestions.append(f)
            seen.add(f)

    lines = [f"I don't have `{typed_nick}` on any roster."]
    if suggestions:
        pretty = " · ".join(f"`{s}`" for s in suggestions)
        lines.append(f"**Did you mean:** {pretty} ?")
    lines.append("")
    lines.append("**All roster nicks (use one of these):**")
    for t in teams["teams"]:
        team_nicks = []
        for r in t["roster"]:
            nn = (r.get("aka") or [None])[0] or r["name"]
            team_nicks.append(nn)
        lines.append(f"  **{t['name']}**: {', '.join(team_nicks)}")
    lines.append("")
    lines.append(f"Retype with one of the above — e.g. `!register {suggestions[0] if suggestions else 'YourNick'} {iana}`.")
    lines.append(f"If you really aren't on a team, add `--new` to go into Open Pool: "
                 f"`!register {typed_nick} {iana} --new`")
    return "\n".join(lines)


def resolve_to_roster(nick: str, teams: dict) -> tuple[str | None, dict | None]:
    """
    Resolve a typed name to (canonical_name, team_dict).

    Try in order:
      1. Exact case-insensitive match against roster `name`.
      2. Exact case-insensitive match against any `aka` on a roster row.
      3. Base-name match (strip clan tags + non-alnum) against `name` or `aka`.

    Returns (None, None) if no match -- caller should treat as Open Pool.
    """
    n_low = nick.strip().lower()
    for t in teams["teams"]:
        for r in t.get("roster", []):
            if r["name"].lower() == n_low:
                return r["name"], t
            for a in r.get("aka", []):
                if a.lower() == n_low:
                    return r["name"], t

    def bare(s: str) -> str:
        s = re.sub(r"\[[^\[\]]*\]\s*$", "", s)
        return re.sub(r"[^a-z0-9]+", "", s.lower())

    nb = bare(nick)
    if nb:
        for t in teams["teams"]:
            for r in t.get("roster", []):
                if bare(r["name"]) == nb:
                    return r["name"], t
                for a in r.get("aka", []):
                    if bare(a) == nb:
                        return r["name"], t

    return None, None


def do_tz(args: str, uname: str, uid: str, dp: dict, privileged: bool) -> str:
    """
    Two forms, resolved greedy-longest-tz-first:

      !tz PKT                    -- self-service; sets the caller's tz.
      !tz Eastern time           -- also self-service (multi-word tz OK).
      !tz PlayerName PKT         -- approver-only; set someone else's tz.
      !tz Stoic Eastern time     -- approver-only; multi-word tz.

    Resolution strategy: try the WHOLE argument as a timezone first. If
    it resolves, treat as self-service. Otherwise walk backwards splitting
    into (player_name, tz) at each word boundary until one resolves.
    """
    txt = args.strip()
    if not txt:
        return "Format: `!tz PKT` (yourself) or `!tz PlayerName PKT` (approver)."

    teams = load_teams()

    def write(player: str, iana: str) -> None:
        ptz = load_players_tz()
        ptz["players"][player] = iana
        save_json(PLAYERS_TZ, ptz)

    # Try 1: (player name | timezone).
    #
    # This is asked FIRST, and it is the whole fix. It used to run second,
    # behind "does the entire string resolve as a timezone?" -- and it
    # does, because resolve_zone() falls back to matching individual bare
    # words. "TigerX Asia/Karachi" resolved to Asia/Karachi, so every
    # `!tz SomeoneElse Zone` was treated as self-service and silently set
    # the CALLER's timezone instead of the named player's. An approver
    # fixing somebody's zone would quietly break their own. Ask the
    # narrower question -- "does the left side name a real player?" --
    # before the greedy one.
    parts = txt.split()
    for split_at in range(len(parts) - 1, 0, -1):
        canonical, _ = resolve_to_roster(" ".join(parts[:split_at]), teams)
        if not canonical:
            continue
        iana = resolve_zone(" ".join(parts[split_at:]))
        if not iana:
            continue
        if not privileged:
            return ("Only approvers can set someone else's timezone. "
                    "To set your own, type just the zone: `!tz PKT`.")
        write(canonical, iana)
        return f"Set **{canonical}** timezone → `{iana}`."

    # Try 2: the whole thing is a timezone -> the caller's own.
    iana = resolve_zone(txt)
    if iana:
        player = player_for_author(uid, uname, dp)
        if not player:
            return (f"I don't know who you are on the league — an approver runs "
                    f"`!register @{uname} as PlayerName` first, then you can `!tz {txt}`.")
        write(player, iana)
        return f"Set your timezone (**{player}**) → `{iana}`."

    # resolve_zone returns None rather than raising, so this path used to be
    # unreachable: `!tz nonsense` fell into set_self(None) and wrote a null
    # timezone over a good one.
    return (f"Couldn't parse `{txt}`. Try `!tz PKT` (yourself) or "
            f"`!tz PlayerName PKT` (approver). Known shortcuts: "
            f"PKT, ET, PT, AST, CET, GMT, IST, +5.")


def do_newweek(uname: str) -> str:
    """
    !newweek -- approver-only. Archive this week's availability and clear.
    """
    sched = load_scheduling()
    prior_week = sched.get("week_of")
    prior_avail = sched.get("availability", {})

    # Archive the current week's snapshot (only if non-empty).
    if prior_avail:
        sched["archive"].append({
            "week_of":       prior_week,
            "closed_at":     datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "closed_by":     uname,
            "availability":  prior_avail,
        })

    # Advance week_of to the coming Monday.
    today = datetime.now(timezone.utc).date()
    from datetime import timedelta as _td
    monday = today
    while monday.weekday() != 0:
        monday += _td(days=1)
    sched["week_of"] = monday.isoformat()
    sched["availability"] = {}
    save_json(SCHEDULING, sched)

    return (f"🗓️ **New week open** — week of `{monday}`. Previous week "
            f"({prior_week}) archived with {len(prior_avail)} player(s).\n"
            f"Everyone: post your week's availability with `!avail <date> <HH-HH>`.")


def do_readiness() -> str:
    """!readiness -- per-team % ready this week + who's missing."""
    sched = load_scheduling()
    teams = load_teams()
    r = find_slot.team_readiness(sched.get("availability", {}), teams)
    week = sched.get("week_of", "?")

    # Build canonical -> friendly per team for readable missing lists.
    friendly_by_team = {}
    for t in teams["teams"]:
        friendly_by_team[t["id"]] = {
            row["name"]: (row.get("aka") or [None])[0] or row["name"]
            for row in t.get("roster", [])
        }

    lines = [f"**Readiness — week of {week}**"]
    for tid in sorted(r.keys()):
        info = r[tid]
        got, need = len(info["responded"]), len(info["responded"]) + len(info["missing"])
        pct = int(round(info["pct"] * 100))
        bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
        line = f"  **{info['team_name']}** {bar} {got}/{need} ({pct}%)"
        if info["missing"]:
            fmap = friendly_by_team.get(tid, {})
            missing_friendly = [fmap.get(n, n) for n in info["missing"]]
            line += f" — missing: {', '.join(missing_friendly)}"
        lines.append(line)
    return "\n".join(lines)


MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _parse_date(text: str, today) -> tuple[str | None, str]:
    """
    Try to parse a leading date from `text`. Returns (YYYY-MM-DD, remainder)
    or (None, text) if no date is at the front.

    Accepted forms:
      2026-08-01                    ISO
      1st Aug   / 1 Aug             day + month
      Aug 1st   / Aug 1             month + day
      1 Aug 2026 / Aug 1 2026       explicit year (any of the above)
    """
    text = text.strip()

    # ISO first
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            datetime(y, mo, d)
            return f"{y:04d}-{mo:02d}-{d:02d}", text[m.end():].lstrip()
        except ValueError:
            return None, text

    # "1st Aug" / "1 Aug" / "1st Aug 2026"
    m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)"
                 r"(?:\s+(\d{4}))?\b", text)
    if m:
        d = int(m.group(1))
        mo = MONTHS.get(m.group(2).lower()[:3])
        y = int(m.group(3)) if m.group(3) else today.year
        if mo:
            try:
                datetime(y, mo, d)
                return f"{y:04d}-{mo:02d}-{d:02d}", text[m.end():].lstrip()
            except ValueError:
                return None, text

    # "Aug 1st" / "Aug 1" / "Aug 1 2026"
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?"
                 r"(?:\s+(\d{4}))?\b", text)
    if m:
        mo = MONTHS.get(m.group(1).lower()[:3])
        if mo:
            d = int(m.group(2))
            y = int(m.group(3)) if m.group(3) else today.year
            try:
                datetime(y, mo, d)
                return f"{y:04d}-{mo:02d}-{d:02d}", text[m.end():].lstrip()
            except ValueError:
                return None, text

    return None, text


def _parse_dow(text: str) -> tuple[str | None, str]:
    """Try to parse a leading day-of-week (Sat, Sun...)."""
    m = re.match(r"^(mon|tue|wed|thu|fri|sat|sun)\w*\b", text, re.I)
    if m:
        return m.group(1).title(), text[m.end():].lstrip()
    return None, text


def _parse_time(text: str) -> tuple[str | None, str, bool]:
    """
    Parse a time. Accepts `8pm`, `8:30pm`, `20:00`, `8`, `20`, `12AM`, `12PM`.
    Returns (HH:MM, remainder, had_ampm) or (None, text, False).
    """
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text.strip(), re.I)
    if not m:
        return None, text, False
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    had_ampm = bool(ampm)
    if ampm == "am":
        if h == 12:
            h = 0
    elif ampm == "pm":
        if h != 12:
            h += 12
    if not (0 <= h <= 24 and 0 <= mi <= 59):
        return None, text, False
    return f"{h:02d}:{mi:02d}", text[m.end():].lstrip(), had_ampm


def _build_windows(date: str, start: str, end: str) -> list[dict]:
    """
    Build one or two windows from a start/end pair on a given date.
    Handles the two edge cases:
      - end wraps past midnight (start > end) -> split into two.
      - end lands exactly on midnight (end == "00:00") -> represent as
        "24:00" on the given date rather than a zero-length next-day slot.
    """
    if end == start:
        return []
    if end > start:
        return [{"date": date, "start_local": start, "end_local": end}]

    # end < start: either "10PM to 12AM" (end is midnight, no next-day segment)
    # or "10PM to 2AM" (end is early morning next day).
    from datetime import timedelta as _td
    windows = [{"date": date, "start_local": start, "end_local": "24:00"}]
    if end != "00:00":
        next_d = (datetime.fromisoformat(date).date() + _td(days=1)).isoformat()
        windows.append({"date": next_d, "start_local": "00:00", "end_local": end})
    return windows


DOW_NAMES = ["monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]


def _expand_shorthand(txt: str, today) -> str:
    """
    Rewrite the shapes people actually type into ones the parser accepts.

    Read the channel and the same three mistakes account for nearly every
    rejected message:

      "every day 9pm to 6am"     -- two players independently typed the
                                    SAME 200-character line instead,
                                    repeating one window seven times,
                                    because repetition was the only way
                                    to say it. Both copy-pasted it from
                                    a third player. That is not a user
                                    error, it is a missing feature.
      "aug7 6pm to 10pm"         -- no space after the month.
      "Fri Sat 20-23"            -- several days sharing one window.

    Expanding here rather than in the parser keeps one code path for the
    real work: everything below still sees plain "<date> <start> to <end>"
    entries, so the timezone and midnight-wrap logic is untouched.
    """
    from datetime import timedelta as _td

    # "aug7" -> "aug 7". Only between a month name and a digit, so a bare
    # "8pm" is never touched.
    txt = re.sub(r"\b(" + "|".join(MONTHS) + r")(\d{1,2})\b",
                 r"\1 \2", txt, flags=re.I)

    # "every day 9pm-6am" / "daily 9pm-6am" / "all week 9pm-6am" / "everyday"
    # -> the same window on each of the next 7 days, dated explicitly.
    m = re.match(r"^\s*(?:every\s*day|everyday|daily|all\s*week|"
                 r"every\s*night|each\s*day)\s+(.*)$", txt, re.I)
    if m:
        window = m.group(1).strip()
        days = [(today + _td(days=i)).strftime("%b %d") for i in range(7)]
        return ", ".join(f"{d} {window}" for d in days)

    # "Mon Wed Fri 8pm-11pm" or "Fri, Sat 20-23" -> one entry per day.
    # Requires 2+ day names up front so a normal "Sat 8pm-10pm" is untouched.
    abbr = {d[:3]: d for d in DOW_NAMES}
    m = re.match(r"^\s*((?:(?:" + "|".join(abbr) + r")[a-z]*[\s,]+){2,})(.*)$",
                 txt, re.I)
    if m and m.group(2).strip():
        names = re.findall(r"[A-Za-z]+", m.group(1))
        window = m.group(2).strip()
        got = [abbr[n.lower()[:3]] for n in names if n.lower()[:3] in abbr]
        if len(got) >= 2:
            return ", ".join(f"{d} {window}" for d in got)

    return txt


def _parse_avail(args: str, today=None,
                 dropped: list | None = None) -> tuple[str | None, list[dict], str | None]:
    """
    Parse `!avail [R#] <entry>[, <entry>]*` where each entry is:
        <date | day-of-week> <start> [to | -] <end>
    with start/end in 12h (8pm) or 24h (20:00). Multiple entries via comma.

    Returns (round_id_or_None, windows_list, err_or_None).
    """
    txt = args.strip()
    today = today or datetime.now(timezone.utc).date()

    round_id = None
    m = re.match(r"^\s*(R\d+)\b\s*", txt, re.I)
    if m:
        round_id = m.group(1).upper()
        txt = txt[m.end():]
    if not txt:
        return round_id, [], "Format: `!avail 1st Aug 8PM to 10PM`"

    txt = _expand_shorthand(txt, today)

    # Split on commas / semicolons / ' and ' / ' or '. NOT on '-' -- that is
    # the time separator. 'or' is here because people write a day's two
    # windows as "6pm to 10pm or 12am to 6am", which read as one broken
    # entry and failed the whole message.
    chunks = re.split(r"\s*(?:,|;|\band\b|\bor\b)\s*", txt, flags=re.I)
    windows: list[dict] = []
    last_date = last_day = None

    for c in chunks:
        c = c.strip()
        if not c:
            continue

        # Prefer date; fall through to day-of-week if no date parses.
        date, rest = _parse_date(c, today=today)
        day = None
        if not date:
            day, rest = _parse_dow(c)
        if not (date or day):
            # A second window for the same day, written without repeating
            # the date: "aug 7 6pm to 10pm or 12am to 6am". The date is
            # simply carried over from the previous entry, which is what
            # the writer meant and what a human reader assumes. Only ever
            # inherits within one message, so a genuinely dateless message
            # still fails loudly on its first entry.
            if last_date or last_day:
                date, day, rest = last_date, last_day, c
            else:
                return round_id, [], (
                    f"Couldn't parse date in `{c}`. Try `1st Aug 8PM to 10PM`, "
                    f"`Sat 8pm-10pm`, or `every day 9pm to 6am`.")
        last_date, last_day = date, day

        # Start time
        start, rest, start_had_ampm = _parse_time(rest)
        if not start:
            return round_id, [], f"Couldn't parse start time in `{c}`."

        # Range separator: 'to', '-', '–' (en-dash), '—' (em-dash), 'until'.
        rest = re.sub(r"^\s*(?:to|until|-|–|—)\s*", "", rest, flags=re.I)

        end, rest, end_had_ampm = _parse_time(rest)
        if not end:
            return round_id, [], f"Couldn't parse end time in `{c}`."

        # Common typo fix: `8-10pm` -> both PM. If start had no am/pm marker
        # but end did (PM), and start's raw hour is <= 12, treat start as PM.
        if not start_had_ampm and end_had_ampm and end.endswith("00") is False:
            pass
        if not start_had_ampm and end_had_ampm:
            sh = int(start.split(":")[0])
            if 0 < sh <= 11:
                # Reparse: add 12 hours
                sh += 12
                start = f"{sh:02d}:{start.split(':')[1]}"

        # Dates in the past are DROPPED, not fatal.
        #
        # People post the week as one line -- "aug 4 9pm to 6am, aug 5 ...,
        # aug 10 9pm to 6am" -- and re-post it a day or two later. The first
        # date has gone by, and refusing the whole message threw away the
        # six good days with it. That is exactly what happened to
        # one2oneonly on 2026-08-05: a complete week lost to one stale date,
        # and the only reason we know is that the rewrite queue kept it.
        # Callers pass `dropped` so the reply can say which days went and
        # nothing disappears quietly.
        if date and date < today.isoformat():
            if dropped is not None:
                dropped.append(date)
            continue

        if date:
            windows.extend(_build_windows(date, start, end))
        else:
            windows.append({"day": day, "start_local": start, "end_local": end})

    if not windows:
        if dropped is not None and dropped:
            return round_id, [], (
                f"Every date in that message has already passed "
                f"(`{'`, `'.join(sorted(set(dropped)))}`). Post the days "
                f"still to come and I'll take them.")
        return round_id, [], "No usable windows found."
    return round_id, windows, None


def do_avail(args: str, uname: str, uid: str, dp: dict,
             msg_id: str | None = None) -> str:
    """
    !avail <windows>  OR  !avail clear
    New model: writes to sched.availability[player], one entry per player
    per week. Reused across every pairing. Not tied to a scheduling round.
    """
    if args.strip().lower() in ("clear", "reset"):
        return _do_avail_clear(uname, uid, dp)

    dropped: list[str] = []
    _, windows, err = _parse_avail(args, dropped=dropped)   # round_id ignored now
    if err or not windows:
        return _queue_for_rewrite(args, uname, uid, dp,
                                  err or "No usable windows found.", msg_id)

    player = player_for_author(uid, uname, dp)
    if not player:
        return (f"I don't know who you are on the league. "
                f"Run `!register YourNick YourZone` first — e.g. "
                f"`!register Cpx PKT`.")

    ptz = load_players_tz()
    tz = ptz["players"].get(player)
    if not tz:
        return (f"You (`{player}`) don't have a timezone declared yet. "
                f"Post `!tz PKT` (or your zone).")

    teams = load_teams()
    on_team = None
    for t in teams["teams"]:
        for r in t.get("roster", []):
            if r["name"] == player:
                on_team = t
                break
        if on_team:
            break
    if not on_team:
        return (f"You (`{player}`) aren't on any team. Only rostered players' "
                f"availability counts. Ask a captain to add you.")

    sched = load_scheduling()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sched.setdefault("availability", {})[player] = {
        "declared_at":    now,
        "declared_in_tz": tz,
        "windows":        windows,
    }
    save_json(SCHEDULING, sched)

    # Preview per timezone so the player sees what we understood.
    week_of = sched.get("week_of")
    preview = []
    for w in windows:
        try:
            if "date" in w:
                s_utc = tz_map.to_utc_from_date(tz, w["date"], w["start_local"])
                e_utc = tz_map.to_utc_from_date(tz, w["date"], w["end_local"])
                label = w["date"]
            else:
                s_utc = tz_map.to_utc(tz, w["day"], w["start_local"], week_of)
                e_utc = tz_map.to_utc(tz, w["day"], w["end_local"],   week_of)
                label = w["day"]
            tz_short = tz.split("/")[-1].replace("_", " ")
            # Print the end's date too when the window crosses midnight UTC,
            # otherwise a perfectly good window renders as "23:00–22:00" and
            # reads as backwards. vAnzO's whole week looked broken this way.
            e_fmt = (e_utc.strftime('%H:%M') if e_utc.date() == s_utc.date()
                     else e_utc.strftime('%a %d %b %H:%M'))
            preview.append(f"  {label} {w['start_local']}–{w['end_local']} {tz_short}"
                           f"  →  {s_utc.strftime('%a %d %b %H:%M')}–{e_fmt} UTC")
        except Exception as e:
            preview.append(f"  (couldn't render: {e})")

    body = "\n".join(preview) if preview else ""

    # Readiness pulse for the poster's team + @-ping missing members.
    readiness = find_slot.team_readiness(sched["availability"], teams)
    tail = _readiness_tail(on_team, readiness, sched, teams, ptz, dp)

    # Dropped days are stated, never swallowed. Somebody who re-posts last
    # week's line needs to see which days did not count.
    skipped = ""
    if dropped:
        days = "`, `".join(sorted(set(dropped)))
        skipped = (f"\n_(Skipped `{days}` — already past. Everything else "
                   f"above is saved.)_")

    return f"Got it, **{player}**:\n```\n{body}\n```{skipped}\n{tail}"


def _queue_for_rewrite(args: str, uname: str, uid: str, dp: dict,
                       err: str, msg_id: str | None) -> str:
    """
    The parser said no. Keep the message, tell the truth about what
    happens next, and invite plain English rather than a third attempt at
    guessing the grammar.

    Deliberately still leads with the parser's specific complaint: for
    someone who made a small typo, that one line is the whole fix and a
    queue would just be slower.
    """
    player = player_for_author(uid, uname, dp)
    n = enqueue_pending(args, uname, uid, player, err, msg_id)
    return (
        f"{err}\n"
        f"I've kept what you wrote (queued as **#{n}**) — nothing is lost, "
        f"and you don't have to fight the format.\n"
        f"**Just say it in plain English** and I'll read it properly, e.g.\n"
        f"```\n!avail every night after 9 my time, and all day Sunday\n```\n"
        f"I'll reply here with exactly what I understood before it counts."
    )


def _do_avail_clear(uname, uid, dp) -> str:
    player = player_for_author(uid, uname, dp)
    if not player:
        return "I don't know who you are on the league yet."
    sched = load_scheduling()
    avail = sched.setdefault("availability", {})
    if player not in avail:
        return f"Nothing to clear — you (**{player}**) hadn't posted this week."
    del avail[player]
    save_json(SCHEDULING, sched)
    return f"Cleared **{player}**'s availability for this week."


def _readiness_tail(on_team: dict, readiness: dict, sched: dict,
                    teams: dict, ptz: dict, dp: dict) -> str:
    """
    Build the after-!avail message: team pulse + @-ping missing members +
    auto-propose top slots for any pair that just hit 100%.
    """
    tid = on_team["id"]
    info = readiness[tid]
    got, tot = len(info["responded"]), len(info["responded"]) + len(info["missing"])
    lines = []

    # canonical -> friendly nickname map for THIS team, so unregistered
    # players get shown as "Beetlebum" not "beetlebum [FUBU]".
    friendly = {}
    for r in on_team.get("roster", []):
        friendly[r["name"]] = (r.get("aka") or [None])[0] or r["name"]

    if info["missing"]:
        # Reverse mapping: nick -> discord_id
        nick_to_uid = {n: u for u, n in dp.get("players", {}).items()}
        pings, unregistered = [], []
        for m in info["missing"]:
            duid = nick_to_uid.get(m)
            if duid:
                pings.append(f"<@{duid}>")
            else:
                unregistered.append(friendly.get(m, m))
        lines.append(f"**{on_team['name']}** {got}/{tot} ready this week.")
        if pings:
            lines.append(f"Still waiting on: {' '.join(pings)}")
        if unregistered:
            lines.append(f"Not yet registered: {', '.join(f'`{n}`' for n in unregistered)}")
    else:
        lines.append(f"🎉 **{on_team['name']}** is 100% ready.")
        # Check if any pair now has BOTH teams at 100% -> auto-propose slots.
        for other_tid, other_info in readiness.items():
            if other_tid == tid:
                continue
            if other_info["pct"] >= 1.0:
                lines.append(_propose_pair(tid, other_tid, sched, teams, ptz))

    return "\n".join(lines)


def _propose_pair(a: int, b: int, sched: dict, teams: dict, ptz: dict) -> str:
    """Compute + format top 3 slots for a fully-ready pair."""
    rosters = find_slot.team_rosters(teams, [a, b])
    grid    = find_slot.compute_availability(sched.get("availability", {}), ptz,
                                              sched.get("week_of"))
    slots   = find_slot.rank_slots(grid, rosters, top=3)
    team_names = {t["id"]: t["name"] for t in teams["teams"]}

    if not slots:
        return (f"\n**{team_names[a]} vs {team_names[b]}** — both ready, but "
                f"no overlapping windows found. Someone update their !avail.")

    header = f"\n**{team_names[a]} vs {team_names[b]}** — both ready! Top slots:"
    body = []
    for i, s in enumerate(slots):
        # Local renderings on one line each
        line = (f"  [{i+1}] `{s['start_utc'].strftime('%a %d %b · %H:%M UTC')}` "
                f"({s['duration_min']} min · {s['total']} available)")
        body.append(line)
    body.append(f"An approver can lock one: `!confirm {a} vs {b} 1`")
    return "\n".join([header] + body)


def _parse_pair(text: str) -> tuple[int, int] | None:
    """Parse '1 vs 3', 'Team 1 vs Team 3', '1v3' -> (1, 3) or None."""
    m = re.match(r"^\s*(?:team\s*)?(\d)\s*(?:vs|v)\s*(?:team\s*)?(\d)\s*",
                 text.strip(), re.I)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if a != b else None


def do_find(args: str) -> str:
    """
    !find                -- ranked slots for every pair of teams (top 2 each).
    !find 1 vs 3         -- ranked slots for a specific pair (top 3).
    """
    sched   = load_scheduling()
    teams   = load_teams()
    tz_data = load_players_tz()
    avail   = sched.get("availability", {})
    week_of = sched.get("week_of")

    pair = _parse_pair(args) if args.strip() else None

    if pair:
        pairs = [pair]
        top   = 3
    else:
        team_ids = sorted(t["id"] for t in teams["teams"])
        pairs = [(a, b) for i, a in enumerate(team_ids) for b in team_ids[i + 1:]]
        top   = 2   # keep each pair short in the "all pairs" report

    if not avail:
        return ("No availability posted this week yet. Everyone: "
                "`!avail <date> <HH-HH>`.")

    team_names = {t["id"]: t["name"] for t in teams["teams"]}
    grid = find_slot.compute_availability(avail, tz_data, week_of)

    chunks = []
    for a, b in pairs:
        rosters = find_slot.team_rosters(teams, [a, b])
        slots   = find_slot.rank_slots(grid, rosters, top=top)
        header = f"**{team_names[a]} vs {team_names[b]}**"
        if not slots:
            chunks.append(f"{header} — no overlapping windows.")
            continue
        lines = [header]
        for i, s in enumerate(slots):
            playable = all(n >= 3 for n in s["per_team"].values())
            mark = "🟢" if playable else "⚪"
            lines.append(
                f"  {mark} [{i+1}] `{s['start_utc'].strftime('%a %d %b · %H:%M UTC')}` "
                f"({s['duration_min']} min · {s['total']}/{sum(len(n) for n in rosters.values())} avail · "
                f"T{a}:{s['per_team'][a]}/T{b}:{s['per_team'][b]})")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def _same_fixture(entry: dict, a: int, b: int,
                  week_of: str | None, start_utc: str) -> bool:
    """
    Is `entry` the same booking as the one being confirmed?

    Pairing is compared unordered, because `!confirm 3 vs 1` and
    `!confirm 1 vs 3` name the same game.

    WEEK, NOT TIME, is the identity where a week is known. Two teams meet
    once per week, so re-confirming them into a *different* slot is a
    reschedule and must replace -- keying on start time instead would
    leave the abandoned slot sitting on the site as a second fixture,
    which is the same bug in a different shape.

    `upcoming` survives !newweek, so entries from earlier weeks are
    legitimately separate and must NOT be replaced -- hence the week
    check rather than pairing alone. Entries written before week_of was
    recorded have none, so those fall back to matching on start time,
    which is exact and cannot swallow an unrelated fixture.
    """
    if sorted(entry.get("match_up") or []) != sorted([a, b]):
        return False
    entry_week = entry.get("week_of")
    if entry_week and week_of:
        return entry_week == week_of
    return entry.get("start_utc") == start_utc


def do_confirm(args: str, uname: str) -> str:
    """
    !confirm 1 vs 3 N  -- lock slot N for the given team pair.

    Legacy syntax `!confirm R# N` is silently mapped to the new form by
    treating the R# as a pair fetched from archive (unused in practice
    now; kept only so existing chat history doesn't error).
    """
    m = re.match(r"^\s*(?:team\s*)?(\d)\s*(?:vs|v)\s*(?:team\s*)?(\d)\s+(\d+)\s*$",
                 args.strip(), re.I)
    if not m:
        return "Format: `!confirm 1 vs 3 N` — e.g. `!confirm 1 vs 3 2`"
    a, b, idx = int(m.group(1)), int(m.group(2)), int(m.group(3))

    sched   = load_scheduling()
    teams   = load_teams()
    tz_data = load_players_tz()
    avail   = sched.get("availability", {})
    week_of = sched.get("week_of")

    rosters = find_slot.team_rosters(teams, [a, b])
    grid    = find_slot.compute_availability(avail, tz_data, week_of)
    slots   = find_slot.rank_slots(grid, rosters, top=max(5, idx))
    if not slots:
        return "No slots available for that pair yet."
    if idx < 1 or idx > len(slots):
        return f"Slot {idx} out of range — {len(slots)} slot(s) found."
    chosen = slots[idx - 1]
    team_names = {t["id"]: t["name"] for t in teams["teams"]}

    payload = find_slot.slots_as_json([a, b], week_of, [chosen], rosters)
    upcoming_entry = {
        "match_up":       [a, b],
        "match_up_names": [team_names[a], team_names[b]],
        "week_of":        week_of,
        "start_utc":      payload["slots"][0]["start_utc"],
        "end_utc":        payload["slots"][0]["end_utc"],
        "duration_min":   payload["slots"][0]["duration_min"],
        "renderings":     payload["slots"][0]["renderings"],
        "per_team":       payload["slots"][0]["per_team"],
        "missing":        payload["slots"][0]["missing"],
        "confirmed_by":   uname,
        "confirmed_at":   datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    # Re-confirming a fixture REPLACES it; it does not add a second one.
    #
    # Running !confirm twice for the same pairing is normal, not a mistake:
    # people re-run it after somebody posts availability so the booking
    # picks up the fuller roster. Appending unconditionally turned that
    # into two bookings for one game on the Coord tab -- and the stale one
    # carried the OLD `missing` list, so the wrong roster was on display
    # with nothing to say which row was current. That happened on
    # 2026-08-02: Team 1 vs Team 3 was confirmed at 23:54 and again at
    # 00:58, and the site showed the fixture twice.
    upcoming = sched.setdefault("upcoming", [])
    idx_old  = next((i for i, e in enumerate(upcoming)
                     if _same_fixture(e, a, b, week_of, upcoming_entry["start_utc"])), None)
    if idx_old is None:
        upcoming.append(upcoming_entry)
        verb = "MATCH SCHEDULED"
    else:
        prior = upcoming[idx_old]
        upcoming[idx_old] = upcoming_entry
        verb = "MATCH RESCHEDULED"
    save_json(SCHEDULING, sched)

    tz_lines = "\n".join(f"  {r['zone_label']}: {r['local'][11:16]}"
                         for r in payload["slots"][0]["renderings"])
    moved = ""
    if verb == "MATCH RESCHEDULED" and prior.get("start_utc") != upcoming_entry["start_utc"]:
        moved = f"Moved from `{str(prior.get('start_utc'))[:16].replace('T', ' ')} UTC`.\n"
    return (f"✅ **{verb}**\n"
            f"{team_names[a]} vs {team_names[b]}\n"
            f"`{chosen['start_utc'].strftime('%a %d %b · %H:%M UTC')}` "
            f"(window: {chosen['duration_min']} min)\n"
            f"{moved}"
            f"```\n{tz_lines}\n```\n"
            f"Added to the site: https://muhammadut.github.io/total-dota-lobby-stats/#coord")


# ── Main loop ─────────────────────────────────────────────────────────

def poll_once(token: str, channel: str, allow, args) -> int:
    """
    Process every unread command once. Returns how many were answered.

    Raises DiscordHTTPError on API failure rather than exiting, so the
    watch loop can decide whether it is worth retrying.
    """
    after = None if args.all else (MARK.read_text(encoding="utf-8").strip()
                                   if MARK.exists() else None)
    q = f"/channels/{channel}/messages?limit=50"
    if after:
        q += "&after=" + after
    msgs = sorted(api_raw(q, token), key=lambda m: int(m["id"]))
    return _handle(msgs, token, channel, allow, args)


def watch(token: str, channel: str, allow, args) -> int:
    """
    Poll forever so commands are answered as they are typed.

    Discord has no way to push to a plain-REST client, so "instant" here
    means a short poll: at the default 3s the worst case a user waits is
    3s and the average is 1.5s, which in a chat window reads as instant.
    One request per interval is 0.33/s against a global budget of 50/s,
    so the cost of being responsive is negligible.

    THE POINT OF THIS FUNCTION IS THAT IT DOES NOT DIE. A one-shot script
    can exit on any error and a human sees it. A listener that exits on a
    dropped packet just stops answering, and nobody finds out until
    somebody complains that the bot ignored them -- which is exactly the
    failure that started this. So transient errors back off and retry;
    only genuinely unrecoverable ones (bad token, missing channel) stop
    the loop, and those stop it loudly.
    """
    # Python block-buffers stdout when it is a pipe rather than a terminal,
    # so a backgrounded watcher writes NOTHING to its log for the first 8KB
    # -- it looks dead exactly when you most want to know it is alive.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:                       # pragma: no cover
        pass

    delay, backoff = args.interval, args.interval
    print(f"  watching every {delay}s — Ctrl+C to stop")
    while True:
        try:
            n = poll_once(token, channel, allow, args)
            if n:
                print(f"    ({n} handled)")
            backoff = delay                      # healthy: reset the penalty
        except DiscordHTTPError as e:
            if not e.transient:
                print(f"\n  STOPPING — {e}", file=sys.stderr)
                print("  This will not fix itself: check the token, the channel "
                      "id, and the bot's channel permissions.", file=sys.stderr)
                return 1
            backoff = min(backoff * 2, 300)
            print(f"    {e.code} — retrying in {backoff}s", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            # Network down, DNS failure, laptop asleep. Always temporary.
            backoff = min(backoff * 2, 300)
            print(f"    network: {e} — retrying in {backoff}s", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n  stopped")
            return 0
        except Exception as e:
            # A bug in one command handler must not take the bot down with
            # it; the message that triggered it is already past the
            # watermark, so the loop moves on rather than wedging on it.
            backoff = min(backoff * 2, 300)
            print(f"    unexpected {type(e).__name__}: {e} — continuing in {backoff}s",
                  file=sys.stderr)
        try:
            time.sleep(backoff)
        except KeyboardInterrupt:
            print("\n  stopped")
            return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true", help="ignore the watermark")
    ap.add_argument("--watch", action="store_true",
                    help="stay running and answer commands as they arrive")
    ap.add_argument("--interval", type=float, default=3.0, metavar="SEC",
                    help="seconds between polls in --watch mode (default 3)")
    args = ap.parse_args()

    token, channel = league_channel()
    allow = approvers()

    if args.watch:
        if args.all:
            return ap.error("--all replays the whole channel; not for --watch.")
        return watch(token, channel, allow, args)

    try:
        poll_once(token, channel, allow, args)
    except DiscordHTTPError as e:
        return _explain(e)
    return 0


def _explain(e: DiscordHTTPError) -> int:
    """One-shot mode: name the broken thing rather than dumping a status."""
    msg = {401: "the bot token is wrong or was regenerated",
           403: "the bot cannot read that channel — give it View Channel "
                "+ Read Message History",
           404: "wrong channel id, or the bot was never added to that server",
           429: "rate limited; wait and retry"}.get(e.code, e.body)
    print(f"  {e.code} — {msg}", file=sys.stderr)
    return 1


def _handle(msgs, token, channel, allow, args) -> int:
    """Dispatch each message to its command and reply. Advances the mark."""
    newest, acted = None, 0
    dp = load_discord_players()

    for m in msgs:
        newest = m["id"]
        author = m.get("author") or {}
        if author.get("bot"):
            continue
        text = (m.get("content") or "").strip()
        if not text or not text.startswith("!"):
            continue

        uid, uname = author.get("id"), author.get("username", "?")
        privileged = (not allow) or (str(uid) in allow)

        # Dispatch. First word is the command.
        head, _, tail = text[1:].partition(" ")
        cmd = head.lower()
        reply = None

        try:
            if cmd == "help":
                reply = do_help()
            elif cmd == "status":
                reply = do_status()
            elif cmd in ("who", "registered", "players"):
                reply = do_who()
            elif cmd == "register":
                reply = do_register(tail, uname, uid, dp, privileged)
                dp = load_discord_players()   # refresh in case do_register wrote
            elif cmd == "tz":
                reply = do_tz(tail, uname, uid, dp, privileged)
            elif cmd == "newweek":
                reply = ("Only approvers can start a new week." if not privileged
                         else do_newweek(uname))
            elif cmd == "readiness":
                reply = do_readiness()
            elif cmd == "schedule":
                reply = ("`!schedule` isn't needed anymore — just post your "
                         "availability with `!avail`, and I'll suggest matches "
                         "when a pair of teams are both ready.")
            elif cmd == "confirm":
                reply = ("Only approvers can confirm a slot." if not privileged
                         else do_confirm(tail, uname))
            elif cmd == "avail":
                reply = do_avail(tail, uname, uid, dp, msg_id=m["id"])
            elif cmd == "find":
                reply = ("Only approvers can run the finder." if not privileged
                         else do_find(tail))
            # Unknown ! commands are ignored silently -- reduces noise from
            # other bots that also use ! prefixes.
        except Exception as e:
            reply = f"Command failed: `{type(e).__name__}: {e}`"

        if reply:
            acted += 1
            if args.dry_run:
                print(f"  [{uname}] {text!r}\n    -> {reply.splitlines()[0][:100]}")
            else:
                post_msg(token, channel, reply)
                print(f"  [{uname}] {text!r} -> replied")

    if newest and not args.dry_run:
        MARK.write_text(newest, encoding="utf-8")
    # In --watch this runs every few seconds, so only speak when something
    # actually happened; a heartbeat line per poll would bury the real ones.
    if acted or not getattr(args, "watch", False):
        print(f"  {acted} command(s) handled"
              + (f", watermark {newest}" if newest and not args.dry_run else ""))
    return acted


if __name__ == "__main__":
    sys.exit(main())
