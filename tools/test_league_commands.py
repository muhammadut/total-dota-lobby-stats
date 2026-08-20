"""
Stress-test league commands with realistic user typos and variations.

    python tools/test_league_commands.py

Not a real test framework (no unittest, no pytest) -- just a script that
prints PASS/FAIL for each case so we can see coverage at a glance and
catch parser rigidity before real users hit it.

BACKS UP AND RESTORES real state before/after each run. Do not remove
the atexit hook -- an interrupted run without it leaves scheduling.json
and the discord player mapping wiped, which will destroy any live
availability data. Learned the hard way when a re-run at 21:00 wiped
Musa's Aug 4-10 nightly schedule.
"""

import atexit
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import discord_league as L
import tz_map
from find_slot import team_readiness


# ── Back up real state at import time; restore on exit (even on error) ──
_BACKUP_PATHS = [L.SCHEDULING, L.DISCORD_PLAYERS, L.PLAYERS_TZ, L.TEAMS_FILE,
                 L.AVAIL_PENDING]
# None means "did not exist before the run" -- restore has to DELETE those,
# not skip them, or a test run leaves a queue of fake pending entries behind
# and !status starts reporting imaginary people waiting.
_BACKUP = {p: (p.read_text(encoding="utf-8") if p.exists() else None)
           for p in _BACKUP_PATHS}

def _restore():
    for p, text in _BACKUP.items():
        if text is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(text, encoding="utf-8")
    print("  [restored real state]")

atexit.register(_restore)

# Fake Discord users we'll pretend to be during tests
UID_UT   = "539898067957186560"   # approver
UID_HURR = "222000000000000002"
UID_STOIC = "111000000000000001"
UID_UGI  = "444000000000000004"


passes = 0
fails  = 0
failures = []


def reset_state():
    """Wipe scheduling.availability + discord_players + players_tz."""
    L.save_json(L.DISCORD_PLAYERS, {"_comment": ["stress test"], "players": {}})
    L.save_json(L.PLAYERS_TZ,      {"_comment": ["stress test"], "players": {}})
    sched = L.load_scheduling()
    sched["availability"] = {}
    L.save_json(L.SCHEDULING, sched)
    L.save_json(L.AVAIL_PENDING, {"pending": [], "resolved": []})


def check(name, condition, actual=None):
    global passes, fails
    if condition:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails += 1
        failures.append((name, actual))
        print(f"  FAIL  {name}")
        if actual is not None:
            first = str(actual).split("\n")[0][:140]
            print(f"        got: {first}")


def group(title):
    print(f"\n═══ {title} ═══")


# ═════════════════════════════════════════════════════════════════════
#  Timezone resolution — the piece that trips users up most
# ═════════════════════════════════════════════════════════════════════
group("tz_map.resolve — variants a human might type")

TZ_CASES = [
    ("PKT",              "Asia/Karachi"),
    ("pkt",              "Asia/Karachi"),
    ("Pakistan",         "Asia/Karachi"),
    ("Karachi",          "Asia/Karachi"),
    ("PKT time",         "Asia/Karachi"),
    ("PKT timezone",     "Asia/Karachi"),
    ("ET",               "America/New_York"),
    ("Eastern",          "America/New_York"),
    ("Eastern time",     "America/New_York"),
    ("EST",              "America/New_York"),
    ("EDT",              "America/New_York"),
    ("PT",               "America/Los_Angeles"),
    ("Pacific",          "America/Los_Angeles"),
    ("Pacific time",     "America/Los_Angeles"),
    ("AST",              "Asia/Riyadh"),
    ("Saudi",            "Asia/Riyadh"),
    ("Riyadh",           "Asia/Riyadh"),
    ("CET",              "Europe/Berlin"),
    ("Berlin",           "Europe/Berlin"),
    ("GMT",              "Europe/London"),
    ("GMT time",         "Europe/London"),
    ("BST",              "Europe/London"),
    ("BST time",         "Europe/London"),
    ("London",           "Europe/London"),
    ("IST",              "Asia/Kolkata"),
    ("India",            "Asia/Kolkata"),
    ("+5",               "Etc/GMT-5"),
    ("-4",               "Etc/GMT+4"),
    ("UTC+5",            "Etc/GMT-5"),
    ("GMT+5",            "Etc/GMT-5"),
    ("UTC-4",            "Etc/GMT+4"),
    ("GMT+5 time",       "Etc/GMT-5"),
    ("Asia/Karachi",     "Asia/Karachi"),
    ("America/New_York", "America/New_York"),
]

