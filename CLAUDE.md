# Total Dota Lobby Stats — working notes

Win/loss tracking for a private Dota 2 inhouse lobby. Screenshots → SQLite →
static dashboard on GitHub Pages, with a Discord bot as the front door.

**Live:** https://muhammadut.github.io/total-dota-lobby-stats/
**Repo:** github.com/muhammadut/total-dota-lobby-stats (PUBLIC)

**The point of the whole system:** at the end of the year, look at the
accumulated data and say who won the most matches. So the failure that matters
is *a wrong number produced quietly and surviving twelve months*. A loud crash
is fine. Silence is not. Every guard in this codebase leans that way.

## The one rule

**Never hand-edit `data/matches.json`, and never hand-run the deploy.**
Reading a screenshot is judgement and belongs to the model; validating, writing,
rebuilding and deploying are mechanical and belong to the tools.

```bash
python tools/discord_pull.py            # Discord -> inbox/
python tools/discord_commands.py        # act on channel instructions
python tools/discord_ask.py --resolve   # apply merge votes
python tools/reconcile.py               # account for every downloaded image
python tools/ingest.py --from f.json --dry-run | --deploy | --amend | --remove REF
python tools/automerge.py [--ask-discord | --merge A B | --unmerge X | --not-same A B]
python tools/crop.py shot.png --rows --tab scoreboard
python load.py && python export_web.py

python tools/discord_pull.py --source league   # league screenshots -> inbox_league/
python tools/league_ingest.py --from f.json    # -> data/league_matches.json (NOT the lobby one)
python tools/league_ingest.py --alias 'New=Roster Name'   # a rename inside the league
python tools/league_ingest.py --amend --from fill.json  # fill NULL columns only
python tools/league_ingest.py --freeze-teams    # BEFORE any roster reshuffle
python tools/league_ingest.py --reset-season LABEL  # archive + start a season over
python tools/league_result.py --ref REF [--series ID] [--apply]  # attach to a series
python tools/league_result.py --list           # every recorded series
python tools/discord_league.py --watch  # league bot, live (see "The league")
python tools/avail_llm.py [--apply]     # read the !avail the regex couldn't
python tools/test_league_commands.py    # 152 cases; run after any league change
python tools/test_league_result.py      # 27 cases; run after any series-association change
```

**Reconcile AFTER the ingest, never before.** A screenshot is "failed"
the moment it is not yet in the ledger, which is also true of one about
to be ingested — running it first condemned a good screenshot and made
the bot tell a real person to repost. `failed` can now heal, but the
ordering is still the rule.

**The bot token is in `tools/discord.local.json` (gitignored).** It has
been rotated once already. Never echo it, never write a `.bak` beside it
(that backup is *not* gitignored), and never ask for it in chat.

The `add-match` skill drives the screenshot path. Say "add these".

## Verifying a transcription

```
sum(team kills)  <=  team score  <=  sum(enemy team deaths)
```

Left gap = a tower kill (credits the team, no player). Right gap = death to
neutrals/Roshan/self (credits nobody). Usually all three are equal. Violating
the *ordering* is arithmetically impossible → a digit was misread. Gaps of 3
have occurred legitimately (this lobby dives high ground), but re-read anything
above 1 at zoom before accepting it.

**Only kills, deaths and score are checksummed.** Names, net worth, GPM, assists
have no verification at all — a misread name silently mints a new player. Zoom
any name you are not certain of.

## Identity — where the year-end number actually breaks

Near-certain renames merge automatically and the user is **told, not asked**
(their explicit instruction). Weaker candidates become a Discord question.
The bar lives in `tools/automerge.py`, calibrated 12/12 against known merges.

**Absolute precondition, never overridden:** two names in the *same match* are
two different people. `load.py` enforces this **transitively** — an incoming
alias is checked against the canonical *and everyone already merged into it*.
Skipping that let one player show 12 games across 10 real matches.

Merges live in the `aliases` array of `data/matches.json`. Never applied
straight to the database. Several real merges share no characters at all
(`cpx22`/`Mandark`, `samundar khan`/`rtz`, `Kael™`/`Dawn of War`,
`Learn some basic_!_`/`MODE:IG.BASHIRA™`) — those only ever come from a human.

## Dating a match

SCOREBOARD tab has **no match header**. Sources, best first:

1. **OVERVIEW tab** — real match id and start time. Found later? `ingest.py --amend`.
2. **Discord upload time** — server-side, absolute. Best for scoreboard-only.
3. **`Dota_2_<date>-<time>.png` filename** — the *capturing client's* clock.
4. File mtime — for shared files this is arrival, 2–3 h after the game.

**Confirmed timezones:** user is **UTC−4**; `stoicheart` is **UTC+5** (9 h gap,
verified twice). A constant offset across several files is a timezone, not a
broken clock. `stoicheart`'s Discord shows "New Delhi" (UTC+5:30) but the
machine clock is UTC+5 — trust the filename gap, not the city.

**The gold counter does NOT identify a client.** It used to be listed here
as a fingerprint (`67,845` user, `26,525` hurrali, `389,225` stoicheart) and
that is wrong: gold is spendable and drifts. hurrali alone read 26,525 →
61,170 → 63,830 inside a week. Use the poster's Discord identity and the
filename clock instead.

## Discord

Bot `DotaLobbyStats#6201` in `#lobby-stats` (channel `1530411273895018668`).
Token in `tools/discord.local.json`, **gitignored — the repo is public**.

Approvers (only their votes/commands change identity): `ut70`
`539898067957186560`, `fzr2k` `364832153843793920`, `stoicheart`
`1149675111557890048`.

Channel understands: `merge A and B`, `not same A and B`, `yes`/`no` (as a
reply), `who is A`, `status`, `help`. Names resolve loosely; ambiguity is
reported, never guessed.

**Two independent watermarks** — `data/discord_watermark.txt` (images) and
`data/discord_cmd_watermark.txt` (commands). They must stay separate or one
reader swallows the other's messages. `data/discord_seen.json` records the fate
of every downloaded image, which is what makes the image watermark safe to
advance past chat.

## Automation

`.github/workflows/sync-matches.yml` — daily 09:00 UTC + manual dispatch.
Pull → if nothing new, exit before starting the model → else Sonnet parses,
ingests, and the workflow commits and pushes. Public repo = free Actions
minutes. `--limit 12` caps a single run so a dump of images can't become a bill.

