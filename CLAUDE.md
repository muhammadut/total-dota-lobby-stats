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
python tools/league_ingest.py --alias 'NewName=Roster Name'   # a rename inside the league
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
9 nights, Fri + Sat, 7 Aug -> 4 Sep (5 weeks).  NO playoffs, NO final —
the season runs straight through, per the user's explicit instruction.
each team: 9 best-of-threes; every pair meets 3x
```

**Season length is expressed as `--meetings`, never as a night count.**
Nights are `3 × meetings` by construction, so a half-finished cycle —
which would leave some pairs having met once more than others — cannot
be expressed. `--first` and `--days` set the calendar. Re-running the
tool with no flags reproduces exactly what is on the site.

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

**As of 2026-08-08 the league ledger holds 8 games across 4 series** —
W1-FRI-S1 (Team 1 1–1 Team 2, open), W1-FRI-S2 (Team 3 beat Team 4 2–1),
W1-SAT-S1 (Team 1 1–0 Team 3, open) and W1-SAT-S2 (Team 2 beat Team 4
2–0). Standings: Team 2 on 6 points, Team 3 on 5, Team 1 on 2, Team 4 on
1. The lobby ledger is untouched at 46 matches throughout — check both
numbers after any league work, because "the league game went into the
lobby ledger" is the exact mistake that was made on day one.

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

**A player name may map to exactly ONE team in `teams.json`.**
`team_index` is a flat dict, so a second entry silently wins or loses by
iteration order. Scarface therefore stays on Team 1's roster and Team 3
merely lists him under `backup`, which is display-only.

**Teams do not always play the slot they were scheduled for.** Team 2 vs
Team 4 was fixtured into Saturday's 3 AM slot and actually played in the
11 PM one. `league_result.py` still resolved it unaided: the acceptance
window is `start − 2h .. end + 6h`, which is wide enough to swallow a slot
swap on the right night, and the roster check is what actually identifies
the fixture. Do not narrow that window to "fix" a mis-slotted game.

The Schedule tab has two layouts, **Timeline** (default, one column per
week scrolling sideways, current week scrolled into view) and **List**.
Both were asked for; keep both.

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
