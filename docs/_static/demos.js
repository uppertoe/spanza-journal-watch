// "How it works" demos for the user guide. Vanilla JS, no dependencies. Each
// demo is a looping timeline of [delay, fn] steps that plays a made-up example
// through a mock of the real screen, with a caption underneath saying what is
// happening. A demo only runs while it is on screen, and with reduced motion
// switched on it shows its final frame instead of animating.
(function () {
  "use strict";
  var reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------- engine ---------------- */
  function makeRunner(build, period) {
    var timers = [], running = false;
    function cycle() {
      timers.forEach(clearTimeout); timers = [];
      build().forEach(function (s) { timers.push(setTimeout(s[1], s[0])); });
      timers.push(setTimeout(cycle, period));
    }
    return {
      start: function () { if (running) return; running = true; cycle(); },
      stop: function () { running = false; timers.forEach(clearTimeout); timers = []; }
    };
  }

  function Demo(root) {
    var frame = root.querySelector(".dm-frame");
    var cap = root.querySelector(".dm-caption span");
    var cursor = document.createElement("div");
    cursor.className = "dm-cursor";
    cursor.innerHTML = '<svg viewBox="0 0 14 14" aria-hidden="true"><path d="M1 1l4.6 12 2-5.1 5.1-2z" fill="#fff" stroke="#222" stroke-width="1.2" stroke-linejoin="round"/></svg>';
    frame.appendChild(cursor);
    var pos = { x: 0, y: 0 };

    this.root = root;
    this.frame = frame;
    this.q = function (sel) { return root.querySelector(sel); };
    this.qa = function (sel) { return [].slice.call(root.querySelectorAll(sel)); };
    this.say = function (html) {
      if (!cap) return;
      cap.classList.remove("show");
      setTimeout(function () { cap.innerHTML = html; cap.classList.add("show"); }, 170);
    };
    this.sayNow = function (html) { if (cap) { cap.innerHTML = html; cap.classList.add("show"); } };
    function place(scale) { cursor.style.transform = "translate(" + pos.x + "px," + pos.y + "px) scale(" + (scale || 1) + ")"; }
    this.moveTo = function (el, dx, dy) {
      if (!el) return;
      var fr = frame.getBoundingClientRect(), r = el.getBoundingClientRect();
      pos = { x: r.left - fr.left + r.width / 2 - 2 + (dx || 0), y: r.top - fr.top + r.height / 2 - 2 + (dy || 0) };
      place(1); cursor.classList.add("on");
    };
    this.click = function () { place(.8); setTimeout(function () { place(1); }, 150); };
    this.hideCursor = function () { cursor.classList.remove("on"); };
    this.press = function (btn) { if (!btn) return; btn.classList.add("press"); setTimeout(function () { btn.classList.remove("press"); }, 180); };
  }

  // Schedules the characters of `text` into `steps`, one per tick, starting at t.
  // Returns the time the last character lands.
  function typeInto(steps, t, el, text, speed) {
    steps.push([t, function () { el.textContent = ""; el.classList.add("dm-caret"); }]);
    for (var i = 1; i <= text.length; i++) {
      (function (n) { steps.push([t + n * speed, function () { el.textContent = text.slice(0, n); }]); })(i);
    }
    var end = t + text.length * speed;
    steps.push([end + 200, function () { el.classList.remove("dm-caret"); }]);
    return end;
  }
  function setBadge(el, cls, text) { if (!el) return; el.className = "dm-badge " + cls; el.textContent = text; }
  function pop(el) { el.classList.remove("dm-in"); void el.offsetWidth; el.classList.add("dm-in"); }
  function flash(el) { if (!el) return; el.classList.remove("dm-flash"); void el.offsetWidth; el.classList.add("dm-flash"); }
  function moveCard(card, col) { var add = col.querySelector(".dm-addcard"); col.insertBefore(card, add || null); card.classList.remove("lift"); pop(card); }

  /* ---------------- coordinator: staging ---------------- */
  function initStage(d) {
    var rows = d.qa(".dm-row"), chip = d.q("[data-chip='paed']"), count = d.q("[data-count]");
    function counts() {
      var shown = rows.filter(function (r) { return !r.classList.contains("gone"); }).length;
      var staged = rows.filter(function (r) { return r.querySelector(".dm-tog").classList.contains("on"); }).length;
      count.textContent = shown + " shown · " + staged + " staged · 0 pushed";
    }
    function setRow(r, on) { r.querySelector(".dm-tog").classList.toggle("on", on); setBadge(r.querySelector(".dm-badge"), on ? "ok" : "muted", on ? "Staged" : "Not staged"); }
    function filter(on) { chip.classList.toggle("on", on); rows.forEach(function (r) { r.classList.toggle("gone", on && r.getAttribute("data-paed") !== "1"); }); counts(); }
    // The paediatric MeSH filter is on when the page opens, as it is in the real intake page.
    function reset() { rows.forEach(function (r) { setRow(r, false); }); filter(true); d.hideCursor(); }
    var paed = rows.filter(function (r) { return r.getAttribute("data-paed") === "1"; });
    var tog = function (i) { return paed[i].querySelector(".dm-tog"); };
    if (reduced) {
      reset(); setRow(paed[0], true); setRow(paed[2], true); counts();
      d.sayNow("Staged means shortlisted. Nothing is sent anywhere until you push.");
      return null;
    }
    return makeRunner(function () {
      return [
        [100, function () { reset(); d.say("The <b>paediatric MeSH filter</b> is already on."); }],
        [3800, function () { d.say("Switch it off to see everything."); d.moveTo(chip); }],
        [4700, function () { d.click(); filter(false); }],
        [6500, function () { d.click(); filter(true); }],
        [7500, function () { d.say("Click the toggle to stage an article. <b>Staged means shortlisted.</b> Nothing is sent yet."); d.moveTo(tog(0)); }],
        [8400, function () { d.click(); setRow(paed[0], true); counts(); }],
        [9200, function () { d.moveTo(tog(2)); }],
        [9900, function () { d.click(); setRow(paed[2], true); counts(); }],
        [11000, function () { d.hideCursor(); }],
        [12000, function () { d.say("Click again to unstage."); d.moveTo(tog(2)); }],
        [12900, function () { d.click(); setRow(paed[2], false); counts(); }],
        [13900, function () { d.hideCursor(); }]
      ];
    }, 15600);
  }

  /* ---------------- coordinator: push to Planka ---------------- */
  function initPush(d) {
    var rows = d.qa(".dm-row"), cards = d.qa(".dm-card[data-for]"), btn = d.q("[data-push]");
    var count = d.q("[data-count]"), cand = d.q("[data-list='candidates']");
    var pushed = {};
    function counts() {
      var staged = rows.filter(function (r) { return r.querySelector(".dm-tog").classList.contains("on"); }).length;
      var n = Object.keys(pushed).length;
      count.textContent = staged + " staged · " + n + " pushed";
    }
    function stage(i, on) { rows[i].querySelector(".dm-tog").classList.toggle("on", on); setBadge(rows[i].querySelector(".dm-badge"), on ? "ok" : "muted", on ? "Staged" : "Not staged"); }
    function push() {
      var delay = 0;
      rows.forEach(function (r, i) {
        if (!r.querySelector(".dm-tog").classList.contains("on") || pushed[i]) return;
        pushed[i] = true;
        setTimeout(function () {
          cards[i].hidden = false; pop(cards[i]);
          setBadge(r.querySelector(".dm-badge"), "info", "Pushed"); counts();
        }, delay); delay += 300;
      });
    }
    function reset() { pushed = {}; cards.forEach(function (c) { c.hidden = true; }); stage(0, true); stage(1, true); stage(2, false); cand.classList.remove("hot"); counts(); d.hideCursor(); }
    if (reduced) {
      reset(); stage(2, true); push();
      d.sayNow("Pushing creates a card in the Candidates list of the Planka board for each staged article.");
      return null;
    }
    return makeRunner(function () {
      return [
        [100, function () { reset(); d.say("Staged articles wait here until you push."); }],
        [2500, function () { d.moveTo(btn); }],
        [3200, function () { d.click(); d.press(btn); }],
        [3500, function () { push(); d.say("Push creates a card for each staged article."); }],
        [6000, function () { d.hideCursor(); cand.classList.add("hot"); d.say("Cards arrive in <b>Candidates</b> for reviewers to pick from."); }],
        [8600, function () { cand.classList.remove("hot"); d.say("Stage more and push again any time. Nothing is duplicated."); d.moveTo(rows[2].querySelector(".dm-tog")); }],
        [9500, function () { d.click(); stage(2, true); counts(); }],
        [10300, function () { d.moveTo(btn); }],
        [11000, function () { d.click(); d.press(btn); }],
        [11300, function () { push(); }],
        [12300, function () { d.hideCursor(); }]
      ];
    }, 14200);
  }

  /* ---------------- coordinator: invite reviewers ---------------- */
  function initInvite(d) {
    var name = d.q("[data-name]"), email = d.q("[data-email]"), add = d.q("[data-add]");
    var rows = d.qa(".dm-rrow"), send = d.q("[data-send]"), resend = d.q("[data-resend]");
    function status(i, cls, text) { setBadge(rows[i].querySelector(".dm-badge"), cls, text); }
    function planka(i, on) { var c = rows[i].querySelector("[data-planka]"); c.innerHTML = on ? '<span class="dm-badge ok">✓ on board</span>' : '<span class="dm-muted">—</span>'; if (on) flash(c.firstChild); }
    function tick(i, on) { rows[i].querySelector(".dm-tick").classList.toggle("on", on); }
    function reset() {
      name.textContent = ""; email.textContent = ""; name.classList.remove("lit", "dm-caret"); email.classList.remove("lit", "dm-caret");
      rows.forEach(function (r, i) { r.classList.remove("show"); status(i, "muted", "Pending"); planka(i, false); tick(i, false); });
      resend.classList.add("dim"); d.hideCursor();
    }
    if (reduced) {
      reset(); rows[0].classList.add("show"); rows[1].classList.add("show");
      status(0, "ok", "Active"); planka(0, true); status(1, "info", "Invited");
      d.sayNow("Add each reviewer, send the invitations, and follow progress in the Status column.");
      return null;
    }
    return makeRunner(function () {
      var s = [], t;
      s.push([100, function () { reset(); d.say("Add each reviewer's name and email."); }]);
      s.push([700, function () { name.classList.add("lit"); }]);
      t = typeInto(s, 800, name, "Dr Priya Nair", 45);
      s.push([t + 250, function () { name.classList.remove("lit"); email.classList.add("lit"); }]);
      t = typeInto(s, t + 350, email, "priya.nair@example.org", 32);
      s.push([t + 250, function () { email.classList.remove("lit"); d.moveTo(add); }]);
      s.push([t + 850, function () { d.click(); d.press(add); }]);
      s.push([t + 1100, function () { name.textContent = ""; email.textContent = ""; rows[0].classList.add("show"); d.say("Reviewers sit as Pending until you send the invites."); }]);
      var t2 = t + 2300;
      s.push([t2, function () { name.classList.add("lit"); }]);
      t = typeInto(s, t2 + 100, name, "Dr Tom Whitlock", 30);
      s.push([t + 200, function () { name.classList.remove("lit"); email.classList.add("lit"); }]);
      t = typeInto(s, t + 300, email, "t.whitlock@example.org", 24);
      s.push([t + 200, function () { email.classList.remove("lit"); d.moveTo(add); }]);
      s.push([t + 700, function () { d.click(); d.press(add); }]);
      s.push([t + 950, function () { name.textContent = ""; email.textContent = ""; rows[1].classList.add("show"); }]);
      var t3 = t + 1700;
      s.push([t3, function () { d.say("<b>Send initial invites</b> emails everyone still pending."); d.moveTo(send); }]);
      s.push([t3 + 900, function () { d.click(); d.press(send); }]);
      s.push([t3 + 1200, function () { status(0, "info", "Invited"); status(1, "info", "Invited"); d.hideCursor(); }]);
      s.push([t3 + 3600, function () { d.say("On accepting, a reviewer becomes Active and joins the board automatically."); }]);
      s.push([t3 + 4100, function () { status(0, "ok", "Active"); planka(0, true); }]);
      s.push([t3 + 7000, function () { d.say("To remind someone, tick them and use <b>Resend to selected</b>."); d.moveTo(rows[1].querySelector(".dm-tick")); }]);
      s.push([t3 + 7800, function () { d.click(); tick(1, true); resend.classList.remove("dim"); }]);
      s.push([t3 + 8400, function () { d.moveTo(resend); }]);
      s.push([t3 + 9000, function () { d.click(); d.press(resend); }]);
      s.push([t3 + 9800, function () { d.hideCursor(); }]);
      return s;
    }, 22000);
  }

  /* ---------------- reviewer: accept the invite ---------------- */
  function initAccept(d) {
    var scenes = d.qa(".dm-scene"), accept = d.q("[data-accept]"), signin = d.q("[data-signin]"), open = d.q("[data-open]");
    function show(i) { scenes.forEach(function (s, k) { s.classList.toggle("show", k === i); }); }
    if (reduced) { show(2); d.sayNow("Accept the invitation with the email address it was sent to, then open Planka and sign in with Journal Watch."); return null; }
    return makeRunner(function () {
      return [
        [100, function () { show(0); d.hideCursor(); d.say("The invitation arrives by email. Click <b>Accept invitation</b>."); }],
        [2800, function () { d.moveTo(accept); }],
        [3500, function () { d.click(); d.press(accept); }],
        [3800, function () { show(1); d.hideCursor(); }],
        [4000, function () { d.say("Sign in with the <b>email address the invitation was sent to</b>. If you do not yet have an account, create one with that address."); }],
        [7400, function () { d.moveTo(signin); }],
        [8100, function () { d.click(); d.press(signin); }],
        [8400, function () { show(2); d.hideCursor(); }],
        [8600, function () { d.say("Access is confirmed. Open Planka and choose <b>Sign in with Journal Watch</b>. No separate password is needed."); }],
        [11200, function () { d.moveTo(open); }],
        [11900, function () { d.click(); d.press(open); }],
        [12400, function () { d.hideCursor(); }]
      ];
    }, 14400);
  }

  /* ---------------- reviewer: pick or add an article ---------------- */
  function initPick(d) {
    var cand = d.q("[data-list='candidates']"), under = d.q("[data-list='under']");
    var card = d.q("[data-pickcard]"), modal = d.q(".dm-modal"), addm = d.q("[data-addmember]"), close = d.q(".dm-close");
    var addcard = d.q(".dm-addcard"), newcard = d.q(".dm-newcard"), typed = newcard.querySelector(".dm-typed");
    var avs = { card: card.querySelector(".dm-av"), modal: modal.querySelector(".dm-av"), neu: newcard.querySelector(".dm-av") };
    var home = card.nextSibling, homeParent = card.parentNode;
    function reset() {
      modal.classList.remove("show"); card.classList.remove("lift");
      homeParent.insertBefore(card, home);
      Object.keys(avs).forEach(function (k) { avs[k].classList.remove("show"); });
      newcard.classList.remove("show"); typed.textContent = ""; typed.classList.remove("dm-caret"); addcard.classList.remove("lit");
      under.classList.remove("hot"); cand.classList.remove("hot"); d.hideCursor();
    }
    function finalState() { reset(); moveCard(card, under); avs.card.classList.add("show"); newcard.classList.add("show"); typed.textContent = "Caudal versus penile block for hypospadias repair"; avs.neu.classList.add("show"); }
    if (reduced) { finalState(); d.sayNow("Add yourself to a card and move it to Under review, or add your own card if the article is not listed."); return null; }
    return makeRunner(function () {
      var s = [
        [100, function () { reset(); cand.classList.add("hot"); d.say("<b>Candidates</b> holds the articles shortlisted by your coordinator."); }],
        [2600, function () { cand.classList.remove("hot"); d.say("Open a card to read the abstract, and add yourself as a member so the editors can see who is covering it."); d.moveTo(card); }],
        [3400, function () { d.click(); }],
        [3600, function () { modal.classList.add("show"); }],
        [4700, function () { d.moveTo(addm); }],
        [5400, function () { d.click(); avs.modal.classList.add("show"); avs.card.classList.add("show"); }],
        [6600, function () { d.moveTo(close); }],
        [7200, function () { d.click(); }],
        [7400, function () { modal.classList.remove("show"); }],
        [7700, function () { d.say("Move the card to <b>Under review</b> while you are working on it."); d.moveTo(card); }],
        [8400, function () { card.classList.add("lift"); }],
        [8700, function () { d.moveTo(under, 0, 10); }],
        [9300, function () { moveCard(card, under); flash(under); }],
        [10600, function () { d.say("To review an article that is not listed, add a card of your own with the title or a link, and add yourself as a member."); d.moveTo(addcard); }],
        [11500, function () { d.click(); addcard.classList.add("lit"); newcard.classList.add("show"); d.hideCursor(); }]
      ];
      var t = typeInto(s, 11800, typed, "Caudal versus penile block for hypospadias repair", 32);
      s.push([t + 500, function () { avs.neu.classList.add("show"); addcard.classList.remove("lit"); }]);
      return s;
    }, 17200);
  }

  /* ---------------- reviewer: finish and move to Publish ready ---------------- */
  function initReady(d) {
    var under = d.q("[data-list='under']"), ready = d.q("[data-list='ready']");
    var card = d.q("[data-readycard]"), modal = d.q(".dm-modal"), close = d.q(".dm-close"), typed = d.q(".dm-typed");
    var home = card.nextSibling, homeParent = card.parentNode;
    var TEXT = "A well-run multicentre trial with a clear message for day-case practice.";
    function reset() { modal.classList.remove("show"); card.classList.remove("lift"); homeParent.insertBefore(card, home); typed.textContent = ""; typed.classList.remove("dm-caret"); ready.classList.remove("hot"); d.hideCursor(); }
    if (reduced) { reset(); moveCard(card, ready); d.sayNow("Write your review below the marker line, then move the card to Publish ready when it is complete."); return null; }
    return makeRunner(function () {
      var s = [
        [100, function () { reset(); }],
        [300, function () { modal.classList.add("show"); d.say("Write your review <b>below the marker line</b>. The article details above it should be left as they are."); }]
      ];
      var t = typeInto(s, 1600, typed, TEXT, 28);
      s.push([t + 500, function () { d.say("A suggested structure is provided. Any format you prefer is fine."); }]);
      s.push([t + 2900, function () { d.moveTo(close); }]);
      s.push([t + 3500, function () { d.click(); }]);
      s.push([t + 3700, function () { modal.classList.remove("show"); }]);
      s.push([t + 4000, function () { d.say("When the review is complete, move the card to <b>Publish ready</b>."); d.moveTo(card); }]);
      s.push([t + 4700, function () { card.classList.add("lift"); }]);
      s.push([t + 5000, function () { d.moveTo(ready, 0, 10); }]);
      s.push([t + 5600, function () { moveCard(card, ready); flash(ready); }]);
      s.push([t + 6400, function () { d.hideCursor(); d.say("<b>Publish ready</b> tells the editors the review can be imported. The card can still be edited afterwards."); }]);
      return s;
    }, 13600);
  }


  /* ---------------- overview: what the platform does ---------------- */
  function countSteps(steps, t, el, from, to, dur) {
    var n = 24;
    for (var i = 1; i <= n; i++) {
      (function (k) {
        var eased = 1 - Math.pow(1 - k / n, 3);
        steps.push([t + Math.round(dur * k / n), function () { el.textContent = Math.round(from + (to - from) * eased); }]);
      })(i);
    }
    return t + dur;
  }
  function initOverview(d) {
    var months = d.qa("[data-win]"), chips = d.qa(".dm-jchip"), nodes = {};
    d.qa(".dm-node").forEach(function (n) { nodes[n.getAttribute("data-node")] = n; });
    var num = function (k) { return nodes[k].querySelector("[data-num]"); };
    function reset() {
      months.forEach(function (m) { m.classList.remove("on"); }); chips.forEach(function (c) { c.classList.remove("on"); });
      Object.keys(nodes).forEach(function (k) { nodes[k].classList.remove("on", "lit"); var e = num(k); if (e) e.textContent = "–"; });
    }
    function lit(k) { Object.keys(nodes).forEach(function (j) { nodes[j].classList.remove("lit"); }); nodes[k].classList.add("on", "lit"); }
    if (reduced) {
      reset(); months.forEach(function (m) { m.classList.add("on"); }); chips.forEach(function (c) { c.classList.add("on"); });
      Object.keys(nodes).forEach(function (k) { nodes[k].classList.add("on"); });
      num("pulled").textContent = "184"; num("filtered").textContent = "31"; num("staged").textContent = "8";
      d.sayNow("PubMed, paediatric filter, your shortlist, the Planka board.");
      return null;
    }
    return makeRunner(function () {
      var s = [
        [100, function () { reset(); d.say("An issue covers a window of a month or two, for example <b>November and December</b>."); }],
        [1200, function () { months[0].classList.add("on"); }],
        [1450, function () { months[1].classList.add("on"); }],
        [3600, function () { d.say("The platform retrieves the window's articles from <b>PubMed</b>."); }]
      ];
      chips.forEach(function (c, i) { s.push([3900 + i * 220, function () { c.classList.add("on"); }]); });
      s.push([4000, function () { lit("pulled"); }]);
      countSteps(s, 4200, num("pulled"), 0, 184, 1700);
      s.push([7000, function () { d.say("The paediatric MeSH filter is applied before you see the list."); lit("filtered"); }]);
      countSteps(s, 7300, num("filtered"), 184, 31, 1300);
      s.push([9800, function () { d.say("You shortlist the articles that merit a review."); lit("staged"); }]);
      countSteps(s, 10100, num("staged"), 31, 8, 1100);
      s.push([12400, function () { d.say("The shortlist goes to the <b>Planka board</b>, where reviewers claim and write."); lit("out"); flash(nodes.out); }]);
      return s;
    }, 16200);
  }

  /* ---------------- coordinator: landing page to your issue ---------------- */
  function initGo(d) {
    var scenes = d.qa(".dm-scene"), go = d.q("[data-gobtn]"), articles = d.q("[data-articles]");
    function show(i) { scenes.forEach(function (s, k) { s.classList.toggle("show", k === i); }); }
    if (reduced) { show(2); d.sayNow("Sign in at /editorial/go, choose the editorial backend, and open an issue from your dashboard."); return null; }
    return makeRunner(function () {
      return [
        [100, function () { show(0); d.hideCursor(); d.say("Sign in at <b>/editorial/go</b> and choose <b>Editorial backend</b>."); }],
        [4200, function () { d.moveTo(go); }],
        [4900, function () { d.click(); d.press(go); }],
        [5200, function () { show(1); d.hideCursor(); }],
        [5400, function () { d.say("Open your issue with <b>Articles</b> or <b>Reviewers</b>."); }],
        [8200, function () { d.moveTo(articles); }],
        [8900, function () { d.click(); d.press(articles); }],
        [9200, function () { show(2); d.hideCursor(); }],
        [9400, function () { d.say("The bar at the top shows which issue you are in. The greyed tabs belong to the chief editor."); }]
      ];
    }, 14000);
  }

  /* ---------------- coordinator: Step 1, load the articles ---------------- */
  // Only animate the line in when it first appears or changes tone; a progress
  // update that re-ran the entrance animation would flicker.
  function statusLine(el, cls, html, spin) {
    var was = el.hidden, before = el.className;
    el.hidden = false; el.className = "dm-status" + (cls ? " " + cls : "");
    el.innerHTML = (spin ? '<span class="dm-spin"></span>' : "") + "<span>" + html + "</span>";
    if (was || before !== el.className) pop(el);
  }
  function initLoad(d) {
    var from = d.q("[data-from]"), to = d.q("[data-to]"), untick = d.q("[data-untick]"), start = d.q("[data-start]");
    var status = d.q("[data-status]"), results = d.q("[data-results]"), count = d.q("[data-count]");
    var JOURNALS = ["Paediatric Anaesthesia", "Anesthesiology", "Anaesthesia", "British Journal of Anaesthesia", "Anaesthesia and Intensive Care"];
    function reset() {
      to.textContent = "September 2026"; from.classList.remove("lit"); to.classList.remove("lit");
      untick.classList.remove("on"); status.hidden = true; results.hidden = true;
      count.textContent = "112 shown · 0 staged · 0 pushed"; d.hideCursor();
    }
    if (reduced) {
      reset(); to.textContent = "October 2026"; untick.classList.add("on"); results.hidden = false;
      statusLine(status, "ok", "Found 6 new article(s) since last check."); count.textContent = "118 shown · 0 staged · 0 pushed";
      d.sayNow("Set the months and journals, then Start intake.");
      return null;
    }
    return makeRunner(function () {
      var s = [
        [100, function () { reset(); d.say("Set the months to the issue's window, here <b>September to October</b>."); }],
        [1600, function () { d.moveTo(to); }],
        [2300, function () { d.click(); to.classList.add("lit"); }],
        [2700, function () { to.textContent = "October 2026"; }],
        [3400, function () { to.classList.remove("lit"); }],
        [4400, function () { d.say("Tick the journals. On a return visit they are already set."); d.moveTo(untick); }],
        [5500, function () { d.click(); untick.classList.add("on"); }],
        [6600, function () { d.moveTo(start); }],
        [7300, function () { d.click(); d.press(start); }],
        [7600, function () { d.hideCursor(); results.hidden = false; pop(results); statusLine(status, "ok", "Loaded 112 cached article(s). Checking PubMed for newer articles in the background."); d.say("The list appears within seconds."); }]
      ];
      JOURNALS.forEach(function (name, i) {
        s.push([10200 + i * 700, function () {
          if (i === 0) d.say("PubMed is then checked for anything newer. This takes a minute or two.");
          statusLine(status, "", "Checking PubMed (" + (i + 1) + "/" + JOURNALS.length + ") · finished " + name, true);
        }]);
      });
      s.push([14400, function () { statusLine(status, "ok", "Found 6 new article(s) since last check."); count.textContent = "118 shown · 0 staged · 0 pushed"; flash(results); d.say("New arrivals are added to the list. You can start shortlisting while the check runs."); }]);
      return s;
    }, 18400);
  }

  /* ---------------- coordinator: checking again as the window fills ---------------- */
  function initRecheck(d) {
    var today = d.q("[data-today]"), rows = d.qa(".dm-row"), check = d.q("[data-check]"), last = d.q("[data-last]");
    var count = d.q("[data-count]"), badge = d.q("[data-new]"), nonew = d.q("[data-nonew]"), seen = d.q("[data-seen]"), status = d.q("[data-status]");
    var callout = d.q("[data-callout]"), phase = d.q("[data-phase]"), note = d.q("[data-note]");
    var NOTE_OPEN = "This issue's window runs to 31 October, so the list is not yet complete. Check again at the end of September, and once more about a fortnight after the window closes.";
    var NOTE_CLOSING = "The window closed on 31 October. Articles published late in October may still be arriving in PubMed. Check again around 14 November, before you settle the shortlist.";
    function setPhase(open, due) {
      setBadge(phase, open ? "info" : "warn", open ? "Window open until 31 Oct" : "Window closed · final articles still arriving");
      note.textContent = open ? NOTE_OPEN : NOTE_CLOSING;
      callout.classList.toggle("due", due); check.classList.toggle("warn", due);
    }
    // Timeline: Aug | Sep | Oct | Nov, each month a quarter of the track.
    var POS = { earlySep: 27, lateSep: 47, midNov: 87 };
    function place(pct) { today.style.left = pct + "%"; }
    function shown() { return rows.filter(function (r) { return !r.classList.contains("gone"); }).length; }
    function setNew(n) {
      if (n > 0) { badge.hidden = false; badge.textContent = n + " new"; nonew.hidden = true; seen.classList.remove("dim"); }
      else { badge.hidden = true; nonew.hidden = false; seen.classList.add("dim"); }
    }
    function reveal(wave) {
      var delay = 0, n = 0;
      rows.forEach(function (r) {
        if (r.getAttribute("data-wave") !== String(wave)) return;
        n += 1;
        setTimeout(function () { r.classList.remove("gone"); r.querySelector(".dm-dot").classList.add("on"); count.textContent = (36 + shown()) + " shown"; }, delay);
        delay += 350;
      });
      return n;
    }
    function clearDots() { rows.forEach(function (r) { r.querySelector(".dm-dot").classList.remove("on"); }); setNew(0); }
    function reset() {
      place(POS.earlySep); rows.forEach(function (r) { if (r.hasAttribute("data-wave")) r.classList.add("gone"); });
      clearDots(); count.textContent = "38 shown"; last.textContent = "Last checked 3 weeks ago"; status.hidden = true; setPhase(true, true); d.hideCursor();
    }
    if (reduced) {
      reset(); place(POS.midNov); rows.forEach(function (r) { r.classList.remove("gone"); });
      rows.filter(function (r) { return r.getAttribute("data-wave") === "2"; }).forEach(function (r) { r.querySelector(".dm-dot").classList.add("on"); });
      setNew(1); count.textContent = "41 shown"; last.textContent = "Last checked just now"; setPhase(false, false);
      d.sayNow("Check for new articles at the end of each month, and a fortnight after the window closes.");
      return null;
    }
    return makeRunner(function () {
      return [
        [100, function () { reset(); d.say("Set up in early September, an issue for <b>September and October</b> holds only what PubMed has so far."); }],
        [4600, function () { d.say("Come back at the <b>end of each month</b>. The card turns amber when a check is due."); place(POS.lateSep); }],
        [7800, function () { d.moveTo(check); }],
        [8500, function () { d.click(); d.press(check); }],
        [8800, function () { d.hideCursor(); statusLine(status, "", "Checking PubMed for new articles…", true); }],
        [10600, function () { statusLine(status, "ok", "Found 2 new article(s) since last check."); last.textContent = "Last checked just now"; reveal(1); setNew(2); setPhase(true, false); }],
        [11200, function () { d.say("New arrivals carry a <b>blue dot</b>. Staged and pushed articles are not disturbed."); }],
        [15200, function () { d.say("Check once more a <b>fortnight after the window closes</b>."); place(POS.midNov); status.hidden = true; last.textContent = "Last checked 4 weeks ago"; setPhase(false, true); }],
        [18600, function () { d.moveTo(check); }],
        [19300, function () { d.click(); d.press(check); }],
        [19600, function () { d.hideCursor(); statusLine(status, "", "Checking PubMed for new articles…", true); }],
        [21400, function () { statusLine(status, "ok", "Found 1 new article(s) since last check."); last.textContent = "Last checked just now"; reveal(2); setNew(3); setPhase(false, false); }],
        [23400, function () { d.say("<b>Mark all seen</b> clears the dots."); d.moveTo(seen); }],
        [24600, function () { d.click(); clearDots(); }],
        [25400, function () { d.hideCursor(); }]
      ];
    }, 28000);
  }

  /* ---------------- boot ---------------- */
  var inits = { overview: initOverview, go: initGo, load: initLoad, stage: initStage, push: initPush, recheck: initRecheck, invite: initInvite, accept: initAccept, pick: initPick, ready: initReady };
  // Run a demo only while it is on screen. Leaving is debounced so a quick
  // scroll past, or a screenshot that briefly resizes the viewport, does not
  // restart the timeline from the beginning.
  var io = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      var el = e.target, r = el._dmRunner; if (!r) return;
      if (e.isIntersecting) { clearTimeout(el._dmLeave); r.start(); }
      else { clearTimeout(el._dmLeave); el._dmLeave = setTimeout(r.stop, 1500); }
    });
  }, { threshold: 0.15 }) : null;

  function boot() {
    document.querySelectorAll("[data-demo]").forEach(function (root) {
      if (root.dataset.demoInit) return;
      var fn = inits[root.getAttribute("data-demo")];
      if (!fn) return;
      root.dataset.demoInit = "1";
      var runner = fn(new Demo(root));
      if (!runner) return;
      root._dmRunner = runner;
      if (io) io.observe(root); else runner.start();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