**Order matters: `load.py` runs BEFORE `discord_commands.py`.** `dota_stats.db`
is gitignored, so a fresh runner has no database — without that first build,
every merge command answers "I don't have a player called X" and `co_occur()`
returns False, disabling the co-occurrence guard.

## Status — as of 2026-08-03

**41 matches, 410 appearances, 31 people, 17 merges, 3 recorded
non-merges.** 2026-07-24 → 2026-08-02. Every match 5v5, none dateless,
nobody holding two slots, live site byte-identical to the build.

The ingest pipeline is no longer a rehearsal — it has run daily for a
week on real screenshots from six different posters. What that surfaced:

- **Both checksum chains exact is common and worth trusting.** When
  `radiant kills = score = dire deaths` AND the mirror holds, all four
  columns cross-validate. `HURR`'s 27/0/8 on Ursa was accepted on that
  basis: the 27 is carried by the dire chain, the 0 by the radiant one.
- **Measure glyphs, never eyeball them.** The pitch/apex lattice settles
  `SpArt`+8 vs `SpaRt`+10, twelve dots vs thirteen, `[|T|z|]` vs
  `[ITIzI]`, four underscores each side of `Tiger X`. Four separate
  captures have now agreed to the exact pixel, and one eyeball guess of
  mine was wrong (13 dots; it is 12).
- **The gold-counter client fingerprint in "Dating a match" is WRONG.**
  Gold is spendable and drifts — hurrali read 26,525, then 61,170, then
  63,830 across a week. It cannot identify a client across days. Poster
  identity and the filename clock are the reliable signals.
- **`played_on` follows the Discord clock, never the filename.**
  stoicheart's client is UTC+5 against the user's UTC−4, so his files
  are named with tomorrow's date. Confirmed twice at exactly 9h.

**The cloud workflow HAS now run, and failed — correctly.** The 09:00 UTC
schedule fired for the first time on 2026-08-03 and died in 8s at the very
first step, `discord_pull.py`, with `DISCORD_BOT_TOKEN:` empty. No repo
secrets are set. That is the good failure: loud, immediate, and *before*
the model step, so it cost nothing.

Needed: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_APPROVERS`
(`539898067957186560,364832153843793920,1149675111557890048`), and
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`.

**Set `DISCORD_APPROVERS` in the same sitting as the bot token, not
after.** Known-unfixed #3 below is dormant only because the pull fails
first; the moment a token makes the run get further, an unset approver
list makes `privileged = (not allow)` true for everyone in the channel.

Triage has still never seen a deliberately bad image.

### Dashboard — Rating, Duos, clickable heroes

Standings rank by **Rating**: the lower bound of a Wilson confidence
interval on win rate, doubled so 100 means "proved better than a coin
flip". It replaces the old "sort by win% then filter to 5+ games" —
sample size is priced in rather than thresholded, so nobody is hidden.
A **Games count** control (Less/More/Most = z of 1.96/2.576/3.0) sets
how much volume is worth; default is More. Do not silently change the
default — it reorders the whole board.

**Duos** tab: for one player, every teammate ranked by win rate *with*
them versus *without*. "Without" deliberately includes games they were
on the enemy side — the honest baseline is your whole record, not a
chosen slice. Two players selected gives head-to-head instead of a bare
count of split games. **Heroes** are clickable → who played it, how often.

The tab lives in `location.hash`, so `#duos=Downlander` is linkable.

## The league (added 2026-08-02, separate from the stats)

Fall 2026 season, Aug 1 → Dec 31, $500 prize, 4 fixed teams of 6. Lives
in its own Discord channel `#dota-league-2026` (`1533521004280680538`)
and its own files — it shares only the bot token and `api()`.

```
data/teams.json        rosters; canonical name + aka + backup stand-in
data/scheduling.json   week_of, availability, upcoming, archive
data/players_tz.json   canonical name -> IANA zone
data/discord_players.json   discord uid -> canonical name
data/avail_pending.json     !avail messages the regex could not read
data/fixtures.json          the season schedule (GENERATED — never hand-edit)
data/mini_tournament.json   the SHORT format proposal (hand-authored input)
data/league_matches.json    the LEAGUE match ledger -- never enters dota_stats.db
data/series_results.json    which match belongs to which best-of-three
tools/make_fixtures.py      generates it, and self-checks fairness
tools/league_ingest.py      writes the league ledger (imports load.validate)
tools/league_result.py      attaches an ingested match to its series
tools/discord_league.py     the bot (plain Python, NO LLM at runtime)
tools/avail_llm.py          drains that queue, with a model
tools/find_slot.py          DST-safe slot ranking
tools/tz_map.py             zone aliases
tools/test_league_commands.py   152 cases, run it after any change
tools/test_league_result.py     27 cases, the series-association rules
```

**The bot is a poller, not a daemon.** `python tools/discord_league.py`
answers what was typed since the watermark and exits — so a command
typed while nothing is running gets *silence*, which is how a
registration sat unanswered for hours. `--watch` is the live mode: polls
every 3s, backs off and retries on 429/5xx/network, stops loudly only on
401/404.

**Start it with `tools/start_watcher.ps1`, never with a bare `--watch`
in an agent or terminal shell.** Run as a child of the calling shell it
dies when that shell does — twice in a row it was killed mid-backoff
after a Discord 503, leaving a log that simply stops. Nothing in the
watch loop was wrong; the *launch* was. `Start-Process` detaches it, and
it has been verified to survive its own launcher being killed.

```powershell
powershell -NoProfile -File tools\start_watcher.ps1            # start (kills any duplicate first)
powershell -NoProfile -File tools\start_watcher.ps1 -Status    # alive? + last log lines
powershell -NoProfile -File tools\start_watcher.ps1 -Stop
powershell -NoProfile -File tools\start_watcher.ps1 -Install   # logon Scheduled Task, survives reboot
```

**Never run two watchers.** Both answer every command and both advance
the same watermark, so the channel sees doubled replies. The start script
kills any existing one first for exactly this reason.

A dead watcher is silent — that is the whole problem. `-Status` is the
first thing to check whenever somebody says the bot ignored them.

**Teams and Coord tabs are read-only.** All input is Discord. Scheduling
state reaches the site only when `export_web.py` is re-run and committed
— a `!confirm` alone does not update the website, and that gap already
shipped a stale build once.

### League gotchas already paid for

- **`!confirm` replaces, never appends.** Re-confirming a pairing is
  normal (people redo it once more availability lands). Appending put
  the same fixture on the site twice, the stale row carrying an old
  `missing` roster. Identity is **pairing + week**, not pairing + start
  time — keying on time would leave the abandoned slot as a second
  fixture. `upcoming` survives `!newweek`, so older weeks are separate.
