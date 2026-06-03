// mfo review editor — local SPA (spec §13.1-13.5).
//
// A dependency-free client over the review API: it lists pages (§13.1), draws the page image with
// clickable region overlays on a zoom/pan canvas (§13.2, §13.5), shows the selected region's OCR,
// translation, candidates, edit history and confidence in the side panel (§13.2), and edits in
// place (§13.3/13.4): translate-in-place, candidate revert, status flags, move/resize, split/merge,
// manual reading-order steps, a low-confidence-first review queue, and a re-rendered page preview.

"use strict";

const STATUSES = [
  { value: "correct", label: "Correct", key: "1" },
  { value: "needs_review", label: "Needs review", key: "2" },
  { value: "ignore", label: "Ignore", key: "3" },
  { value: "manual", label: "Manual", key: "4" },
];

const state = {
  pages: [], // project page index
  page: null, // active page_view payload
  regions: [], // active page regions, in reading order
  unitByRegion: new Map(), // region_id -> unit payload
  selected: null, // index into state.regions, or null
  marked: new Set(), // region_ids tagged for a merge (shift-click)
  queue: [], // review-queue entries, low-confidence first
  preview: false, // showing the re-rendered page over the source
  createMode: false, // drawing a new region instead of panning
  historyScope: "global", // "global" | "page" — what undo/redo and the history panel act on
  projectId: null, // for namespacing the remembered last page
  queueFilter: false, // queue shows only needs-review entries when true
  centerOnClick: false, // recenter the canvas when a region is clicked (arrows always center)
  zoom: 1,
  pan: { x: 0, y: 0 },
};

// Resize handles around the selected region, and which box edges each one moves.
const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

