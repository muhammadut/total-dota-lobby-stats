# League Coordination & Team Standings — Design Spec

**Date**: 2026-08-02
**Owner**: UT
**Status**: Draft — pending user review

## 1. Purpose

Extend the existing individual-stats system to support a **team-based league** with:
- Four fixed teams (per the 2026-08-02 roster screenshot).
- **Continuous play** Aug 1 → Dec 31, 2026. Whoever leads the team-rating leaderboard at Dec 31 23:59 UTC — *among qualified teams* — wins **$500**.
- **Discord-driven coordination** for scheduling matches across four timezones (US, EU, Saudi, Pakistan).
- **Read-only website surface** for standings, next match, and this-week availability.

The system inherits the existing philosophy: source of truth is JSON in git, database is rebuildable, mechanical steps are Python, judgement steps are the model. No new philosophies added.

## 2. Non-goals

Explicitly not solving in v1 (rationale in §11):
- Playoff bracket, group stage, or seeded post-season.
- Elo / opponent-strength adjustment on the rating.
- Team disband / mid-season roster changes (handled ad-hoc by human decision).
- Cross-machine bus factor / handover automation.
- Real-time rules-lawyering framework for prize disputes.
- Confirm-only fast-path for matches (no screenshot). Screenshots stay required.
- Website input surface. Website is read-only; all input flows through Discord.

## 3. Users & trust boundary

Three roles, unchanged from existing system:

| Role | Discord IDs | May |
|---|---|---|
| **Approver** | `ut70` `539898067957186560`, `fzr2k` `364832153843793920`, `stoicheart` `1149675111557890048` | declare timezones, open scheduling rounds, confirm slots, override any command |
| **Player** | mapped via `data/discord_players.json` | post their own availability |
| **Public** | anyone in `#lobby-stats` or `#league-coord` | read; no write commands honored |

## 4. Domain model additions

### 4.1 Teams

New file `data/teams.json`:

```json
{
  "season": "2026-fall",
  "teams": [
    {
      "id": 1,
      "name": "Team 1",
      "roster": [
        {"name": "HURR [PK_]",                 "role": "core"},
        {"name": "Cpx",                        "role": "core"},
        {"name": "UT",                         "role": "core"},
        {"name": "SOMA [L3vi]",                "role": "support"},
        {"name": "Obno[X]iouS- 不快な [M.C]",   "role": "support"},
        {"name": "TigerX [GB]",                "role": "stand_in"}
      ]
    }
  ]
}
```

Notes:
- Each roster has exactly 5 non-`stand_in` entries (`core` or `support`) and exactly 1 `stand_in`. `load.py` enforces this.
- Role (`core` / `support`) is display-only; only `stand_in` is behaviorally distinct (see §4.2).
- Player names in `teams.json` must resolve through the existing `aliases` in `matches.json`. If a name matches no player row (even via alias), `load.py` refuses.
- One player is on exactly one team. `load.py` enforces this.

### 4.2 Team-on-match tagging *(resolves failure #1)*

Schema change to `matches` table:

```sql
ALTER TABLE matches ADD COLUMN radiant_team_id INTEGER REFERENCES teams(id);
ALTER TABLE matches ADD COLUMN dire_team_id    INTEGER REFERENCES teams(id);
```

Both nullable. Pre-league matches (matches 1–14 in the current DB) leave them NULL and are excluded from team standings. Every match added during the league gets both fields.

`matches.json` per-match adds:

```json
"radiant_team_id": 1,
"dire_team_id": 2
```

At ingest time, the transcriber (model) declares the team pairing based on which roster the 5 players on each side belong to. `ingest.py` refuses the match if:
- Either declared `team_id` does not exist in `teams.json`, OR
- Fewer than 3 of the 5 players on a side belong to the claimed team (counting `core`, `support`, and `stand_in` roles as team members).

The "3 of 5" floor allows one stand-in from the other team plus one gap for a truly missing player, without silently mis-tagging a mostly-mixed roster.

### 4.3 Discord player mapping *(resolves failure #9)*

New file `data/discord_players.json`:

```json
{
  "539898067957186560": "UT",
  "364832153843793920": "Cpx",
  "…": "…"
}
```

Approvers populate this via `!register @DiscordUser as PlayerName` command. Player names must resolve to a `players` row in the DB. Commands from unmapped user IDs are refused with a helpful message.