- **Parse what people type, not what the grammar wants.** Every rejected
  message in the channel was a missing feature, not user error: three
  people posted the same 200-char line because `every day 9pm to 6am`
  did not exist; `aug7`, `or` between two windows, `Fri Sat 20-23`, and
  `UK (GMT+1)` were all refused. The bot even rejected its own
  instruction when autocorrect turned `--new` into `—new`.
- **Ambiguous zone abbreviations are a silent-wrong-number risk.** `gst`
  resolves to Asia/Dubai; a British player meaning GMT was registered 3h
  off and only caught it because the reply *names the resolved zone*.
  Keep that echo. When a zone is written with a parenthetical, prefer
  the outside (`UK` → Europe/London, DST-aware) over the inside
  (`GMT+1`, a fixed offset that is wrong all winter).
- **Read the channel before theorising.** Every real bug this week was
  found in the message log, not the code.

### The season schedule (added 2026-08-03)

Fixed weekly fixtures, decided up front, replacing "find a slot when both
teams are ready". Four teams split into two matches exactly three ways —
`(1v2,3v4)`, `(1v3,2v4)`, `(1v4,2v3)` — and each split is one night: two
best-of-three series, one per slot, so every team plays once and can watch
the other. Three nights is therefore **one full cycle**, after which every
pair has met exactly once.

```
Slot 1  11:00 PM – 2:00 AM PKT      Slot 2  3:00 AM – 6:00 AM PKT
SEASON RESET 2026-08-14 — tier draft, five teams, ledger archived.
20 round-robin series over 10 nights, Fri + Sat, 22 Aug -> 26 Sep,
then a BEST-OF-FIVE FINAL on Fri 2 Oct between the top two.
every pair meets 2x; each team: 8 series, 4 of them at 3 AM
```

**`--meetings` went 2 -> 1 -> 2 again (2026-08-19), on the players' ask.**
It costs nothing to move: nights with a recorded result are carried
verbatim, so the three played series survived both changes untouched and
only the unplayed remainder was re-solved. Regenerate with
`make_fixtures.py --first 2026-08-22 --meetings 2` — `--first` is the
first night to KEEP, not the first night of the season.

### Team names (2026-08-19)

The captains named their teams: **1 `no_nakhra_clan`, 2 `cpx_king`,
3 `khanna_clan`, 4 `gillu_&_co`, 5 `axo_clan`.** Names live in
`teams.json` only — `fixtures.json`, `league_matches.json` and
`series_results.json` all key on the **id**, so a rename is one file and
nothing needs migrating. Anything printing `f"Team {id}"` is now showing
a label nobody in the channel uses; `league_result.resolve` was changed
to name the teams in its refusals for exactly that reason.

**Two tests asserted the literal string `Team 1`** and failed the moment
the names changed. They now read the names out of `teams.json`. A test
that hard-codes a label the league can rename is testing the wrong
thing — the same class of bug as prose with no checksum.

**There IS a final now, and it is the one series with no teams.**
`make_fixtures.py` emits it with `"teams": []` and a `decided_by` note
(`--no-final` omits it). Every fairness check skips it — a knockout would
fail "every pair meets N times" by definition. It can only be reached by
`league_result.py --series FINAL`, because a teamless fixture never
matches an inferred pair, and `export_web` fills its teams in from the
recorded results on the way out. In the browser `isTBD()` routes it to a
placeholder box; note that `s.teams ? …` was WRONG there — an empty array
is truthy in JS, so the List view rendered `team-pill--undefined`.

**Five teams changes the shape, not just the count.** Two slots a night
means four play and **one has a bye**. After the reset every team byes
twice and plays 8 series, and the 3 AM slot splits 4 each. (Before the
reset the numbers were lopsided — Team 5 joined after weekend one, so it
owed more games and could never sit out. An uneven bye count is the
correct answer when the teams did not all start together.)

**`--meetings` is a SEASON target, not another rotation.** Nights already
played count towards it, so `--meetings 2` after weekend one leaves four
pairs owing one game and six owing two. That is why the generator no
longer cycles the circle method: a fixed rotation can only express a
season where every pair owes the same number. `plan_nights()` searches
instead — depth-first, most-constrained pair first, sending home whoever
has sat out least, pruning on "a pair owes more games than there are
nights left" and "a team owes more games than there are nights left".

**Series ids are DATE-based (`2026-08-29-S1`), never week-based.** They
used to be `W{week}-{Day}-S{slot}` with the week counted from `--first`.
A carried night keeps the id it was generated with, so moving `--first`
re-numbered the new nights while carried ones kept the old numbering —
and 22 Aug (carried, `W2-SAT-S1`) collided head-on with 29 Aug (new,
`W2-SAT-S1`). `series_results.json` is keyed by id, so one recorded
result attached itself to BOTH fixtures, and `carried()` then treated two
nights as played. `build()` now refuses to write duplicate ids at all.
If ids ever change again, migrate the result keys by matching the
recorded winners against the fixture's teams — never by position.

**Season length is never expressed as a night count.** Nights fall out of
`sum(remaining meetings) ÷ 2`, so a schedule that leaves some pairs having
met more often than others cannot be expressed. `--first`, `--days` and
`--teams` set the rest. Re-running with no flags reproduces the site.

**A night with a recorded result is carried forward verbatim, never
regenerated** (`carried()`, on unless `--no-carry`). Weekend one was
played by FOUR teams in pairings a five-team round-robin cannot even
express; regenerating it would have orphaned eleven real games. The
fairness checks therefore cover the generated part of the season only.

**The 3 AM slot is solved across the whole season at once, and seeded
with the nights already played.** A greedy pass shipped first and refused
to write a five-team schedule: it produced 7/7/4/6/6 when **6/6/6/6/6
exists**. The pairing that balances tonight can strand a team three weeks
later, which a greedy cannot see. `late_plan()` searches exhaustively up
to `LATE_EXACT_MAX` nights, greedy beyond. Seeding matters: balancing
only the remainder would leave whoever drew 3 AM in week one permanently
ahead. Current season splits it exactly — 4 nights each.

**The fairness checks cover the WHOLE season, carried nights included** —
every pair on exactly `--meetings`, every team on the same number of
series, late slot within the arithmetic minimum. That is what the promise
on the site is about, so that is what gets checked.