// -- tiny helpers -------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    if (child) node.append(child);
  }
  return node;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function sendJSON(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

function status(text) {
  $("#status-text").textContent = text;
}

function pct(conf) {
  return conf === null || conf === undefined ? "—" : `${Math.round(conf * 100)}%`;
}

function selectedRegion() {
  return state.selected === null ? null : state.regions[state.selected];
}

// -- project + page list (§13.1) ----------------------------------------------------------

async function loadProject() {
  const data = await getJSON("/api/project");
  const p = data.project;
  state.projectId = p.id;
  document.title = `${p.name} — mfo review`;
  $("#project-meta").textContent = `${p.name} · ${p.source_lang} → ${p.target_lang} · ${p.reading_direction}`;
  state.pages = data.pages;
  renderPageList();

  const lowTotal = data.pages.reduce((n, pg) => n + pg.low_confidence, 0);
  const aiTotal = data.pages.reduce((n, pg) => n + (pg.ai_flagged || 0), 0);
  const aiNote = aiTotal ? `, ${aiTotal} AI-flagged` : "";
  status(`${data.pages.length} page(s), ${lowTotal} low-confidence region(s)${aiNote}.`);
  if (data.pages.length) {
    // Reopen on the page the user last viewed (per project), falling back to the first.
    const lastId = readLastPage();
    const pick = data.pages.find((pg) => pg.page_id === lastId) || data.pages[0];
    selectPage(pick.page_id);
  }
}

function lastPageKey() {
  return `mfo:last-page:${state.projectId}`;
}

function readLastPage() {
  try {
    return localStorage.getItem(lastPageKey());
  } catch {
    return null;
  }
}

function rememberLastPage(pageId) {
  try {
    localStorage.setItem(lastPageKey(), pageId);
  } catch {
    /* storage may be unavailable; the last page just won't be remembered */
  }
}

// Re-fetch the project index and redraw the page list so per-page counts (regions/units/
// low-confidence) stay live after an edit, without disturbing the current selection.
async function refreshProject() {
  try {
    const data = await getJSON("/api/project");
    state.pages = data.pages;
    renderPageList();
  } catch {
    /* a transient refresh failure shouldn't interrupt the edit that triggered it */
  }
}

function renderPageList() {
  const list = $("#page-list");
  list.replaceChildren();
  for (const page of state.pages) {
    const li = el("li", { "data-page-id": page.page_id }, [
      el("span", { class: "page-idx", text: String(page.index + 1) }),
      el("span", { class: "muted", text: `${page.regions}r / ${page.units}u` }),
    ]);
    if (page.low_confidence > 0) {
      li.append(el("span", { class: "lc-badge", text: String(page.low_confidence) }));
    }
    if (state.page && page.page_id === state.page.page_id) li.classList.add("active");
    li.addEventListener("click", () => selectPage(page.page_id));
    list.append(li);
  }
}

// -- page editor (§13.2) ------------------------------------------------------------------

async function selectPage(pageId, keepSelectionId = null) {
  const view = await getJSON(`/api/pages/${pageId}`);
  applyPageView(view, keepSelectionId);
  status(`Page ${view.index + 1}: ${view.regions.length} region(s), ${view.units.length} unit(s).`);
}

// Adopt a (possibly freshly mutated) page_view: refresh state, redraw, and keep the selection on
// the same region id where possible so a region op doesn't bounce the user away from their work.
function applyPageView(view, keepSelectionId = null) {
  const priorId = keepSelectionId ?? (selectedRegion() && selectedRegion().region_id);
  const samePage = state.page && state.page.page_id === view.page_id;
  state.page = view;
  if (!samePage) rememberLastPage(view.page_id);
  state.regions = view.regions; // already reading-order sorted by the API

  state.unitByRegion = new Map();
  for (const unit of view.units) {
    for (const rid of unit.ordered_region_ids) state.unitByRegion.set(rid, unit);
  }

  const idx = state.regions.findIndex((r) => r.region_id === priorId);
  state.selected = idx >= 0 ? idx : null;
  state.marked = new Set([...state.marked].filter((id) => state.unitByRegion.has(id) || hasRegion(id)));

  for (const li of $("#page-list").children) {
    li.classList.toggle("active", li.dataset.pageId === view.page_id);
  }

  const img = $("#page-img");
  const stage = $("#canvas-stage");
  stage.style.width = `${view.width}px`;
  stage.style.height = `${view.height}px`;
  img.style.width = `${view.width}px`;
  img.style.height = `${view.height}px`;
  if (!samePage) {
    img.src = `/api/pages/${view.page_id}/image`;
    hidePreview();
  }
  $("#canvas-empty").hidden = true;

  drawRegions();
  if (!samePage) fitPage();
  renderInspector();
}

function hasRegion(id) {
  return state.regions.some((r) => r.region_id === id);
}

function drawRegions() {
  const overlay = $("#region-overlay");
  overlay.replaceChildren();
  state.regions.forEach((region, i) => {
    const b = region.bbox;
    const box = el("div", {
      class:
        `region-box status-${region.status}` +
        (region.low_confidence ? " low" : "") +
        (region.ai_flagged ? " ai" : "") +
        (state.marked.has(region.region_id) ? " marked" : ""),
      "data-idx": String(i),
    });
    box.style.left = `${b.x}px`;
    box.style.top = `${b.y}px`;
    box.style.width = `${b.width}px`;
    box.style.height = `${b.height}px`;
    box.append(el("span", { class: "region-tag", text: region.type }));
    box.addEventListener("mousedown", (e) => onRegionMouseDown(e, i));
    if (i === state.selected) {
      box.classList.add("selected");
      for (const dir of HANDLES) {
        const handle = el("div", { class: `region-handle handle-${dir}` });
        handle.addEventListener("mousedown", (e) => onResizeMouseDown(e, i, dir));
        box.append(handle);
      }
    }
    overlay.append(box);
  });
}

// `center` recenters the canvas on the region. Arrow/queue navigation always centers (it's how the
// user sees where they jumped); a plain mouse click only centers when the user opts in (item 12).
function selectRegion(i, { center = false } = {}) {
  state.selected = i;
  drawRegions();
  renderInspector();
  if (center) scrollSelectedIntoView();
}

// -- side panel (§13.2) -------------------------------------------------------------------

function renderInspector() {
  const body = $("#inspector-body");
  const empty = $("#inspector-empty");
  if (state.selected === null) {
    body.hidden = true;
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  body.hidden = false;
  body.replaceChildren();

  const region = state.regions[state.selected];
  const unit = state.unitByRegion.get(region.region_id);

  body.append(metadataSection(region));
  body.append(statusSection(region));
  body.append(regionOpsSection(region));
  body.append(ocrSection(region));
  if (unit) {
    body.append(translationSection(unit));
    body.append(candidatesSection(unit));
    body.append(historySection(unit));
  } else {
    body.append(section("Translation", [el("p", { class: "muted", text: "No unit links this region." })]));
  }
}

function section(title, children) {
  return el("div", { class: "insp-section" }, [el("h3", { text: title }), ...[].concat(children)]);
}

function confBar(conf, low) {
  const bar = el("div", { class: `conf-bar${low ? " low" : ""}` });
  const fill = el("span");
  fill.style.width = conf ? `${Math.round(conf * 100)}%` : "0%";
  bar.append(fill);
  return bar;
}

function metadataSection(region) {
  const dl = el("dl", { class: "kv" }, [
    el("dt", { text: "Type" }),
    el("dd", { text: region.type }),
    el("dt", { text: "Order" }),
    el("dd", { text: region.reading_order_index === null ? "—" : String(region.reading_order_index) }),
    el("dt", { text: "Confidence" }),
    el("dd", {}, [
      el("span", {
        class: `chip ${region.low_confidence ? "warn" : "good"}`,
        text: pct(region.confidence),
      }),
    ]),
  ]);
  return section("Region", [dl, confBar(region.confidence, region.low_confidence)]);
}

// Review status flags (FR-40): a user choice that automation must not clobber (I-3).
function statusSection(region) {
  const row = el("div", { class: "btn-row" });
  for (const s of STATUSES) {
    const btn = el("button", {
      class: region.status === s.value ? "active" : "",
      title: `Mark ${s.label.toLowerCase()} (${s.key})`,
      text: s.label,
    });
    btn.addEventListener("click", () => setStatus(s.value));
    row.append(btn);
  }
  return section("Status", [row]);
}

// Region geometry/structure ops (FR-38/39): split, merge, reading-order nudge.
function regionOpsSection(region) {
  const row = el("div", { class: "btn-row" });
  const split = el("button", { title: "Split into two (s)", text: "Split ⬍" });
  split.addEventListener("click", () => splitRegion("horizontal"));
  const splitV = el("button", { title: "Split left/right", text: "Split ⬌" });
  splitV.addEventListener("click", () => splitRegion("vertical"));
  const merge = el("button", {
    class: state.marked.size ? "active" : "",
    title: "Merge marked regions (shift-click to mark, m)",
    text: `Merge (${state.marked.size})`,
  });
  merge.addEventListener("click", mergeMarked);
  const up = el("button", { title: "Move earlier in reading order", text: "Order ↑" });
  up.addEventListener("click", () => nudgeOrder(-1));
  const down = el("button", { title: "Move later in reading order", text: "Order ↓" });
  down.addEventListener("click", () => nudgeOrder(1));
  const del = el("button", { class: "danger", title: "Delete region (Del)", text: "Delete ✕" });
  del.addEventListener("click", deleteRegion);
  row.append(split, splitV, merge, up, down, del);
  return section("Region ops", [el("p", { class: "muted hint", text: mergeHint() }), row]);
}

// Merge needs ≥2 regions tagged; nudge the user toward shift-click when they haven't yet.
function mergeHint() {
  return state.marked.size
    ? `${state.marked.size} region(s) marked — press Merge or m.`
    : "Shift-click regions to mark them, then Merge.";
}

function ocrSection(region) {
  const rerun = el("button", { title: "Run OCR on this region now", text: "Re-run OCR" });
  rerun.addEventListener("click", reocrRegion);
  const head = el("div", { class: "btn-row" }, [rerun]);
  if (!region.ocr.length) {
    return section("OCR", [head, el("p", { class: "muted", text: "No OCR for this region." })]);
  }
  const blocks = region.ocr.map((span) => {
    const block = el("div", {}, [el("div", { class: "text-block source", text: span.text })]);
    const head = el("div", { class: "cand-head" }, [
      el("span", { text: `conf ${pct(span.confidence)}` }),
    ]);
    if (span.alternatives && span.alternatives.length) {
      head.append(el("span", { class: "muted", text: `· ${span.alternatives.length} alt` }));
    }
    block.append(head);
    return block;
  });
  return section("OCR", [head, ...blocks]);
}

// Editable translation (FR-37): a textarea + Save; user text wins over automation (I-3).
function translationSection(unit) {
  const ta = el("textarea", { class: "insp-edit", id: "translation-edit", rows: "3" });
  ta.value = unit.translation || "";
  const row = el("div", { class: "btn-row" });
  const save = el("button", { class: "primary", title: "Save (Ctrl+Enter)", text: "Save" });
  save.addEventListener("click", () => saveTranslation(unit.unit_id, ta.value));
  const reset = el("button", { title: "Discard changes", text: "Reset" });
  reset.addEventListener("click", () => {
    ta.value = unit.translation || "";
  });
  const retrans = el("button", { title: "Re-run machine translation now", text: "Re-translate" });
  retrans.addEventListener("click", () => retranslateUnit(unit.unit_id));
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      saveTranslation(unit.unit_id, ta.value);
    }
  });
  row.append(save, reset, retrans);
  return section("Translation", [ta, row]);
}

