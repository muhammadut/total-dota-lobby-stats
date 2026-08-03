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
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import discord_league as L
import tz_map
from find_slot import team_readiness


# ── Back up real state at import time; restore on exit (even on error) ──
_BACKUP_PATHS = [L.SCHEDULING, L.DISCORD_PLAYERS, L.PLAYERS_TZ, L.TEAMS_FILE]
_BACKUP = {p: p.read_text(encoding="utf-8") for p in _BACKUP_PATHS if p.exists()}

def _restore():
    for p, text in _BACKUP.items():
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

r = L.do_avail("Aug 3 8PM to 10PM", "ut70", UID_UT, dp)
check("do_avail full path — preview shows date + tz + UTC",
      "2026-08-03" in r and "New York" in r and "UTC" in r, r)
check("do_avail readiness tail — Team 1 pinged",
      "Team 1" in r and "1/6" in r, r)


# ═════════════════════════════════════════════════════════════════════
#  !find — all pairs vs specific pair
# ═════════════════════════════════════════════════════════════════════
group("!find — all pairs and specific pair")

reset_state()
dp = L.load_discord_players()
L.do_register("UT ET", "ut70", UID_UT, dp, privileged=True)
dp = L.load_discord_players()
L.do_avail("Aug 4 8PM to 11PM", "ut70", UID_UT, dp)

r = L.do_find("")
check("find (no args) — lists all 6 pairs",
      r.count("**Team") == 6, r[:200])

r = L.do_find("1 vs 3")
check("find '1 vs 3' — single pair",
      "**Team 1 vs Team 3**" in r and r.count("**Team") == 1, r[:200])

r = L.do_find("Team 1 vs Team 3")
check("find 'Team 1 vs Team 3' — verbose form works",
      "**Team 1 vs Team 3**" in r, r[:200])

r = L.do_find("1v3")
check("find '1v3' — compact form works",
      "**Team 1 vs Team 3**" in r, r[:200])


# ═════════════════════════════════════════════════════════════════════
#  !readiness
# ═════════════════════════════════════════════════════════════════════
group("!status")

r = L.do_status()
check("status — no crash, shows week + team pulse",
      "week of" in r.lower() and "readiness" in r.lower(), r)


group("!readiness")

r = L.do_readiness()
check("readiness — shows all 4 teams",
      all(f"Team {i}" in r for i in (1, 2, 3, 4)), r)
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

print(f"\n{'═' * 60}")
print(f"  {passes} passed · {fails} failed")
if failures:
    print("\n  Failing cases:")
    for name, actual in failures:
        print(f"    • {name}")
sys.exit(0 if fails == 0 else 1)