**The schedule was Sat + Sun until 2026-08-08 and it was wrong** — the
season actually started on Friday 7 Aug, and the first two series were
recorded under `W1-SAT-*` ids for a night that never happened. Rebuilding
is safe *because* results live in `data/series_results.json`: unlink,
regenerate, relink with `--series`. Nothing was retyped by hand.

**`make_fixtures.py` refuses to write a schedule that isn't fair** — it
checks that every pair meets equally often, that every team plays the
same number of series, and that the 3 AM slot is shared as evenly as the
arithmetic allows. Those are exactly the errors nobody notices until the
season is over.

**The 3 AM slot cannot always divide evenly, and the guard knows why.**
With `k` cycles, `T1 + T2 = 2a + 2k`, so all four late-slot counts share
a parity; they sum to `6k`, so an exact quarter-share of `1.5k` is only an
integer when `k` is **even**. An odd `k` would need two teams on `1.5k−0.5`
and two on `1.5k+0.5` — mixed parity, impossible. So the check demands a
gap of 0 for even `--meetings` and refuses anything above 2 for odd, and
prints which team drew the short straw. At 3 meetings that is **Team 1,
who plays 3 late nights to everyone else's 5**. Use an even `--meetings`
if that matters. The greedy chooser was verified against brute force at
3, 6, 9, 12 and 42 nights.

**Slot 2 is the next calendar day.** A Friday-night late match is really
Saturday 3 AM. The site prints the weekday with every time for this reason.

**Schedule prose is generated from the payload, not typed into the HTML**
(`app.js::fxCopy`). The old copy said "Saturday and Sunday" and "each team
takes the late slot exactly half its nights"; both went false the moment
the schedule was regenerated, and prose has no checksum to catch it.

**Times are 12-hour everywhere, on the user's explicit instruction.** The
Schedule tab renders each series into every league timezone at *export*
time (`export_web.py::build_fixtures`) so the browser never does timezone
maths — same reasoning as `build_coord`.

`LEAGUE_ZONES` in `export_web.py` is a fixed list, not derived from
`players_tz.json`: the people in the most awkward zones (Malaysia at
2 AM–5 AM) are exactly the ones who haven't registered, so deriving it
would quietly drop the worst-affected row.

### The Tournament tab — a SECOND LEDGER, not a filter (2026-08-07)

**League games and lobby games must never mix, and the mechanism is
separate storage, not a flag.** `data/league_matches.json` holds the
league's matches and **never enters `dota_stats.db`**. `load.py` builds
that database from `data/matches.json` alone, so no lobby statistic can
include a league game — the rows are not there to be selected. There is
no query to forget to filter.

This was got wrong once, on the day it was built: the first league
screenshot went into `matches.json`, and ten players had a league game
folded into their inhouse record. The user's correction was explicit —
*"need it seperate"*. A flag would have worked until the first query
forgot it, which is precisely the failure this project cannot have.

```bash
python tools/discord_pull.py --source league    # -> inbox_league/
# ...parse it exactly as any screenshot...
python tools/league_ingest.py --from g1.json    # -> data/league_matches.json
python tools/league_result.py --ref discord-123 --apply   # attach to a series
python export_web.py                            # nothing shows until this runs
```

`league_ingest.py` **imports** `load.validate` rather than reimplementing
it, so the kills/score/deaths chain has one definition. On top of it, the
league gate: each side must be exactly one team's roster, the two sides
must differ, and the match must not already be in the lobby ledger. A pub
game cannot be filed as a league game by accident.

**`tools/ingest.py --remove REF` exists** because of this. Until it was
added, un-recording a match meant hand-editing `matches.json`, which the
project forbids — so a match filed in the wrong ledger had no supported
way back out.

The **Tournament** tab has three views: team standings, a league-only
player leaderboard, and the best-of-three series. Team records come from
the league ledger directly now, not from fixture attachment — safe
because nothing can enter that ledger without resolving to two rosters.
Games not yet in a series still show, marked **Unattached**: a screenshot
posted before the schedule is settled must never silently vanish.

**Results live in `data/series_results.json`, never in `fixtures.json`.**
`make_fixtures.py::build()` recreates every series from scratch with
`"games": []` — writing results into the schedule means the next
regeneration silently erases the season. The two files meet only in
`export_web.py::build_fixtures`, on the way to the browser.

**`--source league` has its own watermark, seen-ledger and inbox.** Same
rule that already separates the image reader from the command reader:
share a marker between two readers and one swallows the other's
messages. The `lobby` default path is byte-for-byte unchanged. Separate
inboxes also matter for `reconcile.py`, which accounts for every file in
`inbox/` against `matches.json` — a league image sitting there would read
as a failed ingest forever.

**Association is checked, never guessed.** A game joins a series only if
all five players on a side are on the same team's roster, the two sides
are two *different* teams, those teams actually have a fixture against
each other, and the clock lands in that fixture's night. Ambiguity is
refused and reported — the alternative already happened once: team
records were *inferred* from rosters, and a casual pub game whose sides
lined up put Team 3 on 1-0 before a league game had been played.

Series-level guards refuse a game once a Bo3 is decided (2 wins) or full
(3 games), and refuse a `source_ref` already attached elsewhere.
`--unlink` detaches and renumbers. A result pointing at a series id the
schedule no longer contains is **printed as a warning at export** rather
than silently vanishing.

**As of 2026-08-08 the league ledger holds 11 games across 4 series**,
weekend one complete bar one dead rubber — W1-FRI-S1 (Team 2 beat Team 1
2–1), W1-FRI-S2 (Team 3 beat Team 4 2–1), W1-SAT-S1 (Team 3 beat Team 1
2–1) and W1-SAT-S2 (Team 2 beat Team 4 2–0). Standings: Team 2 and
Team 3 tied on 10 points, Team 1 on 2, Team 4 on 1. The lobby ledger is
untouched at 46 matches throughout — check both numbers after any league
work, because "the league game went into the lobby ledger" is the exact
mistake that was made on day one.

**The team standings have their own tie-break (`app.js::teamSort`).** The
generic `tourSort` breaks a tie on MORE GAMES PLAYED, which is right on
the individual board — more games is more evidence — and wrong on a
points table. Team 2 (4–1) and Team 3 (4–2) both reached 10 points and
the generic rule ranked the worse record first. Team standings now break
a tie the way every league does: series won, then game difference, then
win rate.

**A best-of-three can finish on a different night than it started.** The
Team 1 vs Team 2 decider was played a day after games 1 and 2, so the
clock check correctly refused to place it and listed the three fixtures
between those teams. `--series W1-FRI-S1` is the documented answer, and
the refusal message says so. Do not widen `LATE` to make this class of
game self-attach — a Bo3 finished a week later would then land on the
wrong fixture silently.