function candidatesSection(unit) {
  if (!unit.candidates.length) return el("span");
  const cards = unit.candidates.map((c) => {
    const selected = c.id === unit.selected_candidate_id;
    const card = el("div", { class: `cand${selected ? " selected" : ""}`, title: "Use this candidate" }, [
      el("div", { class: "cand-head" }, [
        el("span", { class: "chip", text: c.kind }),
        selected ? el("span", { class: "chip good", text: "selected" }) : null,
        el("span", { class: "muted", text: pct(c.confidence) }),
      ]),
      el("div", { text: c.text || "—" }),
      // The AI layer's rationale folds in its reasoning, ambiguity warnings, and speaker-shift
      // hints — keep that uncertainty visible to the reviewer (I-4, FR-30).
      c.rationale ? el("div", { class: "cand-rationale muted", text: c.rationale }) : null,
    ]);
    if (!selected) card.addEventListener("click", () => selectCandidate(unit.unit_id, c.id));
    return card;
  });
  return section(`Candidates (${unit.candidates.length})`, cards);
}

function historySection(unit) {
  if (!unit.edits.length) {
    return section("Edit history", [el("p", { class: "muted", text: "No edits yet." })]);
  }
  const rows = unit.edits
    .slice()
    .reverse()
    .map((e) =>
      el("div", { class: "edit" }, [
        el("div", { class: "edit-head" }, [
          el("span", { text: e.action }),
          el("span", { class: "muted", text: e.editor }),
          el("span", { class: "muted", text: new Date(e.timestamp).toLocaleString() }),
        ]),
        el("div", { class: "edit-diff" }, [
          el("span", { class: "before", text: e.before || "∅" }),
          document.createTextNode(" → "),
          el("span", { class: "after", text: e.after || "∅" }),
        ]),
      ]),
    );
  return section(`Edit history (${unit.edits.length})`, rows);
}

