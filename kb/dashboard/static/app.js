"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? (n / 1e3).toFixed(1) + "K" : Math.round(n).toLocaleString();
const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many || one + "s"}`;

// One colour and name per kind of record. Several kinds can share a colour, as email and texts do.
const FAMILY = {
  agent: { label: "Agent sessions", one: "Agent session", v: "--s1" },
  text: { label: "Texts", one: "Texts", v: "--s2" },
  mail: { label: "Email", one: "Email", v: "--s2" },
  commit: { label: "Commits", one: "Commit", v: "--s4" },
  pr: { label: "Pull requests and issues", one: "Pull request or issue", v: "--s4" },
  note: { label: "Notes", one: "Note", v: "--s3" },
};
const GROUPS = [
  { label: "Agent sessions", families: ["agent"], v: "--s1" },
  { label: "Email and texts", families: ["mail", "text"], v: "--s2" },
  { label: "Notes", families: ["note"], v: "--s3" },
  { label: "Code", families: ["commit", "pr"], v: "--s4" },
];
const fam = f => FAMILY[f] || FAMILY.note;
const ICON = {
  ok: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="var(--good)"/><path d="M4.5 8.2 7 10.5l4.5-5" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  bad: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="var(--critical)"/><path d="M8 4.2v4.6M8 11.2v.4" stroke="#fff" stroke-width="1.9" stroke-linecap="round"/></svg>`,
  late: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="var(--serious)" stroke-width="2"/><path d="M8 4.5V8l2.2 1.6" stroke="var(--serious)" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>`,
};

// ---------- time ----------
const isDateOnly = ts => /T00:00:00(\.0+)?Z$/.test(ts);
const when = ts => ts ? new Date(ts) : null;
const dayOf = ts => isDateOnly(ts) ? new Date(ts.slice(0, 10) + "T12:00:00Z") : new Date(ts);
const fmtDay = (ts, o) => dayOf(ts).toLocaleDateString([], isDateOnly(ts) ? { ...o, timeZone: "UTC" } : o);
const shortDay = ts => ts ? fmtDay(ts, { month: "short", day: "numeric", year: "numeric" }) : "";
const clock = d => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const stamp = ts => isDateOnly(ts) ? shortDay(ts) : `${shortDay(ts)}, ${clock(new Date(ts))}`;
function ago(t, now = Date.now()) {
  const m = Math.round((now - new Date(t)) / 60000);
  if (m < 1) return "just now"; if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60); if (h < 36) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}
const dLabel = (d, o = { month: "short", day: "numeric" }) => new Date(d + "T12:00:00Z").toLocaleDateString([], { ...o, timeZone: "UTC" });

// ---------- helpers ----------
const tip = $("#tip");
function showTipAt(x0, y0, html) { tip.innerHTML = html; tip.style.display = "block"; const r = tip.getBoundingClientRect(); let x = x0 + 14, y = y0 + 14; if (x + r.width > innerWidth - 8) x = Math.max(8, x0 - r.width - 14); if (y + r.height > innerHeight - 8) y = y0 - r.height - 14; tip.style.left = x + "px"; tip.style.top = y + "px"; }
const hideTip = () => tip.style.display = "none";
addEventListener("scroll", hideTip, { passive: true });

const terms = q => [...new Set((String(q || "").toLowerCase().match(/\w{3,}/g) || []))];
// Text with the query's words wrapped in <mark>, escaped piece by piece so nothing in it is read as markup.
function hl(text, q) {
  text = String(text ?? "");
  const t = terms(q);
  if (!t.length) return esc(text);
  const re = new RegExp(t.map(x => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
  let out = "", at = 0, m;
  while ((m = re.exec(text))) { out += esc(text.slice(at, m.index)) + "<mark>" + esc(m[0]) + "</mark>"; at = m.index + m[0].length; }
  return out + esc(text.slice(at));
}
const enc = encodeURIComponent;
const href = (kind, key) => `/${kind}/${enc(key)}`;
const link = (kind, key, name) => `<a href="${href(kind, key)}">${esc(name)}</a>`;
const famDot = f => `<span class="fam" style="background:var(${fam(f).v})"></span>`;
const safeHref = u => /^(https?:\/\/|obsidian:\/\/|imessage:\/\/|sms:)/.test(u || "") ? u : null;

// The run's key arrives once, in a meta tag on the page the printed address opens. It is kept for this tab only, taken
// out of the address, and sent in a header with every request for data. It is in no cookie.
const KEY = (() => {
  const meta = document.querySelector('meta[name="superhistory-key"]');
  if (meta) {
    const k = meta.content;
    meta.remove();
    try { sessionStorage.setItem("superhistory-key", k); } catch (e) { /* kept in memory only */ }
    history.replaceState(null, "", location.pathname + location.hash);
    return k;
  }
  try { return sessionStorage.getItem("superhistory-key") || ""; } catch (e) { return ""; }
})();

async function api(path) {
  const res = await fetch("/api/" + path, { headers: { Accept: "application/json", "X-Superhistory-Key": KEY } });
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) { const err = new Error((data && data.error) || `The dashboard answered ${res.status}.`); err.status = res.status; throw err; }
  return data;
}
let overviewCache = null;
async function overviewData(fresh) { if (fresh || !overviewCache || Date.now() - overviewCache.at > 15000) overviewCache = { at: Date.now(), data: await api("overview") }; return overviewCache.data; }

// ---------- theme ----------
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $("#theme").setAttribute("aria-label", `Switch to ${t === "dark" ? "light" : "dark"} theme`);
  try { localStorage.setItem("theme", t); } catch (e) { /* private mode */ }
}
(function () {
  let t = null;
  try { t = localStorage.getItem("theme"); } catch (e) { /* private mode */ }
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  setTheme(t);
  $("#theme").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
})();

// ---------- router ----------
const app = $("#app");
const routes = [
  [/^\/$/, overviewPage, "overview"],
  [/^\/projects$/, projectsPage, "projects"],
  [/^\/people$/, peoplePage, "people"],
  [/^\/project\/(.+)$/, m => groupPage("project", decodeURIComponent(m[1])), "projects"],
  [/^\/person\/(.+)$/, m => groupPage("person", decodeURIComponent(m[1])), "people"],
  [/^\/source\/(.+)$/, m => groupPage("source", decodeURIComponent(m[1])), "overview"],
  [/^\/search$/, searchPage, null],
  [/^\/item\/(\d+)$/, m => itemPage(+m[1]), null],
];
let token = 0;
async function render() {
  const my = ++token;
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const hit = routes.map(([re, fn, tab]) => [re.exec(path), fn, tab]).find(r => r[0]);
  document.querySelectorAll("#tabs a").forEach(a => { if (a.dataset.tab === (hit && hit[2])) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  if (!hit) { app.innerHTML = `<div class="empty">There is no page here. <a href="/">Back to the overview</a></div>`; document.title = "Not found · Superhistory"; return; }
  try {
    const html = await hit[1](hit[0]);
    if (my !== token) return;
    app.innerHTML = html.html;
    document.title = (html.title ? html.title + " · " : "") + "Superhistory";
    if (html.after) html.after();
  } catch (err) {
    if (my !== token) return;
    const missing = err.status === 404;
    if (err.status === 403) { app.innerHTML = `<div class="card err-note"><b>This tab has no key</b>${esc(err.message)}</div>`; document.title = "No key · Superhistory"; return; }
    app.innerHTML = `<div class="card err-note"><b>${missing ? "Not found" : "The dashboard could not read your history"}</b>${esc(missing ? "That record is not in your history, or it was replaced when its source refreshed." : err.message)}<div style="margin-top:12px"><a class="btn" href="/">Back to the overview</a></div></div>`;
    document.title = (missing ? "Not found" : "Problem") + " · Superhistory";
  }
}
function go(url) { history.pushState(null, "", url); render().then(() => { const h = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1))); if (h) h.scrollIntoView(); else scrollTo(0, 0); }); }
document.addEventListener("click", e => {
  const a = e.target.closest("a[href]");
  if (!a || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || a.target) return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin || u.pathname.startsWith("/static/") || u.pathname.startsWith("/api/")) return;
  e.preventDefault();
  go(u.pathname + u.search + u.hash);
});
addEventListener("popstate", render);

$("#searchform").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (q) go("/search?q=" + enc(q));
});
addEventListener("keydown", e => {
  if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) { e.preventDefault(); $("#q").focus(); }
});

// ---------- shared pieces ----------
function itemRow(it, q) {
  return `<a class="listrow" href="/item/${it.id}${q ? "?q=" + enc(q) : ""}"><span class="t" title="${esc(it.title)}">${esc(it.title || "(untitled)")}</span><span class="w">${esc(shortDay(it.ts))}</span><span class="src">${famDot(it.family)}${esc(fam(it.family).one)} · ${esc(it.source)}</span></a>`;
}
function famChips(families) {
  return Object.entries(families).map(([f, n]) => `<span class="chiplink" style="cursor:default">${famDot(f)}${esc(plural(n, fam(f).one, fam(f).label))}</span>`).join("");
}
function spark(days, color = "--s1", h = 40) {
  if (!days.length) return "";
  const w = 300, max = Math.max(1, ...days.map(d => d.n)), bw = w / days.length;
  const total = days.reduce((a, d) => a + d.n, 0);
  let s = `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Items per day, last ${days.length} days, ${total} in all">`;
  days.forEach((d, i) => { const bh = d.n ? Math.max(2, (h - 1) * d.n / max) : 2; s += `<rect x="${i * bw + 1}" y="${h - bh}" width="${Math.max(1, bw - 2)}" height="${bh}" rx="1.5" fill="${d.n ? `var(${color})` : "var(--axis)"}"/>`; });
  return s + "</svg>";
}
function trimDays(daily, n) { return daily.days.slice(-n); }
function nameList(rows, kind) {
  if (!rows.length) return `<div class="empty">None found.</div>`;
  return `<div class="namelist">` + rows.map(r => `<a class="listrow" href="${href(kind, r.key)}"><span class="t">${esc(r.name)}</span><span class="w num">${r.count.toLocaleString()}</span><span class="src">${Object.entries(r.families).map(([f, n]) => `${famDot(f)}${n} ${esc(n === 1 ? fam(f).one.toLowerCase() : fam(f).label.toLowerCase())}`).join(" · ")}${r.last ? ` · last ${esc(shortDay(r.last))}` : ""}</span></a>`).join("") + `</div>`;
}

