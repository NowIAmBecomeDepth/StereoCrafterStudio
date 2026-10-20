/* search.js -- the manual's search box (user, 2026-10-17: "add a search bar to the manual so a user can
   quickly find something they're looking for").

   It puts a search box in the top bar of every page. On the manual's own pages it searches
   search_index.js (every page's sections, written by build_offline.py); in the offline copy, where every
   page is already on the one page, it reads the sections from the page itself. Results are sections,
   best first: a word in a section's title counts most. Picking one goes to it and marks the words
   there for a few seconds.

   Keys: / or Ctrl+K to search, Up / Down to move, Enter to open, Esc to close. */
(function () {
  "use strict";
  var bar = document.querySelector(".caption");
  if (!bar) return;
  var offline = !!document.querySelector(".manual-page");

  // ---- the sections ---------------------------------------------------------------------
  function words(s) { return (s || "").toLowerCase().replace(/[^\wÀ-ɏ]+/g, " ").trim(); }

  function fromPage() {
    // the offline copy: each heading starts a section; it runs to the next heading
    var out = [], cur = null;
    var pages = document.querySelectorAll(".manual-page");
    Array.prototype.forEach.call(pages, function (pg) {
      var hero = pg.querySelector(".hero h1");
      var part = hero ? hero.textContent.trim() : "";
      var walker = document.createTreeWalker(pg, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT), h2 = "";
      var n, anchor = pg.id;
      while ((n = walker.nextNode())) {
        if (n.nodeType === 1) {
          if (n.closest && n.closest("nav, .anchor, script, style")) continue;
          if (n.id) anchor = n.id;
          if (/^H[1-4]$/.test(n.tagName)) {
            if (!n.id) n.id = "s-" + out.length;          // something to jump to
            anchor = n.id;
            var title = n.textContent.replace("#", "").replace(/\s+/g, " ").trim();
            if (n.tagName === "H2") h2 = title;
            cur = { part: part + (n.tagName !== "H2" && h2 ? " · " + h2 : ""), title: title, id: anchor,
                    href: "#" + anchor, text: "" };
            out.push(cur);
          }
        } else if (cur && n.parentNode && !(n.parentNode.closest && n.parentNode.closest("h1, h2, h3, h4, nav, .anchor"))) {
          cur.text += n.nodeValue;
        }
      }
    });
    return out;
  }

  var sections = offline ? fromPage() : (window.MANUAL_INDEX || []);
  sections.forEach(function (s) {
    s.text = s.text.replace(/\s+/g, " ").trim();
    s._t = words(s.title);
    s._b = words(s.text);
  });

  // ---- the box ---------------------------------------------------------------------------
  var box = document.createElement("div");
  box.className = "search";
  box.setAttribute("role", "search");
  box.innerHTML =
    '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><circle cx="11" cy="11" r="6.5" fill="none" ' +
    'stroke="currentColor" stroke-width="2"/><path d="M16 16l5 5" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round"/></svg>' +
    '<input type="search" placeholder="Search the manual" aria-label="Search the manual" autocomplete="off" ' +
    'spellcheck="false"><kbd class="search-key">/</kbd>' +
    '<div class="search-results" role="listbox" hidden></div>';
  bar.appendChild(box);
  var input = box.querySelector("input"), list = box.querySelector(".search-results");
  var shown = [], active = -1;

  function score(s, terms) {
    var total = 0;
    for (var i = 0; i < terms.length; i++) {
      var t = terms[i], inTitle = s._t.indexOf(t) >= 0, inBody = s._b.indexOf(t) >= 0;
      if (!inTitle && !inBody) return 0;                  // every word must be there
      if (inTitle) total += (" " + s._t + " ").indexOf(" " + t + " ") >= 0 ? 12 : 8;
      if (inBody) {
        var hits = s._b.split(t).length - 1;
        total += Math.min(hits, 6) + ((" " + s._b + " ").indexOf(" " + t + " ") >= 0 ? 2 : 0);
      }
    }
    var phrase = terms.join(" ");
    if (terms.length > 1 && (s._t.indexOf(phrase) >= 0 || s._b.indexOf(phrase) >= 0)) total += 10;
    return total;
  }

  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function mark(text, terms) {
    var html = esc(text);
    terms.forEach(function (t) {
      if (!t) return;
      html = html.replace(new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi"), "<mark>$1</mark>");
    });
    return html;
  }

  function snippet(s, terms) {
    var low = s.text.toLowerCase(), at = -1;
    for (var i = 0; i < terms.length && at < 0; i++) at = low.indexOf(terms[i]);
    if (at < 0) return s.text.slice(0, 120);
    var a = Math.max(0, at - 50), b = Math.min(s.text.length, at + 90);
    return (a > 0 ? "…" : "") + s.text.slice(a, b).trim() + (b < s.text.length ? "…" : "");
  }

  function run() {
    var q = words(input.value);
    var terms = q ? q.split(" ").filter(function (t) { return t.length > 1 || /\d/.test(t); }) : [];
    if (!terms.length) { close(); return; }
    shown = sections.map(function (s) { return { s: s, v: score(s, terms) }; })
      .filter(function (r) { return r.v > 0; })
      .sort(function (x, y) { return y.v - x.v; })
      .slice(0, 12);
    active = shown.length ? 0 : -1;
    if (!shown.length) {
      list.innerHTML = '<div class="search-none">Nothing found for “' + esc(input.value.trim()) + '”</div>';
    } else {
      list.innerHTML = shown.map(function (r, i) {
        return '<a class="search-hit" role="option" href="' + esc(r.s.href) + '" data-i="' + i + '">' +
          '<span class="search-where">' + esc(r.s.part) + '</span>' +
          '<span class="search-title">' + mark(r.s.title, terms) + '</span>' +
          '<span class="search-snip">' + mark(snippet(r.s, terms), terms) + '</span></a>';
      }).join("");
    }
    list.hidden = false;
    paint();
  }

  function paint() {
    Array.prototype.forEach.call(list.querySelectorAll(".search-hit"), function (el, i) {
      el.classList.toggle("on", i === active);
      if (i === active) el.scrollIntoView({ block: "nearest" });
    });
  }

  function close() { list.hidden = true; active = -1; }

  function go(i) {
    var r = shown[i];
    if (!r) return;
    try { sessionStorage.setItem("manual-search", input.value.trim()); } catch (e) { /* private window */ }
    close();
    input.blur();
    if (offline || r.s.href.charAt(0) === "#") { location.hash = r.s.href.slice(r.s.href.indexOf("#")); highlight(); }
    else location.href = r.s.href;
  }

  input.addEventListener("input", run);
  input.addEventListener("focus", function () { if (input.value.trim()) run(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown" && shown.length) { active = (active + 1) % shown.length; paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && shown.length) { active = (active - 1 + shown.length) % shown.length; paint(); e.preventDefault(); }
    else if (e.key === "Enter") { go(active < 0 ? 0 : active); e.preventDefault(); }
    else if (e.key === "Escape") { if (list.hidden) input.blur(); close(); }
  });
  list.addEventListener("mousedown", function (e) { e.preventDefault(); });     // keep the box's focus
  list.addEventListener("click", function (e) {
    var a = e.target.closest(".search-hit");
    if (a) { e.preventDefault(); go(+a.getAttribute("data-i")); }
  });
  document.addEventListener("click", function (e) { if (!box.contains(e.target)) close(); });
  document.addEventListener("keydown", function (e) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || "");
    if ((e.key === "/" && !typing) || (e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey))) {
      e.preventDefault(); input.focus(); input.select();
    }
  });

  // ---- arriving at a result: its words marked for a moment --------------------------------
  function highlight() {
    var q;
    try { q = sessionStorage.getItem("manual-search"); sessionStorage.removeItem("manual-search"); } catch (e) { return; }
    if (!q || !location.hash) return;
    var start = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (!start) return;
    var terms = words(q).split(" ").filter(function (t) { return t.length > 1; });
    if (!terms.length) return;
    var re = new RegExp("(" + terms.map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("|") + ")", "gi");
    // the section: from the target on, up to the next heading that isn't inside it
    var nodes = [];
    var walker = document.createTreeWalker(document.querySelector("main") || document.body,
                                           NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    walker.currentNode = start;
    for (var n = start; n; n = walker.nextNode()) {
      if (n !== start && n.nodeType === 1 && /^H[1-4]$/.test(n.tagName) && !start.contains(n)) break;
      if (n.nodeType === 3 && !n.parentNode.closest("script, style, mark, nav") && n.nodeValue.search(re) >= 0) nodes.push(n);
      re.lastIndex = 0;
    }
    var marks = [];
    nodes.forEach(function (t) {
      var span = document.createElement("span");
      span.innerHTML = esc(t.nodeValue).replace(re, '<mark class="search-found">$1</mark>');
      Array.prototype.forEach.call(span.querySelectorAll("mark"), function (m) { marks.push(m); });
      t.parentNode.replaceChild(span, t);
    });
    setTimeout(function () {
      marks.forEach(function (m) { m.classList.add("fade"); });
      setTimeout(function () { marks.forEach(function (m) { m.replaceWith(document.createTextNode(m.textContent)); }); }, 1200);
    }, 4000);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", highlight); else highlight();
})();
