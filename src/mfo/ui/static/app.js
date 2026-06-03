// mfo review editor — local SPA (spec §13.1-13.5).
//
// A dependency-free client over the read API from batch 6.1: it lists pages (§13.1), draws the
// page image with clickable region overlays on a zoom/pan canvas (§13.2, §13.5), and shows the
// selected region's OCR, translation, candidates, edit history and confidence in the side panel
// (§13.2). In-place editing and region ops land in batch 6.3; this batch is read + navigate.

"use strict";

const state = {
  pages: [], // project page index
  page: null, // active page_view payload
  regions: [], // active page regions, in reading order
  unitByRegion: new Map(), // region_id -> unit payload
  selected: null, // index into state.regions, or null
  zoom: 1,
  pan: { x: 0, y: 0 },
};

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

function status(text) {
  $("#status-text").textContent = text;
}

function pct(conf) {
  return conf === null || conf === undefined ? "—" : `${Math.round(conf * 100)}%`;
}

// -- project + page list (§13.1) ----------------------------------------------------------

async function loadProject() {
  const data = await getJSON("/api/project");
  const p = data.project;
  document.title = `${p.name} — mfo review`;
  $("#project-meta").textContent = `${p.name} · ${p.source_lang} → ${p.target_lang} · ${p.reading_direction}`;
  state.pages = data.pages;

  const list = $("#page-list");
  list.replaceChildren();
  for (const page of data.pages) {
    const li = el("li", { "data-page-id": page.id }, [
      el("span", { class: "page-idx", text: String(page.index + 1) }),
      el("span", { class: "muted", text: `${page.regions}r / ${page.units}u` }),
    ]);
    if (page.low_confidence > 0) {
      li.append(el("span", { class: "lc-badge", text: String(page.low_confidence) }));
    }
    li.addEventListener("click", () => selectPage(page.id));
    list.append(li);
  }

  const lowTotal = data.pages.reduce((n, pg) => n + pg.low_confidence, 0);
  status(`${data.pages.length} page(s), ${lowTotal} low-confidence region(s).`);
  if (data.pages.length) selectPage(data.pages[0].id);
}

// -- page editor (§13.2) ------------------------------------------------------------------

async function selectPage(pageId) {
  const view = await getJSON(`/api/pages/${pageId}`);
  state.page = view;
  state.selected = null;

  state.unitByRegion = new Map();
  for (const unit of view.units) {
    for (const rid of unit.ordered_region_ids) state.unitByRegion.set(rid, unit);
  }
  state.regions = view.regions; // already reading-order sorted by the API

  for (const li of $("#page-list").children) {
    li.classList.toggle("active", li.dataset.pageId === pageId);
  }

  const img = $("#page-img");
  const stage = $("#canvas-stage");
  stage.style.width = `${view.width}px`;
  stage.style.height = `${view.height}px`;
  img.style.width = `${view.width}px`;
  img.style.height = `${view.height}px`;
  img.src = `/api/pages/${pageId}/image`;
  $("#canvas-empty").hidden = true;

  drawRegions();
  fitPage();
  renderInspector();
  status(`Page ${view.index + 1}: ${view.regions.length} region(s), ${view.units.length} unit(s).`);
}

function drawRegions() {
  const overlay = $("#region-overlay");
  overlay.replaceChildren();
  state.regions.forEach((region, i) => {
    const b = region.bbox;
    const box = el("div", {
      class: `region-box${region.low_confidence ? " low" : ""}`,
      "data-idx": String(i),
    });
    box.style.left = `${b.x}px`;
    box.style.top = `${b.y}px`;
    box.style.width = `${b.width}px`;
    box.style.height = `${b.height}px`;
    box.append(el("span", { class: "region-tag", text: region.type }));
    box.addEventListener("mousedown", (e) => e.stopPropagation()); // don't start a pan
    box.addEventListener("click", () => selectRegion(i));
    overlay.append(box);
  });
}

function selectRegion(i) {
  state.selected = i;
  for (const box of $("#region-overlay").children) {
    box.classList.toggle("selected", Number(box.dataset.idx) === i);
  }
  renderInspector();
  scrollSelectedIntoView();
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
    el("dt", { text: "Status" }),
    el("dd", { text: region.status }),
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

function ocrSection(region) {
  if (!region.ocr.length) {
    return section("OCR", [el("p", { class: "muted", text: "No OCR for this region." })]);
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
  return section("OCR", blocks);
}

function translationSection(unit) {
  return section("Translation", [
    el("div", { class: "text-block", text: unit.translation || "—" }),
  ]);
}

function candidatesSection(unit) {
  if (!unit.candidates.length) return el("span");
  const cards = unit.candidates.map((c) => {
    const selected = c.id === unit.selected_candidate_id;
    return el("div", { class: `cand${selected ? " selected" : ""}` }, [
      el("div", { class: "cand-head" }, [
        el("span", { class: "chip", text: c.kind }),
        selected ? el("span", { class: "chip good", text: "selected" }) : null,
        el("span", { class: "muted", text: pct(c.confidence) }),
      ]),
      el("div", { text: c.text || "—" }),
    ]);
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

// -- input wiring -------------------------------------------------------------------------

function stepRegion(delta) {
  if (!state.regions.length) return;
  const next =
    state.selected === null
      ? delta > 0
        ? 0
        : state.regions.length - 1
      : (state.selected + delta + state.regions.length) % state.regions.length;
  selectRegion(next);
}

function stepPage(delta) {
  if (!state.pages.length || !state.page) return;
  const cur = state.pages.findIndex((p) => p.id === state.page.page_id);
  const next = Math.min(state.pages.length - 1, Math.max(0, cur + delta));
  if (next !== cur) selectPage(state.pages[next].id);
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

function wirePanZoom() {
  const vp = $("#canvas-viewport");
  let dragging = false;
  let last = { x: 0, y: 0 };

  vp.addEventListener("mousedown", (e) => {
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
    switch (e.key) {
      case "ArrowDown":
      case "j":
        e.preventDefault();
        stepRegion(1);
        break;
      case "ArrowUp":
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
  } catch {
    /* ignore */
  }

  $("#zoom-in").addEventListener("click", () => setZoom(state.zoom * 1.2));
  $("#zoom-out").addEventListener("click", () => setZoom(state.zoom / 1.2));
  $("#zoom-fit").addEventListener("click", fitPage);
  $("#theme-toggle").addEventListener("click", toggleTheme);
  window.addEventListener("resize", () => applyTransform());

  wirePanZoom();
  wireKeyboard();

  loadProject().catch((err) => status(`Failed to load project: ${err.message}`));
}

document.addEventListener("DOMContentLoaded", init);