**A side is read from its STARTERS, and stand-ins are shared.** The rule
was once "all five players on a side belong to the same team", and it
refused a real result the first time it met reality: Scarface [FUBU]
filled a Team 1 slot on 7 Aug and a Team 3 slot on 8 Aug, so no
name-to-team map can be right for both. `league_result.side_team` now
resolves a side when every *starter* present is on one team, at least
three of them are there (`MIN_STARTERS`), and every remaining slot holds
a registered stand-in — whichever team's sheet that stand-in sits on.

A relaxed guard has to be measured, not assumed: run every one of the 46
inhouse games through both rules and count how many resolve to two league
teams. Old rule 0, new rule **0** — the anti-pub guard is untouched,
because a casual mix is a mix of *starters*, which still refuses. All
seven previously-recorded league games keep the exact same attribution.

**Stand-ins are LOOSE — whoever is free that night (2026-08-19).** The
rule was "every starter on a side is on one team, and the rest are
*registered* stand-ins", and it refused three real results in one evening.
CPX's slot was covered by Stoic one game and HURR the next; neither is a
registered stand-in and both start elsewhere. `side_team` now resolves a
side on a **plurality with a margin of two**: the leading roster needs
`MIN_STARTERS` present and at least two more than any other single team.
So 4-1 is a team with a sub, 3-1-1 is a team with two subs, and 3-2 still
refuses — half a side borrowed from one place reads either way.

**That relaxation was measured, and it does cost something.** Run every
recorded inhouse game through both rules: the strict one resolved 0 to
two league teams, the plurality one resolves **1** (`discord-1533296439172792350`,
3-1 and 4-1). The roster check alone can no longer tell that pub game from
a league game. What still stops it is the OTHER gate — a match already in
`data/matches.json` is refused — plus the fact that a human chooses which
screenshots go through `league_ingest.py` at all. If those two ever stop
being true, this rule is not enough on its own.

**A player name may map to exactly ONE team in `teams.json`.**
`team_index` is a flat dict, so a second entry silently wins or loses by
iteration order. Scarface therefore stays on Team 1's roster and Team 3
merely lists him under `backup`, which is display-only.

### The Mini Cups tab — MORE THAN ONE cup (2026-08-24)

`data/mini_tournament.json` holds a **list** of them now: `seasons`,
oldest first, plus `current` naming the one the tab opens on. A
segmented control switches between them and it hides itself at one
season, because a control that cannot change anything is furniture.

**Team ids are SCOPED TO A SEASON.** Season 2's team 1 (Team Toxic) is
not Season 1's team 1 (no_nakhra_clan). Results, tie-breaks, standings
and the bracket all live inside their own season object and nothing
crosses; `MI_TEAM` in the browser is rebuilt on every switch rather than
merged, for exactly that reason.

**A season either names its own teams inline or resolves ids against
`teams.json`.** Season 1 does the latter — it was played by the
league's registered teams. Season 2 does the former: its six are new,
none is on any roster in `teams.json`, and a team named in this file can
never claim a league result, which is what makes naming them here safe.

**`provisional` had to stop meaning "absent from `teams.json`".** Under
the old rule all six of Season 2's teams would have drawn as unconfirmed,
which is false — five of them name a full five. It now means the ROSTER
is not settled: explicit if the season says so, otherwise "has no named
players". Only `Seeker's Return` (five unnamed slots) is provisional.

**Season 2's pools were DRAWN, and the draw is reproducible.**
`random.Random(20260824).shuffle([1..6])` → first three Pool A, last
three Pool B. The seed is in the file and printed on the page. A draw
nobody can re-run is indistinguishable from one that was arranged —
same class of problem as a result with no screenshot.

**One bad season does not take the tab down.** A season that fails its
checks is skipped with a message naming it, and the others still draw.
The tab only hides when nothing at all can be built. Duplicate season
ids refuse the lot, because two seasons sharing an id collide in the
switcher; a `current` pointing at nothing falls back to the newest.

**Rosters are drawn under the team name on the pool card**, wrapping
rather than truncating — a roster missing a name off the end is worse
than a taller card. Captains carry a `C`; an unfilled slot renders as
`TBD` rather than being skipped, because four names and a gap is a
different thing from four names.

### The Mini Cup format — a proposal, then a thing being played (2026-08-19)

A shorter way to run the league, drawn on the site so the captains can
look at it before anyone commits. **Two pools of three** play a round
robin — A is teams 1/3/5, B is 2/4/6 — the top two of each go into a
**four-team double elimination** bracket and the third in each pool is
out. Winners of the pools meet first: that winner goes straight to the
grand final, the loser drops with a life left; the two runners-up play
an elimination match. **10 matches over 5 nights**, against 21 over 11
for the season on the Schedule tab.

**The group stage is one table, not three lines inside each pool
card.** It reuses the Schedule tab's `.sp` / `.sp-tbl` component rather
than growing a second one, so the two read alike and the phone layout is
already solved — below 620px each row becomes a block instead of
scrolling the Result column off the edge, which is the column the table
exists for. Listing the six matches in both places would have been two
things to keep in step; the pool card's job is the roster and where each
place goes next.

**`data/mini_tournament.json` is an INPUT, not an output** — the
opposite of `fixtures.json`, which is generated and must never be
hand-edited. It holds what cannot be worked out — which teams are in
which pool, how many advance, the best-of at each stage, the tie-break
ladder — and `export_web.build_mini()` derives the rest: the six pool
matches, the four boxes, which box feeds which slot of which other box,
the final placings, every count, and the comparison against the season.
Move a team between pools and the whole page follows. Same reason as
`app.js::fxCopy`: prose and counts typed by hand have no checksum, and
the schedule copy went false the first time the season was regenerated.

**A configuration it cannot draw is refused, never approximated.** The
bracket shape is specific to two pools with two advancing, so three
pools, three advancing, a team in two pools at once, or a pool no larger
than the number that advance all print why and return `None` — and the
tab then **hides itself**. All four refusals were run against the real
file and restored afterwards. A team on no roster anywhere only warns:
the bracket is still right, it just says `Team 6`.

**Connector lines are correct HERE and wrong on the Schedule timeline.**
The timeline is a round robin where nothing progresses from one column
to the next, so arrows there would imply a knockout that does not exist.
This is an actual bracket, and the *drop* line — the loser of the upper
match falling to the lower final — is the whole point of a double
elimination, so it is drawn, dashed, in Dire red. Red means knocked out
and green means won the thing, the same as everywhere else.