for input_str, want in TZ_CASES:
    try:
        got = tz_map.resolve(input_str)
        check(f"resolve({input_str!r})", got == want,
              f"expected {want!r}, got {got!r}")
    except Exception as e:
        check(f"resolve({input_str!r})", False, f"raised {type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════════════════
#  !register — self-service, aka lookup, first-claim-wins
# ═════════════════════════════════════════════════════════════════════
group("!register — Nick + Zone")

reset_state()
dp = L.load_discord_players()

# Basic register with aka lookup
r = L.do_register("Cpx PKT", "ut70", UID_UT, dp, privileged=True)
check("register 'Cpx PKT' resolves to Mandark [<MC>]",
      "Mandark [<MC>]" in r, r)

dp = L.load_discord_players()

# Same user re-registers same nick — should update tz, not refuse
r = L.do_register("Cpx Eastern", "ut70", UID_UT, dp, privileged=True)
check("re-register same nick same user — accepted",
      "" in r and "already" not in r.lower(), r)

dp = L.load_discord_players()

# Same nick different user — refuse
r = L.do_register("Cpx PKT", "intruder", UID_HURR, dp, privileged=False)
check("different user tries to claim same nick — refused",
      "already registered by another" in r.lower(), r)

dp = L.load_discord_players()

# Multi-word tz
r = L.do_register("Hurr GMT time", "hurrali", UID_HURR, dp, privileged=False)
check("register with 'GMT time' (multi-word tz)",
      "HURR [PK_]" in r and "Europe/London" in r, r)

dp = L.load_discord_players()

# Unregistered nick lands in Open Pool
r = L.do_register("SomeNewPerson PKT", "newperson", UID_UGI, dp, privileged=False)
check("register unrostered nick lands in Open Pool",
      "Open Pool" in r, r)

# Missing tz
dp = L.load_discord_players()
r = L.do_register("Cpx", "ut70", UID_UT, dp, privileged=True)
check("register with only 1 arg — error message",
      "timezone" in r.lower() or "need" in r.lower(), r)

# Empty
r = L.do_register("", "ut70", UID_UT, dp, privileged=True)
check("register with empty args — help message",
      "Format" in r or "format" in r, r)


# ═════════════════════════════════════════════════════════════════════
#  !avail — date parser, midnight, past-date rejection, multi-window
# ═════════════════════════════════════════════════════════════════════
group("!avail — date & time parsing")

reset_state()

# Register two players from Team 1 (UT and Hurr) so avail is accepted
dp = L.load_discord_players()
L.do_register("UT ET", "ut70", UID_UT, dp, privileged=True)
dp = L.load_discord_players()
L.do_register("Hurr PKT", "hurrali", UID_HURR, dp, privileged=False)
dp = L.load_discord_players()

today = date(2026, 8, 2)

AVAIL_CASES = [
    ("Aug 3 8PM to 10PM",                   True,  "20:00"),
    ("3rd Aug 8PM to 10PM",                 True,  "20:00"),
    ("Aug 3rd 8PM to 10PM",                 True,  "20:00"),
    ("3 Aug 8pm-10pm",                      True,  "20:00"),
    ("Aug 3 8-10pm",                        True,  "20:00"),  # both PM heuristic
    ("2026-08-03 20:00-22:00",              True,  "20:00"),  # ISO
    ("Aug 3 8pm to 10pm, Aug 4 9pm-11pm",   True,  "20:00"),  # multiple
    ("Aug 3 10PM to 2AM",                   True,  "22:00"),  # crosses midnight
    ("Aug 3 10PM to 12AM",                  True,  "22:00"),  # ends at midnight
    ("Aug 5 20:00 to 23:00",                True,  "20:00"),  # 24h with 'to'
    ("Aug 5 8pm until 10pm",                True,  "20:00"),  # 'until'
    ("1st Aug 8PM to 10PM",                 False, None),     # past date
    ("Aug 3",                               False, None),     # no time
    ("Aug 3 8PM",                           False, None),     # no end time
    ("garbage input",                       False, None),     # nonsense
]

for input_str, should_succeed, expected_start in AVAIL_CASES:
    _, windows, err = L._parse_avail(input_str, today=today)
    if should_succeed:
        ok = err is None and windows and windows[0].get("start_local") == expected_start
        check(f"parse_avail {input_str!r}", ok, err or windows)
    else:
        check(f"parse_avail {input_str!r} → rejected", err is not None, err)


# End-to-end do_avail
reset_state()
dp = L.load_discord_players()
L.do_register("UT ET", "ut70", UID_UT, dp, privileged=True)
dp = L.load_discord_players()

# do_avail goes through the REAL clock, unlike the _parse_avail cases
# above which pin `today`. It was written with a hardcoded "Aug 3" and
# started failing the moment the date rolled over to Aug 4 -- the parser
# correctly refuses a date in the past. Use tomorrow, so this keeps
# testing the thing it means to test on every future day.
_tmw = date.today() + timedelta(days=1)
r = L.do_avail(f"{_tmw:%b} {_tmw.day} 8PM to 10PM", "ut70", UID_UT, dp)
check("do_avail full path — preview shows date + tz + UTC",
      _tmw.isoformat() in r and "New York" in r and "UTC" in r, r)
# Which team UT is on, and how big it is, are read from teams.json rather
# than hardcoded. The captains move people between teams -- UT went from
# Team 1 to Team 2 on 2026-08-07 -- and a test that pins the answer fails
# on the roster change rather than on the behaviour it is checking.
_tj = json.loads(L.TEAMS_FILE.read_text(encoding="utf-8"))
_ut_team = next(t for t in _tj["teams"]
                if any(r["name"] == "UT" for r in t["roster"]))
check(f"do_avail readiness tail — {_ut_team['name']} pinged",
      _ut_team["name"] in r and f"1/{len(_ut_team['roster'])}" in r, r)


# ═════════════════════════════════════════════════════════════════════
#  !find — all pairs vs specific pair
# ═════════════════════════════════════════════════════════════════════
group("!find — all pairs and specific pair")

reset_state()
dp = L.load_discord_players()
L.do_register("UT ET", "ut70", UID_UT, dp, privileged=True)
dp = L.load_discord_players()
# Relative to today, not a fixed "Aug 4" — the same time bomb that broke
# the do_avail test above, left in a second place. Everything downstream
# here needs the availability to actually save.
_fs = date.today() + timedelta(days=1)
L.do_avail(f"{_fs:%b} {_fs.day} 8PM to 11PM", "ut70", UID_UT, dp)

r = L.do_find("")
# Pairs, not a constant. This read "all 6 pairs" and broke the day a fifth
# team was added, which is n*(n-1)/2 = 10 -- derive it from the roster.
# The NAMES come from the roster too: they were hard-coded as "Team 1" and
# broke the day the captains named their teams. A test that asserts a
# label the league can rename is testing the wrong thing.
_n = len(_tj["teams"])
_pairs = _n * (_n - 1) // 2
_tname = {t["id"]: t["name"] for t in _tj["teams"]}
_hdr = r.count("**")  // 2
check(f"find (no args) — lists all {_pairs} pairs",
      _hdr == _pairs, r[:200])

_1v3 = f"**{_tname[1]} vs {_tname[3]}**"
r = L.do_find("1 vs 3")
check("find '1 vs 3' — single pair",
      _1v3 in r and r.count("**") // 2 == 1, r[:200])

r = L.do_find(f"{_tname[1]} vs {_tname[3]}")
check("find 'Team 1 vs Team 3' — verbose form works", _1v3 in r, r[:200])

r = L.do_find("1v3")
check("find '1v3' — compact form works", _1v3 in r, r[:200])


# ═════════════════════════════════════════════════════════════════════
#  !readiness
# ═════════════════════════════════════════════════════════════════════
group("!status")

r = L.do_status()
check("status — no crash, shows week + team pulse",
      "week of" in r.lower() and "readiness" in r.lower(), r)


group("!readiness")

r = L.do_readiness()
check(f"readiness — shows all {len(_tj['teams'])} teams",
      all(t["name"] in r for t in _tj["teams"]), r)
check("readiness — shows the week",
      "week of" in r.lower(), r)
check("readiness — shows progress bar",
      "▓" in r or "░" in r, r)


# ═════════════════════════════════════════════════════════════════════
#  !confirm
# ═════════════════════════════════════════════════════════════════════
group("!confirm")

r = L.do_confirm("1 vs 3 1", "ut70")
# "MATCH SCHEDULED" or "MATCH RESCHEDULED" -- which one comes back depends
# on whether this pairing is already booked for the current week, and the
# real scheduling.json usually means it is. Both are success.
check("confirm '1 vs 3 1' — writes upcoming or rejects with reason",
      "MATCH" in r or "No slots" in r or "out of range" in r, r)

r = L.do_confirm("bad input", "ut70")
check("confirm bad input — error message",
      "Format" in r, r)


# ═════════════════════════════════════════════════════════════════════
#  Timezones as people actually write them
# ═════════════════════════════════════════════════════════════════════
group("timezone resolution")

check("tz — 'UK (GMT+1)' resolves (was rejected in-channel)",
      L.resolve_zone("UK (GMT+1)") == "Europe/London", L.resolve_zone("UK (GMT+1)"))
check("tz — prefers the DST-aware zone over the fixed offset",
      L.resolve_zone("UK (GMT+1)") == "Europe/London", L.resolve_zone("UK (GMT+1)"))
check("tz — bare parenthetical still works",
      L.resolve_zone("(GMT+1)") is not None, L.resolve_zone("(GMT+1)"))
check("tz — 'Eastern time'", L.resolve_zone("Eastern time") == "America/New_York")
check("tz — 'pakistan'", L.resolve_zone("pakistan") == "Asia/Karachi")
check("tz — trailing punctuation tolerated", L.resolve_zone("PKT,") == "Asia/Karachi")
check("tz — nonsense still returns None", L.resolve_zone("nonsense zone") is None)

# The name/zone split must prefer a real roster player on the left.
reset_state()
dp = L.load_discord_players()
r = L.do_register("HURR UK (GMT+1)", "hurrali", "222000000000000002", dp, False)
check("register — 'HURR UK (GMT+1)' binds HURR, not a player called 'HURR UK'",
      "HURR [PK_]" in r and "HURR UK" not in r, r)
check("register — and gets Europe/London",
      L.load_players_tz()["players"].get("HURR [PK_]") == "Europe/London",
      L.load_players_tz()["players"])

reset_state()
r = L.do_register("Rogue Agent Asia/Karachi", "x", "222000000000000009",
                  L.load_discord_players(), False)
check("register — two-word NAME still splits correctly",
      "Rogue Agent" in r and "Registered" in r, r)


# ═════════════════════════════════════════════════════════════════════
#  !avail shorthand — every one of these is a line a real player typed
#  in #dota-league-2026 and had rejected, or typed the long way because
#  the short way did not exist.
# ═════════════════════════════════════════════════════════════════════
group("!avail shorthand (regressions from the live channel)")

from datetime import date as _date
T = _date(2026, 8, 3)

def parsed(text):
    _, w, err = L._parse_avail(text, today=T)
    return w, err

# The 200-char line two players copy-pasted, and the six words that
# should replace it, must produce IDENTICAL windows.
long_form = ("aug 4 9pm to 6am, aug 5 9pm to 6am, aug 6 9pm to 6am, aug 7 9pm to 6am, "
             "aug 8 9pm to 6am, aug 9 9pm to 6am, aug 10 9pm to 6am")
w_long, e_long = parsed(long_form)
w_short, e_short = parsed("every day 9pm to 6am")
check("avail — the long repetitive form still parses", not e_long, e_long)
check("avail — 'every day' parses", not e_short, e_short)
check("avail — 'every day' yields a full week",
      len({x.get('date') for x in w_short}) >= 7, sorted({x.get('date') for x in w_short}))
check("avail — 'daily' is a synonym", not parsed("daily 9pm-6am")[1])
check("avail — 'all week' is a synonym", not parsed("all week 9pm-6am")[1])

# hurrali's line: failed on BOTH 'aug7' (no space) and 'or'.
w, e = parsed("aug7 6pm to 10 pm or 12 am to 6 am , aug 8 12pm to 1 am")
check("avail — 'aug7' without a space", not e, e)
check("avail — 'or' as a second window, inheriting the date",
      not e and len(w) >= 3, f"{len(w)} windows, err={e}")

# salman's line: two days sharing one window.
w, e = parsed("Fri Sat 20 - 23")
check("avail — 'Fri Sat 20-23' (day list)", not e and len(w) == 2, f"{len(w)}, {e}")
check("avail — 'Mon Wed Fri 8pm-11pm'", not parsed("Mon Wed Fri 8pm-11pm")[1])

# A comma-separated second window with no date inherits the first.
w, e = parsed("aug 5 8pm to 10pm, 11pm to 1am")
check("avail — bare second window inherits the date", not e and len(w) >= 2, f"{len(w)}, {e}")

# Must NOT regress: single forms, and real garbage must still be refused.
check("avail — single 'Sat 8pm-10pm' unchanged", not parsed("Sat 8pm-10pm")[1])
check("avail — single 'Aug 5 20:00-22:00' unchanged", not parsed("Aug 5 20:00-22:00")[1])
check("avail — a dateless message is still refused", bool(parsed("9pm to 6am")[1]))
check("avail — garbage is still refused", bool(parsed("garbage input")[1]))


# ═════════════════════════════════════════════════════════════════════
#  !who
# ═════════════════════════════════════════════════════════════════════
group("!who")

reset_state()                       # nobody registered
r = L.do_who()
n_roster = sum(len(t["roster"]) for t in L.load_teams()["teams"])
check("who — lists every team", all(t["name"] in r for t in L.load_teams()["teams"]), r)
check("who — counts 0 when nobody is registered", f"**0 of {n_roster}**" in r, r)
check("who — prompts the unregistered", "!register" in r, r)
# Must stay inside Discord's 2000-char message limit at full roster size.
check("who — fits in one Discord message", len(r) < 2000, f"{len(r)} chars")

# Registering someone must move the count and show their zone.
L.do_register("Hurr GMT", "hurrali", "222000000000000002", L.load_discord_players(),
              privileged=False)
r = L.do_who()
check("who — counts a registered player", f"**1 of {n_roster}**" in r, r)
check("who — shows their timezone", "London" in r, r)
check("who — marks them done", "✅Hurr" in r, r)

r = L.do_status()
check("status — reports the registration count too", "Registered with a timezone" in r, r)

# Re-confirming must REPLACE, not append. Two bookings for one game put
# the same fixture on the Coord tab twice, the stale one carrying an
# out-of-date `missing` roster, with nothing to say which is current.
# This is the regression that shipped on 2026-08-02.
def _upcoming():
    return L.load_scheduling().get("upcoming", [])

def _count(pair):
    return sum(1 for e in _upcoming() if sorted(e.get("match_up") or []) == sorted(pair))

before = _count([1, 3])
r1 = L.do_confirm("1 vs 3 1", "ut70")
mid = _count([1, 3])
r2 = L.do_confirm("1 vs 3 1", "ut70")
after = _count([1, 3])
booked = "MATCH" in r1 and "MATCH" in r2      # slots existed for the pair

check("confirm twice, same slot — one booking, not two",
      (not booked) or after == mid, f"before={before} mid={mid} after={after}")
check("confirm twice — second reply says RESCHEDULED",
      (not booked) or "RESCHEDULED" in r2, r2)

# Reversed pairing is the same game, so it must also replace.
r3 = L.do_confirm("3 vs 1 1", "ut70")
check("confirm reversed pairing — still one booking",
      (not booked) or _count([1, 3]) == mid, f"count={_count([1,3])}")

# A different pairing is a different game and must be added, not swallowed.
n_before = len(_upcoming())
r4 = L.do_confirm("2 vs 4 1", "ut70")
check("confirm a different pairing — added alongside",
      "MATCH" not in r4 or len(_upcoming()) == n_before + 1,
      f"{n_before} -> {len(_upcoming())}")


# ═════════════════════════════════════════════════════════════════════
#  Legacy !schedule -> friendly deprecation
# ═════════════════════════════════════════════════════════════════════
group("!schedule (removed)")

# do_schedule was removed; the dispatcher redirects. We can only verify
# by looking at the dispatcher; here we just ensure do_newweek exists
# and readiness has been tested.

# ═════════════════════════════════════════════════════════════════════
#  !newweek
# ═════════════════════════════════════════════════════════════════════
group("!newweek")

r = L.do_newweek("ut70")
check("newweek — archives + resets",
      "New week open" in r or "week open" in r.lower(), r)

# After newweek, availability should be empty
sched = L.load_scheduling()
check("newweek — availability wiped", not sched.get("availability"))


# ═════════════════════════════════════════════════════════════════════
#  Final reset + summary
# ═════════════════════════════════════════════════════════════════════
reset_state()



# ═════════════════════════════════════════════════════════════════════
#  --new flag, as mangled by autocorrect
# ═════════════════════════════════════════════════════════════════════
group("--new flag (dash variants)")

for dash, label in [("--", "plain"), ("—", "em-dash (autocorrect)"),
                    ("–", "en-dash"), ("-", "single")]:
    reset_state()
    r = L.do_register(f"Soooze gmt {dash}new", "soooze", "222000000000000077",
                      L.load_discord_players(), False)
    check(f"register --new works with {label} {dash!r}", "Registered" in r, r)

reset_state()
# A nick that is genuinely on no roster, computed rather than guessed.
# This used to be "Soooze", who became a real Team 3 stand-in on
# 2026-08-07 -- at which point the test was asserting that a rostered
# player is unknown, and failed for the right reason.
_known = {c.lower() for t in json.loads(L.TEAMS_FILE.read_text(encoding="utf-8"))["teams"]
          for r_ in t["roster"] for c in [r_["name"]] + r_.get("aka", [])}
_unknown = next(n for n in ("Zzqqxv", "Nobody42", "NotARealNick", "Qqxzzv")
                if n.lower() not in _known)
r = L.do_register(f"{_unknown} gmt", "soooze", "222000000000000077",
                  L.load_discord_players(), False)
check("register without --new still prompts for an unknown nick",
      "Registered" not in r, r)


# ═════════════════════════════════════════════════════════════════════
#  The rewrite queue — an unparseable !avail must be KEPT, not dropped
#
#  Two players (Khuni Billa, HURR) hit a parse error in the live channel
#  and never re-posted, so their availability is missing from this week
#  entirely. The parser getting better does not fix the ones already
#  lost; keeping the message does.
# ═════════════════════════════════════════════════════════════════════
group("!avail rewrite queue")

import avail_llm                                             # noqa: E402

reset_state()
L.do_register("KHUNI_BILLA PKT", "ugi_ali9839", UID_UGI,
              L.load_discord_players(), False)

r = L.do_avail("fri sat sun", "ugi_ali9839", UID_UGI,
               L.load_discord_players(), msg_id="9001")
check("queue — the parser's own complaint still leads the reply",
      "Couldn't parse" in r, r)
check("queue — the reply says the message was kept",
      "#1" in r and "kept" in r.lower(), r)
check("queue — invites plain English",
      "plain English" in r, r)

pend = L.load_pending()["pending"]
check("queue — one entry recorded", len(pend) == 1, pend)
check("queue — keeps the raw text verbatim",
      pend and pend[0]["raw"] == "fri sat sun", pend)
check("queue — remembers who it was",
      pend and pend[0]["player"] == "Khuni Billa [UGI|]", pend)
check("queue — keeps the discord message id for the link",
      pend and pend[0]["message_id"] == "9001", pend)

# Re-typing the identical line is what people do when nothing answers.
L.do_avail("fri sat sun", "ugi_ali9839", UID_UGI,
           L.load_discord_players(), msg_id="9002")
check("queue — retyping the same line does not stack a second entry",
      len(L.load_pending()["pending"]) == 1, L.load_pending()["pending"])

L.do_avail("sometime next week maybe", "ugi_ali9839", UID_UGI,
           L.load_discord_players(), msg_id="9003")
check("queue — a genuinely different message does get its own entry",
      len(L.load_pending()["pending"]) == 2, L.load_pending()["pending"])

r = L.do_status()
check("status — an undrained queue is visible in the channel",
      "Waiting on a closer read" in r and "**2**" in r, r)

# A message that parses must never end up in the queue.
reset_state()
L.do_register("KHUNI_BILLA PKT", "ugi_ali9839", UID_UGI,
              L.load_discord_players(), False)
_tomorrow = date.today() + timedelta(days=1)
_good = f"{_tomorrow.strftime('%b').lower()} {_tomorrow.day} 9pm to 11pm"
r = L.do_avail(_good, "ugi_ali9839", UID_UGI, L.load_discord_players())
check("queue — a parseable message is accepted, not queued",
      "Got it" in r and not L.load_pending()["pending"], r)


# ═════════════════════════════════════════════════════════════════════
#  vet() — what the model is allowed to hand back
#
#  The model rewrites free text into the canonical grammar and nothing
#  else, so every rewrite has to survive the SAME parser a player's
#  typing would have. These are the cases where a plausible-looking
#  rewrite must still be refused.
# ═════════════════════════════════════════════════════════════════════
group("avail_llm.vet — rewrites that must be refused")

_today = date.today()
_ws, _we = _today, _today + timedelta(days=8)

for label, canonical, want_ok in [
    ("a normal rewrite is accepted",
     f"{_tomorrow.strftime('%b').lower()} {_tomorrow.day} 9pm to 6am", True),
    ("empty rewrite", "", False),
    ("None rewrite", None, False),
    ("prose instead of the grammar", "he is free most evenings", False),
    ("a time with no date", "9pm to 6am", False),
    ("a date outside the league week",
     f"{(_today + timedelta(days=30)).strftime('%b').lower()} "
     f"{(_today + timedelta(days=30)).day} 9pm to 11pm", False),
    ("a date in the past",
     f"{(_today - timedelta(days=3)).strftime('%b').lower()} "
     f"{(_today - timedelta(days=3)).day} 9pm to 11pm", False),
]:
    windows, why = avail_llm.vet(canonical, _ws, _we, _today)
    check(f"vet — {label}", (windows is not None) == want_ok, why or windows)

# The week bounds never start before today: a rewrite must not be able to
# fill in days that have already gone.
_ws2, _we2 = avail_llm.week_bounds({"week_of": "2020-01-01"}, _today)
check("vet — week_bounds clamps a stale week_of forward to today",
      _ws2 == _today, _ws2)


# ═════════════════════════════════════════════════════════════════════
#  Preview rendering — a window crossing midnight UTC must not read
#  backwards. vAnzO's entire week rendered as "23:00–22:00".
# ═════════════════════════════════════════════════════════════════════
group("!avail preview — windows that cross midnight UTC")

reset_state()
L.do_register("Soma GMT", "soma3031", "222000000000000088",
              L.load_discord_players(), False)
r = L.do_avail(f"{_tomorrow.strftime('%b').lower()} {_tomorrow.day} 12am to 11pm",
               "soma3031", "222000000000000088", L.load_discord_players())
check("preview — the end stamp carries its own date when it lands on the "
      "next UTC day",
      "Got it" in r and r.count("Aug") + r.count("Sep") >= 2, r)
check("preview — no longer renders as an end before its start",
      "23:00–22:00 UTC" not in r, r)

reset_state()


# ═════════════════════════════════════════════════════════════════════
#  !tz PlayerName Zone must set THAT PLAYER, not the caller
#
#  resolve_zone() falls back to matching individual bare words, so
#  "TigerX Asia/Karachi" resolves to Asia/Karachi. do_tz asked "is the
#  whole string a timezone?" first, so every `!tz Someone Zone` looked
#  like self-service and quietly set the CALLER's timezone. Applying a
#  roster sheet of 24 players rewrote one approver's zone 24 times and
#  changed nobody else's.
# ═════════════════════════════════════════════════════════════════════
group("!tz — setting someone else's zone")

reset_state()
L.do_register("UT est", "ut70", UID_UT, L.load_discord_players(), True)
check("tz setup — caller starts on New York",
      L.load_players_tz()["players"].get("UT") == "America/New_York",
      L.load_players_tz()["players"])

r = L.do_tz("TigerX Asia/Karachi", "ut70", UID_UT, L.load_discord_players(), True)
ptz = L.load_players_tz()["players"]
check("tz — an IANA zone sets the NAMED player", ptz.get("TigerX [GB]") == "Asia/Karachi", r)
check("tz — and does NOT touch the caller", ptz.get("UT") == "America/New_York", ptz)

r = L.do_tz("Trollmitsu Europe/Stockholm", "ut70", UID_UT, L.load_discord_players(), True)
ptz = L.load_players_tz()["players"]
check("tz — resolves an aka to the canonical name",
      ptz.get("Trollmitsu [<MC>]") == "Europe/Stockholm", r)
check("tz — caller still untouched after a second call",
      ptz.get("UT") == "America/New_York", ptz)

r = L.do_tz("Stoic PKT", "ut70", UID_UT, L.load_discord_players(), True)
check("tz — shorthand zone for another player still works",
      L.load_players_tz()["players"].get("Stoic") == "Asia/Karachi", r)

# Self-service must still work, including multi-word and parenthesised zones.
r = L.do_tz("Eastern time", "ut70", UID_UT, L.load_discord_players(), True)
check("tz — bare multi-word zone is self-service",
      "your timezone" in r and L.load_players_tz()["players"].get("UT") == "America/New_York", r)
r = L.do_tz("UK (GMT+1)", "ut70", UID_UT, L.load_discord_players(), True)
check("tz — parenthesised zone is self-service, outside wins",
      L.load_players_tz()["players"].get("UT") == "Europe/London", r)

# Garbage must not write anything. resolve_zone returns None rather than
# raising, so this used to reach set_self(None) and store a null zone.
L.do_tz("UT est", "ut70", UID_UT, L.load_discord_players(), True)
before_tz = dict(L.load_players_tz()["players"])
r = L.do_tz("complete nonsense here", "ut70", UID_UT, L.load_discord_players(), True)
check("tz — unparseable input writes nothing",
      L.load_players_tz()["players"] == before_tz, r)
check("tz — and says so", "Couldn't parse" in r, r)

# A non-approver may set their own, never someone else's.
r = L.do_tz("Stoic PKT", "someone", "999000000000000123",
            L.load_discord_players(), False)
check("tz — a non-approver cannot set another player's zone",
      "Only approvers" in r, r)

reset_state()


# ═════════════════════════════════════════════════════════════════════
#  A past date drops that day, it does not kill the message
#
#  People post the whole week on one line and re-post it a day later.
#  The first date has gone by, and refusing the message threw away the
#  six good days with it — one2oneonly lost a complete week that way on
#  2026-08-05, and only the rewrite queue kept any record of it.
# ═════════════════════════════════════════════════════════════════════
group("!avail — dates that have already passed")

_t = date(2026, 8, 5)
_dropped = []
_, _w, _e = L._parse_avail(
    "aug 4 9pm to 6am, aug 5 9pm to 6am, aug 6 9pm to 6am", today=_t,
    dropped=_dropped)
check("past date — the message is still accepted", _e is None, _e)
check("past date — the stale day is reported", _dropped == ["2026-08-04"], _dropped)
check("past date — the good days survive",
      _w and all(x["date"] >= "2026-08-05" for x in _w if "date" in x), _w)
check("past date — nothing from the dropped day is kept",
      _w and not any(x.get("date") == "2026-08-04" for x in _w), _w)

# All-past is still an error, and names the dates rather than one of them.
_d2 = []
_, _w2, _e2 = L._parse_avail("aug 1 9pm to 11pm, aug 2 9pm to 11pm",
                             today=_t, dropped=_d2)
check("past date — an entirely stale message is refused",
      _e2 is not None and "already passed" in _e2, _e2)
check("past date — and lists every stale date",
      _e2 and "2026-08-01" in _e2 and "2026-08-02" in _e2, _e2)

# Without the `dropped` argument the old callers still behave.
_, _w3, _e3 = L._parse_avail("aug 6 9pm to 6am", today=_t)
check("past date — the plain 3-tuple call still works", _e3 is None and _w3, _e3)

# End to end: the reply must SAY what it skipped.
reset_state()
L.do_register("UT est", "ut70", UID_UT, L.load_discord_players(), True)
_y = date.today() - timedelta(days=1)
_m = date.today() + timedelta(days=1)
r = L.do_avail(f"{_y:%b} {_y.day} 9pm to 11pm, {_m:%b} {_m.day} 9pm to 11pm",
               "ut70", UID_UT, L.load_discord_players())
check("past date — reply saves the future day", "Got it" in r, r)
check("past date — and names the skipped one",
      "Skipped" in r and _y.isoformat() in r, r)

reset_state()


print(f"\n{'═' * 60}")
print(f"  {passes} passed · {fails} failed")
if failures:
    print("\n  Failing cases:")
    for name, actual in failures:
        print(f"    • {name}")
sys.exit(0 if fails == 0 else 1)
