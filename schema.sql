-- =====================================================================
-- Dota 2 inhouse stats tracker -- schema
-- Engine: SQLite 3
-- Grain: one row in match_players per (match, player). 10 rows per match.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- players
-- display_name is the FULL raw label as rendered by Dota, clan tag and
-- all ("HURR [PK_]"), because that string is what we actually observed.
-- base_name / clan_tag are split out so alias detection has something to
-- match on when someone renames themselves between games.
--
-- merged_into: soft alias pointer. If we later confirm that "dotagerm"
-- and "GerM" are the same human, we set merged_into on one of them
-- instead of deleting or rewriting history. All reporting views resolve
-- through it, but the raw observed names stay intact.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    id           INTEGER PRIMARY KEY,
    display_name TEXT    NOT NULL UNIQUE,
    base_name    TEXT    NOT NULL,
    clan_tag     TEXT,
    merged_into  INTEGER REFERENCES players(id),
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_players_base ON players(base_name);
CREATE INDEX IF NOT EXISTS idx_players_clan ON players(clan_tag);

-- ---------------------------------------------------------------------
-- matches
-- source_ref  : idempotency key -- which screenshot/import produced this
--               row. Present even when Dota's own match id was not
--               captured, so re-running the loader never duplicates.
-- dota_match_id: the real Dota match id. UNIQUE but nullable, so it can
--               be backfilled later without disturbing anything.
-- winning_side: the ONE place a result is recorded. Everything else
--               derives from it.
-- result_confidence: 'confirmed' = the screen stated the winner
--                    unambiguously; 'inferred' = we deduced it. Never
--                    silently launder a guess into a fact.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    id                INTEGER PRIMARY KEY,
    source_ref        TEXT    NOT NULL UNIQUE,
    dota_match_id     TEXT    UNIQUE,
    played_on         TEXT,            -- 'YYYY-MM-DD'      (date only)
    played_at         TEXT,            -- 'YYYY-MM-DD HH:MM' (NULL if unknown)
    duration_seconds  INTEGER,
    game_mode         TEXT,
    radiant_team_name TEXT,
    dire_team_name    TEXT,
    radiant_score     INTEGER,
    dire_score        INTEGER,
    winning_side      TEXT    NOT NULL CHECK (winning_side IN ('radiant','dire')),
    result_confidence TEXT    NOT NULL DEFAULT 'confirmed'
                              CHECK (result_confidence IN ('confirmed','inferred')),
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(played_on);

-- ---------------------------------------------------------------------
-- match_players
-- Note there is deliberately NO `won` column -- see v_results.
-- Columns beyond k/d/a are nullable because the OVERVIEW post-game tab
-- only exposes name + net worth + KDA, while the SCOREBOARD tab exposes
-- the full stat line. Both are valid sources; one is just richer.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS match_players (
    match_id        INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES players(id),
    side            TEXT    NOT NULL CHECK (side IN ('radiant','dire')),
    hero            TEXT,
    hero_level      INTEGER,
    kills           INTEGER NOT NULL,
    deaths          INTEGER NOT NULL,
    assists         INTEGER NOT NULL,
    net_worth       INTEGER,
    last_hits       INTEGER,
    denies          INTEGER,
    gpm             INTEGER,
    xpm             INTEGER,
    hero_damage     INTEGER,
    building_damage INTEGER,
    healing         INTEGER,
    -- Remaining SCOREBOARD-tab columns. Only that tab exposes these;
    -- OVERVIEW-sourced rows leave them NULL.
    bounty_runes        INTEGER,
    outposts            INTEGER,
    damage_received_raw INTEGER,
    damage_reduced_pct  REAL,     -- e.g. 48.6 for "48.6%"
    PRIMARY KEY (match_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_mp_player ON match_players(player_id);

-- =====================================================================
-- VIEWS
-- =====================================================================

-- Resolve alias chains one level deep: raw player row -> canonical row.
DROP VIEW IF EXISTS v_player;
CREATE VIEW v_player AS
SELECT p.id                                        AS raw_id,
       COALESCE(c.id, p.id)                        AS player_id,
       COALESCE(c.display_name, p.display_name)    AS player_name
FROM players p
LEFT JOIN players c ON c.id = p.merged_into;

-- The workhorse. Every appearance, with win/loss DERIVED, never stored.
DROP VIEW IF EXISTS v_results;
CREATE VIEW v_results AS
SELECT m.id                AS match_id,
       m.dota_match_id,
       m.source_ref,
       m.played_on,
       m.played_at,
       m.duration_seconds,
       m.game_mode,
       m.result_confidence,
       vp.player_id,
       vp.player_name,
       mp.side,
       mp.hero,
       mp.kills, mp.deaths, mp.assists, mp.net_worth,
       CASE WHEN mp.side = m.winning_side THEN 1 ELSE 0 END AS won
FROM match_players mp
JOIN matches  m  ON m.id      = mp.match_id
JOIN v_player vp ON vp.raw_id = mp.player_id;

-- THE headline table: win/loss record per player.
DROP VIEW IF EXISTS v_player_record;
CREATE VIEW v_player_record AS
SELECT player_name,
       COUNT(*)                                                   AS games,
       SUM(won)                                                   AS wins,
       COUNT(*) - SUM(won)                                        AS losses,
       ROUND(100.0 * SUM(won) / COUNT(*), 1)                      AS win_pct,
       -- NULL (not an error) when a player has never lost: W/L is undefined.
       ROUND(1.0 * SUM(won) / NULLIF(COUNT(*) - SUM(won), 0), 2)  AS wl_ratio,
       SUM(kills)                                                 AS kills,
       SUM(deaths)                                                AS deaths,
       SUM(assists)                                               AS assists,
       ROUND(1.0 * (SUM(kills) + SUM(assists))
             / NULLIF(SUM(deaths), 0), 2)                         AS kda,
       MIN(played_on)                                             AS first_seen,
       MAX(played_on)                                             AS last_seen
FROM v_results
GROUP BY player_id, player_name;

-- One row per match, newest first.
DROP VIEW IF EXISTS v_match_log;
CREATE VIEW v_match_log AS
SELECT m.id,
       m.dota_match_id,
       COALESCE(m.played_at, m.played_on)          AS played,
       m.game_mode,
       CASE WHEN m.duration_seconds IS NULL THEN NULL
            ELSE printf('%d:%02d', m.duration_seconds / 60,
                                   m.duration_seconds % 60) END   AS duration,
       COALESCE(m.radiant_team_name, 'Radiant')                   AS radiant,
       m.radiant_score,
       m.dire_score,
       COALESCE(m.dire_team_name, 'Dire')                         AS dire,
       m.winning_side,
       m.result_confidence,
       m.source_ref
FROM matches m
ORDER BY COALESCE(m.played_at, m.played_on) DESC, m.id DESC;

-- Per-player match-by-match log ("W/L with date of match").
DROP VIEW IF EXISTS v_player_match_log;
CREATE VIEW v_player_match_log AS
SELECT player_name,
       COALESCE(played_at, played_on) AS played,
       dota_match_id,
       side,
       CASE won WHEN 1 THEN 'WIN' ELSE 'LOSS' END AS result,
       hero,
       kills || '/' || deaths || '/' || assists   AS kda,
       net_worth
FROM v_results
ORDER BY player_name, played;