### 4.4 Season config

New file `data/season.json`:

```json
{
  "id": "2026-fall",
  "start_at": "2026-08-01T00:00:00Z",
  "end_at":   "2026-12-31T23:59:59Z",
  "cutoff_at": "2026-12-31T23:59:59Z",
  "submission_grace_until": "2027-01-07T23:59:59Z",
  "qualification_floor_pct": 0.80,
  "warning_thresholds": {"yellow": 1.00, "orange": 0.80, "red": 0.70},
  "grace_periods": [
    {"start": "2026-12-22", "end": "2026-12-28", "reason": "Christmas"}
  ],
  "prize_usd": 500
}
```

- `end_at` — season play ends.
- `cutoff_at` — no matches with `played_on > cutoff_at` are counted.
- `submission_grace_until` — matches played before cutoff may be *submitted* until this date. After that, `matches.json` is frozen for the season.
- `grace_periods` — pace calculation ignores matches played inside these windows, and warning pings are suppressed.

## 5. Ranking system

Two independent axes, both live:

### 5.1 Wilson rating (unchanged from existing individual Standings)

Wilson score lower bound of team win rate, `z=2.576`, computed from the count of team-tagged matches won vs. total team-tagged matches. Same formula as the existing `app.js` individual rating; the function is extracted and shared.

### 5.2 Pace *(games per opponent slot, partially resolves failure #3)*

```
pace_count(team) = matches_played_by_team / 3
```

The divisor is always 3 (the number of possible opponents), not the count of *distinct opponents faced*. Dividing by distinct-faced would reward playing only one opponent (matches/1 > matches/3), which is the opposite of the intent.

```
league_median_pace = median(pace_count(t) for t in teams)
qualification_floor = 0.80 × league_median_pace
```

