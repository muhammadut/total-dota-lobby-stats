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
          h.who[r.name] = 1;
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
      h.players = Object.keys(h.who);
      return h;
    }).sort(function (a, b) {
      return b.picks - a.picks || b.wins - a.wins || a.hero.localeCompare(b.hero);
    });

    return { players: players, heroes: heroes };
  }

  /* ── State ────────────────────────────────────────────────────── */
  var YEARS = D.meta.years || [];
  var state = { year: YEARS[0] || "all", sortKey: "games", sortDir: -1 };
  var cur = { matches: [], players: [], heroes: [] };

  function applyYear() {
    cur.matches = state.year === "all"
      ? D.matches.slice()
      : D.matches.filter(function (m) { return m.year === state.year; });
    var agg = aggregate(cur.matches);
    cur.players = agg.players;
    cur.heroes  = agg.heroes;
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

    var label = state.year === "all" ? "All time" : state.year;
    $("#tagline").textContent = cur.matches.length
      ? label + " · " + cur.matches.length + " matches of Captains Mode"
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

    var list = cur.players.slice().sort(function (a, b) {
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
      if (i < 3 && k === "games") tr.classList.add("is-lead");
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
        '<td class="c-opt dim">' + num(p.avgGpm) + "</td>";
      tr.addEventListener("click", function () { openDrawer(p); });
      tr.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDrawer(p); }
      });
      tb.appendChild(tr);
    });
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
    cur.matches.slice().reverse().forEach(function (m, i) {
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
    cur.heroes.forEach(function (h, i) {
      var w = Math.round((h.wins / h.picks) * 100);
      var src = heroSrc(h.hero, "art");
      var c = el("div", "hcard");
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
      wrap.appendChild(c);
    });
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
         ["Win rate", pct(p.winPct)], ["KDA", rat(p.kda)]]
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

  function closeDrawer() {
    drawer.hidden = true; scrim.hidden = true;
    document.body.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }

  /* ── Tabs ─────────────────────────────────────────────────────── */
  function wireTabs() {
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
      t.addEventListener("click", function () {
        Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (o) {
          o.classList.remove("is-active"); o.setAttribute("aria-selected", "false");
        });
        Array.prototype.forEach.call(document.querySelectorAll(".view"), function (v) {
          v.classList.remove("is-active");
        });
        t.classList.add("is-active"); t.setAttribute("aria-selected", "true");
        $("#view-" + t.getAttribute("data-view")).classList.add("is-active");
      });
    });
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

  $("#drawerClose").addEventListener("click", closeDrawer);
  scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !drawer.hidden) closeDrawer();
  });
})();