// -- mutations (§13.3/13.4) ---------------------------------------------------------------

async function saveTranslation(unitId, text) {
  try {
    await sendJSON("PUT", `/api/units/${unitId}/translation`, { text });
    await selectPage(state.page.page_id);
    invalidatePreview();
    status("Translation saved.");
  } catch (err) {
    status(`Save failed: ${err.message}`);
  }
}

async function selectCandidate(unitId, candidateId) {
  try {
    await sendJSON("POST", `/api/units/${unitId}/select`, { candidate_id: candidateId });
    await selectPage(state.page.page_id);
    invalidatePreview();
    status("Candidate selected.");
  } catch (err) {
    status(`Select failed: ${err.message}`);
  }
}

async function setStatus(value) {
  const region = selectedRegion();
  if (!region) return;
  try {
    const view = await sendJSON("PUT", `/api/regions/${region.region_id}/status`, { status: value });
    applyPageView(view, region.region_id);
    status(`Marked ${value}.`);
  } catch (err) {
    status(`Status failed: ${err.message}`);
  }
}

async function saveRegionBBox(region, bbox) {
  try {
    const view = await sendJSON("PUT", `/api/regions/${region.region_id}/bbox`, bbox);
    applyPageView(view, region.region_id);
    invalidatePreview();
    status("Region box updated.");
  } catch (err) {
    status(`Move failed: ${err.message}`);
    selectPage(state.page.page_id, region.region_id);
  }
}

async function splitRegion(orientation) {
  const region = selectedRegion();
  if (!region) return;
  try {
    const view = await sendJSON("POST", `/api/regions/${region.region_id}/split`, {
      orientation,
      ratio: 0.5,
    });
    applyPageView(view, region.region_id);
    invalidatePreview();
    refreshProject();
    status("Region split.");
  } catch (err) {
    status(`Split failed: ${err.message}`);
  }
}

function toggleMark(regionId) {
  if (state.marked.has(regionId)) state.marked.delete(regionId);
  else state.marked.add(regionId);
  drawRegions();
  renderInspector();
}

async function mergeMarked() {
  const ids = [...state.marked];
  if (ids.length < 2) {
    status("Shift-click at least two regions to merge.");
    return;
  }
  try {
    const view = await sendJSON("POST", `/api/regions/merge`, { region_ids: ids });
    state.marked.clear();
    applyPageView(view, ids[0]);
    invalidatePreview();
    refreshProject();
    status(`Merged ${ids.length} regions.`);
  } catch (err) {
    status(`Merge failed: ${err.message}`);
  }
}

// Manual reading-order correction (FR-20): swap the selected region with its neighbour.
async function nudgeOrder(delta) {
  if (state.selected === null) return;
  const target = state.selected + delta;
  if (target < 0 || target >= state.regions.length) return;
  const ids = state.regions.map((r) => r.region_id);
  [ids[state.selected], ids[target]] = [ids[target], ids[state.selected]];
  const movedId = state.regions[state.selected].region_id;
  try {
    const view = await sendJSON("PUT", `/api/pages/${state.page.page_id}/order`, {
      ordered_region_ids: ids,
    });
    applyPageView(view, movedId);
    invalidatePreview();
    status("Reading order updated.");
  } catch (err) {
    status(`Reorder failed: ${err.message}`);
  }
}

async function deleteRegion() {
  const region = selectedRegion();
  if (!region) return;
  try {
    const view = await sendJSON("DELETE", `/api/regions/${region.region_id}`);
    state.marked.delete(region.region_id);
    state.selected = null;
    applyPageView(view);
    invalidatePreview();
    refreshProject();
    status("Region deleted.");
  } catch (err) {
    status(`Delete failed: ${err.message}`);
  }
}

// -- create region: draw a box, then OCR + translate it (§13.3) ---------------------------

function toggleCreateMode(on) {
  state.createMode = on === undefined ? !state.createMode : on;
  $("#create-btn").classList.toggle("active", state.createMode);
  $("#canvas-viewport").classList.toggle("creating", state.createMode);
  status(state.createMode ? "Draw a box for the new region (Esc to cancel)." : "");
}

// Viewport client coords → source-image (stage-local) coords, undoing the pan/zoom transform.
function toImageCoords(clientX, clientY) {
  const vp = $("#canvas-viewport").getBoundingClientRect();
  return {
    x: (clientX - vp.left - state.pan.x) / state.zoom,
    y: (clientY - vp.top - state.pan.y) / state.zoom,
  };
}