// ---------- overview ----------
function problemCount(D) {
  const last = when(D.last_ingest);
  const stale = !last || (Date.now() - last) > D.stale_hours * 36e5;
  return { stale, n: D.failing.length + D.away.length + (stale && D.items ? 1 : 0) };
}
function healthIssues(D, stale) {
  const last = when(D.last_ingest);
  let html = "";
  if (!last && D.items) html += `<div class="issue">${ICON.late}<span class="what">No refresh has been recorded</span><span class="when"></span><span class="why">Run <span class="mono">superhistory ingest</span> to read your sources.</span></div>`;
  else if (stale && D.items) html += `<div class="issue">${ICON.late}<span class="what">No refresh since ${esc(stamp(D.last_ingest))}</span><span class="when">${esc(ago(last))}</span><span class="why">Run <span class="mono">superhistory ingest</span>, or <span class="mono">superhistory schedule on</span> to refresh in the background.</span></div>`;
  for (const f of D.failing) html += `<div class="issue">${ICON.bad}<span class="what">${esc(f.unit)}</span><span class="when">failing since ${esc(ago(f.t))}</span><span class="why mono" style="color:var(--ink-2)">${esc(f.error || "no reason recorded")}</span></div>`;
  for (const a of D.away) html += `<div class="issue">${ICON.late}<span class="what">Cannot reach ${esc(a.host)}</span><span class="when">since ${esc(ago(a.since))}</span><span class="why">Its sources wait until it is back online.</span></div>`;
  if (D.old_rows) html += `<div class="issue">${ICON.late}<span class="what">${plural(D.old_rows, "row")} predate today's masking</span><span class="when"></span><span class="why">The next ingest masks them again, or run <span class="mono">superhistory remask</span>.</span></div>`;
  if (D.mask_capped) html += `<div class="issue">${ICON.late}<span class="what">Masking reached its pass limit ${plural(D.mask_capped, "time")}</span><span class="when"></span><span class="why">It masked the part still changing whole.</span></div>`;
  return html;
}
function runsStrip(D) {
  const runs = D.runs.slice(-96);
  if (!D.log) return `<div class="runs"><div class="runs-head"><b>Recent refreshes</b></div><div class="empty" style="padding:12px 0">No ingest log yet. It appears after <span class="mono">superhistory ingest</span> has run.</div></div>`;
  if (!runs.length) return `<div class="runs"><div class="runs-head"><b>Recent refreshes</b></div><div class="empty" style="padding:12px 0">No refresh in the last 14 days.</div></div>`;
  const bad = runs.filter(r => r.failures).length;
  return `<div class="runs" id="runs"><div class="runs-head"><b>Last ${plural(runs.length, "refresh", "refreshes")}</b><span>${runs.length - bad} clean${bad ? ` · ${bad} with failures` : ""}</span></div>
    <div class="strip" id="strip" role="list" aria-label="Refreshes, oldest first" style="grid-template-columns:repeat(${runs.length},minmax(0,1fr))">${runs.map((r, i) => `<i role="listitem" tabindex="${i === runs.length - 1 ? 0 : -1}" class="${r.failures ? "err" : "ok"}" data-i="${i}" aria-label="${esc(runLabel(r))}"></i>`).join("")}</div>
    <div class="axis" aria-hidden="true"><span>${esc(stamp(runs[0].t))}</span><span>${esc(stamp(runs[runs.length - 1].t))}</span></div>
    <div class="stripdetail" id="stripdetail" aria-live="polite" style="margin-top:8px">Hover or focus a block to see that refresh.</div>
    <div class="key" aria-hidden="true"><span><i style="background:var(--good)"></i>No failures</span><span><i style="background:repeating-linear-gradient(135deg,var(--critical) 0 3px,transparent 3px 5px)"></i>Something failed</span></div></div>`;
}
const runLabel = r => `${stamp(r.t)}: ` + (r.failures ? `${plural(r.failures, "failure")}${r.failed.length ? ", " + r.failed.join(", ") : ""}` : "no failures");
function wireStrip(D) {
  const strip = $("#strip"); if (!strip) return;
  const runs = D.runs.slice(-96), detail = $("#stripdetail");
  const select = (i, e) => { strip.querySelectorAll("i").forEach(x => x.classList.remove("sel")); const c = strip.children[i]; c.classList.add("sel"); detail.textContent = runLabel(runs[i]); if (e) showTipAt(e.clientX, e.clientY, `<b>${esc(stamp(runs[i].t))}</b>${runs[i].failures ? esc(plural(runs[i].failures, "failure") + (runs[i].failed.length ? ": " + runs[i].failed.join(", ") : "")) : "No failures"}`); };
  strip.addEventListener("pointermove", e => { const c = e.target.closest("i"); if (c) select(+c.dataset.i, e); });
  strip.addEventListener("pointerleave", hideTip);
  strip.addEventListener("focusin", e => { const c = e.target.closest("i"); if (c) select(+c.dataset.i); });
  strip.addEventListener("keydown", e => {
    const cur = document.activeElement.closest("i"); if (!cur) return;
    const i = +cur.dataset.i, n = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: runs.length - 1 }[e.key];
    if (n == null || n < 0 || n >= runs.length) return;
    e.preventDefault(); cur.tabIndex = -1; strip.children[n].tabIndex = 0; strip.children[n].focus();
  });
}
function kpiTiles(D) {
  const byGroup = GROUPS.map(g => ({ g, n: g.families.reduce((a, f) => a + (D.families[f] || 0), 0) }));
  const total = D.items || 1;
  return [
    { label: "Things in your history", value: fmt(D.items), delta: `${fmt(D.rows)} searchable pieces`, viz: `<div class="comp" role="img" aria-label="${byGroup.map(c => `${c.g.label} ${Math.round(100 * c.n / total)}%`).join(", ")}">${byGroup.map(c => `<span style="flex:${c.n};background:var(${c.g.v})" title="${c.g.label}: ${c.n.toLocaleString()}"></span>`).join("")}</div><div class="cap">${byGroup.filter(c => c.n).map(c => `${c.g.label} ${Math.round(100 * c.n / total)}%`).join(" · ")}</div>` },
    { label: "Projects", value: fmt(D.project_total), delta: D.projects[0] ? `Most active: ${esc(D.projects[0].name)}` : "No agent sessions or code yet", viz: `<a class="linkbtn" href="/projects">All projects</a>` },
    { label: "People", value: fmt(D.people_total), delta: D.people[0] ? `Most active: ${esc(D.people[0].name)}` : "No texts or email yet", viz: `<a class="linkbtn" href="/people">All people</a>` },
    { label: "Newest item", value: D.newest ? esc(shortDay(D.newest)) : "–", delta: `${D.db_mb} MB on disk`, viz: `${D.summaries ? plural(D.summaries, "summary", "summaries") + " written" : "No summaries written"}` },
  ];
}
function niceMax(m) { if (m <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(m))); for (const k of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (k * p >= m) return k * p; }
function groupDay(d) { return GROUPS.map(g => g.families.reduce((a, f) => a + (d.by[f] || 0), 0)); }
function chartHTML(days) {
  const el = $("#chart"); if (!el || !el.clientWidth) return;
  const data = days.map(d => ({ day: d.day, v: groupDay(d) }));
  const W = el.clientWidth, H = 260, L = 46, R = 4, T = 8, B = 26;
  const max = niceMax(Math.max(1, ...data.map(d => d.v.reduce((a, b) => a + b, 0))));
  const bw = (W - L - R) / data.length, w = Math.max(3, Math.min(18, bw - 3));
  const y = v => T + (H - T - B) * (1 - v / max);
  let s = `<svg width="${W}" height="${H}">`;
  for (let k = 0; k <= 4; k++) { const v = max * k / 4; s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(${k ? "--grid" : "--axis"})" ${k ? 'stroke-dasharray="2 4"' : ""}/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`; }
  const every = data.length <= 14 ? 2 : data.length <= 30 ? 7 : 14;
  data.forEach((d, i) => {
    const x = L + i * bw + (bw - w) / 2; let acc = 0;
    d.v.forEach((v, j) => { if (!v) return; const y1 = y(acc + v), h = Math.max(1, y(acc) - y1 - (acc ? 2 : 0)); s += `<rect x="${x}" y="${y1}" width="${w}" height="${h}" rx="${Math.min(3, w / 2)}" fill="var(${GROUPS[j].v})"/>`; acc += v; });
    if ((data.length - 1 - i) % every === 0) s += `<text x="${x + w / 2}" y="${H - 7}" text-anchor="middle">${dLabel(d.day)}</text>`;
  });
  s += `<line id="xh" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--line-2)" visibility="hidden"/></svg>`;
  el.innerHTML = s;
  const xh = el.querySelector("#xh");
  const hide = () => { hideTip(); xh.setAttribute("visibility", "hidden"); };
  const at = e => {
    const r = el.getBoundingClientRect(), i = Math.floor((e.clientX - r.left - L) / bw);
    if (i < 0 || i >= data.length) return hide();
    const d = data[i], sum = d.v.reduce((a, b) => a + b, 0), cx = L + i * bw + bw / 2;
    xh.setAttribute("x1", cx); xh.setAttribute("x2", cx); xh.setAttribute("visibility", "visible");
    showTipAt(e.clientX, e.clientY, `<b>${esc(dLabel(d.day, { weekday: "short", month: "short", day: "numeric" }))}</b>` + GROUPS.map((g, j) => `<div class="r"><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:var(${g.v});margin-right:6px"></i>${g.label}</span><span class="num">${d.v[j].toLocaleString()}</span></div>`).join("") + `<div class="r" style="margin-top:4px;padding-top:4px;border-top:1px solid var(--line)"><span>Total</span><span class="num">${sum.toLocaleString()}</span></div>`);
  };
  el.onpointermove = at; el.onclick = at; el.onpointerleave = hide;
}
function tableHTML(days) {
  return `<table><thead><tr><th>Day</th>${GROUPS.map(g => `<th class="r">${g.label}</th>`).join("")}<th class="r">Total</th></tr></thead><tbody>${days.slice().reverse().map(d => { const v = groupDay(d); return `<tr><td>${esc(dLabel(d.day, { weekday: "short", month: "short", day: "numeric" }))}</td>${v.map(n => `<td class="r num">${n}</td>`).join("")}<td class="r num">${d.n}</td></tr>`; }).join("")}</tbody></table>`;
}
function sourcesTable(D) {
  const rows = D.sources.map(s => {
    const bad = s.failing.length;
    const state = bad ? `<span class="state">${ICON.bad}Failing</span>` : s.last_ok ? `<span class="state">${ICON.ok}Refreshed ${esc(ago(s.last_ok))}</span>` : s.service ? "" : `<span class="state">${ICON.late}${s.configured ? "Not refreshed yet" : "No refresh recorded"}</span>`;
    const name = s.service ? esc(s.name) : `<a href="${href("source", s.name)}">${esc(s.name)}</a>`;
    return `<tr class="${bad ? "" : "okrow"}"><td>${name}</td><td class="r num">${s.items == null ? "" : s.items.toLocaleString()}</td><td class="hide-sm">${s.newest ? esc(shortDay(s.newest)) : ""}</td><td>${state}${bad ? `<div class="mono" style="color:var(--muted);margin-top:2px">${esc(s.failing[0].error)}</div>` : ""}</td></tr>`;
  }).join("");
  return `<table class="sources-table"><thead><tr><th>Source</th><th class="r">Items</th><th class="hide-sm">Newest item</th><th>Last refresh</th></tr></thead><tbody>${rows || `<tr><td colspan="4" class="empty">No sources yet.</td></tr>`}</tbody></table>`;
}
function failureList(D) {
  if (!D.log) return `<div class="empty">No ingest log yet.</div>`;
  if (!D.failures.length) return `<div class="empty">No failures in the last 14 days.</div>`;
  return D.failures.map(f => `<div class="inc">${ICON.bad}<div><div class="head"><span class="what">${esc(f.what)}</span><span class="when">${esc(stamp(f.t))}</span></div>${f.reason ? `<div class="why mono">${esc(f.reason)}</div>` : ""}</div></div>`).join("");
}
function bars(rows, kind, color) {
  if (!rows.length) return `<li class="empty">Nothing yet</li>`;
  const max = rows[0].count;
  return rows.map(r => `<li><a class="row" href="${href(kind, r.key)}" aria-label="${esc(r.name)}, ${plural(r.count, "item")}"><span class="t">${esc(r.name)}</span><span class="n num">${r.count.toLocaleString()}</span><span class="track" aria-hidden="true"><span class="fill" style="display:block;width:${Math.max(2, 100 * r.count / max)}%;background:var(${color})"></span></span></a></li>`).join("");
}
async function overviewPage() {
  const D = await overviewData(true);
  const { stale, n } = problemCount(D);
  const last = when(D.last_ingest);
  const empty = !D.items;
  const headline = empty ? "Nothing in your history yet" : n ? `${n} ${n > 1 ? "parts" : "part"} of your history ${n > 1 ? "aren't" : "isn't"} refreshing` : "Your history is up to date";
  const meta = empty ? `Add a source, then run <span class="mono">superhistory ingest</span>.` : last ? `Last refresh ${esc(stamp(D.last_ingest))} (${esc(ago(last))})` : "No refresh recorded";
  const issues = healthIssues(D, stale);
  const real = D.sources.filter(s => !s.service), sources = real.length, refreshed = real.filter(s => s.last_ok).length;
  const days = trimDays(D.daily, 60);
  const html = `
    <div class="hero" id="health"><h1 id="headline" tabindex="-1">${esc(headline)}</h1><div class="meta">${meta}</div></div>
    <div class="card health"><div class="issues">${issues || `<div class="allgood">${ICON.ok}<span><b style="color:var(--ink)">${empty ? "Nothing to refresh." : refreshed === sources ? `All ${plural(sources, "source")} healthy.` : `Nothing is failing.`}</b> ${empty ? "" : refreshed === sources ? "Nothing needs you." : `${refreshed} of ${sources} sources have a recorded refresh.`}</span></div>`}</div>${runsStrip(D)}</div>
    <div class="section-head"><h2>At a glance</h2></div>
    <div class="grid g-4">${kpiTiles(D).map(t => `<div class="card kpi"><div class="label">${t.label}</div><div class="value num">${t.value}</div><div class="delta">${t.delta}</div><div class="viz">${t.viz}</div></div>`).join("")}</div>
    <div class="section-head"><h2>Activity</h2><span class="aside">${D.daily.end ? "Ending " + esc(dLabel(D.daily.end, { month: "short", day: "numeric", year: "numeric" })) : ""}</span></div>
    <div class="card"><div class="card-head"><div><h3>Items by the day they happened</h3><div class="sub" id="act-sub"></div></div><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button class="linkbtn" id="tabletoggle" type="button" aria-expanded="false" aria-controls="acttable">Show as table</button><div class="seg" id="range" role="group" aria-label="Date range">${[14, 30, 60, 90].map(d => `<button type="button" data-d="${d}">${d} days</button>`).join("")}</div></div></div>
      <div class="legend">${GROUPS.map(g => `<span><i style="background:var(${g.v})"></i>${g.label}</span>`).join("")}</div>
      <div id="chart" aria-hidden="true" style="height:260px"></div><div class="tablewrap" id="acttable" hidden></div></div>
    <div class="section-head"><h2>Projects and people</h2></div>
    <div class="grid g-2">
      <div class="card"><h3>Projects</h3><div class="sub">Agent sessions, commits and pull requests, by the folder or repository they belong to.</div><ul class="list bars">${bars(D.projects, "project", "--s1")}</ul><a class="linkbtn more" href="/projects">All ${D.project_total.toLocaleString()} projects</a></div>
      <div class="card"><h3>People</h3><div class="sub">Email threads and days of texts, by the other person.</div><ul class="list bars">${bars(D.people, "person", "--s2")}</ul><a class="linkbtn more" href="/people">All ${D.people_total.toLocaleString()} people</a></div>
    </div>
    <div class="section-head"><h2>Sources and failures</h2></div>
    <div class="grid g-main"><div class="card"><h3>Sources</h3><div class="sub">Each source, how much it holds, and its last good refresh</div>${sourcesTable(D)}</div>
      <div class="card"><h3>Recent failures</h3><div class="sub">From the ingest log, the last 14 days</div><div>${failureList(D)}</div></div></div>`;
  const after = () => {
    let range = 30;
    try { range = +localStorage.getItem("range") || 30; } catch (e) { /* private mode */ }
    if (![14, 30, 60, 90].includes(range)) range = 30;
    const all = trimDays(D.daily, 90);
    const draw = () => {
      const part = all.slice(-range);
      document.querySelectorAll("#range button").forEach(b => b.setAttribute("aria-pressed", +b.dataset.d === range));
      const total = part.reduce((a, d) => a + d.n, 0);
      $("#act-sub").textContent = `${fmt(total)} items dated in the last ${range} days, about ${fmt(total / range)} a day`;
      chartHTML(part);
      $("#acttable").innerHTML = tableHTML(part);
    };
    $("#range").onclick = e => { const b = e.target.closest("button"); if (!b) return; range = +b.dataset.d; try { localStorage.setItem("range", range); } catch (x) { /* private mode */ } draw(); };
    $("#tabletoggle").onclick = e => { const t = $("#acttable"); t.hidden = !t.hidden; e.target.setAttribute("aria-expanded", !t.hidden); e.target.textContent = t.hidden ? "Show as table" : "Hide table"; };
    draw();
    addEventListener("resize", () => { if ($("#chart")) draw(); }, { once: true });
    wireStrip(D);
    if (location.hash === "#health") $("#health").scrollIntoView();
  };
  return { html, title: "", after };
}
function renderPill(D) {
  const { n } = problemCount(D);
  $("#pill").innerHTML = `<span class="dot ${n ? "bad" : ""}"></span><span id="pilltext">${D.items ? (n ? `${plural(n, "problem")}` : "Healthy") : "Empty"}</span>`;
  $("#pill").setAttribute("aria-label", `${$("#pilltext").textContent}, see health`);
}

// ---------- projects, people ----------
async function projectsPage() {
  const rows = await api("projects");
  const html = `<div class="hero"><h1 tabindex="-1">Projects</h1><div class="meta">${rows.length ? `${plural(rows.length, "project")}, found in your agent sessions, commits and pull requests. Each is a folder or repository.` : "None found yet."}</div></div><div class="card" style="padding:8px 20px">${nameList(rows, "project")}</div>`;
  return { html, title: "Projects" };
}
async function peoplePage() {
  const d = await api("people");
  const html = `<div class="hero"><h1 tabindex="-1">People</h1><div class="meta">${plural(d.people.length, "person", "people")} you texted or wrote to${d.senders.length ? `, and ${plural(d.senders.length, "list or notification sender")}` : ""}.</div></div>
    <div class="card" style="padding:8px 20px">${nameList(d.people, "person")}</div>
    ${d.senders.length ? `<div class="section-head"><h2>Lists and notifications</h2><span class="aside">Senders whose threads all carry one of Gmail's category labels</span></div><div class="card" style="padding:8px 20px">${nameList(d.senders, "person")}</div>` : ""}`;
  return { html, title: "People" };
}
async function groupPage(kind, key) {
  const d = await api(`${kind}?key=${enc(key)}`);
  const noun = { project: "Project", person: "Person", source: "Source" }[kind];
  const parent = { project: ["/projects", "Projects"], person: ["/people", "People"], source: ["/", "Overview"] }[kind];
  const name = kind === "source" ? d.name : d.name;
  const facts = [];
  if (kind === "person") {
    if (d.addresses.length) facts.push(`<span><b>Addresses</b> ${d.addresses.map(esc).join(", ")}</span>`);
    if (d.first) facts.push(`<span><b>First</b> ${esc(shortDay(d.first))}</span>`);
    if (d.last) facts.push(`<span><b>Latest</b> ${esc(shortDay(d.last))}</span>`);
  } else if (kind === "project" && d.last) facts.push(`<span><b>Latest</b> ${esc(shortDay(d.last))}</span>`);
  const total = d.total;
  const html = `<div class="crumbs"><a href="${parent[0]}">${parent[1]}</a><span aria-hidden="true">/</span><span>${esc(name)}</span></div>
    <div class="hero" style="padding-top:12px"><h1 tabindex="-1">${esc(name)}</h1><div class="meta">${noun} · ${plural(total, "item")}${d.automated ? " · a list or notification sender" : ""}</div>
      ${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}<div class="chips-static">${famChips(d.families)}</div>
      <div class="sparkrow">${spark(trimDays(d.daily, 60))}<div class="cap" style="color:var(--muted);font-size:12px">Items per day, the 60 days to ${d.daily.end ? esc(dLabel(d.daily.end)) : "now"}</div></div></div>
    <div class="section-head"><h2>Items</h2><span class="aside" id="shown"></span></div>
    <div class="card" style="padding:8px 20px"><div id="items"></div><div style="padding:8px 0"><button class="btn" id="more" type="button" hidden></button></div></div>`;
  const after = () => {
    let shown = 0;
    const paint = page => {
      $("#items").insertAdjacentHTML("beforeend", page.items.map(i => itemRow(i)).join("") || `<div class="empty">Nothing here.</div>`);
      shown += page.items.length;
      $("#shown").textContent = `${shown.toLocaleString()} of ${total.toLocaleString()}, newest first`;
      const more = $("#more"); more.hidden = shown >= total; more.textContent = `Show ${Math.min(page.limit, total - shown)} more`;
    };
    paint(d);
    $("#more").onclick = async e => {
      e.target.disabled = true;
      try { paint(await api(`${kind}?key=${enc(key)}&offset=${shown}`)); } catch (err) { e.target.textContent = "Could not load more: " + err.message; }
      e.target.disabled = false;
    };
  };
  return { html, title: name, after };
}

// ---------- search ----------
async function searchPage() {
  const params = new URLSearchParams(location.search);
  const q = params.get("q") || "", src = params.get("source") || "";
  $("#q").value = q;
  const [hits, D] = await Promise.all([q.trim() ? api(`search?q=${enc(q)}${src ? "&source=" + enc(src) : ""}`) : [], overviewData()]);
  const chips = D.sources.filter(s => !s.service && s.items).map(s => `<a class="chip" aria-pressed="${s.name === src}" href="/search?q=${enc(q)}${s.name === src ? "" : "&source=" + enc(s.name)}" style="text-decoration:none;display:inline-flex;align-items:center">${esc(s.name)}</a>`).join("");
  const list = hits.map(h => `<a class="hit" href="/item/${h.id}?q=${enc(q)}"><div class="h"><span>${famDot(h.family)}${esc(fam(h.family).one)} · ${esc(h.source)}</span><span>${esc(shortDay(h.ts))}</span></div><div class="title">${hl(h.title || "(untitled)", q)}</div><div class="snip">${hl(h.excerpt, q)}</div></a>`).join("");
  const html = `<div id="results" class="on"><div class="card-head" style="margin-bottom:12px"><h1 class="serif" style="margin:0;font-size:24px" tabindex="-1">${q.trim() ? `Results for “${esc(q)}”` : "Search"}</h1></div>
    <div class="chips" role="group" aria-label="Filter by source">${chips}</div>
    <div class="card" style="padding:0" aria-live="polite">${list || `<div class="empty">${q.trim() ? "Nothing matches. Try other distinctive words." : "Type words from what you are looking for."}</div>`}</div></div>`;
  return { html, title: q ? `Search: ${q}` : "Search" };
}

// ---------- reader ----------
function turnHTML(t, q) {
  if (t.role === "tool") {
    const first = (t.input || "").split("\n", 1)[0].slice(0, 140);
    return `<details class="tool${t.failed ? " failed" : ""}" data-at="${t.at}"><summary><b>${esc(t.name)}</b> ${esc(first)}${t.failed ? " · failed" : ""}</summary><pre class="mono">${hl(t.input || "", q)}</pre>${t.output != null ? `<pre class="mono out">${hl(t.output, q)}</pre>` : ""}</details>`;
  }
  const who = { user: "You", assistant: "Assistant", error: "Error", result: "Result" }[t.role] || t.role;
  return `<div class="turn ${esc(t.role)}" data-at="${t.at}"><div class="who">${who}</div><div class="says">${hl(t.text, q)}</div></div>`;
}
async function itemPage(id) {
  const params = new URLSearchParams(location.search);
  const q = params.get("q") || "", page = params.get("page");
  const d = await api(`item?id=${id}${page != null ? "&page=" + enc(page) : ""}${q ? "&q=" + enc(q) : ""}`);
  if (d.error) return { html: `<div class="card err-note"><b>This item cannot be shown</b>${esc(d.error)}</div>`, title: "Item" };
  const pageLink = n => `/item/${d.id}?page=${n}${q ? "&q=" + enc(q) : ""}`;
  const pager = d.pages > 1 ? `<div class="pager"><a class="btn" href="${pageLink(d.page - 1)}" aria-disabled="${d.page === 0}" ${d.page === 0 ? 'tabindex="-1"' : ""}>Previous page</a><a class="btn" href="${pageLink(d.page + 1)}" aria-disabled="${d.page >= d.pages - 1}" ${d.page >= d.pages - 1 ? 'tabindex="-1"' : ""}>Next page</a><span class="pos">Page ${d.page + 1} of ${d.pages} · characters ${d.offset.toLocaleString()} to ${d.end.toLocaleString()} of ${d.total.toLocaleString()}</span></div>` : "";
  const people = d.people.map(p => link("person", p.key, p.name)).join(", ");
  const url = safeHref(d.url);
  const meta = [`<span>${famDot(d.family)}${esc(fam(d.family).one)} · ${esc(d.source)}</span>`, `<span>${esc(stamp(d.ts))}</span>`,
    d.project ? `<span>Project: ${link("project", d.project.key, d.project.name)}</span>` : "", people ? `<span>With: ${people}</span>` : "",
    d.superseded ? `<span class="tag warn">Replaced by a newer record</span>` : ""].filter(Boolean).join("");
  const where = url ? `<a class="btn" href="${esc(url)}" rel="noreferrer noopener" target="_blank">Open the original</a>` : d.link ? `<span class="mono" style="color:var(--muted)">${esc(d.link)}</span>` : "";
  const sums = d.summaries.length ? `<div class="sumbox"><b>Topics found in this item</b><ul style="margin:0;padding-left:18px">${d.summaries.map(s => `<li>${esc(s.title)}</li>`).join("")}</ul></div>` : "";
  let body;
  if (d.session) {
    body = `<div class="session">${d.head ? `<div class="head mono">${esc(d.head)}</div>` : ""}${d.turns.map(t => turnHTML(t, q)).join("") || `<div class="empty">Nothing on this page.</div>`}</div>`;
  } else {
    let text = d.text;
    if (d.anchor != null && d.anchor > 0 && d.anchor < text.length) body = `<div class="body">${hl(text.slice(0, d.anchor), q)}<span class="anchor" id="anchor">The record you opened starts here</span>${hl(text.slice(d.anchor), q)}</div>`;
    else body = `<div class="body">${hl(text, q)}</div>`;
  }
  const html = `<div class="reader"><div class="crumbs"><a href="/">Overview</a><span aria-hidden="true">/</span><a href="${href("source", d.source)}">${esc(d.source)}</a></div>
    <div class="hero" style="padding:12px 0 6px"><h1 tabindex="-1">${esc(d.title || "(untitled)")}</h1><div class="facts" style="gap:6px 18px">${meta}</div><div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">${where}</div></div>
    ${sums}${pager}${body}${pager}</div>`;
  const after = () => {
    if (d.session && d.anchor != null) {
      const marks = [...document.querySelectorAll("[data-at]")].filter(e => +e.dataset.at <= d.anchor);
      const target = marks[marks.length - 1];
      if (target) { target.classList.add("here"); if (target.tagName === "DETAILS") target.open = true; target.scrollIntoView({ block: "center" }); }
    } else if (d.anchor) { const a = $("#anchor"); if (a) a.scrollIntoView({ block: "center" }); }
  };
  return { html, title: d.title, after };
}

overviewData().then(renderPill).catch(() => { $("#pilltext").textContent = "Not available"; });
render();