**The lines are measured after layout, not drawn with CSS elbows.** The
boxes are sized by the grid at whatever width the browser gives them, so
a hard-coded elbow is right at exactly one width. `miLines()` reads the
box rects and emits one SVG of orthogonal paths, elbowing just left of
the target so two lines arriving at the same box share a gutter.

**Nothing is written ON the paths, and that was the second attempt.**
Labelling the two lines `wins` / `loses` read perfectly at 1440px and sat
across a box at 900px: the column gap is a fixed 3.6rem, so the only
clear space is whatever vertical gap between boxes happens to fall beside
the elbow — and that moves as the boxes reflow. A key underneath says it
once and cannot collide with anything.

**A hidden tab measures 0×0**, which would collapse every line onto the
origin — so `miLines()` bails on a zero-width grid, a `ResizeObserver`
redraws the moment the tab is revealed, and `showTab` calls it too so
the lines are never a frame late. The bail is what keeps a good drawing
from being overwritten with zeros when another tab is opened.

**Team 6 is named here and still not in `teams.json`.** It is two people
(Narai, Vanilla) and three empty chairs, and a roster row in `teams.json`
is what resolves a player to a team — so it lives in the mini file as a
display-only `provisional_teams` entry. Anything provisional is drawn
**outlined and dashed** rather than filled, everywhere it appears, so it
cannot read as a settled side. `team-chip--6` / `team-pill--6` exist
(petrol, the gap between teal and slate) for when it becomes real.

**Three teams playing one game each can all finish 1–1**, and then the
result between two of them settles nothing. That is why the tie-break
ladder is three deep and printed on the page rather than left to the
night it happens.

**None of it is scheduled**, and if the league plays it the games go
through `tools/league_ingest.py` into the league ledger exactly as any
other league game.

**Results here are REPORTED, and that is a weaker thing than every other
number on the site (2026-08-22).** A `results` entry in
`data/mini_tournament.json` names a match and a winner — no scoreboard,
no player lines, no `team kills <= team score <= enemy deaths` to check
it against. The first four went in from a Discord message because the
games were played and none of them was in either ledger. So the page
**says so**: the group table's foot counts how many are unbacked, and
the closing note leads with it. Give a result a `source_ref` naming a
game in `data/league_matches.json` and it stops being flagged.

**Team 6 cannot enter the league ledger at all**, which is why a
screenshot would not have helped here: `league_ingest` resolves a side
against the rosters in `teams.json`, and `narai_co` has none. Any
`narai_co` game is unbackable until that team is real.

**What IS checked: the match exists, the winner played in it, and no
match has two results.** Those are how a typo becomes a wrong team in
the bracket. All three refuse loudly, tested against the real file.

**A reported result was wrong, and nothing could catch it (2026-08-23).**
`no_nakhra_clan vs khanna_clan` went in as a no_nakhra win off the
message *"for no_nakhra vs khanna -> no nakhra won"*. It was actually
khanna's win in the group and no_nakhra's win in the **replay** —
same two teams, two different games. The pool read 2-0/1-1/0-2 and
looked perfectly decided for two days. Nothing detected it; the user
did, because the standings did not match what he had watched. **This is
exactly what "reported, not transcribed" costs** — a screenshot would
have carried a date and ten player lines and could not have been filed
against the wrong game. Ask which stage a result belongs to whenever a
pairing can occur twice.

**A tie-break is a REPLAY, and its fixtures are generated, not
configured.** Where the group stage leaves teams level, `pool_standings`
emits a round robin among just those teams (`TB-<pool>-<a>v<b>`) and
reads `tie_break_results` for it. A result naming a game no pool needs
is refused and the refusal lists the tie-breaks that do exist — the
alternative is a recorded result silently going nowhere. The printed
ladder was changed to match what the league actually does: head-to-head,
then a replay. It used to promise "kill difference across the pool
games", which nothing on this page can compute.

**The replay can circle too.** Three teams, one game each, A beat B, B
beat C, C beat A — again. `_split_level` returns the group intact, the
pool stays undecided, the card says *The replay circled too* and the
bracket slots stay empty. Verified both endings: a decisive replay
resolves the pool and fills the bracket, a circular one fills nothing.

**Tie-break games are counted apart from the ten.** They are not part of
the format's promise, so `totals.tie_break_matches` is its own number and
the HUD reads "+3 to break a tie" rather than quietly making it 13.

**A pool the results do not separate is left UNDECIDED, never ordered.**
`pool_standings()` groups on wins, splits a level group by the results
among only those teams, and if that still does not separate them the
whole group is flagged `tied` — the pool card says *Level — needs a
tie-break* and the bracket slot stays empty. Verified both ways: a
perfect 1–1–1 cycle in a pool of three refuses to rank anybody and
leaves the upper-match slot `None`, while a two-way tie that
head-to-head does settle fills it. Guessing there would put the wrong
team in the bracket, weeks before anyone noticed.

**The status pill is derived from the count of results, not from
`status`.** `Proposal — nothing agreed yet` sitting above four recorded
results is the page contradicting itself, and a hand-set flag is exactly
how that happens. It reads Proposal at zero played, `Under way — N of
10` in between, and Finished at the end.

**A ninth tab no longer fits between a phone and a laptop.** The tab bar
scrolled below 560px only; it now scrolls below 960px, because the row
is centred and the overflow gets clipped at BOTH ends — "Standings" and
"Heroes" would each lose letters.

### Resetting a season (2026-08-14)

```bash
python tools/league_ingest.py --reset-season 2026-fall-v1
```

**It archives, it does not delete.** `data/league_matches.json` and
`data/series_results.json` are copied to `data/archive/LABEL_*` and then
emptied; the first attempt's 11 games live there. It prints the lobby
ledger count before and after and **fails loudly if that number moved** —
a league reset must cost `data/matches.json` nothing.

Then regenerate: `make_fixtures.py --first <first night>`. With the
results gone there is nothing to carry, so the whole season is fresh.

**The 2026-08-19 reshuffle moved seven people, not just the names.**
Seeker 3→2, Vanzo 2→3, BeastMode 4→3, Bashira 3→4, Oden Jr 5→2, and Germ
and Tiger-Y came off the stand-in benches into starting slots on 1 and 2.
Khuni Billa came out of Team 1's five and is a stand-in there now.
`--freeze-teams` was run FIRST, so all seven recorded games kept the team
they were played for — recomputing would have read them as mixes and
dropped them out of the standings.