function startDraw(e) {
  if (!state.page) return;
  const origin = toImageCoords(e.clientX, e.clientY);
  const ghost = el("div", { class: "region-box draw-ghost" });
  $("#region-overlay").append(ghost);

  function rect(ev) {
    const p = toImageCoords(ev.clientX, ev.clientY);
    return {
      x: Math.min(origin.x, p.x),
      y: Math.min(origin.y, p.y),
      width: Math.abs(p.x - origin.x),
      height: Math.abs(p.y - origin.y),
    };
  }
  function onMove(ev) {
    const b = rect(ev);
    ghost.style.left = `${b.x}px`;
    ghost.style.top = `${b.y}px`;
    ghost.style.width = `${b.width}px`;
    ghost.style.height = `${b.height}px`;
  }
  function onUp(ev) {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    ghost.remove();
    const b = rect(ev);
    toggleCreateMode(false);
    if (b.width >= 4 && b.height >= 4) createRegion(b);
  }
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

async function createRegion(bbox) {
  try {
    status("Creating region — OCR + translate…");
    const view = await sendJSON("POST", `/api/pages/${state.page.page_id}/regions`, bbox);
    applyPageView(view);
    invalidatePreview();
    refreshProject();
    status("Region created (OCR + translated).");
  } catch (err) {
    status(`Create failed: ${err.message}`);
  }
}

// -- per-region OCR / translate jobs (§13.3) ----------------------------------------------

async function reocrRegion() {
  const region = selectedRegion();
  if (!region) return;
  try {
    status("Running OCR…");
    const view = await sendJSON("POST", `/api/regions/${region.region_id}/ocr`, {});
    applyPageView(view, region.region_id);
    invalidatePreview();
    refreshProject();
    status("OCR re-run.");
  } catch (err) {
    status(`OCR failed: ${err.message}`);
  }
}

async function retranslateUnit(unitId) {
  try {
    status("Translating…");
    await sendJSON("POST", `/api/units/${unitId}/translate`, {});
    await selectPage(state.page.page_id);
    invalidatePreview();
    status("Re-translated.");
  } catch (err) {
    status(`Translate failed: ${err.message}`);
  }
}

// -- re-render preview (§13.3) ------------------------------------------------------------

async function showPreview() {
  if (!state.page) return;
  try {
    status("Rendering preview…");
    const info = await sendJSON("POST", `/api/pages/${state.page.page_id}/render`, {});
    const img = $("#render-preview");
    // Cache-bust so a re-render after edits actually reloads the image.
    img.src = `${info.render_url}?t=${Date.now()}`;
    img.style.width = `${state.page.width}px`;
    img.style.height = `${state.page.height}px`;
    img.hidden = false;
    state.preview = true;
    $("#render-toggle").classList.add("active");
    $("#region-overlay").hidden = true;
    const note = info.overflow ? ` (${info.overflow} overflow)` : "";
    status(`Showing rendered preview${note}.`);
  } catch (err) {
    status(`Preview failed: ${err.message}`);
  }
}

function hidePreview() {
  state.preview = false;
  $("#render-preview").hidden = true;
  $("#region-overlay").hidden = false;
  $("#render-toggle").classList.remove("active");
}

function togglePreview() {
  if (state.preview) hidePreview();
  else showPreview();
}

// An edit invalidates a shown preview; drop it so the user re-renders to see the change.
function invalidatePreview() {
  if (state.preview) hidePreview();
}

// -- review queue (§13.4) -----------------------------------------------------------------

async function loadQueue() {
  const data = await getJSON("/api/review-queue");
  state.queue = data.entries;
  renderQueue();
}

// The "needs review" filter narrows the queue to entries flagged needs_review (item 7).
function filteredQueue() {
  return state.queueFilter
    ? state.queue.filter((e) => e.status === "needs_review")
    : state.queue;
}

function renderQueue() {
  const list = $("#queue-list");
  list.replaceChildren();
  $("#queue-filter").classList.toggle("active", state.queueFilter);
  const cur = selectedRegion();
  const entries = filteredQueue();
  if (!entries.length) {
    const msg = state.queueFilter ? "No needs-review regions." : "Queue is empty.";
    list.append(el("p", { class: "muted", text: msg }));
    return;
  }
  entries.forEach((entry, i) => {
    const cls = `queue-row${entry.low_confidence ? " low" : ""}${entry.ai_flagged ? " ai" : ""}`;
    const row = el("div", { class: cls, "data-i": String(i) }, [
      el("span", { class: "dot" }),
      el("span", { class: "page-idx", text: `p${entry.page_index + 1}` }),
      el("span", { class: "muted", text: pct(entry.confidence) }),
      // An AI badge marks units the optional AI layer flagged as uncertain; its rationale (the
      // reasoning/warnings) rides along as the row tooltip so the reviewer sees why (FR-30, I-4).
      entry.ai_flagged ? el("span", { class: "ai-badge", text: "AI", title: entry.ai_rationale || "AI-flagged" }) : null,
      el("span", { class: "muted", text: entry.status }),
    ]);
    if (entry.ai_rationale) row.title = entry.ai_rationale;
    if (cur && entry.region_id === cur.region_id) row.classList.add("active");
    row.addEventListener("click", () => gotoQueueEntry(i));
    list.append(row);
  });
}

async function gotoQueueEntry(i) {
  const entry = filteredQueue()[i];
  if (!entry) return;
  if (!state.page || state.page.page_id !== entry.page_id) {
    await selectPage(entry.page_id, entry.region_id);
  } else {
    const idx = state.regions.findIndex((r) => r.region_id === entry.region_id);
    if (idx >= 0) selectRegion(idx, { center: true });
  }
  renderQueue();
}

function stepQueue(delta) {
  const entries = filteredQueue();
  if (!entries.length) return;
  const cur = selectedRegion();
  const at = cur ? entries.findIndex((e) => e.region_id === cur.region_id) : -1;
  const next = at < 0 ? (delta > 0 ? 0 : entries.length - 1) : (at + delta + entries.length) % entries.length;
  gotoQueueEntry(next);
}

function toggleQueueFilter() {
  state.queueFilter = !state.queueFilter;
  renderQueue();
}

function queueOpen() {
  return !$("#queue-panel").hidden;
}

// When an overlay panel (queue or history) is up, stop the page list behind it from scrolling too,
// so the two scrollbars don't fight (item 9).
function syncPanelOverlay() {
  const open = !$("#queue-panel").hidden || !$("#history-panel").hidden;
  $("#pages-panel").classList.toggle("panel-open", open);
}

async function toggleQueue() {
  const panel = $("#queue-panel");
  const showing = panel.hidden;
  if (showing) {
    $("#history-panel").hidden = true; // the two overlays are mutually exclusive
    $("#history-btn").classList.remove("active");
    await loadQueue();
  }
  panel.hidden = !showing;
  $("#queue-btn").classList.toggle("active", showing);
  syncPanelOverlay();
}

// -- undo / redo history (§13; FR-42) -----------------------------------------------------

function historyScopePageId() {
  return state.historyScope === "page" && state.page ? state.page.page_id : null;
}

async function stepHistory(kind) {
  const pageId = historyScopePageId();
  try {
    const res = await sendJSON("POST", `/api/${kind}`, { page_id: pageId });
    if (!res.affected_page_id) {
      status(kind === "undo" ? "Nothing to undo." : "Nothing to redo.");
    } else {
      await selectPage(res.affected_page_id);
      status(kind === "undo" ? "Undid an edit." : "Redid an edit.");
    }
    await refreshProject();
    renderHistory(res.history);
    if (!$("#queue-panel").hidden) await loadQueue();
  } catch (err) {
    status(`${kind} failed: ${err.message}`);
  }
}

const undo = () => stepHistory("undo");
const redo = () => stepHistory("redo");

async function loadHistory() {
  const pageId = historyScopePageId();
  const data = await getJSON(`/api/history${pageId ? `?page_id=${pageId}` : ""}`);
  renderHistory(data);
}

function renderHistory(data) {
  if (!data) return;
  $("#history-scope").textContent = data.scope === "page" ? "This page" : "Global";
  $("#undo-btn").disabled = !data.can_undo;
  $("#redo-btn").disabled = !data.can_redo;
  const list = $("#history-list");
  list.replaceChildren();
  if (!data.entries.length) {
    list.append(el("p", { class: "muted", text: "No edits yet." }));
    return;
  }
  for (const e of data.entries) {
    list.append(
      el("div", { class: `history-row${e.undone ? " undone" : ""}` }, [
        el("span", { class: "page-idx", text: `#${e.seq}` }),
        el("span", { text: e.action.replace(/_/g, " ") }),
        el("span", { class: "muted", text: e.editor }),
      ]),
    );
  }
}

async function toggleHistory() {
  const panel = $("#history-panel");
  const showing = panel.hidden;
  if (showing) {
    $("#queue-panel").hidden = true; // the two overlays are mutually exclusive
    $("#queue-btn").classList.remove("active");
    await loadHistory();
  }
  panel.hidden = !showing;
  $("#history-btn").classList.toggle("active", showing);
  syncPanelOverlay();
}

function toggleHistoryScope() {
  state.historyScope = state.historyScope === "global" ? "page" : "global";
  loadHistory();
}

// -- zoom & pan (§13.5) -------------------------------------------------------------------

function applyTransform() {
  $("#canvas-stage").style.transform =
    `translate(${state.pan.x}px, ${state.pan.y}px) scale(${state.zoom})`;
  $("#zoom-level").textContent = `${Math.round(state.zoom * 100)}%`;
}

function setZoom(z, center) {
  const vp = $("#canvas-viewport").getBoundingClientRect();
  const cx = (center ? center.x : vp.width / 2) - state.pan.x;
  const cy = (center ? center.y : vp.height / 2) - state.pan.y;
  const next = Math.min(8, Math.max(0.05, z));
  const ratio = next / state.zoom;
  state.pan.x -= cx * (ratio - 1);
  state.pan.y -= cy * (ratio - 1);
  state.zoom = next;
  applyTransform();
}

function fitPage() {
  if (!state.page) return;
  const vp = $("#canvas-viewport").getBoundingClientRect();
  const pad = 24;
  const z = Math.min((vp.width - pad) / state.page.width, (vp.height - pad) / state.page.height, 1);
  state.zoom = z > 0 ? z : 1;
  state.pan.x = (vp.width - state.page.width * state.zoom) / 2;
  state.pan.y = (vp.height - state.page.height * state.zoom) / 2;
  applyTransform();
}

function scrollSelectedIntoView() {
  if (state.selected === null) return;
  const b = state.regions[state.selected].bbox;
  const vp = $("#canvas-viewport").getBoundingClientRect();
  const cx = (b.x + b.width / 2) * state.zoom;
  const cy = (b.y + b.height / 2) * state.zoom;
  state.pan.x = vp.width / 2 - cx;
  state.pan.y = vp.height / 2 - cy;
  applyTransform();
}

// -- region drag: move & resize (FR-38) ---------------------------------------------------

function onRegionMouseDown(e, i) {
  e.stopPropagation(); // don't start a canvas pan
  if (e.shiftKey) {
    toggleMark(state.regions[i].region_id);
    return;
  }
  if (i !== state.selected) {
    selectRegion(i, { center: state.centerOnClick });
    return;
  }
  // Drag the already-selected box to move it (FR-38).
  const region = state.regions[i];
  const start = { x: e.clientX, y: e.clientY };
  const origin = { ...region.bbox };
  const box = e.currentTarget;
  box.classList.add("dragging");

  function onMove(ev) {
    const dx = (ev.clientX - start.x) / state.zoom;
    const dy = (ev.clientY - start.y) / state.zoom;
    box.style.left = `${origin.x + dx}px`;
    box.style.top = `${origin.y + dy}px`;
  }
  function onUp(ev) {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    box.classList.remove("dragging");
    const dx = (ev.clientX - start.x) / state.zoom;
    const dy = (ev.clientY - start.y) / state.zoom;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return; // a click, not a drag
    saveRegionBBox(region, {
      x: origin.x + dx,
      y: origin.y + dy,
      width: origin.width,
      height: origin.height,
    });
  }
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

// Resize the selected box by dragging any edge/corner handle (FR-38). `dir` is a compass string
// (n/s/e/w and corners) naming which edges move; the opposite edges stay pinned.
function onResizeMouseDown(e, i, dir) {
  e.stopPropagation();
  e.preventDefault();
  const region = state.regions[i];
  const start = { x: e.clientX, y: e.clientY };
  const origin = { ...region.bbox };
  const box = e.currentTarget.parentElement;
  const west = dir.includes("w");
  const east = dir.includes("e");
  const north = dir.includes("n");
  const south = dir.includes("s");

  function compute(ev) {
    const dx = (ev.clientX - start.x) / state.zoom;
    const dy = (ev.clientY - start.y) / state.zoom;
    let { x, y, width, height } = origin;
    if (east) width = Math.max(1, origin.width + dx);
    if (west) {
      width = Math.max(1, origin.width - dx);
      x = origin.x + (origin.width - width); // keep the east edge fixed
    }
    if (south) height = Math.max(1, origin.height + dy);
    if (north) {
      height = Math.max(1, origin.height - dy); // keep the south edge fixed
      y = origin.y + (origin.height - height);
    }
    return { x, y, width, height };
  }
  function apply(b) {
    box.style.left = `${b.x}px`;
    box.style.top = `${b.y}px`;
    box.style.width = `${b.width}px`;
    box.style.height = `${b.height}px`;
  }
  function onMove(ev) {
    apply(compute(ev));
  }
  function onUp(ev) {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    const b = compute(ev);
    const moved =
      Math.abs(b.x - origin.x) >= 1 ||
      Math.abs(b.y - origin.y) >= 1 ||
      Math.abs(b.width - origin.width) >= 1 ||
      Math.abs(b.height - origin.height) >= 1;
    if (moved) saveRegionBBox(region, b);
  }
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

// -- input wiring -------------------------------------------------------------------------

function stepRegion(delta) {
  if (!state.regions.length) return;
  const next =
    state.selected === null
      ? delta > 0
        ? 0
        : state.regions.length - 1
      : (state.selected + delta + state.regions.length) % state.regions.length;
  selectRegion(next, { center: true });
}

function stepPage(delta) {
  if (!state.pages.length || !state.page) return;
  const cur = state.pages.findIndex((p) => p.page_id === state.page.page_id);
  const next = Math.min(state.pages.length - 1, Math.max(0, cur + delta));
  if (next !== cur) selectPage(state.pages[next].page_id);
}

function focusEdit() {
  const ta = $("#translation-edit");
  if (ta) {
    ta.focus();
    ta.select();
  }
}

function toggleTheme() {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try {
    localStorage.setItem("mfo-theme", root.dataset.theme);
  } catch {
    /* storage may be unavailable; theme just won't persist */
  }
}

// When on, a mouse click on a region also recenters the canvas on it; arrows/queue always do.
function toggleCenterOnClick() {
  state.centerOnClick = !state.centerOnClick;
  $("#center-toggle").classList.toggle("active", state.centerOnClick);
  try {
    localStorage.setItem("mfo-center-click", state.centerOnClick ? "1" : "");
  } catch {
    /* storage may be unavailable; the preference just won't persist */
  }
}

function wirePanZoom() {
  const vp = $("#canvas-viewport");
  let dragging = false;
  let last = { x: 0, y: 0 };

  vp.addEventListener("mousedown", (e) => {
    if (state.createMode) {
      startDraw(e); // drawing a new region takes over from panning
      return;
    }
    dragging = true;
    last = { x: e.clientX, y: e.clientY };
    vp.classList.add("panning");
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    state.pan.x += e.clientX - last.x;
    state.pan.y += e.clientY - last.y;
    last = { x: e.clientX, y: e.clientY };
    applyTransform();
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
    vp.classList.remove("panning");
  });
  vp.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const rect = vp.getBoundingClientRect();
      const center = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      setZoom(state.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), center);
    },
    { passive: false },
  );
}

