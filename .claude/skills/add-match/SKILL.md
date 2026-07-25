---
name: add-match
description: Parse Dota 2 post-game screenshots into the lobby stats database, verify them arithmetically, and republish the dashboard. Use when the user pastes or points at Dota 2 screenshots, or says "add these matches", "log these games", "new games", "update the site", "add to the ledger", or names a folder of post-game captures.
---

# Add matches from screenshots

Turns Dota 2 post-game screenshots into verified rows in `data/matches.json`,
then rebuilds the database and republishes the dashboard.

**The split matters:** reading a screenshot is a judgement task and stays with
you. Everything after — checking arithmetic, refusing duplicates, rebuilding,
deploying — is mechanical and belongs to `tools/ingest.py`. Never hand-edit
`data/matches.json` and never hand-run the deploy; ingest does both, the same
way every time.

## 1. Find the screenshots

Default folder: `C:\Users\UT\Pictures\Screenshots`. Use whatever path the user
gives instead. List by modified time and read each one.

Check what is already recorded first, so you can tell new games from
re-captures of old ones:

```bash
python stats.py log
```

## 2. Identify each screenshot

Look at the highlighted tab in the top nav.

| | **OVERVIEW** | **SCOREBOARD** |
|---|---|---|
| Match ID, date, duration, mode | ✅ | ❌ |
| Team names, score, winner | ✅ | ✅ (explicit WINNER badge) |
| Player names, net worth, K/D/A | ✅ | ✅ |
| Hero names + levels | ❌ art only | ✅ |
| LH/DN, GPM, XPM, damage, healing | ❌ | ✅ |

They are complementary. If both tabs of the same match are present, merge them
into ONE record — do not create two. If only a SCOREBOARD is present, the match
has no ID/date/duration; that is acceptable, leave them `null`.

**When the two tabs disagree, the scoreboard wins.** It is structured data with
an explicit winner badge; the overview header has been observed rendering stale
text from a previously-viewed match.

## 3. Extract the fields

Per match: `source_ref` (unique, e.g. `screenshot-12`), `dota_match_id`,
`played_on` (`YYYY-MM-DD`), `played_at` (`YYYY-MM-DD HH:MM`), `duration_seconds`,
`game_mode`, `radiant_team_name`, `dire_team_name`, `radiant_score`,
`dire_score`, `winning_side` (`radiant`|`dire`), `result_confidence`
(`confirmed` if the screen states the winner, else `inferred`), `notes`.

Per player: `name`, `side`, `hero`, `hero_level`, `kills`, `deaths`, `assists`,
`net_worth`, and where the scoreboard shows them `last_hits`, `denies`, `gpm`,
`xpm`, `hero_damage`, `building_damage`, `healing`, `bounty_runes`, `outposts`,
`damage_received_raw`, `damage_reduced_pct`.

A generic "The Radiant" / "The Dire" heading means the team was unnamed — store
`null`, not the literal string.

## 4. Verify before ingesting

**The arithmetic checksum.** For each team:

```
sum(team kills)  <=  team score  <=  sum(enemy team deaths)
```

Left gap = a hero killed by a tower (credits the team, no player). Right gap =
a hero killed by neutrals/Roshan/itself (credits nobody). Usually all three are
equal. **A violation of the ordering is arithmetically impossible and means you
misread a digit** — go back and zoom rather than recording it.

Cross-checking one team's deaths against the other's kills is the strong form,
because it validates the death column too.

**Zoom on anything ambiguous.** Digits and clan tags are where errors live:

```bash
python tools/crop.py "<shot>.png" --rows          # standard bands
python tools/crop.py "<shot>.png" --grid          # find coordinates
python tools/crop.py "<shot>.png" 3080 1370 360 70 --scale 6
```

Then Read the printed path. Phone photos of a monitor are much less reliable
than PNG captures — a `[<MG>]` in one such photo turned out to be `[<MC>]`.

**Names have no checksum.** Kill totals catch a bad digit; nothing catches a
misread letter. Zoom on any player name you are not certain of.

## 5. Check identities

New spellings of an existing player split one human into two rows and make both
win rates wrong.

```bash
python stats.py aliases
```

Report candidates to the user and **ask** — never merge on your own judgement.
Half the merges in this project share no name root at all (`cpx22`/`Mandark`,
`samundar khan`/`rtz`, `Kael™`/`Dawn of War`); no heuristic finds those. When
the user confirms, add to the `aliases` array in `data/matches.json` as
`{"canonical": "...", "alias": "...", "note": "..."}` and re-run `load.py`.

## 6. Ingest

Write the parsed matches to a temp JSON file (one object, or an array), then:

```bash
python tools/ingest.py --from new.json --dry-run   # always dry-run first
python tools/ingest.py --from new.json --deploy    # write, rebuild, push
```

Ingest refuses the batch — writing nothing — on a roster that isn't 5-a-side,
an impossible arithmetic ordering, a duplicate `source_ref` or `dota_match_id`,
or a **content fingerprint** matching an existing match (the same roster and
K/D/A already recorded). That last one is the guard against adding a match
twice from a re-capture; if it fires, the game is already in the database.

`--deploy` commits and pushes; GitHub Pages rebuilds in about a minute.
`export_web.py` re-stamps the asset hash automatically, so returning visitors
cannot end up with a half-cached mix of old and new files.

## 7. Report

Tell the user: which matches were added, each one's result, any checksum notes,
any new alias candidates awaiting their decision, and the live URL —
https://muhammadut.github.io/total-dota-lobby-stats/

If ingest refused the batch, say exactly which rule fired and what you need in
order to proceed. Do not work around a refusal by editing `data/matches.json`
directly.