**Known limitation — not fully closing the collusion path**: two friendly teams can still inflate their own pace by playing each other 100+ times, pushing the median up and squeezing a third team out. The Wilson rating partially discourages this (a 0.5 win-rate over 100 matches doesn't beat a 0.7 win-rate over 30), but the qualification cutoff still moves. If this becomes a real problem in practice, v1.1 adds a per-opponent cap (e.g., "any single opponent contributes at most 2× the team's mean matches-per-opponent"). Deferred because it adds complexity for an attack that hasn't happened yet.

Live status tier per team:

| Tier | Condition |
|---|---|
| 🟢 On pace | pace ≥ median |
| 🟡 Below median | 100–80% of median |
| 🟠 Warning | 80–70% of median |
| 🔴 At risk | < 70% of median |
| ⚫ Ineligible | < qualification_floor at cutoff_at |

## 6. Prize

At `cutoff_at` (Dec 31 23:59 UTC), among teams with `pace_count ≥ qualification_floor`:
- Highest Wilson rating wins **$500**.
- Tiebreaker 1: total wins.
- Tiebreaker 2: head-to-head record.
- Tiebreaker 3: earliest to reach current match count (rewards earlier commitment).

Winner **provisionally announced Jan 1**. **Confirmed Jan 8** after the submission grace window closes. Any dispute must land in that week.

## 7. Coordination flow

### 7.1 Discord commands

New channel `#league-coord` (channel ID TBD). Commands parsed by an extended `tools/discord_commands.py`.

| Command | Who | Effect |
|---|---|---|
| `!register @user as PlayerName` | approver | maps Discord user ID → player name (writes `discord_players.json`) |
| `!tz PlayerName TzName` | approver | sets timezone (writes `players_tz.json`) |
| `!schedule Team X vs Team Y` | approver | opens a scheduling round; returns round ID `R42` |
| `!avail [R42] Sat 20-23` | player | posts availability (round ID optional if exactly one round is open) |
| `!avail [R42] clear` | player | clears their availability in that round |
| `!find [R42]` | approver | computes top 3 slots, posts to channel |
| `!find [R42] --strict` | approver | only proposes slots where 10/10 are available |
| `!confirm [R42] N` | approver | locks slot N; writes `upcoming.json`; posts to channel |
| `!reschedule [R42]` | approver | reverts confirmation, reopens finding |
| `!cancel [R42]` | approver | closes a round without confirming |
| `!status` | anyone | current standings + pace + open rounds |

Round IDs are auto-generated. Commands may omit the round ID if exactly one round is open; if 0 or 2+ are open, the ID is required.

### 7.2 Availability semantics *(resolves failure #2 — DST)*

Availability stored as **wall-clock intent + IANA TZ at declaration time**:

```json
{
  "player": "Stoic",
  "declared_at": "2026-10-15T18:32:00Z",
  "declared_in_tz": "Asia/Karachi",
  "windows": [
    {"day": "Sat", "start_local": "21:00", "end_local": "24:00"}
  ]
}
```

Every UTC conversion happens **at query time**, using the current DST rule for the declared TZ. Never cache the UTC form. This means: if the player says "Sat 21:00 PKT" and the league week straddles a DST boundary, the UTC instant recomputes correctly. Pakistan doesn't observe DST, but US and EU do — the correctness matters for cross-tz players.

Ambiguous phrases (`"evening"`, `"morning"`, `"night"`, `"anytime"`) cause the bot to reply with a multiple-choice clarification, not a guess.

Windows crossing local midnight are stored as a single record and split into two UTC segments at query time.

### 7.3 Slot finder

`tools/find_slot.py`:
- Reads all player availability for a round.
- For each 30-minute UTC bucket in the next 7 days, count players available.
- Rank buckets by (a) count desc, (b) earliest start.
- Return top 3, plus any additional 10/10 buckets if `--strict`.
- Output includes wall-clock rendering in the 4 team-relevant timezones.

Testable **without Discord**: hand-populate `data/availability_2026-W32.json` and invoke the script directly.

## 8. Bot notifications

### 8.1 Weekly pace post

Sunday 09:00 UTC, generated by `tools/pace.py` and posted by the workflow. **Signal-only** (resolves failure #5):

- If all 4 teams are 🟢 and no rank change since last week, post is a **one-liner**: `Week 8/22 — all on pace, Team 3 leading, 14 weeks left.`
- Otherwise, full pace table with tier dots and countdown.
- Never post identical content two weeks running. If the state is unchanged from last week's state, skip the post entirely.

### 8.2 Individual warnings

- 🟠 warning: DM to the team's captain (defaulted to the first player in `core`). Never a public channel post.
- 🔴 at-risk: DM to captain plus one public "final call" post at 4 weeks out and 1 week out.
- All warnings suppressed during `grace_periods`.

### 8.3 Milestone posts

- Nov 1: "Two months to season end."
- Dec 1: "Final month."
- Dec 24: "One week."
- Dec 31 morning: "Final day — all matches played today count if uploaded by Jan 7."

## 9. Website changes

### 9.1 New tabs

Add two tabs to the existing tab bar (`docs/index.html`):

| Tab | Content |
|---|---|
| **Teams** | 4-team roster grid (visual, similar to your screenshot). Team standings table (Rank, Team, GP, W, L, %, Pace, Rating, Status dot). Click a team to drill into per-player breakdown. |
| **Coord** | "Next Match" card (or "No match scheduled" placeholder). This-week availability heatmap (30-min buckets × 7 days, cell shade = count of available players). Season countdown. Prize amount + top-3 leaderboard preview. |

Both tabs read from the existing `docs/data.js` (which will gain a `teams` and `coord` block via `export_web.py`).

### 9.2 Data payload additions

`export_web.py` extended to emit:

```js
window.LOBBY.teams = {
  season: "2026-fall",
  teams: [ /* full roster */ ],
  standings: [ /* per-team rank, W/L, rating, pace, status */ ]
};

window.LOBBY.coord = {
  next_match: { /* team_a, team_b, when_utc, per-tz rendering */ } | null,
  availability_this_week: { /* 30-min buckets keyed by "Mon-16:00" */ },
  season_countdown_days: 152,
  prize_usd: 500
};
```

Everything computed in Python at export time. No JS-side computation.

### 9.3 Matches tab (existing)

At scale (200+ matches), the current render-every-card approach will slow the page. **Add pagination** (25 matches per page, "load more" button). No other change.

## 10. Testing

Unit tests, no framework beyond Python's `unittest`:
- `test_dst.py` — Nov 1 (US DST) and Oct 25 (EU DST) transitions produce correct UTC deltas.
- `test_pace.py` — collusion scenario (Team A × Team B play 20 matches, no other opponents) does not inflate their pace count.
- `test_wilson.py` — team-level rating matches individual-level formula for equivalent W/L records.
- `test_find_slot.py` — happy path (all 10 free), degenerate (nobody free), midnight-crossing windows, DST-crossing week.
- `test_ingest_team.py` — refuse team-tagged match where <3 declared team members are on that side.
- `test_qualification.py` — team below floor at cutoff is not on the prize podium even at higher rating.

All Python-side logic testable **without Discord credentials** — the Discord I/O layer is a thin translator with its own separate integration test.

## 11. Failures deliberately not solved in v1 *(from pre-mortem)*

| Failure | Why deferred |
|---|---|
| Opponent-strength adjustment (Elo) | Overkill for 4 teams; Wilson bound plus tiebreakers is honest with the data. |
| Bus factor of 1 | Hobby league; README documents setup; forking is the recovery path. |
| Team disband mid-season | Vanishingly unlikely; if it happens, human decision beats a rule. |
| Rules-lawyering framework | Group already trusts each other; add rules when needed, not preemptively. |
| Confirm-only fast path (no screenshot) | Loses the arithmetic checksum; add if screenshot burden proves intolerable. |
| `data.js` size at scale | 1.6MB projection is fine for wifi. Compress if someone complains. |
| Threshold cliff sensitivity | The 80% floor only matters at `cutoff_at` (Dec 31). Between now and then, the tier system (yellow/orange/red) provides a gradient of warning — a team briefly below 80% gets an orange DM, not immediate red-out. If they climb back above by cutoff, they qualify. |

## 12. Build order

Each step is testable in isolation. Steps 1–7 need no Discord token; only step 8 does.

| # | Deliverable | Test surface | Days |
|---|---|---|---|
| 1 | `data/teams.json` + `load.py` team-membership enforcement | unit + `python load.py` | 0.5 |
| 2 | Schema change (`radiant_team_id` / `dire_team_id`) + `ingest.py` validation | `test_ingest_team.py` | 0.5 |
| 3 | `tools/team_standings.py` (Wilson rating per team, pace calc) | `test_wilson.py`, `test_pace.py` | 1 |
| 4 | `export_web.py` extension for `teams` + `coord` blocks | manual inspection of `data.js` | 0.5 |
| 5 | Website: Teams tab (roster grid + standings table) | browser | 1 |
| 6 | `tools/find_slot.py` + `data/availability_*.json` schema | `test_find_slot.py`, `test_dst.py` | 1 |
| 7 | Website: Coord tab (Next Match + availability heatmap) | browser | 1 |
| 8 | Discord: extend `discord_commands.py` for new grammar | integration test in real channel | 1.5 |
| 9 | `tools/pace.py` + weekly-post generator | dry-run against real season data | 0.5 |
| 10 | `.github/workflows` extension for daily pace check | first live run | 0.5 |

Total: ~8 dev days.

## 13. Open items (approver decision during review)

- **Grace period list.** Christmas Dec 22–28 assumed; add others as needed.
- **`league-coord` channel ID.** Create channel, capture ID.
- **Team names.** Currently `"Team 1" .. "Team 4"`. If teams have picked names, use those.
- **Captain per team.** Defaulted to first player in `core`. Override in `teams.json` if needed.
- **Screenshot burden monitoring.** How many matches / week before we revisit confirm-only?
- **Prize disbursement mechanism.** Out of scope for the system; noting for the humans.

## 14. Dependencies on existing code

- `load.py`: schema migration for team_id columns; roster validation; deletion cleanup unchanged.
- `ingest.py`: team-pairing declaration; refuse-on-mismatch; existing arithmetic checksum unchanged.
- `stats.py`: no changes (individual reports still valid).
- `export_web.py`: emit two new payload blocks.
- `app.js`: two new views + Wilson function extracted to shared helper.
- `discord_commands.py`: new command grammar; existing merge/status commands unchanged.
- `discord_pull.py`: unchanged.
- `automerge.py`: unchanged.
- `.github/workflows/sync-matches.yml`: add pace-check step after ingest.

## 15. Risks not covered above

- **Cloud workflow has never run.** Per `CLAUDE.md`, no repo secrets set. Building on unproven ground. First real run of the workflow should be a stub that just posts "hello" — verify creds work before running real logic.
- **Season resets not yet designed.** After Dec 31, we need to archive 2026 and start 2027. Not blocking v1 but should be in v1.1.
- **Discord message rate limits.** At high volume (Sunday post + reactions across 4 teams) we may hit them. Bot should backoff-and-retry, not crash.