**Team 5 now has FOUR starters** (the sheet's offlane cell says TBD) and
**Team 2 has five for the first time**. `MIN_STARTERS` is 3, so four
starters plus a stand-in still resolves. Do not invent a name to fill a
TBD cell.

**Team 6 is on the captains' sheet and deliberately not in the repo.** It
lists Narai (mid) and Vanilla (carry) against three TBDs, and neither
name appears in either ledger. Six teams over two slots is TWO byes a
night, not one — a different schedule shape — so it waits for a real
roster and an explicit go-ahead.

**Rosters carry `position` and `tier` now** (mid/carry/offlane/support,
and the draft pool 1–4 or `legend`). Both are **display only** — nothing
resolves identity or team from them, so a wrong one costs a label, not a
result.

**The Teams tab is a LINEUP SHEET, not a list of names.** Every card runs
the same five slots in the same order behind a fixed-width position rail,
so the five cards read across as one grid — mid against mid, carry against
carry. That alignment is the whole design, which is why a filled row is
always exactly two lines (canonical name and substitute share one) and
why `.lu` carries a `min-height`.

**An unfilled slot keeps its row**, hatched and labelled *Open*.
Collapsing it would let Team 2 read as a complete team of four. The
header shows `4/5` in Dire red for the same reason, and the standings
row says "4 of 5" rather than a roster size — stand-ins are no longer on
the cards, so counting them there was two different counts of one team.

**Stand-ins moved to the draft table** (`#tierTable`), which joins
`league.tiers` to the rosters and answers what the pools alone cannot:
where each pick ended up. A name in a pool that is on nobody's roster
reads "—". Note `.slot` was already taken by the Coord view — the lineup
classes are `.lu*` for that reason.

**Team 2 has THREE starters** — CPX, Obnoxious, Vanzo. The sheet leaves
its carry blank, and on 2026-08-14 Musa moved to stand-in after playing
FOR TEAM 4. `MIN_STARTERS` is 3, so its three starters plus two
registered stand-ins still resolve; the carry and second support render
as **Open**. Inventing a name to fill them is exactly the failure this
project cannot have.

**Identity resolved 2026-08-14, both by the user, neither guessable.**
`Lucky_Gatto [BCRT]` **is Gillu**, Team 4's carry — he filled that slot
in all three games of the Team 2 series and Gillu had never appeared on
a scoreboard. And **Musa is a shared stand-in, not a Team 2 starter**: a
starter of one team on the other team's side makes the lineup a mix,
which `side_team` refuses, and it refused correctly. Still unidentified:
Eros and Eldritch (both starters), Theekra and Rebel.

**Teams play fixtures early.** The Team 2 vs Team 4 Bo3 was played on
14 Aug against a 22 Aug fixture, so the clock check refused it and listed
both scheduled meetings. `--series W2-SAT-S1` is the answer, exactly as
for a series finished late — do not widen the window to absorb a whole
week.

### A recorded game's teams are FROZEN (2026-08-08)

Team attribution used to be recomputed from `teams.json` on every export.
That is correct exactly as long as nobody changes team. The five-team
reshuffle moved **Rogue Agent from Team 4 to Team 5** — and he had already
played five recorded games *for Team 4*. Recomputing would have turned
those line-ups into "a mix of teams" and dropped five real results out of
the standings, silently, weeks later.

So every row in `data/league_matches.json` now carries
`radiant_team_id` / `dire_team_id`, resolved **once at ingest** against
the roster in force that night. `export_web.build_tournament` and
`league_result.resolve` prefer the stored ids and only fall back to live
resolution for a row written before this existed.

```bash
python tools/league_ingest.py --freeze-teams   # BEFORE any reshuffle
```

**Run that before editing `teams.json`, not after.** It can only stamp
what still resolves; once the roster has moved, the answer it needed is
gone. The live roster still gates what may ENTER the ledger — a new game
must look like a league game *today* — it just no longer rewrites what is
already in it.

### `league_ingest.py --amend` fills blanks, and only blanks

A 1920-wide capture is cut off after net worth, so LH/DN/GPM/XPM/damage
land as NULL. When the scrolled-right screenshot arrives later, `--amend`
merges it in: matches are found by `source_ref`, players by name.

Filling a NULL is always allowed. **Changing a value that is already
recorded is refused** unless `--overwrite` is passed, and
`name/side/kills/deaths/assists` can never be amended at all — they are
identity and the checksum chain. Without that asymmetry an "amend" is
indistinguishable from a silent rewrite. The amended match is then
re-validated exactly as a new one, against all the others, so a patch
cannot edit one match into a copy of another or flip a winner.

**Teams do not always play the slot they were scheduled for.** Team 2 vs
Team 4 was fixtured into Saturday's 3 AM slot and actually played in the
11 PM one. `league_result.py` still resolved it unaided: the acceptance
window is `start − 2h .. end + 6h`, which is wide enough to swallow a slot
swap on the right night, and the roster check is what actually identifies
the fixture. Do not narrow that window to "fix" a mis-slotted game.

The Schedule tab has two layouts, **Timeline** (default, one column per
NIGHT scrolling sideways, the next night scrolled into view) and
**List**. Both were asked for; keep both.

**Week numbering is gone from the whole site (2026-08-19), on the user's
instruction** — "remove the Week 2 ... coz we dont know which teams show
up etc." A week header groups two nights into one unit and implies they
get played together, which is precisely what does not happen: teams play
a fixture early, finish a decider days later, swap slots. The date is the
only part of a fixture that has ever held up. So a Timeline column is one
night, the List is a flat run of nights, and a series card carries its
date alone. `currentWeek()` became `currentNight()`; `allNights()`
flattens the payload, which still arrives grouped in `FX.weeks` — the
generator is unchanged, only the display.

Timeline draws each match as a **box with the two teams stacked**, after
the TI bracket overview — a flat `A vs B` line reads as prose, a stacked
pair reads as a fixture and leaves an obvious slot for each team's score.
There are no connector lines between columns and there must not be: this
is a round-robin, nothing progresses from one week to the next, and
bracket arrows would imply a knockout that does not exist.

In List, a night is a **block, not another row**. It first shipped with
the same hairline between nights as between the matches inside them, so
Saturday and Sunday ran together as four identical lines. The seam
between nights is now a full rule, alternate nights are tinted, and the
date is stacked under the weekday.

**Above both layouts sits one table, and it lists PLAYED matches only**
(`app.js::drawProgress`). It replaced a five-by-five
"who-owes-whom" grid, which answered a question nobody asks — how many
series does this *pair* still owe — and made finding a single result a
matrix-reading exercise. It then listed every fixture, played
or not, which at two meetings a pair is twenty rows of "still to play"
burying the three that happened — so on the user's instruction it now
shows results only, and what is left is the count in the header plus the
schedule underneath, which is the thing built to show it. It is built by
walking the schedule itself, not from `FX.progress`, so the header counts
can never disagree with the rows beneath them. Below 620px the three
columns become a two-line block per row — a sideways-scrolling table hid
the Result column, which is the entire point of the table.

`team-pill--5` did not exist until this was built: Team 5 rendered as
white text on a transparent pill everywhere the List view drew a pill.

### Team records come from fixtures, never from inference (2026-08-03)

The Teams tab used to **infer** which team each side of a match belonged
to — 3+ players from one roster on a side meant that side "was" that
team, for any match after the season start. That put Team 3 on 1–0 and
Team 1 on 0–1 before a single league match had been played, off one
ordinary inhouse game whose sides happened to line up.

It was always going to do that: these people play inhouse together every
night, in whatever mix shows up. **A guess is not a record.** Team
records now count only matches whose `source_ref` is recorded in a
fixture's `games` array, and the teams come from the fixture, not the
roster. With nothing recorded yet, every team correctly reads 0.

Individual standings are unaffected — every match still counts there.

### A rejected `!avail` is kept, not dropped (added 2026-08-03)

Widening the regex only helps the *next* person. `fri sat sun` (Khuni
Billa) and `aug7 ... or ...` (HURR) were both refused, neither re-posted,
and both are missing from this week's numbers — the fix shipped after the
data was already lost. So a parse failure now writes the message to
`data/avail_pending.json` and says so, and `!status` shows the backlog so
an undrained queue is loud rather than silent.

`tools/avail_llm.py` drains it with a model. **The model rewrites free
text into the canonical grammar and nothing else** — the string it
returns is fed through the same `_parse_avail()` and written through the
same `do_avail()`, so it cannot express a window the deterministic parser
would have refused, and every existing guard (roster, declared timezone,
no past dates) still runs. On top of that a rewrite is refused if it
doesn't parse, lands outside the league week, or comes back
`confidence: low` (override with `--include-low`). Everything applied is
echoed into the channel quoting the player's original words, so the
person who typed it is the last check.

Calibration as observed: it declined `fri sat sun` (no times stated) and
`cant play this week sorry` (a decline, not a window), and marked every
inferred end time `low`. Do not "fix" that caution — inventing a window
nobody stated is exactly the silent-wrong-number failure.

**Nothing LLM runs inside the 3s poll loop.** The bot's only job on a
failure is to keep the message; reading it happens when someone runs the
tool.

### `automerge.py` picks the established name as canonical

When it merged the two `Rogue Agent` rows it kept `[ITIzI]` — the *misread*
spelling — because that row had the earlier appearances. The count is right
(one person, 2 games) but the dashboard shows a name that was never real.
**Now fixable without hand-editing:** `--unmerge ALIAS` then `--merge
CANONICAL ALIAS` the other way round. Both go through the same
co-occurrence precondition as an auto-merge.

### Claims about "first" / "newest" must be queried, not recalled

Three notes shipped saying a thing was new when it was not — "first
custom team name" (they go back to 07-24), "second appearance" (it was
the fourth), "third one-point game" written as the second. All three
were written from what this session had seen rather than from the
ledger, and all three had to be amended. **Before writing "first",
"only" or "Nth" into a note, query `matches.json`.** Amend with
`ingest.py --amend` and a notes-only patch generated from the existing
file, then verify no non-note field moved.