function wireKeyboard() {
  window.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    // Undo/redo (Ctrl/Cmd+Z, Ctrl/Cmd+Y or Ctrl/Cmd+Shift+Z) act on the current history scope.
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
      e.preventDefault();
      undo();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "y" || e.key.toLowerCase() === "z")) {
      e.preventDefault();
      redo();
      return;
    }
    const statusKey = STATUSES.find((s) => s.key === e.key);
    if (statusKey) {
      e.preventDefault();
      setStatus(statusKey.value);
      return;
    }
    switch (e.key) {
      case "Delete":
      case "Backspace":
        e.preventDefault();
        deleteRegion();
        break;
      case "a":
        e.preventDefault();
        toggleCreateMode();
        break;
      case "Escape":
        if (state.createMode) toggleCreateMode(false);
        break;
      case "ArrowDown":
        e.preventDefault();
        queueOpen() ? stepQueue(1) : stepRegion(1);
        break;
      case "ArrowUp":
        e.preventDefault();
        queueOpen() ? stepQueue(-1) : stepRegion(-1);
        break;
      case "j":
        e.preventDefault();
        stepRegion(1);
        break;
      case "k":
        e.preventDefault();
        stepRegion(-1);
        break;
      case "ArrowRight":
        stepPage(1);
        break;
      case "ArrowLeft":
        stepPage(-1);
        break;
      case "+":
      case "=":
        setZoom(state.zoom * 1.2);
        break;
      case "-":
        setZoom(state.zoom / 1.2);
        break;
      case "0":
        fitPage();
        break;
      case "e":
        e.preventDefault();
        focusEdit();
        break;
      case "s":
        splitRegion("horizontal");
        break;
      case "m":
        mergeMarked();
        break;
      case "r":
        togglePreview();
        break;
      case "n":
        stepQueue(1);
        break;
      case "p":
        stepQueue(-1);
        break;
      case "d":
        toggleTheme();
        break;
    }
  });
}

