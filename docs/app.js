/* ═══════════════════════════════════════════════════════════════════
   Total Dota Lobby Stats — view layer.
   Reads window.LOBBY (data.js) and window.HERO_SLUG (heroes.js).
   No framework, no build step: three static files GitHub Pages can
   serve forever.

   Standings and hero records are aggregated HERE rather than in the
   exporter, because the year filter has to recompute every average and
   win rate from whatever subset of matches is showing.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var D = window.LOBBY, SLUG = window.HERO_SLUG || {};
  if (!D) { console.error("data.js did not load"); return; }

  var CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var el = function (t, c, h) {
    var n = document.createElement(t);
    if (c) n.className = c;
    if (h !== undefined) n.innerHTML = h;
    return n;
  };
  // Everything from the data file passes through here before touching
  // innerHTML. Names in this lobby include "[<MC>]", "¤GerM¤" and "™".
  var esc = function (s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };
  var num = function (n) { return n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"); };
  var dur = function (s) { return s ? Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") : "—"; };
  var pct = function (n) { return n === null || n === undefined ? "—" : n.toFixed(1) + "%"; };
  var rat = function (n) { return n === null || n === undefined ? "∞" : n.toFixed(2); };

  var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function pretty(iso) {
    if (!iso) return "—";
    var p = iso.split(" "), d = p[0].split("-");
    var s = Number(d[2]) + " " + MON[Number(d[1]) - 1] + " " + d[0];
    return p[1] ? s + " · " + p[1] : s;
  }

  /* Hero art. icons/ ≈5 KB for inline avatars, full portrait ≈50 KB for
     the hero cards, renders/ (transparent) for the masthead band. */
  function heroSrc(hero, kind) {
    var s = SLUG[hero];
    if (!s) return "";
    if (kind === "icon")   return CDN + "/icons/" + s + ".png";
    if (kind === "render") return CDN.replace("/images/", "/videos/") + "/renders/" + s + ".png";
    return CDN + "/" + s + ".png";
  }
  function faceTag(hero, cls, kind) {
    var src = heroSrc(hero, kind || "icon");
    if (!src) return '<span class="' + cls + '"></span>';
    return '<img class="' + cls + '" src="' + src + '" alt="" loading="lazy" decoding="async"' +
           ' onerror="this.style.visibility=\'hidden\'">';
  }

  /* ── Rating ───────────────────────────────────────────────────────
     One number that orders the board without anyone having to pair a
     win rate with a "5+ games" filter by hand.

     Raw win rate cannot do it: a 1-0 record scores 100% and outranks
     every real season. A minimum-games cutoff only moves the problem —
     it is an arbitrary line, and it hides the people below it entirely.

     So rank by the LOWER BOUND of a 95% confidence interval on the win
     rate (Wilson). It answers "what win rate does this record actually
     support?", and evidence moves it on its own: 1-0 supports only
     20.7%, while 18-8 over 26 games supports 50.0%. More games at a
     good rate therefore always beats fewer games at a great one, with
     no threshold anywhere.

     50% is the exact population mean here — every match has five
     winners and five losers — so a score above 50 is genuinely and
     measurably above lobby average, not merely above the sample.

     z is how much certainty the score demands, and it is therefore the
     dial for how heavily games played counts. At z=1.96 a 3-1 record
     (30.1) just edges a 10-10 one (29.9) — statistically fair, but it
     puts a four-game player sixth. Raising z widens the interval, which
     costs a small sample far more than a large one, so volume rises:
     at 2.576 that same 3-1 falls behind four longer records, and at 3.0
     it drops nine places. Exposed as a control because "how much should
     turning up count?" is a matter of taste, not of statistics. */
  function wilson(wins, n, z) {
    if (!n) return 0;
    var p = wins / n, z2 = z * z;
    var centre = p + z2 / (2 * n);
    var margin = z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n));
    return Math.max(0, (centre - margin) / (1 + z2 / n)) * 100;
  }

  /* ── Aggregation ──────────────────────────────────────────────── */
  function aggregate(ms) {
    var P = {}, H = {};

    ms.forEach(function (m) {
      m.radiant.concat(m.dire).forEach(function (r) {
        var p = P[r.name];
        if (!p) {
          p = P[r.name] = { name: r.name, games: 0, wins: 0, losses: 0,
            kills: 0, deaths: 0, assists: 0, netSum: 0, netN: 0, bestNet: 0,
            gpmSum: 0, xpmSum: 0, rateN: 0, history: [], picks: {} };
        }
        p.games++;
        if (r.won) p.wins++; else p.losses++;
        p.kills += r.k; p.deaths += r.d; p.assists += r.a;
        if (r.net != null) { p.netSum += r.net; p.netN++; if (r.net > p.bestNet) p.bestNet = r.net; }
        if (r.gpm != null) { p.gpmSum += r.gpm; p.xpmSum += (r.xpm || 0); p.rateN++; }
        p.history.push({ seq: m.seq, played: m.played_at || m.played_on, won: r.won,
                         hero: r.hero, k: r.k, d: r.d, a: r.a, net: r.net });
        if (r.hero) p.picks[r.hero] = (p.picks[r.hero] || 0) + 1;

        if (r.hero) {
          var h = H[r.hero];
          if (!h) h = H[r.hero] = { hero: r.hero, picks: 0, wins: 0, losses: 0, who: {} };
          h.picks++;
          if (r.won) h.wins++; else h.losses++;
          // Per-player, not just a name set: a hero card that says
          // "picked 9 times" is only half an answer without "by whom".
          var q = h.who[r.name];
          if (!q) q = h.who[r.name] = { name: r.name, picks: 0, wins: 0, losses: 0 };
          q.picks++;
          if (r.won) q.wins++; else q.losses++;
        }
      });
    });

    var players = Object.keys(P).map(function (k) {
      var p = P[k];
      p.winPct = p.games ? (p.wins / p.games) * 100 : 0;
      // Undefined, not zero: dividing by no deaths has no answer.
      p.kda    = p.deaths ? (p.kills + p.assists) / p.deaths : null;
      p.avgGpm = p.rateN ? Math.round(p.gpmSum / p.rateN) : null;
      p.avgXpm = p.rateN ? Math.round(p.xpmSum / p.rateN) : null;
      p.avgNet = p.netN  ? Math.round(p.netSum / p.netN)  : null;
      p.history.sort(function (a, b) { return a.seq - b.seq; });
      p.heroes = Object.keys(p.picks)
        .map(function (h) { return { hero: h, picks: p.picks[h] }; })
        .sort(function (a, b) { return b.picks - a.picks || a.hero.localeCompare(b.hero); });
      p.topHero = p.heroes.length ? p.heroes[0].hero : null;
      return p;
    });

    var heroes = Object.keys(H).map(function (k) {
      var h = H[k];
      h.roster = Object.keys(h.who).map(function (n) { return h.who[n]; })
        .sort(function (a, b) { return b.picks - a.picks || b.wins - a.wins || a.name.localeCompare(b.name); });
      h.players = h.roster.map(function (q) { return q.name; });
      return h;
    }).sort(function (a, b) {
      return b.picks - a.picks || b.wins - a.wins || a.hero.localeCompare(b.hero);
    });

    return { players: players, heroes: heroes };
  }

  /* ── State ────────────────────────────────────────────────────── */
  var YEARS = D.meta.years || [];
  var state = {
    year: YEARS[0] || "all",
    // Rating, not games: it is the column that already accounts for both
    // sample size and win rate, so the default view needs no filter set.
    sortKey: "rating", sortDir: -1,
    qPlayers: "", minGames: 1,
    qHeroes: "", heroSort: "picks",
    qMatches: "", matchSort: "recent",
    duoPick: [], duoSort: "delta", duoMin: 2, qDuos: "",
    // 99%. Deliberately stricter than the textbook 95%: this is a
    // participation league, and a four-game record should not sit
    // alongside a twenty-game one.
    evidence: 2.576,
    // Schedule times default to Pakistan: it is the league's reference
    // clock, the one the slots were chosen in, and where most players are.
    fxZone: "Pakistan",
    fxView: "timeline"
  };
  var cur = { matches: [], players: [], heroes: [] };

  var hay = function (s) { return String(s || "").toLowerCase(); };
  var match = function (q, parts) {
    if (!q) return true;
    var needle = q.toLowerCase().trim();
    for (var i = 0; i < parts.length; i++) {
      if (hay(parts[i]).indexOf(needle) !== -1) return true;
    }
    return false;
  };

  // Wire a segmented pill group: sets state[key] from data-<attr> and redraws.
  function segment(id, attr, key, redraw, cast) {
    var wrap = $("#" + id);
    if (!wrap) return;
    wrap.addEventListener("click", function (e) {
      var b = e.target.closest(".seg");
      if (!b) return;
      var val = b.getAttribute("data-" + attr);
      state[key] = cast ? cast(val) : val;
      Array.prototype.forEach.call(wrap.querySelectorAll(".seg"), function (o) {
        o.classList.toggle("is-on", o === b);
      });
      redraw();
    });
  }

  // Typing should filter as you go, but redrawing 60 cards per keystroke is
  // wasteful — coalesce to one redraw per idle moment.
  function searchBox(id, key, redraw) {
    var el0 = $("#" + id);
    if (!el0) return;
    var t = null;
    el0.addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(function () { state[key] = el0.value; redraw(); }, 120);
    });
  }

  function applyYear() {
    // A match with no recorded date has no year, and must not silently
    // vanish from every view — show it under whichever year is selected.
    cur.matches = state.year === "all"
      ? D.matches.slice()
      : D.matches.filter(function (m) { return !m.year || m.year === state.year; });
    var agg = aggregate(cur.matches);
    cur.players = agg.players;
    cur.heroes  = agg.heroes;
    rescore();
  }

  // Rating depends on a setting the reader can change, so it is stamped
  // onto each player rather than computed inside aggregate() — the sort
  // comparator, the drawer and the Duos header all read p.rating, and
  // they must never disagree about which setting produced it.
  /* The raw lower bound tops out around 44 in a season this size, which
     reads like a broken percentage sitting next to a 69.2% win rate.
     Doubling it puts the field on a 0-100 scale with a fixed, meaningful
     ceiling: 100 is where the bound reaches 50%, i.e. where a record
     PROVES its owner beats a coin flip at the chosen confidence.

     The anchor is a constant, not a normalisation against the current
     field, so a player's rating never moves because somebody else
     played a game. Doubling is monotonic, so the order you approved is
     untouched. Scores above 100 are possible and are not an error --
     they mean the case is proven with room to spare -- but only on the
     most lenient setting, where the current best is 101. */
  var SCALE = 2;
  function rescore() {
    cur.players.forEach(function (p) {
      p.rating = wilson(p.wins, p.games, state.evidence) * SCALE;
    });
  }
  // Fixed bands, because the scale itself has fixed meaning: 70 is most
  // of the way to proven, 55 is a clearly winning record with evidence
  // behind it. Below that the number is not yet saying much, so it stays
  // grey rather than being coloured red -- a 3-2 record is not a failure,
  // it is an unfinished sentence.
  function ratingTier(r) {
    return r >= 70 ? " is-elite" : r >= 55 ? " is-strong" : "";
  }

  /* ── Masthead ─────────────────────────────────────────────────── */
  // A filmstrip of the most-drafted heroes, blurred back into the paper.
  // Deliberately the 53 KB portraits, NOT the transparent renders — those
  // are ~1.7 MB each, so a seven-hero band would cost 12 MB of masthead.
  function drawBand() {
    var art = $("#bandArt");
    art.innerHTML = "";
    var pool = cur.heroes.slice(0, 12);
    if (!pool.length) return;
    var need = 14, k = 0;
    while (k < need) {
      var h = pool[k % pool.length];
      var src = heroSrc(h.hero, "art");
      if (src) {
        var img = new Image();
        img.src = src; img.alt = ""; img.decoding = "async";
        img.onerror = function () { this.remove(); };
        art.appendChild(img);
      }
      k++;
    }
  }

  function drawTally() {
    // Deliberately NOT showing the player-game count (matches x 10). It is
    // an internal integrity figure -- the loader checks that every player's
    // wins and losses sum to it -- and next to "Matches 7" a second, larger
    // count just reads as a contradiction.
    var kills = 0, longest = 0, played = 0;
    cur.matches.forEach(function (m) {
      m.radiant.concat(m.dire).forEach(function (r) { kills += r.k; });
      if (m.duration_seconds) {
        played += m.duration_seconds;
        if (m.duration_seconds > longest) longest = m.duration_seconds;
      }
    });
    var hrs = played / 3600;
    var items = [
      ["Matches", cur.matches.length, "Matches played in this lobby"],
      ["Players", cur.players.length, "Distinct people, after merging renamed accounts"],
      ["Heroes", cur.heroes.length, "Distinct heroes picked at least once"],
      ["Kills", num(kills), "Hero kills across every match"],
      ["Longest", longest ? dur(longest) : "—", "Longest match of the season"],
      ["Hours", hrs ? hrs.toFixed(1) : "—", "Total time in game, where duration was captured"]
    ];
    var wrap = $("#tally");
    wrap.innerHTML = "";
    items.forEach(function (t) {
      var d = el("div"); d.title = t[2];
      d.appendChild(el("dt", null, esc(t[0])));
      d.appendChild(el("dd", null, esc(t[1])));
      wrap.appendChild(d);
    });

    // Don't hardcode the game mode — the lobby plays Captains Mode and All
    // Pick, and a mode named in the tagline that isn't universal is a lie.
    var label = state.year === "all" ? "All time" : state.year;
    var modes = {};
    cur.matches.forEach(function (m) { if (m.game_mode) modes[m.game_mode] = 1; });
    var names = Object.keys(modes);
    var suffix = names.length === 1 ? " of " + names[0] : "";
    $("#tagline").textContent = cur.matches.length
      ? label + " · " + cur.matches.length + " matches" + suffix
      : label + " · nothing recorded yet";
  }

  function drawYears() {
    var wrap = $("#yearFilter");
    wrap.innerHTML = "";
    var opts = YEARS.slice();
    if (YEARS.length > 1) opts.push("all");
    opts.forEach(function (y) {
      var b = el("button", "year" + (y === state.year ? " is-on" : ""),
                 y === "all" ? "All time" : esc(y));
      b.setAttribute("aria-pressed", y === state.year ? "true" : "false");
      b.addEventListener("click", function () {
        if (state.year === y) return;
        state.year = y;
        renderAll();
      });
      wrap.appendChild(b);
    });
    // A lone year is a label, not a choice — but keep it visible so the
    // control doesn't appear out of nowhere when 2027 arrives.
    wrap.hidden = opts.length === 0;
  }

  /* ── Standings ────────────────────────────────────────────────── */
  function drawStandings() {
    var tb = $("#standings tbody");
    tb.innerHTML = "";
    var k = state.sortKey, dir = state.sortDir;

    var list = cur.players.filter(function (p) {
      if (p.games < state.minGames) return false;
      return match(state.qPlayers, [p.name, p.topHero]);
    }).sort(function (a, b) {
      var x = a[k], y = b[k];
      if (k === "name") return dir * String(x).localeCompare(String(y));
      if (x === null || x === undefined) x = dir === -1 ?  Infinity : -Infinity;
      if (y === null || y === undefined) y = dir === -1 ?  Infinity : -Infinity;
      if (x !== y) return dir * (x - y);
      // Ties break on evidence, then performance — so sorting by games
      // can never put a 1-4 record above a 3-2 one.
      if (a.games !== b.games)   return b.games - a.games;
      if (a.winPct !== b.winPct) return b.winPct - a.winPct;
      return String(a.name).localeCompare(String(b.name));
    });

    list.forEach(function (p, i) {
      var tr = el("tr");
      tr.style.setProperty("--i", i);
      if (i < 3 && (k === "rating" || k === "games")) tr.classList.add("is-lead");
      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      tr.setAttribute("aria-label", "Record for " + p.name);
      tr.innerHTML =
        '<td><span class="rank">' + (i + 1) + "</span></td>" +
        '<td class="c-player"><span class="who">' + faceTag(p.topHero, "who__face") +
          '<span><span class="who__name">' + esc(p.name) + "</span>" +
          (p.topHero ? '<span class="who__hero">' + esc(p.topHero) + "</span>" : "") +
          "</span></span></td>" +
        "<td>" + p.games + "</td>" +
        '<td class="w-num">' + p.wins + "</td>" +
        '<td class="l-num">' + p.losses + "</td>" +
        '<td><div class="meter"><span style="width:' + p.winPct.toFixed(1) + '%"></span></div></td>' +
        '<td class="pct">' + pct(p.winPct) + "</td>" +
        '<td class="c-kda">' + p.kills + " / " + p.deaths + " / " + p.assists + "</td>" +
        "<td>" + rat(p.kda) + "</td>" +
        '<td class="c-opt dim">' + num(p.avgGpm) + "</td>" +
        '<td class="c-rating"><span class="rating' + ratingTier(p.rating) + '">' +
          p.rating.toFixed(1) + "</span></td>";
      tr.addEventListener("click", function () { openDrawer(p); });
      tr.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDrawer(p); }
      });
      tb.appendChild(tr);
    });

    if (!list.length) {
      tb.innerHTML = '<tr><td colspan="11" class="none">' +
        'No players match that filter.</td></tr>';
    }
  }

  function wireSort() {
    Array.prototype.forEach.call(document.querySelectorAll("#standings th[data-sort]"), function (th) {
      th.addEventListener("click", function () {
        var k = th.getAttribute("data-sort");
        if (k === state.sortKey) state.sortDir = -state.sortDir;
        else { state.sortKey = k; state.sortDir = (k === "name") ? 1 : -1; }
        Array.prototype.forEach.call(document.querySelectorAll("#standings th"), function (o) {
          o.removeAttribute("aria-sort");
        });
        th.setAttribute("aria-sort", state.sortDir === 1 ? "ascending" : "descending");
        drawStandings();
      });
    });
  }

  /* ── Matches ──────────────────────────────────────────────────── */
  function board(sideCls, roster, label, won) {
    var t = el("table", "board");
    t.appendChild(el("caption", sideCls, esc(label) + (won ? " — victory" : "")));
    var head = el("tr");
    ["Player", "Lvl", "K / D / A", "Net", "LH / DN", "GPM", "XPM", "Hero dmg"]
      .forEach(function (h) { head.appendChild(el("th", null, h)); });
    var th = el("thead"); th.appendChild(head); t.appendChild(th);

    var tb = el("tbody");
    roster.forEach(function (r) {
      var tr = el("tr");
      tr.innerHTML =
        '<td><span class="b-who">' + faceTag(r.hero, "b-face") +
          '<span><span class="b-name">' + esc(r.name) + "</span><br>" +
          '<span class="b-hero">' + esc(r.hero || "—") + "</span></span></span></td>" +
        "<td>" + (r.lvl || "—") + "</td>" +
        "<td>" + r.k + " / " + r.d + " / " + r.a + "</td>" +
        "<td>" + num(r.net) + "</td>" +
        "<td>" + num(r.lh) + " / " + num(r.dn) + "</td>" +
        "<td>" + num(r.gpm) + "</td>" +
        "<td>" + num(r.xpm) + "</td>" +
        "<td>" + num(r.hdmg) + "</td>";
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    return t;
  }

  function drawMatches() {
    var wrap = $("#matches");
    wrap.innerHTML = "";

    var list = cur.matches.filter(function (m) {
      if (!state.qMatches) return true;
      var parts = [m.radiant_team_name, m.dire_team_name, m.dota_match_id];
      m.radiant.concat(m.dire).forEach(function (r) { parts.push(r.name, r.hero); });
      return match(state.qMatches, parts);
    });

    var order = state.matchSort;
    if (order === "recent")      list = list.slice().reverse();
    else if (order === "oldest") list = list.slice();
    else if (order === "kills")  list = list.slice().sort(function (a, b) {
      return (b.radiant_score + b.dire_score) - (a.radiant_score + a.dire_score);
    });

    if (!list.length) {
      wrap.innerHTML = '<p class="none">No matches contain that.</p>';
      return;
    }

    list.forEach(function (m, i) {
      var rWon = m.winning_side === "radiant";
      var card = el("div", "match");
      card.style.setProperty("--i", i);

      var head = el("button", "match__head");
      head.setAttribute("aria-expanded", "false");
      head.innerHTML =
        '<span class="match__seq">' + String(m.seq).padStart(2, "0") + "</span>" +
        '<span class="team team--radiant' + (rWon ? "" : " is-loser") + '">' +
          '<span class="team__name">' + esc(m.radiant_team_name || "The Radiant") + "</span>" +
          '<span class="team__tag">Radiant</span></span>' +
        '<span class="score">' +
          '<span class="' + (rWon ? "win" : "") + '">' + m.radiant_score + "</span>" +
          '<span class="sep">–</span>' +
          '<span class="' + (rWon ? "" : "win") + '">' + m.dire_score + "</span></span>" +
        '<span class="team team--dire team--r' + (rWon ? " is-loser" : "") + '">' +
          '<span class="team__name">' + esc(m.dire_team_name || "The Dire") + "</span>" +
          '<span class="team__tag">Dire</span></span>' +
        '<span class="match__meta"><span>' + esc(dur(m.duration_seconds)) + "</span>" +
          "<span>" + esc(m.played_at ? m.played_at.split(" ")[1] : "—") + "</span>" +
          '<span class="chev" aria-hidden="true"></span></span>';

      var body = el("div", "match__body"), inner = el("div", "match__inner");
      var w1 = el("div", "board-wrap");
      w1.appendChild(board("radiant", m.radiant, m.radiant_team_name || "The Radiant", rWon));
      var w2 = el("div", "board-wrap");
      w2.appendChild(board("dire", m.dire, m.dire_team_name || "The Dire", !rWon));
      inner.appendChild(w1); inner.appendChild(w2);
      if (m.notes) inner.appendChild(el("p", "match__note", esc(m.notes)));
      body.appendChild(inner);

      head.addEventListener("click", function () {
        var open = card.classList.toggle("is-open");
        head.setAttribute("aria-expanded", open ? "true" : "false");
      });
      card.appendChild(head); card.appendChild(body);
      wrap.appendChild(card);
    });
  }

  /* ── Heroes ───────────────────────────────────────────────────── */
  function drawHeroes() {
    var wrap = $("#heroes");
    wrap.innerHTML = "";

    var list = cur.heroes.filter(function (h) {
      return match(state.qHeroes, [h.hero].concat(h.players));
    });
    if (state.heroSort === "win") {
      list = list.slice().sort(function (a, b) {
        // Win rate alone would float a 1-pick 100% above a 4-pick 75%, so
        // ties and near-ties fall back to sample size.
        var wa = a.wins / a.picks, wb = b.wins / b.picks;
        return (wb - wa) || (b.picks - a.picks) || a.hero.localeCompare(b.hero);
      });
    } else if (state.heroSort === "name") {
      list = list.slice().sort(function (a, b) { return a.hero.localeCompare(b.hero); });
    }

    if (!list.length) {
      wrap.innerHTML = '<p class="none">No heroes match that.</p>';
      return;
    }

    list.forEach(function (h, i) {
      var w = Math.round((h.wins / h.picks) * 100);
      var src = heroSrc(h.hero, "art");
      var c = el("button", "hcard");
      c.type = "button";
      c.setAttribute("aria-label", "Who has played " + h.hero);
      c.style.setProperty("--i", i);
      c.innerHTML =
        '<div class="hcard__art">' +
          '<span class="hcard__picks">' + h.picks + (h.picks === 1 ? " pick" : " picks") + "</span>" +
          (src ? '<img src="' + src + '" alt="" loading="lazy" decoding="async"' +
                 ' onerror="this.style.display=\'none\'">' : "") +
        "</div>" +
        '<div class="hcard__body">' +
          '<div class="hcard__name">' + esc(h.hero) + "</div>" +
          '<div class="hcard__row"><span class="hcard__wl"><b>' + h.wins + "W</b> · <i>" +
            h.losses + "L</i></span><span>" + w + "%</span></div>" +
          '<div class="hcard__meter"><span style="width:' + w + '%"></span></div>' +
          '<div class="hcard__who">' + esc(h.players.join(", ")) + "</div>" +
        "</div>";
      c.addEventListener("click", function () { openHeroDrawer(h); });
      wrap.appendChild(c);
    });
  }

  /* ── Duos: with / without ─────────────────────────────────────────
     "Am I better with this person, or is it just that we both play a
     lot?" needs three records side by side, not one.

     WITH    = they were on my team
     WITHOUT = they were not on my team — includes games they sat out
               AND games they were on the enemy side
     AGAINST = they were on the enemy team (a subset of WITHOUT)

     WITHOUT deliberately folds in the enemy games. The question is
     "does having them beside me help?", and the honest comparison is
     every other game I played, not a hand-picked slice of them. AGAINST
     is broken out separately because it answers a different question
     and would otherwise be invisible. */
  function sideOf(m, name) {
    for (var i = 0; i < m.radiant.length; i++) if (m.radiant[i].name === name) return "radiant";
    for (var j = 0; j < m.dire.length; j++)    if (m.dire[j].name === name)    return "dire";
    return null;
  }
  function rec() { return { g: 0, w: 0, l: 0 }; }
  function add(r, won) { r.g++; if (won) r.w++; else r.l++; return r; }
  function pctOf(r) { return r.g ? (r.w / r.g) * 100 : null; }

  // Every teammate/opponent split for one player, in a single pass.
  function partners(name) {
    var out = {};
    function slot(n) {
      if (!out[n]) out[n] = { name: n, with: rec(), without: rec(), against: rec() };
      return out[n];
    }
    var mine = [];
    cur.matches.forEach(function (m) {
      var side = sideOf(m, name);
      if (!side) return;
      var team = side === "radiant" ? m.radiant : m.dire;
      var foe  = side === "radiant" ? m.dire    : m.radiant;
      var won  = m.winning_side === side;
      mine.push({ m: m, won: won, mates: {}, foes: {} });
      var last = mine[mine.length - 1];
      team.forEach(function (r) { if (r.name !== name) last.mates[r.name] = 1; });
      foe.forEach(function (r) { last.foes[r.name] = 1; });
    });
    // Anyone who ever shared a match, on either side, gets a row.
    var seen = {};
    mine.forEach(function (g) {
      Object.keys(g.mates).forEach(function (n) { seen[n] = 1; });
      Object.keys(g.foes).forEach(function (n) { seen[n] = 1; });
    });
    Object.keys(seen).forEach(function (n) {
      var s = slot(n);
      mine.forEach(function (g) {
        if (g.mates[n]) add(s.with, g.won);
        else            add(s.without, g.won);
        if (g.foes[n])  add(s.against, g.won);
      });
    });
    return { rows: Object.keys(out).map(function (n) { return out[n]; }), games: mine.length };
  }

  // Combined view for a selected set: all on one team, or split apart.
  function squad(names) {
    var together = rec(), split = rec(), perSolo = {}, perSplit = {};
    names.forEach(function (n) { perSolo[n] = rec(); perSplit[n] = rec(); });
    cur.matches.forEach(function (m) {
      var sides = names.map(function (n) { return sideOf(m, n); });
      var present = sides.filter(Boolean);
      if (!present.length) return;
      var all = present.length === names.length;
      var same = all && present.every(function (s) { return s === present[0]; });
      var opposed = present.length > 1 &&
                    !present.every(function (s) { return s === present[0]; });
      if (same) {
        add(together, m.winning_side === present[0]);
      } else if (opposed) {
        split.g++;   // they were on opposing sides; nobody "wins" as a group
        // ...but the individuals certainly did, and for two people that is
        // just the head-to-head record — the obvious question to ask.
        names.forEach(function (n, i) {
          if (sides[i]) add(perSplit[n], m.winning_side === sides[i]);
        });
      }
      // Each member's record in games where the full squad was NOT together.
      names.forEach(function (n, i) {
        if (sides[i] && !same) add(perSolo[n], m.winning_side === sides[i]);
      });
    });
    return { together: together, split: split, apart: perSolo, versus: perSplit };
  }

  function statCard(label, r, hint) {
    var p = pctOf(r);
    return '<div class="duo-stat">' +
      '<p class="duo-stat__label">' + esc(label) + "</p>" +
      '<p class="duo-stat__big">' + (p === null ? "—" : p.toFixed(0) + "<i>%</i>") + "</p>" +
      '<p class="duo-stat__sub">' + r.g + (r.g === 1 ? " game · " : " games · ") +
        "<b>" + r.w + "W</b> <i>" + r.l + "L</i></p>" +
      (hint ? '<p class="duo-stat__hint">' + esc(hint) + "</p>" : "") +
      "</div>";
  }

  function drawDuos() {
    var pickWrap = $("#duoPicker"), body = $("#duoBody");
    if (!pickWrap) return;

    // ── the picker ──
    var roster = cur.players.slice().sort(function (a, b) {
      return b.games - a.games || a.name.localeCompare(b.name);
    });
    var q = state.qDuos.toLowerCase().trim();
    pickWrap.innerHTML = "";
    roster.forEach(function (p) {
      var on = state.duoPick.indexOf(p.name) !== -1;
      if (q && !on && hay(p.name).indexOf(q) === -1) return;
      var b = el("button", "chip" + (on ? " is-on" : ""),
        esc(p.name) + '<span class="chip__n">' + p.games + "</span>");
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.addEventListener("click", function () {
        var i = state.duoPick.indexOf(p.name);
        if (i === -1) state.duoPick.push(p.name); else state.duoPick.splice(i, 1);
        drawDuos();
      });
      pickWrap.appendChild(b);
    });

    var sel = state.duoPick.filter(function (n) {
      return cur.players.some(function (p) { return p.name === n; });
    });

    if (!sel.length) {
      body.innerHTML = '<p class="none">Pick a player above to see who they win with — ' +
        "and who they win without. Pick several to check the squad.</p>";
      return;
    }

    var h = "";

    /* ── one player: the full teammate ledger ── */
    if (sel.length === 1) {
      var me = cur.players.filter(function (p) { return p.name === sel[0]; })[0];
      var pr = partners(sel[0]);

      h += '<div class="duo-head">' + faceTag(me.topHero, "duo-head__face") +
        "<div><h3>" + esc(me.name) + "</h3>" +
        "<p>" + me.games + " games · " + me.wins + "W " + me.losses + "L · " +
          pct(me.winPct) + " overall · rating " + me.rating.toFixed(1) + "</p></div></div>";

      var rows = pr.rows.filter(function (r) { return r.with.g >= state.duoMin; });
      rows.sort(function (a, b) {
        if (state.duoSort === "games") return b.with.g - a.with.g || a.name.localeCompare(b.name);
        if (state.duoSort === "name")  return a.name.localeCompare(b.name);
        var da = (pctOf(a.with) || 0) - (pctOf(a.without) === null ? 0 : pctOf(a.without));
        var db = (pctOf(b.with) || 0) - (pctOf(b.without) === null ? 0 : pctOf(b.without));
        return db - da || b.with.g - a.with.g;
      });

      h += '<div class="card table-card"><div class="table-scroll">' +
        '<table class="grid duo-grid"><thead><tr>' +
        '<th class="c-player">Alongside</th>' +
        '<th class="c-num">Together</th><th class="c-num">W–L</th><th class="c-num">With</th>' +
        '<th class="c-num">Without</th><th class="c-num">Swing</th>' +
        '<th class="c-bar">With / without</th>' +
        '<th class="c-num c-opt">Against</th>' +
        "</tr></thead><tbody>";

      if (!rows.length) {
        h += '<tr><td colspan="8" class="none">No one has played ' + state.duoMin +
             "+ games on their team.</td></tr>";
      }
      rows.forEach(function (r) {
        var pw = pctOf(r.with), po = pctOf(r.without), ag = pctOf(r.against);
        var d = (po === null || pw === null) ? null : pw - po;
        h += "<tr>" +
          '<td class="c-player"><span class="who"><span class="who__name">' + esc(r.name) +
            "</span></span></td>" +
          "<td>" + r.with.g + "</td>" +
          '<td class="c-kda"><b class="w-num">' + r.with.w + "</b>–<b class=\"l-num\">" +
            r.with.l + "</b></td>" +
          '<td class="pct">' + (pw === null ? "—" : pw.toFixed(0) + "%") + "</td>" +
          '<td class="dim">' + (po === null ? "—" : po.toFixed(0) + "%") + "</td>" +
          '<td class="' + (d === null ? "dim" : d > 0 ? "w-num" : d < 0 ? "l-num" : "dim") + '">' +
            (d === null ? "—" : (d > 0 ? "+" : "") + d.toFixed(0)) + "</td>" +
          '<td><div class="duo-bars">' +
            '<div class="duo-bar"><span style="width:' + (pw || 0).toFixed(1) + '%"></span></div>' +
            '<div class="duo-bar duo-bar--ghost"><span style="width:' +
              (po === null ? 0 : po).toFixed(1) + '%"></span></div>' +
          "</div></td>" +
          '<td class="c-opt dim">' + (r.against.g ? r.against.w + "–" + r.against.l +
            " · " + (ag === null ? "—" : ag.toFixed(0) + "%") : "—") + "</td>" +
          "</tr>";
      });
      h += "</tbody></table></div></div>" +
        '<p class="note"><b>With</b> is games they were on your team. <b>Without</b> is every ' +
        "other game you played — including ones they were on the enemy side, because the " +
        "honest comparison is your whole record, not a chosen slice of it. " +
        "<b>Swing</b> is the gap between the two, in points.</p>";

    /* ── several players: does the squad hold up together? ── */
    } else {
      var sq = squad(sel);
      h += '<div class="duo-head"><div><h3>' + esc(sel.join("  +  ")) + "</h3>" +
        "<p>" + sel.length + " players selected</p></div></div>";
      /* The second card used to read "Split up — 9 games, neither won nor
         lost by the group", which is true and completely unhelpful: a bare
         count of games with no result attached. For two people those games
         have an obvious meaning — it is the head-to-head — so show that
         instead. For three or more there is no single scoreline, so name
         each player's record and say plainly what the card is counting. */
      var opp;
      if (sel.length === 2) {
        var va = sq.versus[sel[0]], vb = sq.versus[sel[1]];
        opp = '<div class="duo-stat">' +
          '<p class="duo-stat__label">Head to head</p>' +
          '<p class="duo-stat__big">' + va.w + '<i class="vs">&ndash;</i>' + vb.w + "</p>" +
          '<p class="duo-stat__sub">' + esc(sel[0]) + " won " + va.w +
            " &middot; " + esc(sel[1]) + " won " + vb.w + "</p>" +
          '<p class="duo-stat__hint">' + sq.split.g +
            (sq.split.g === 1 ? " game" : " games") + " on opposite teams</p></div>";
      } else {
        opp = '<div class="duo-stat">' +
          '<p class="duo-stat__label">Against each other</p>' +
          '<p class="duo-stat__big">' + sq.split.g +
            "<i>" + (sq.split.g === 1 ? " game" : " games") + "</i></p>" +
          '<p class="duo-stat__sub">split across both teams, so there is ' +
            "no shared result</p>" +
          '<p class="duo-stat__hint">' + sel.map(function (n) {
            return esc(n) + " " + sq.versus[n].w + "&ndash;" + sq.versus[n].l;
          }).join(" &middot; ") + "</p></div>";
      }

      h += '<div class="duo-stats">' +
        statCard("On the same team", sq.together, "all " + sel.length + " together") +
        opp + "</div>";

      h += '<p class="d-sub">Each of them, when the squad was not together</p>' +
        '<div class="card table-card"><div class="table-scroll">' +
        '<table class="grid duo-grid"><thead><tr><th class="c-player">Player</th>' +
        '<th class="c-num">Apart</th><th class="c-num">W–L</th><th class="c-num">Win rate</th>' +
        '<th class="c-num">With squad</th><th class="c-num">Swing</th></tr></thead><tbody>';
      var tp = pctOf(sq.together);
      sel.forEach(function (n) {
        var r = sq.apart[n], p2 = pctOf(r);
        var d = (tp === null || p2 === null) ? null : tp - p2;
        h += "<tr>" +
          '<td class="c-player"><span class="who"><span class="who__name">' + esc(n) + "</span></span></td>" +
          "<td>" + r.g + "</td>" +
          '<td class="c-kda"><b class="w-num">' + r.w + '</b>–<b class="l-num">' + r.l + "</b></td>" +
          '<td class="dim">' + (p2 === null ? "—" : p2.toFixed(0) + "%") + "</td>" +
          '<td class="pct">' + (tp === null ? "—" : tp.toFixed(0) + "%") + "</td>" +
          '<td class="' + (d === null ? "dim" : d > 0 ? "w-num" : d < 0 ? "l-num" : "dim") + '">' +
            (d === null ? "—" : (d > 0 ? "+" : "") + d.toFixed(0)) + "</td></tr>";
      });
      h += "</tbody></table></div></div>" +
        '<p class="note">A squad only has a shared win rate when every selected player was on ' +
        "the <b>same team</b>. Games where they were split across both sides are counted " +
        "separately — one of them won and one of them lost, so the group did neither.</p>";
    }

    body.innerHTML = h;
  }

  /* ── Drawer ───────────────────────────────────────────────────── */
  var drawer = $("#drawer"), scrim = $("#scrim"), lastFocus = null;

  function openDrawer(p) {
    lastFocus = document.activeElement;
    var h =
      '<div class="d-top">' + faceTag(p.topHero, "d-face") +
        '<div><p class="d-eyebrow">' +
          (state.year === "all" ? "All time" : state.year) + "</p>" +
          '<h2 class="d-name" id="drawerName">' + esc(p.name) + "</h2></div></div>" +
      '<dl class="d-stats">' +
        [["Games", p.games], ["Won", p.wins], ["Lost", p.losses],
         ["Win rate", pct(p.winPct)], ["Rating", p.rating.toFixed(1)], ["KDA", rat(p.kda)]]
        .map(function (s) { return "<div><dt>" + esc(s[0]) + "</dt><dd>" + esc(s[1]) + "</dd></div>"; })
        .join("") + "</dl>" +
      '<p class="d-sub">Averages</p><dl class="d-stats">' +
        [["GPM", num(p.avgGpm)], ["XPM", num(p.avgXpm)],
         ["Net worth", num(p.avgNet)], ["Best net", num(p.bestNet)]]
        .map(function (s) { return "<div><dt>" + esc(s[0]) + "</dt><dd>" + esc(s[1]) + "</dd></div>"; })
        .join("") + "</dl>" +
      '<p class="d-sub">Match by match</p>';

    p.history.forEach(function (m) {
      h += '<div class="d-row">' +
        '<span class="d-pill ' + (m.won ? "w" : "l") + '">' + (m.won ? "Win" : "Loss") + "</span>" +
        faceTag(m.hero, "d-face-sm") +
        '<span><span class="d-hero">' + esc(m.hero || "—") + "</span><br>" +
          '<span class="d-when">' + esc(pretty(m.played)) + "</span></span>" +
        '<span class="d-kda">' + m.k + "/" + m.d + "/" + m.a +
          '<br><span class="d-when">' + num(m.net) + " net</span></span></div>";
    });

    if (p.heroes.length > 1) {
      h += '<p class="d-sub">Most played</p>';
      p.heroes.forEach(function (x) {
        h += '<div class="d-row"><span class="d-pill w">' + x.picks + "</span>" +
             faceTag(x.hero, "d-face-sm") +
             '<span class="d-hero">' + esc(x.hero) + "</span>" +
             '<span class="d-kda">' + (x.picks === 1 ? "once" : x.picks + "×") + "</span></div>";
      });
    }

    $("#drawerBody").innerHTML = h;
    drawer.hidden = false; scrim.hidden = false;
    document.body.style.overflow = "hidden";
    $("#drawerClose").focus();
  }

  // Who has played this hero, how often, and how it went for each of them.
  function openHeroDrawer(h) {
    lastFocus = document.activeElement;
    var w = Math.round((h.wins / h.picks) * 100);
    var art = heroSrc(h.hero, "art");
    var s =
      '<div class="d-hero-art">' +
        (art ? '<img src="' + art + '" alt="" decoding="async" onerror="this.remove()">' : "") +
        '<div class="d-hero-cap"><p class="d-eyebrow">' +
          (state.year === "all" ? "All time" : state.year) + "</p>" +
          '<h2 class="d-name" id="drawerName">' + esc(h.hero) + "</h2></div></div>" +
      '<dl class="d-stats">' +
        [["Picks", h.picks], ["Won", h.wins], ["Lost", h.losses],
         ["Win rate", w + "%"], ["Players", h.roster.length]]
        .map(function (x) { return "<div><dt>" + esc(x[0]) + "</dt><dd>" + esc(x[1]) + "</dd></div>"; })
        .join("") + "</dl>" +
      '<p class="d-sub">Picked by</p>';

    h.roster.forEach(function (q) {
      var qw = Math.round((q.wins / q.picks) * 100);
      s += '<div class="d-row d-row--hero">' +
        '<span class="d-pill ' + (q.wins >= q.losses ? "w" : "l") + '">' + q.picks + "&times;</span>" +
        '<span><span class="d-hero">' + esc(q.name) + "</span><br>" +
          '<span class="d-when">' + q.wins + "W · " + q.losses + "L</span></span>" +
        '<span class="d-mini"><span style="width:' + qw + '%"></span></span>' +
        '<span class="d-kda">' + qw + "%</span></div>";
    });

    $("#drawerBody").innerHTML = s;
    drawer.hidden = false; scrim.hidden = false;
    document.body.style.overflow = "hidden";
    $("#drawerClose").focus();
  }

  function closeDrawer() {
    drawer.hidden = true; scrim.hidden = true;
    document.body.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }

  /* ── Tabs ─────────────────────────────────────────────────────────
     The tab lives in location.hash so a view can be linked to — "look
     at #duos=HURR%20%5BPK_%5D" is the natural way to share a finding
     with the person it is about, and it costs one line to support. */
  function showTab(name) {
    var view = $("#view-" + name);
    if (!view) return false;
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (o) {
      var on = o.getAttribute("data-view") === name;
      o.classList.toggle("is-active", on);
      o.setAttribute("aria-selected", on ? "true" : "false");
    });
    Array.prototype.forEach.call(document.querySelectorAll(".view"), function (v) {
      v.classList.remove("is-active");
    });
    view.classList.add("is-active");
    return true;
  }

  function fromHash() {
    var raw = decodeURIComponent(String(location.hash || "").replace(/^#/, ""));
    if (!raw) return;
    var eq = raw.indexOf("="), name = eq === -1 ? raw : raw.slice(0, eq);
    if (eq !== -1 && name === "duos") {
      // Names can contain commas? None do, but split on the pipe instead
      // so a future "Fear, Inc" cannot silently become two players.
      state.duoPick = raw.slice(eq + 1).split("|").filter(Boolean);
    }
    if (showTab(name)) drawDuos();
  }

  function wireTabs() {
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
      t.addEventListener("click", function () {
        var name = t.getAttribute("data-view");
        showTab(name);
        var frag = name === "duos" && state.duoPick.length
          ? "duos=" + state.duoPick.map(encodeURIComponent).join("|") : name;
        if (history.replaceState) history.replaceState(null, "", "#" + frag);
        else location.hash = frag;
      });
    });
    window.addEventListener("hashchange", fromHash);
  }

  /* ── Teams (league) ───────────────────────────────────────────────
     The Teams tab shows the four fixed rosters and a team-level
     standings table using the SAME Wilson rating as the individual
     board — win-rate lower bound, so 5-0 does not beat 12-2. */

  var LEAGUE = D.league || null;

  /* A match counts towards a team's record ONLY if it was explicitly
     recorded against a scheduled series. It used to be *inferred* -- any
     match after the season start where 3+ players on one side shared a
     team was booked as a league result. That is wrong here: these people
     play inhouse together every night, so a casual pub game whose sides
     happened to line up became a permanent win. It put Team 3 on 1-0 and
     Team 1 on 0-1 before a single league match had been played.

     Guessing is exactly the failure this project cannot have, so team
     records now come only from results tied to a fixture. Until results
     are recorded, every team correctly reads 0. */
  function leagueGames() {
    var out = {};
    if (!FX) return out;
    FX.weeks.forEach(function (w) {
      w.nights.forEach(function (n) {
        n.series.forEach(function (s) {
          (s.games || []).forEach(function (g) {
            if (g && g.source_ref) {
              out[g.source_ref] = { teams: s.teams, winner: g.winner };
            }
          });
        });
      });
    });
    return out;
  }

  function aggregateTeams(ms) {
    if (!LEAGUE) return null;
    var T = {};
    LEAGUE.teams.forEach(function (t) {
      T[t.id] = { id: t.id, name: t.name, roster: t.roster,
                  games: 0, wins: 0, losses: 0,
                  opponents: {} };
    });

    var booked = leagueGames();

    ms.forEach(function (m) {
      var link = m.source_ref ? booked[m.source_ref] : null;
      if (!link || !link.teams || link.teams.length !== 2) return;

      var a = link.teams[0], b = link.teams[1];
      var win = link.winner;
      if (!T[a] || !T[b] || (win !== a && win !== b)) return;

      var lose = win === a ? b : a;
      T[a].games++; T[b].games++;
      T[win].wins++; T[lose].losses++;
      T[a].opponents[b] = (T[a].opponents[b] || 0) + 1;
      T[b].opponents[a] = (T[b].opponents[a] || 0) + 1;
    });

    // Compute pace, rating, status.
    var teams = LEAGUE.teams.map(function (t) { return T[t.id]; });
    teams.forEach(function (t) {
      t.winPct = t.games ? (t.wins / t.games) * 100 : 0;
      t.rating = wilson(t.wins, t.games, state.evidence) * SCALE;
      // Pace: games per opponent slot. Always /3, not /distinct-faced
      // (which would perversely reward playing only one opponent).
      t.pace = t.games / 3;
    });

    // League median pace and qualification floor.
    var paces = teams.map(function (t) { return t.pace; }).sort(function (a, b) { return a - b; });
    var median = paces.length
      ? (paces.length % 2
          ? paces[(paces.length - 1) / 2]
          : (paces[paces.length / 2 - 1] + paces[paces.length / 2]) / 2)
      : 0;
    var floorPct = (LEAGUE.season && LEAGUE.season.qualification_floor_pct) || 0.80;
    var floor = median * floorPct;

    teams.forEach(function (t) {
      var r = median ? t.pace / median : 1;
      var tier;
      if (t.pace === 0 && median === 0) tier = "empty";       // no league games yet
      else if (r >= 1.00)               tier = "green";
      else if (r >= 0.80)               tier = "yellow";
      else if (r >= 0.70)               tier = "orange";
      else                              tier = "red";
      t.tier = tier;
      t.eligible = t.pace >= floor;
    });

    // Rank: rating desc, then wins desc, then name asc.
    teams.sort(function (a, b) {
      return (b.rating - a.rating) || (b.wins - a.wins)
          || a.name.localeCompare(b.name);
    });

    return { teams: teams, median: median, floor: floor,
             totalMatches: teams.reduce(function (s, t) { return s + t.games; }, 0) / 2 };
  }

  var TIER_LABEL = { green: "On pace", yellow: "Below median",
                     orange: "Warning", red: "At risk", empty: "—" };

  function drawLeagueHud(agg) {
    var wrap = $("#leagueHud");
    if (!wrap) return;
    if (!LEAGUE) { wrap.innerHTML = ""; return; }
    var s = LEAGUE.season || {};
    var end = s.end_at ? new Date(s.end_at) : null;
    var now = new Date();
    var daysLeft = end ? Math.max(0, Math.ceil((end - now) / 86400000)) : null;
    var prize = s.prize_usd != null ? "$" + s.prize_usd.toLocaleString("en-US") : "";
    var totalMatches = agg ? agg.totalMatches : 0;

    wrap.innerHTML =
      '<div class="hud card">' +
        '<div class="hud__cell">' +
          '<div class="hud__label">Season</div>' +
          '<div class="hud__value">' + esc(s.name || "—") + '</div>' +
        '</div>' +
        '<div class="hud__cell">' +
          '<div class="hud__label">Days left</div>' +
          '<div class="hud__value">' + (daysLeft != null ? daysLeft : "—") + '</div>' +
        '</div>' +
        '<div class="hud__cell">' +
          '<div class="hud__label">League matches</div>' +
          '<div class="hud__value">' + totalMatches + '</div>' +
        '</div>' +
        '<div class="hud__cell hud__cell--prize">' +
          '<div class="hud__label">Prize</div>' +
          '<div class="hud__value">' + esc(prize) + '</div>' +
        '</div>' +
      '</div>';
  }

  function drawTeamStandings(agg) {
    var tb = $("#teamStandings tbody");
    if (!tb) return;
    tb.innerHTML = "";
    if (!agg) {
      tb.innerHTML = '<tr><td colspan="10" class="none">' +
        'League roster not loaded. Add data/teams.json and re-export.</td></tr>';
      return;
    }
    agg.teams.forEach(function (t, i) {
      var tr = el("tr");
      tr.style.setProperty("--i", i);
      if (i === 0 && t.games > 0) tr.classList.add("is-lead");
      if (!t.eligible && agg.floor > 0) tr.classList.add("is-inelig");
      tr.innerHTML =
        '<td><span class="rank">' + (i + 1) + "</span></td>" +
        '<td class="c-player"><span class="who">' +
          '<span class="team-chip team-chip--' + t.id + '">' + t.id + '</span>' +
          '<span><span class="who__name">' + esc(t.name) + "</span>" +
          '<span class="who__hero">' + t.roster.length + " players</span>" +
          "</span></span></td>" +
        "<td>" + t.games + "</td>" +
        '<td class="w-num">' + t.wins + "</td>" +
        '<td class="l-num">' + t.losses + "</td>" +
        '<td><div class="meter"><span style="width:' + t.winPct.toFixed(1) + '%"></span></div></td>' +
        '<td class="pct">' + (t.games ? pct(t.winPct) : "—") + "</td>" +
        '<td>' + (t.games ? t.pace.toFixed(1) : "—") + "</td>" +
        '<td class="tier tier--' + t.tier + '" title="' + esc(TIER_LABEL[t.tier]) + '">' +
          '<span class="tier-dot"></span></td>' +
        '<td class="c-rating"><span class="rating' + ratingTier(t.rating) + '">' +
          (t.games ? t.rating.toFixed(1) : "—") + "</span></td>";
      tb.appendChild(tr);
    });
  }

  var ROLE_LABEL = { core: "Core", support: "Support", stand_in: "Stand-in" };

  function drawRosterGrid() {
    var wrap = $("#teamsGrid");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!LEAGUE) return;
    LEAGUE.teams.forEach(function (t) {
      var card = el("div", "team-card card");
      var byRole = { core: [], support: [], stand_in: [] };
      t.roster.forEach(function (r) { (byRole[r.role] || byRole.core).push(r); });
      // Header
      var head = el("div", "team-card__head");
      head.innerHTML =
        '<span class="team-chip team-chip--' + t.id + '">' + t.id + '</span>' +
        '<span class="team-card__name">' + esc(t.name) + '</span>' +
        '<span class="team-card__meta">' + t.roster.length + ' players</span>';
      card.appendChild(head);
      // Look up friendly display for any canonical name (e.g. r.backup names
      // "TigerX [GB]" but the roster shows him elsewhere as "TigerX").
      function friendlyOf(canonical) {
        var hit = t.roster.filter(function (x) { return x.name === canonical; })[0];
        if (hit && hit.aka && hit.aka.length) return hit.aka[0];
        return canonical;
      }

      // Roster rows -- show aka when set (friendly nickname), fall back to name.
      // Slots with a `backup` render as "Primary / Backup" (matches the source
      // spreadsheet's "Beetlebum/Musa" notation).
      var body = el("div", "team-card__body");
      ["core", "support", "stand_in"].forEach(function (role) {
        var group = byRole[role];
        if (!group.length) return;
        var section = el("div", "team-card__section");
        section.innerHTML = '<div class="team-card__role">' + esc(ROLE_LABEL[role]) + '</div>';
        var list = el("ul", "team-card__players");
        group.forEach(function (r) {
          var li = el("li", "team-card__player" + (role === "stand_in" ? " is-standin" : ""));
          var display = (r.aka && r.aka.length) ? r.aka[0] : r.name;
          var mainLine = esc(display);
          if (r.backup) {
            mainLine += ' <span class="team-card__backup-sep">/</span> ' +
                        '<span class="team-card__backup">' + esc(friendlyOf(r.backup)) + '</span>';
          }
          // Show canonical as subtitle when the display differs from the name.
          var subtitle = (display !== r.name)
            ? '<span class="team-card__canonical">' + esc(r.name) + '</span>' : '';
          li.innerHTML = mainLine + subtitle;
          list.appendChild(li);
        });
        section.appendChild(list);
        body.appendChild(section);
      });
      card.appendChild(body);
      wrap.appendChild(card);
    });

    // Open Pool card — appended AFTER the 4 team cards. Only rendered
    // when there's at least one player in the pool. Same "team-card" shell
    // so it visually belongs; distinct chip so it doesn't read as a 5th team.
    var pool = LEAGUE.open_pool || [];
    if (pool.length) {
      var pcard = el("div", "team-card team-card--pool card");
      var phead = el("div", "team-card__head");
      phead.innerHTML =
        '<span class="team-chip team-chip--pool" title="Open Pool">◇</span>' +
        '<span class="team-card__name">Open Pool</span>' +
        '<span class="team-card__meta">' + pool.length +
          (pool.length === 1 ? ' player' : ' players') + '</span>';
      pcard.appendChild(phead);
      var pbody = el("div", "team-card__body");
      var psec = el("div", "team-card__section");
      psec.innerHTML = '<div class="team-card__role">Available</div>';
      var plist = el("ul", "team-card__players");
      pool.forEach(function (p) {
        var li = el("li", "team-card__player");
        li.innerHTML = esc(p.name);
        plist.appendChild(li);
      });
      psec.appendChild(plist);
      pbody.appendChild(psec);
      pcard.appendChild(pbody);
      wrap.appendChild(pcard);
    }
  }

  function drawTeams() {
    if (!LEAGUE) {
      drawTeamStandings(null);
      drawRosterGrid();
      drawLeagueHud(null);
      return;
    }
    var agg = aggregateTeams(cur.matches);
    drawLeagueHud(agg);
    drawTeamStandings(agg);
    drawRosterGrid();
  }

  /* ── Coord (scheduling) ─────────────────────────────────────────
     Read-only display of confirmed upcoming matches + open scheduling
     rounds. Input happens in Discord (#dota-league-2026); this tab
     renders whatever the last export knew about.

     All the DST-safe UTC math ran in Python at export time (see
     export_web.py::build_coord). The browser just formats what it's
     given. This keeps zoneinfo out of the browser -- Intl.DateTimeFormat
     has patchy IANA support in older mobile browsers. */

  /* ── Schedule (season fixtures) ───────────────────────────────────
     The whole season, week by week. Every series arrives from
     export_web.py already rendered into each league timezone (see
     build_fixtures), so switching country here is a lookup, not a
     conversion -- the browser never does timezone maths.

     Times are 12-hour throughout. The league reads this tab to decide
     when to show up, and "23:00" is one mental conversion away from
     turning up twelve hours late. */

  var FX = D.fixtures || null;

  function fxZones() {
    var first = FX && FX.weeks[0] && FX.weeks[0].nights[0].series[0];
    return (first && first.local) ? first.local.map(function (l) { return l.label; }) : [];
  }

  function drawZonePicker() {
    var host = $("#fxZone");
    if (!host || !FX) return;
    var zones = fxZones();
    if (!zones.length) return;
    host.innerHTML = '<span class="seg-label">Times in</span>' +
      zones.map(function (z) {
        return '<button class="seg' + (z === state.fxZone ? " is-on" : "") +
               '" data-zone="' + esc(z) + '">' + esc(z) + "</button>";
      }).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".seg"), function (b) {
      b.addEventListener("click", function () {
        state.fxZone = b.getAttribute("data-zone");
        drawZonePicker();
        drawSchedule();
      });
    });
  }

  function fxWindow(s) {
    var hit = (s.local || []).filter(function (l) { return l.label === state.fxZone; })[0];
    return hit ? hit.window : s.pkt_window;
  }

  function fxTeam(id) {
    var t = ((D.league && D.league.teams) || []).filter(function (x) { return x.id === id; })[0];
    return t ? t.name : "Team " + id;
  }

  function renderSeries(s) {
    var slotCls = s.slot === 2 ? " is-late" : "";
    var who = s.teams
      ? '<span class="team-chip team-chip--' + s.teams[0] + '">' + s.teams[0] + '</span>' +
        '<span class="fx-name">' + esc(fxTeam(s.teams[0])) + '</span>' +
        '<span class="fx-vs">vs</span>' +
        '<span class="fx-name">' + esc(fxTeam(s.teams[1])) + '</span>' +
        '<span class="team-chip team-chip--' + s.teams[1] + '">' + s.teams[1] + '</span>'
      : '<span class="fx-tbd">' + esc(s.label || "To be decided") + '</span>';
    var bo = s.best_of === 5 ? "best of 5" : "best of 3";
    return '<div class="fx-series' + slotCls + '">' +
      '<span class="fx-slot' + slotCls + '">' + (s.slot === 2 ? "Late" : "Early") + '</span>' +
      '<span class="fx-when">' + esc(fxWindow(s)) + '</span>' +
      '<span class="fx-teams">' + who + '</span>' +
      '<span class="fx-bo">' + bo + '</span>' +
    '</div>';
  }

  function dayLabel(iso) {
    var d = iso.split("-");
    return Number(d[2]) + " " + MON[Number(d[1]) - 1];
  }

  // The week in progress: the first whose last night has not yet passed.
  // Everything before it is done, so both layouts can mark and scroll to it.
  function currentWeek() {
    var today = new Date().toISOString().slice(0, 10);
    var hit = FX.weeks.filter(function (w) {
      return w.nights[w.nights.length - 1].date >= today;
    })[0];
    return hit ? hit.week : null;
  }

  /* Timeline: one column per week, scrolling sideways, the way a
     tournament bracket reads. Twenty-one weeks stacked vertically is a
     very long page in which every week looks the same; side by side, the
     shape of the season is visible at a glance and the current week can
     be scrolled to. */
  /* One match = one box with the two teams stacked, the way a tournament
     bracket draws it. A flat "A vs B" line reads as a list of text; a
     stacked pair reads as a fixture, and leaves an obvious place for each
     team's series score to land once results are recorded. */
  function renderMatchBox(s) {
    var late = s.slot === 2;
    var sc = s.score || [0, 0];
    var played = (s.games || []).length > 0;
    function row(i) {
      var tid = s.teams[i];
      var won = played && sc[i] > sc[1 - i];
      return '<div class="mb-row' + (won ? " is-won" : "") + '">' +
        '<span class="team-chip team-chip--' + tid + '">' + tid + '</span>' +
        '<span class="mb-team">' + esc(fxTeam(tid)) + '</span>' +
        '<span class="mb-score">' + (played ? sc[i] : "–") + '</span>' +
      '</div>';
    }
    return '<div class="mb' + (late ? " is-late" : "") + '">' +
      '<div class="mb-when">' +
        '<span class="mb-slot">' + (late ? "Late" : "Early") + '</span>' +
        esc(fxWindow(s)) +
      '</div>' +
      row(0) + row(1) +
    '</div>';
  }

  function drawTimeline(host, cur) {
    host.innerHTML = '<div class="tl-rail" id="tlRail">' +
      FX.weeks.map(function (w) {
        var nights = w.nights.map(function (n) {
          return '<div class="tl-night">' +
            '<div class="tl-night__day">' + esc(n.day) + " " +
              esc(dayLabel(n.date)) + '</div>' +
            n.series.map(renderMatchBox).join("") +
          '</div>';
        }).join("");
        var isCur = w.week === cur;
        var done = cur !== null && w.week < cur;
        return '<div class="tl-col' + (isCur ? " is-current" : "") +
                 (done ? " is-done" : "") + '"' +
                 (isCur ? ' id="tlNow"' : "") + '>' +
          '<div class="tl-col__head">' +
            '<span class="tl-col__n">Week ' + w.week + '</span>' +
            (isCur ? '<span class="tl-col__now">Now</span>' : '') +
          '</div>' +
          '<div class="tl-col__body">' + nights + '</div>' +
        '</div>';
      }).join("") +
    '</div>';

    // Bring the live week into view without yanking the whole page.
    var rail = $("#tlRail"), now = $("#tlNow");
    if (rail && now) rail.scrollLeft = Math.max(0, now.offsetLeft - rail.offsetLeft - 16);
  }

  function drawList(host, cur) {
    host.innerHTML = FX.weeks.map(function (w) {
      var nights = w.nights.map(function (n, i) {
        return '<div class="fx-night' + (i % 2 ? " is-alt" : "") + '">' +
          '<div class="fx-night__day">' +
            '<span class="fx-night__dow">' + esc(n.day) + '</span>' +
            '<span class="fx-night__date">' + esc(dayLabel(n.date)) + '</span>' +
          '</div>' +
          '<div class="fx-night__body">' +
            n.series.map(renderSeries).join("") +
          '</div>' +
        '</div>';
      }).join("");
      var isCur = w.week === cur;
      return '<div class="fx-week card' + (isCur ? " is-current" : "") + '">' +
        '<div class="fx-week__head">' +
          '<span class="fx-week__n">Week ' + w.week + '</span>' +
          '<span class="fx-week__phase">' + esc(w.week_of ? dayLabel(w.week_of) : "") + '</span>' +
          (isCur ? '<span class="fx-week__now">This week</span>' : '') +
        '</div>' +
        '<div class="fx-week__body">' + nights + '</div>' +
      '</div>';
    }).join("");
  }

  function drawSchedule() {
    var host = $("#scheduleBody");
    if (!host) return;
    if (!FX || !FX.weeks || !FX.weeks.length) {
      host.innerHTML = '<div class="coord-section coord-section--empty">' +
        'No schedule has been generated yet.</div>';
      return;
    }
    var cur = currentWeek();
    if (state.fxView === "list") drawList(host, cur);
    else drawTimeline(host, cur);
  }

  var COORD = D.coord || null;

  var TZ_LABELS = [
    ["Karachi",  "Asia/Karachi"],
    ["New York", "America/New_York"],
    ["Riyadh",   "Asia/Riyadh"],
    ["Berlin",   "Europe/Berlin"]
  ];

  function fmtLocal(iso) {
    // iso is already in the local zone from Python; just re-render the
    // human-readable part. Falls back to the raw ISO if Date parsing fails.
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
    // Extract the hour/minute portion from ISO (avoid Date.getHours which
    // would apply the BROWSER's tz, undoing the Python conversion).
    var m = iso.match(/T(\d{2}):(\d{2})/);
    var hhmm = m ? (m[1] + ":" + m[2]) : "??:??";
    return days[d.getUTCDay()] + " " + hhmm;
  }

  function fmtUtc(iso) {
    var m = iso.match(/(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!m) return iso;
    var days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
    var date = new Date(iso);
    return days[date.getUTCDay()] + " " + Number(m[3]) + " " + MON[Number(m[2]) - 1]
           + " · " + m[4] + ":" + m[5] + " UTC";
  }

  function renderNextMatch(m) {
    // Confirmed upcoming match. Shape from scheduling.json's `upcoming`.
    var tzRows = TZ_LABELS.map(function (pair) {
      // We need per-tz rendering. If the confirmed match doesn't carry
      // it, fall back to UTC only. (find_slot output includes renderings
      // for confirmed matches; if a raw upcoming entry is written by
      // hand it may not.)
      var r = (m.renderings || []).filter(function (x) {
        return x.iana === pair[1] || x.zone_label === pair[0];
      })[0];
      var when = r ? fmtLocal(r.local) : "—";
      return '<div class="tz-row"><span class="tz-row__label">' + esc(pair[0]) +
             '</span><span class="tz-row__time">' + esc(when) + '</span></div>';
    }).join("");

    return '<div class="next-match card">' +
      '<div class="next-match__head">' +
        '<span class="next-match__tag">Next Match</span>' +
        '<span class="next-match__utc">' + esc(fmtUtc(m.start_utc)) + '</span>' +
      '</div>' +
      '<div class="next-match__pair">' +
        '<span class="team-chip team-chip--' + m.match_up[0] + '">' + m.match_up[0] + '</span>' +
        '<span class="next-match__name">' + esc(m.match_up_names[0]) + '</span>' +
        '<span class="next-match__vs">vs</span>' +
        '<span class="next-match__name">' + esc(m.match_up_names[1]) + '</span>' +
        '<span class="team-chip team-chip--' + m.match_up[1] + '">' + m.match_up[1] + '</span>' +
      '</div>' +
      '<div class="next-match__tzs">' + tzRows + '</div>' +
    '</div>';
  }

  function renderSlot(slot, idx) {
    var tzRows = slot.renderings.map(function (r) {
      return '<div class="tz-row"><span class="tz-row__label">' + esc(r.zone_label) +
             '</span><span class="tz-row__time">' + esc(fmtLocal(r.local)) + '</span></div>';
    }).join("");

    var teamRows = Object.keys(slot.per_team).sort().map(function (tid) {
      var n = slot.per_team[tid];
      var missing = (slot.missing[tid] || []).slice(0, 3).join(", ");
      var more = (slot.missing[tid] || []).length > 3
        ? ' <span class="dim">(+' + (slot.missing[tid].length - 3) + ' more)</span>' : "";
      return '<div class="slot__team-row">' +
        '<span class="team-chip team-chip--' + tid + '">' + tid + '</span>' +
        '<span class="slot__team-avail">' + n + '/' + (n + (slot.missing[tid] || []).length) + '</span>' +
        (missing ? '<span class="slot__team-missing">missing: ' + esc(missing) + more + '</span>' : '') +
      '</div>';
    }).join("");

    var playable = Object.keys(slot.per_team).every(function (k) { return slot.per_team[k] >= 3; });

    return '<div class="slot' + (idx === 0 && playable ? ' slot--best' : '') + '">' +
      '<div class="slot__head">' +
        '<span class="slot__idx">' + (idx + 1) + '</span>' +
        '<span class="slot__utc">' + esc(fmtUtc(slot.start_utc)) + '</span>' +
        '<span class="slot__dur">' + slot.duration_min + " min window</span>" +
        '<span class="slot__total">' + slot.total + " available</span>" +
      '</div>' +
      '<div class="slot__body">' +
        '<div class="slot__tzs">' + tzRows + '</div>' +
        '<div class="slot__teams">' + teamRows + '</div>' +
      '</div>' +
    '</div>';
  }

  function renderRound(r) {
    var slotsHtml = r.slots && r.slots.length
      ? r.slots.map(renderSlot).join("")
      : '<div class="slot slot--empty">No overlapping windows yet — waiting for more availability.</div>';

    var warnsHtml = (r.warnings || []).length
      ? '<ul class="round__warnings">' +
          r.warnings.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join("") +
        '</ul>'
      : "";

    return '<div class="round card">' +
      '<div class="round__head">' +
        '<span class="round__id">Round ' + esc(r.round_id) + '</span>' +
        '<span class="round__matchup">' +
          '<span class="team-chip team-chip--' + r.match_up[0] + '">' + r.match_up[0] + '</span>' +
          esc(r.match_up_names[0]) + ' vs ' + esc(r.match_up_names[1]) +
          '<span class="team-chip team-chip--' + r.match_up[1] + '">' + r.match_up[1] + '</span>' +
        '</span>' +
        '<span class="round__meta">' +
          r.respondents.length + '/' + r.roster_size + ' responded' +
          (r.week_of ? ' · week of ' + esc(r.week_of) : '') +
        '</span>' +
      '</div>' +
      (r.note ? '<div class="round__note">' + esc(r.note) + '</div>' : '') +
      '<div class="round__slots">' + slotsHtml + '</div>' +
      warnsHtml +
    '</div>';
  }

  function drawCoord() {
    var wrap = $("#coordBody");
    if (!wrap) return;
    if (!COORD) {
      wrap.innerHTML = '<p class="none">Scheduling data not loaded. Add data/scheduling.json and re-export.</p>';
      return;
    }

    var chunks = [];

    // Next-match card(s)
    if (COORD.upcoming && COORD.upcoming.length) {
      chunks.push('<div class="coord-section">' +
        COORD.upcoming.map(renderNextMatch).join("") +
      '</div>');
    } else {
      chunks.push('<div class="coord-section coord-section--empty">' +
        '<div class="empty-card card">' +
          '<span class="empty-card__tag">Next Match</span>' +
          '<p>No confirmed match yet. Once a captain runs <code>!confirm N</code> in Discord, the winner appears here.</p>' +
        '</div>' +
      '</div>');
    }

    // Open rounds
    if (COORD.open_rounds && COORD.open_rounds.length) {
      chunks.push('<div class="coord-section">' +
        '<h3 class="coord-section__title">Open scheduling rounds</h3>' +
        COORD.open_rounds.map(renderRound).join("") +
      '</div>');
    } else {
      chunks.push('<div class="coord-section coord-section--empty">' +
        '<div class="empty-card card">' +
          '<span class="empty-card__tag">Open rounds</span>' +
          '<p>No scheduling round is open. A captain can start one with <code>!schedule Team X vs Team Y</code>.</p>' +
        '</div>' +
      '</div>');
    }

    wrap.innerHTML = chunks.join("");
  }

  /* ── Render ───────────────────────────────────────────────────── */
  function renderAll() {
    applyYear();
    drawYears();
    drawBand();
    drawTally();
    drawStandings();
    drawMatches();
    drawHeroes();
    drawDuos();
    drawTeams();
    drawCoord();
    drawZonePicker();
    drawSchedule();
    var none = cur.matches.length === 0;
    $("#empty").hidden = !none;
    Array.prototype.forEach.call(document.querySelectorAll(".view"), function (v) {
      v.style.display = none ? "none" : "";
    });
  }

  var al = D.meta.aliases || [];
  $("#aliasNote").textContent = al.length
    ? al.length + (al.length === 1 ? " name was" : " names were") + " merged this way."
    : "";

  renderAll();
  wireSort();
  wireTabs();
  fromHash();

  searchBox("qPlayers", "qPlayers", drawStandings);
  searchBox("qHeroes",  "qHeroes",  drawHeroes);
  searchBox("qMatches", "qMatches", drawMatches);
  searchBox("qDuos",    "qDuos",    drawDuos);
  segment("minGames",  "min",  "minGames",  drawStandings, Number);
  segment("heroSort",  "sort", "heroSort",  drawHeroes);
  segment("matchSort", "sort", "matchSort", drawMatches);
  segment("duoSort",   "sort", "duoSort",   drawDuos);
  segment("duoMin",    "min",  "duoMin",    drawDuos, Number);
  segment("fxView", "fxview", "fxView", drawSchedule);
  segment("evidence",  "z", "evidence", function () {
    rescore();
    drawStandings();
    drawDuos();          // the Duos header prints the rating too
  }, Number);

  var clr = $("#duoClear");
  if (clr) clr.addEventListener("click", function () {
    state.duoPick = []; state.qDuos = "";
    var q = $("#qDuos"); if (q) q.value = "";
    drawDuos();
  });

  $("#drawerClose").addEventListener("click", closeDrawer);
  scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !drawer.hidden) closeDrawer();
  });
})();