## Known-unfixed (adversarial review, 2026-07-25)

Three reviewers audited ingest, identity and automation. The critical findings
are fixed and verified against reproductions. These remain **open**:

1. **Non-atomic writes.** Every writer uses `Path.write_text` (truncate). A
   crash mid-write leaves the year's ledger empty or half-written; git is the
   only recovery. Wants temp-file + `os.replace`.
2. **`norm()` erases non-ASCII.** `毒奶粉2024` and `开心果2024` both reduce to
   `2024` and would auto-merge. Also `Player1`/`Player2` at 86% similarity.
   Needs a minimum *alphabetic* stem, not just 4 characters.
3. **Approvals fail open in CI.** `tools/discord.local.json` is gitignored, so
   if `DISCORD_APPROVERS` is unset the approver list is empty and
   `privileged = (not allow)` makes **everyone** an approver. Set the secret.
4. ~~**`not same A and B` doesn't un-merge.**~~ **FIXED** —
   `automerge.py --unmerge ALIAS` exists, and `--not-same` refuses if the
   pair is currently merged rather than silently recording a lie.
5. A stray `yes` from an approver applies the single open merge question even
   if it wasn't a reply to it.
6. `dump_matches()` silently drops unknown top-level keys in `matches.json`.
7. A dateless match (`played_on` null) is counted in **every** year by the
   dashboard filter. Currently 0 such matches, but it would corrupt a year-end
   board.
8. **League: `!newweek` does not clear `upcoming`.** Bookings accumulate
   across weeks. Harmless now (the dedupe keys on week), but the Coord
   tab will eventually list past fixtures as "NEXT MATCH".
9. **League: the Coord tab renders `upcoming` in insertion order**, so a
   later-dated fixture can appear above the genuinely next one, and both
   are labelled "NEXT MATCH".
10. **League: `backup` may name someone off the roster** (Team 4's Xmen
    is backed by `Gillu`, who has no roster row). Display-only, so it
    renders as the sheet reads and breaks nothing — but that person
    cannot register or post availability.

## Cautions

- Items on the scoreboard are icons, never text. Not transcribed — a wrong item
  is worse than no item.
- Screenshots arrive at 5120×2160, 1920×1080 and 1920×1200, cut at different
  columns. Missing columns stay NULL.
- `export_web.py` re-stamps a content hash onto asset URLs. Without it a
  returning visitor can hold a cached `app.js` beside a fresh `data.js` and the
  page renders blank. This has actually happened.
- Don't surface internal integrity metrics in the UI. The player-game count
  (matches × 10) was shown once and read as a contradiction next to "Matches 7".
- Dashboard is light, Apple/Airtable-adjacent; colour is rationed so Radiant
  green and Dire red always mean something.