function init() {
  try {
    const saved = localStorage.getItem("mfo-theme");
    if (saved) document.documentElement.dataset.theme = saved;
    state.centerOnClick = localStorage.getItem("mfo-center-click") === "1";
  } catch {
    /* ignore */
  }
  $("#center-toggle").classList.toggle("active", state.centerOnClick);

  $("#zoom-in").addEventListener("click", () => setZoom(state.zoom * 1.2));
  $("#zoom-out").addEventListener("click", () => setZoom(state.zoom / 1.2));
  $("#zoom-fit").addEventListener("click", fitPage);
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#center-toggle").addEventListener("click", toggleCenterOnClick);
  $("#render-toggle").addEventListener("click", togglePreview);
  $("#render-btn").addEventListener("click", showPreview);
  $("#create-btn").addEventListener("click", () => toggleCreateMode());
  $("#queue-btn").addEventListener("click", toggleQueue);
  $("#queue-filter").addEventListener("click", toggleQueueFilter);
  $("#history-btn").addEventListener("click", toggleHistory);
  $("#history-scope").addEventListener("click", toggleHistoryScope);
  $("#undo-btn").addEventListener("click", undo);
  $("#redo-btn").addEventListener("click", redo);
  window.addEventListener("resize", () => applyTransform());

  wirePanZoom();
  wireKeyboard();

  loadProject().catch((err) => status(`Failed to load project: ${err.message}`));
}

document.addEventListener("DOMContentLoaded", init);
