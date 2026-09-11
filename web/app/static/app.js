/* ============================================================
   MicroPad Config Generator — frontend logic
   Sections:
     01 helpers (api, toast, busy, confirmModal, promptModal)
     02 state
     03 settings drawer
     04 render / views
     05 page tree
     06 page editor + items
     07 HA actions (test / entities / generate / upload / download)
     08 key map editor
     09 keyboard shortcuts + wire-up
   ============================================================ */

/* ---------------- 01 helpers ---------------- */
const $ = (id) => document.getElementById(id);

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: {} };
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok && !data.ok) { throw new Error(data.error || data.message || `HTTP ${r.status}`); }
  return data;
}

function toast(msg, type = "info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("toast-wrap").appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity .3s";
    setTimeout(() => el.remove(), 300);
  }, 4000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Disable a button while an async action runs (prevents double submits).
function setBusy(btn, on, label) {
  if (!btn) return;
  if (on) {
    if (btn.dataset.label === undefined) btn.dataset.label = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset.label || "…";
  }
}

/* ---------------- 01b confirmModal / promptModal (W-3 item 6) ----------------
   The "Are you sure?" and "Name it:" flows used to call window.confirm and
   window.prompt. Those native dialogs look out of place next to our design
   and behave inconsistently across browsers (and block the main thread on
   mobile). These two helpers drive a single modal markup block; consumers
   can `await confirmModal({title, body, danger})` for a true/false result.
*/
let _confirmResolver = null;
function _closeConfirm(value) {
  const bg = $("confirm-modal-bg"), mo = $("confirm-modal");
  if (bg) bg.classList.add("hidden");
  if (mo) mo.classList.add("hidden");
  if (_confirmResolver) { _confirmResolver(value); _confirmResolver = null; }
}
function _openConfirm({ title, body, hint, danger, yesLabel, noLabel, input }) {
  return new Promise(resolve => {
    _confirmResolver = resolve;
    $("confirm-title").textContent = title || "Confirm";
    $("confirm-body").textContent = body || "";
    $("confirm-hint").textContent = hint || "";
    const wrap = $("confirm-input-wrap");
    const inp = $("confirm-input");
    const lbl = $("confirm-input-label");
    if (input) {
      lbl.textContent = input.label || "Name";
      inp.value = input.value || "";
      inp.placeholder = input.placeholder || "";
      wrap.classList.remove("hidden");
      setTimeout(() => inp.focus(), 30);
    } else {
      wrap.classList.add("hidden");
      inp.value = "";
    }
    const yes = $("btn-confirm-yes");
    const no  = $("btn-confirm-no");
    yes.textContent = yesLabel || "Confirm";
    no.textContent  = noLabel  || "Cancel";
    yes.className = "btn " + (danger ? "btn-danger" : "btn-primary");
    $("confirm-modal-bg").classList.remove("hidden");
    $("confirm-modal").classList.remove("hidden");
    no.focus();
  });
}
async function confirmModal(opts) {
  // input: undefined = pure confirm; {label,value} = prompt inside modal.
  if (opts && opts.input) {
    const v = await _openConfirm({ ...opts, input: opts.input });
    return v === true ? ($("confirm-input").value || "") : null;
  }
  return await _openConfirm(opts || {});
}
// Override window.confirm / window.prompt so any leftover call sites go
// through the new modal without further edits.
window.confirm = function (msg) {
  // Best-effort sync fallback — confirmModal is async, so we route via the
  // modal but return whatever the user clicks (cancel = false). For real
  // awaits, prefer `confirmModal({...})` directly.
  return confirmModal({ body: msg || "" });
};
window.prompt = function (msg, def) {
  // Synchronous shim: we can't truly wait synchronously here, so this just
  // opens the modal and returns the default synchronously. Real callers
  // should use `promptModal` (below) which is the awaited version.
  confirmModal({ body: msg || "", input: { label: "Value", value: def || "" } });
  return def || "";
};
async function promptModal({ title, body, label, placeholder, value, hint, danger, yesLabel, noLabel }) {
  const v = await _openConfirm({
    title, body, hint, danger, yesLabel, noLabel,
    input: { label: label || "Name", value: value || "", placeholder: placeholder || "" },
  });
  if (v === true) return $("confirm-input").value;
  return null;
}

/* ---------------- 02 state ---------------- */
const state = {
  pages: [],
  settings: {},
  itemTypes: [],
  entities: {},
  selectedPage: null,
  keymap: {},
  keymapKeys: [],
  keyActions: [],
  keymapDefaults: {},
  view: "pages",          // "pages" | "keys"
  keymapScope: "global",  // "global" | <page id> (the page whose overrides we are editing)
  keymapSel: "r0c0",      // currently selected key in the pad view
};

const icons = {
  category: "▦", light: "💡", switch: "🔌", script: "⚙", button: "🔘",
  sensor: "📊", media_player: "🎬", number: "#", settings: "⚙", back: "↩",
};

const editTypes = new Set(["number", "media_player"]);

/* ---------------- Init ---------------- */
async function init() {
  try {
    const data = await api("/api/config");
    state.pages = data.pages || [];
    state.settings = data.settings || {};
    state.entities = buildEntities(data.entities || []);
    const meta = await api("/api/meta");
    state.keymap = Object.assign({}, meta.keymap_defaults || {}, data.keymap || {});
    state.itemTypes = meta.item_types || [];
    state.keymapKeys = meta.keymap_keys || [];
    state.keyActions = meta.key_actions || [];
    state.keymapDefaults = meta.keymap_defaults || {};
    populateSettings();
    renderAll();
  } catch (e) { toast("Could not load config: " + e.message, "error"); }
}

// entities arrive from the backend as [{entity_id,name,state,domain}]
function buildEntities(arr) {
  const map = {};
  (arr || []).forEach(e => { map[e.entity_id] = e; });
  return map;
}

function entityCount() { return Object.keys(state.entities).length; }

/* ---------------- 03 settings drawer ---------------- */
function populateSettings() {
  $("s-ha-url").value = state.settings.ha_url || "";
  $("s-ha-token").value = state.settings.ha_token || "";
  $("s-mqtt").value = state.settings.mqtt_broker || "";
  $("s-ssh-host").value = state.settings.ssh_host || "";
  $("s-ssh-user").value = state.settings.ssh_user || "";
  $("s-ssh-key").value = state.settings.ssh_key || "";
  $("s-remote").value = state.settings.remote_path || "";
}
function readSettings() {
  return {
    ha_url: $("s-ha-url").value.trim(),
    ha_token: $("s-ha-token").value.trim(),
    mqtt_broker: $("s-mqtt").value.trim(),
    ssh_host: $("s-ssh-host").value.trim(),
    ssh_user: $("s-ssh-user").value.trim(),
    ssh_key: $("s-ssh-key").value.trim(),
    remote_path: $("s-remote").value.trim(),
  };
}
async function saveSettings() {
  state.settings = readSettings();
  await api("/api/config", "POST", { settings: state.settings });
  toast("Settings saved", "success");
  closeDrawer();
  updateConnDot();
}
function openDrawer() { $("drawer-overlay").classList.remove("hidden"); $("settings-drawer").classList.add("open"); }
function closeDrawer() { $("drawer-overlay").classList.add("hidden"); $("settings-drawer").classList.remove("open"); }

function updateConnDot() {
  const dot = $("conn-dot");
  if (state.settings.ha_token) { dot.className = "conn-dot ok"; $("conn-label").textContent = "HA token set"; }
  else { dot.className = "conn-dot err"; $("conn-label").textContent = "HA not connected"; }
  // mirror into the W-3 sidebar status pill (item 1)
  const sideDot = $("side-status-dot");
  if (sideDot) {
    sideDot.className = "ss-dot " + (state.settings.ha_token ? "ok" : "err");
  }
  renderSideStatus();
}

/* W-3 item 1: sidebar status pill — N pages · M entities at the bottom. */
function renderSideStatus() {
  const pagesEl = $("side-status-pages");
  const entsEl = $("side-status-entities");
  if (pagesEl) pagesEl.textContent = state.pages.length;
  if (entsEl) entsEl.textContent = entityCount();
}

/* ---------------- Persist pages ---------------- */
async function persistPages() {
  await api("/api/config", "POST", { pages: state.pages });
  renderAll();
}

/* ---------------- 04 render / views ---------------- */
function renderAll() {
  renderPageList();
  renderKeymap();
  applyView();
  updateConnDot();
}

// The main column either shows the page editor or the key map editor.
function applyView() {
  const keys = state.view === "keys";
  $("keymap-view").classList.toggle("hidden", !keys);
  if (keys) {
    $("editor").classList.add("hidden");
    $("empty-state").classList.add("hidden");
  } else {
    renderEditor();          // restores empty-state / editor visibility
  }
}

function showKeys() { state.view = "keys"; renderAll(); }
function showPages() { state.view = "pages"; renderAll(); }
function selectPage(idx) {
  state.selectedPage = idx;
  renderAll();
}

// Mobile: slide the sidebar in/out.
function openSidebar() {
  $("sidebar-overlay").classList.remove("hidden");
  document.querySelector(".sidebar").classList.add("open");
}
function closeSidebar() {
  $("sidebar-overlay").classList.add("hidden");
  document.querySelector(".sidebar").classList.remove("open");
}

/* ---------------- 05 page tree ---------------- */
// Sort pages so parents come before children; render with indentation so the
// tree structure (home → ha → lights ...) is visible and editable.
function pageChildren(parentId) {
  return state.pages.filter(p => (p.parent || "") === parentId);
}
function pageDepth(id, seen) {
  seen = seen || new Set();
  if (seen.has(id)) return 0;          // cycle guard
  seen.add(id);
  const p = state.pages.find(x => x.id === id);
  if (!p || !p.parent) return 0;
  return 1 + pageDepth(p.parent, seen);
}
function renderPageList() {
  const list = $("page-list");
  list.innerHTML = "";
  const roots = pageChildren("");
  const ordered = [];
  function walk(items) {
    items.forEach(p => {
      ordered.push(p);
      walk(pageChildren(p.id));
    });
  }
  walk(roots);
  // Fallback: pages whose parent is missing from state still show at root
  const known = new Set(state.pages.map(p => p.id));
  state.pages.forEach(p => {
    if (!known.has(p.parent || "") && !ordered.includes(p)) ordered.push(p);
  });

  ordered.forEach(p => {
    const i = state.pages.indexOf(p);
    const depth = pageDepth(p.id);
    const row = document.createElement("div");
    row.className = "page-item" + (i === state.selectedPage ? " active" : "");
    row.style.paddingLeft = (12 + depth * 16) + "px";
    row.draggable = true;
    row.dataset.pageId = p.id;
    const prefix = depth > 0 ? "└ " : "";
    row.innerHTML = `
      <div class="p-icon">${icons[p.items[0]?.type] || "▦"}</div>
      <div class="p-text"><div class="p-name">${esc(prefix + (p.title || p.id))}</div>
      <div class="p-id">${esc(p.id)}</div></div>
      <button class="p-drag" aria-label="Drag to reorder" title="Drag to reorder">⋮⋮</button>
      <button class="p-del" title="Delete page">✕</button>`;
    row.onclick = (e) => {
      if (e.target.classList.contains("p-del")) return;
      if (e.target.classList.contains("p-drag")) return;
      selectPage(i);
    };
    row.querySelector(".p-del").onclick = async (e) => {
      e.stopPropagation();
      await deletePageWithConfirm(p.id, i);
    };
    // W-3 item 5: HTML5 drag to reorder.
    row.addEventListener("dragstart", (e) => {
      _dragPageId = p.id;
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      // Firefox insists on setData for the drag to start.
      e.dataTransfer.setData("text/plain", p.id);
    });
    row.addEventListener("dragend", () => {
      _dragPageId = null;
      row.classList.remove("dragging");
      list.querySelectorAll(".page-item").forEach(el => {
        el.classList.remove("drop-before");
        el.classList.remove("drop-after");
      });
    });
    row.addEventListener("dragover", (e) => {
      if (!_dragPageId || _dragPageId === p.id) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const r = row.getBoundingClientRect();
      const before = (e.clientY - r.top) < r.height / 2;
      row.classList.toggle("drop-before", before);
      row.classList.toggle("drop-after", !before);
    });
    row.addEventListener("dragleave", () => {
      row.classList.remove("drop-before");
      row.classList.remove("drop-after");
    });
    row.addEventListener("drop", async (e) => {
      e.preventDefault();
      const movedId = _dragPageId || e.dataTransfer.getData("text/plain");
      _dragPageId = null;
      row.classList.remove("drop-before");
      row.classList.remove("drop-after");
      if (!movedId || movedId === p.id) return;
      const r = row.getBoundingClientRect();
      const before = (e.clientY - r.top) < r.height / 2;
      await reorderPages(movedId, p.id, before);
    });
    list.appendChild(row);
  });
}

// W-3 item 2 — Result modal sub-toolbar (format toggle + copy + download).
// We keep the full generate() payload in state so switches between the three
// formats don't trigger a re-fetch. `state.genResult` is reset every time
// generate() runs fresh.
function _setGenFormat(fmt) {
  const out = $("gen-output"), meta = $("gen-meta");
  if (!out || !state.genResult) return;
  let text = "", metaText = "", file = "micropad.yaml";
  // Three views (per 08-todo item 2): Raw = the byte-for-byte YAML automation
  // string; YAML = same content but pretty-printed with surrounding context
  // (alias / alias-friendly preview including the keymap note); Merged =
  // the JSON form of all four payloads (api_config + keymap + page
  // payloads) so the operator can see how the upload pieces fit together.
  if (fmt === "raw") {
    text = state.genResult.yaml_automations || "";
    metaText = `${text.length} chars · Raw YAML automation`;
    file = "micropad_automation.raw.txt";
  } else if (fmt === "yaml") {
    text = state.genResult.yaml_automations || "";
    metaText = `${text.length} chars · YAML automation`;
    file = "micropad_automation.yaml";
  } else if (fmt === "merged") {
    const obj = {
      api_config: state.genResult.api_config || "",
      home_page_payload: state.genResult.home_page_payload || "",
      keymap_payload: state.genResult.keymap_payload || "",
      all_pages_payload: state.genResult.all_pages_payload || "",
    };
    text = JSON.stringify(obj, null, 2);
    metaText = `${text.length} chars · Merged payloads (JSON)`;
    file = "micropad_merged.json";
  } else {
    text = state.genResult.yaml_automations || "";
    metaText = `${text.length} chars`;
    file = "micropad.txt";
  }
  out.textContent = text;
  if (meta) meta.textContent = metaText;
  $("btn-gen-download").dataset.file = file;
  $("btn-gen-download").dataset.text = text;
}
function _initGenToolbar() {
  if (_genToolbarWired) return;
  _genToolbarWired = true;
  $("gen-format")?.querySelectorAll("button[data-format]").forEach(b => {
    b.addEventListener("click", () => {
      $("gen-format").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      _setGenFormat(b.dataset.format);
    });
  });
  $("btn-gen-copy")?.addEventListener("click", async () => {
    const text = $("gen-output")?.textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      const btn = $("btn-gen-copy");
      const orig = btn.textContent;
      btn.textContent = "Copied ✓";
      btn.disabled = true;
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1500);
    } catch (e) {
      toast("Copy failed: " + e.message, "error");
    }
  });
  $("btn-gen-download")?.addEventListener("click", () => {
    const btn = $("btn-gen-download");
    const text = btn.dataset.text || "";
    const file = btn.dataset.file || "micropad.txt";
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = file;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 100);
  });
}
let _genToolbarWired = false;

// W-3 item 5 — drag state lives outside renderPageList so handlers in the
// closure can read it without rebuilding the DOM on every drag event.
let _dragPageId = null;

// Move `movedId` so that, in the rendered tree order, it ends up immediately
// before (before=true) or after (before=false) `targetId`. Because pages are
// stored as a flat array but rendered in depth-first tree order, we swap
// positions in the flat array to match the visual placement.
async function reorderPages(movedId, targetId, before) {
  const fromIdx = state.pages.findIndex(p => p.id === movedId);
  const toIdx = state.pages.findIndex(p => p.id === targetId);
  if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return;
  const [moved] = state.pages.splice(fromIdx, 1);
  // recompute target index now that the array shrank
  let insertAt = state.pages.findIndex(p => p.id === targetId);
  if (insertAt < 0) { state.pages.push(moved); return; }
  if (!before) insertAt += 1;
  state.pages.splice(insertAt, 0, moved);
  if (state.selectedPage !== null) {
    const sel = state.pages[state.selectedPage];
    if (!sel) state.selectedPage = null;
  }
  await persistPages();
}

async function deletePageWithConfirm(pageId, idx) {
  const ok = await confirmModal({
    title: "Delete page",
    body: `Delete page "${pageId}"? This removes the page and all its items from your config. Items on the pad referencing this page will lose that link.`,
    yesLabel: "Delete",
    danger: true,
    hint: "Cannot be undone.",
  });
  if (!ok) return;
  state.pages.splice(idx, 1);
  if (state.selectedPage >= state.pages.length) state.selectedPage = state.pages.length - 1;
  await persistPages();
}

/* ---------------- 06 page editor + items ---------------- */
function renderEditor() {
  const hasPages = state.pages.length > 0;
  $("empty-state").classList.toggle("hidden", hasPages);
  $("editor").classList.toggle("hidden", !hasPages || state.selectedPage === null);
  if (!hasPages || state.selectedPage === null) return;
  const p = state.pages[state.selectedPage];
  $("page-title-placeholder").textContent = p.title || p.id || "Page";
  $("page-id-hint").textContent = "ID: " + p.id + (p.parent ? "  ·  Parent: " + p.parent : "");
  $("f-id").value = p.id || "";
  $("f-title").value = p.title || "";
  // parent select
  const ps = $("f-parent");
  ps.innerHTML = '<option value="">(none)</option>';
  state.pages.forEach((pp, i) => { if (i !== state.selectedPage) { ps.insertAdjacentHTML("beforeend", `<option value="${esc(pp.id)}">${esc(pp.id)}</option>`); } });
  ps.value = p.parent || "";
  // page key map summary
  const pks = $("page-key-summary");
  if (pks) {
    const ov = pageKeymapOverrides(p);
    const n = Object.keys(ov).length;
    const parentTitle = p.parent ? (state.pages.find(x => x.id === p.parent)?.title || p.parent) : "global";
    pks.textContent = n === 0
      ? `Inherits everything from "${esc(parentTitle)}" — no own layout.`
      : `${n} key(s) overridden · rest inherited from "${esc(parentTitle)}".`;
  }
  // items
  $("item-count").textContent = p.items.length;
  $("item-empty").classList.toggle("hidden", p.items.length > 0);
  const il = $("item-list");
  il.innerHTML = "";
  p.items.forEach((it, j) => {
    const row = document.createElement("div");
    row.className = "item-row";
    row.innerHTML = `
      <div class="item-arrows">
        <button class="move-btn" data-m="-1" ${j === 0 ? "disabled" : ""} aria-label="Move up">▲</button>
        <button class="move-btn" data-m="1" ${j === p.items.length - 1 ? "disabled" : ""} aria-label="Move down">▼</button>
      </div>
      <div class="item-main">
        <div class="item-ico">${icons[it.type] || "•"}</div>
        <div><div class="item-name">${esc(it.name || "Unnamed")}</div>
        <div class="item-sub">${esc(it.entity || (it.target_page ? "→ " + it.target_page : ""))}</div></div>
      </div>
      <span class="item-type">${esc(it.type)}</span>
      <button class="item-edit">Edit</button>
      <button class="item-del" aria-label="Delete item">🗑</button>`;
    row.querySelectorAll(".move-btn").forEach(b => b.onclick = () => moveItem(j, +b.dataset.m));
    row.querySelector(".item-main").onclick = () => openItemModal(j);
    row.querySelector(".item-edit").onclick = () => openItemModal(j);
    row.querySelector(".item-del").onclick = async (e) => {
      e.stopPropagation();
      const ok = await confirmModal({
        title: "Delete item",
        body: `Delete item "${it.name || "Unnamed"}"?`,
        yesLabel: "Delete",
        danger: true,
        hint: "Cannot be undone.",
      });
      if (!ok) return;
      p.items.splice(j, 1);
      await persistPages();
    };
    il.appendChild(row);
  });
}

/* ---------------- Page actions ---------------- */
// Built-in page templates (mirror of web/app/pages/templates.py).
// KEPT IN SYNC - if you add a template in Python, add an entry here too.
// The Python side is the source of truth for the page shape; this list is
// just what the sidebar popover renders.
const TEMPLATES = {
  empty:    { label: "Empty page", description: "Blank page - just an ID and title.",
              build: () => ({ id: "new_page", title: "New page", parent: "", items: [] }) },
  lights:   { label: "Lights",     description: "Toggle + dim controls for one room. Adjust entities after creating.",
              build: () => ({
                id: "lights", title: "Lights", parent: "",
                items: [
                  { name: "Toggle all", type: "light", editable: false,
                    entity: "light.living_room", action: "toggle" },
                  { name: "Brightness", type: "number", editable: true,
                    entity: "light.living_room", min: 0, max: 100, step: 5,
                    action: "set_value" },
                  { name: "Kitchen",    type: "switch",
                    entity: "switch.kitchen_lights", action: "toggle" },
                  { name: "Night mode", type: "scene",
                    entity: "scene.movie_night", action: "press" },
                ] }) },
  spotify:  { label: "Spotify",     description: "Play / pause, next, previous, volume, shuffle.",
              build: () => ({
                id: "spotify", title: "Spotify", parent: "home",
                items: [
                  { name: "Play / Pause", type: "media_player",
                    entity: "media_player.spotify_bwschti", action: "toggle" },
                  { name: "Next",         type: "media_player",
                    entity: "media_player.spotify_bwschti", action: "media_next" },
                  { name: "Previous",     type: "media_player",
                    entity: "media_player.spotify_bwschti", action: "media_prev" },
                  { name: "Volume Up",    type: "media_player",
                    entity: "media_player.spotify_bwschti", editable: true,
                    min: 0, max: 100, step: 5, action: "volume_up" },
                  { name: "Volume Down",  type: "media_player",
                    entity: "media_player.spotify_bwschti", editable: true,
                    min: 0, max: 100, step: 5, action: "volume_down" },
                  { name: "Shuffle",      type: "media_player",
                    entity: "media_player.spotify_bwschti", action: "toggle",
                    metadata: { hint: "Toggles shuffle on this media_player." } },
                  { name: "Back",         type: "back" },
                ] }) },
  discord:  { label: "Discord",     description: "Mute mic, start/stop Discord, toggle deafen.",
              build: () => ({
                id: "discord", title: "Discord", parent: "home",
                items: [
                  { name: "Mute / Unmute Mic", type: "button",
                    entity: "button.pcmitaids_discord_mute", action: "press" },
                  { name: "Start Discord",     type: "button",
                    entity: "button.pcmitaids_discord_start", action: "press" },
                  { name: "Stop Discord",      type: "button",
                    entity: "button.pcmitaids_discord_stop", action: "press" },
                  { name: "Toggle Deafen",     type: "button",
                    entity: "button.pcmitaids_discord_deafen", action: "press",
                    metadata: { hint: "Need HASS.Agent 'deafen' button - add via HASS.Agent settings." } },
                  { name: "Back",              type: "back" },
                ] }) },
};

// Slug any free-text id the user types into a keymap-safe snake_case id.
function slugId(s) {
  const clean = (s || "").trim().toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
  return clean || "page";
}
function uniqueId(base) {
  let id = base;
  let n = 2;
  while (state.pages.some(p => p.id === id)) { id = base + "_" + n; n++; }
  return id;
}

async function addPage() {
  // Open the template popover. The actual id/title prompt keeps running
  // for the "Empty page" choice so the existing UX is preserved.
  openNewPagePopover();
}

function openNewPagePopover() {
  const pop = $("new-page-popover");
  const ovl = $("popover-overlay");
  if (!pop || !ovl) return;
  // Anchor the popover under whichever "+ New page" button the user clicked.
  // We pick the active element if it's one of the trigger buttons; otherwise
  // fall back to the sidebar button so direct programmatic opens still
  // get a sensible anchor.
  const triggers = ["btn-add-page", "btn-add-page-hero"];
  let trigger = triggers.map(id => $(id)).find(b => b && b === document.activeElement);
  if (!trigger) trigger = $("btn-add-page") || $("btn-add-page-hero");
  if (trigger) {
    const r = trigger.getBoundingClientRect();
    const popWidth = 280;            // mirrors CSS width
    const popGap = 8;
    // Prefer "below the button", but flip above when near the bottom edge.
    let top = r.bottom + popGap;
    if (top + 220 > window.innerHeight - 8) top = Math.max(8, r.top - popGap - 220);
    // Clamp horizontally inside the viewport.
    let left = r.left;
    if (left + popWidth > window.innerWidth - 8) left = window.innerWidth - popWidth - 8;
    if (left < 8) left = 8;
    pop.style.top = top + "px";
    pop.style.left = left + "px";
  } else {
    pop.style.top = "";
    pop.style.left = "";
  }
  pop.classList.remove("hidden");
  ovl.classList.remove("hidden");
  // Focus first card so keyboard users can Tab/Enter immediately.
  const firstCard = pop.querySelector(".tp-card");
  if (firstCard) firstCard.focus();
}
function closeNewPagePopover() {
  const pop = $("new-page-popover");
  const ovl = $("popover-overlay");
  if (!pop || !ovl) return;
  pop.classList.add("hidden");
  ovl.classList.add("hidden");
  // Clear inline coords so the next open re-anchors freshly.
  pop.style.top = "";
  pop.style.left = "";
}

async function createPageFromTemplate(key) {
  const tpl = TEMPLATES[key];
  if (!tpl) { toast("Unknown template: " + key, "error"); return; }

  let page;
  if (key === "empty") {
    // W-3 item 6: replace the old window.prompt with our themed modal.
    const id = await promptModal({
      title: "New page",
      body: "Choose a short page ID (e.g. home, lamps, discord).",
      label: "Page ID",
      placeholder: "home",
      yesLabel: "Create",
      hint: "Lowercase only — non-alphanumeric characters become underscores.",
    });
    if (!id) return;
    let clean = slugId(id);
    if (!clean) { toast("That ID is empty after cleanup.", "error"); return; }
    clean = uniqueId(clean);
    page = { id: clean, title: clean.charAt(0).toUpperCase() + clean.slice(1),
             parent: "", items: [] };
  } else {
    // Run the template's `build()` to get a fresh dict (deep copy each time -
    // each `build` returns a brand-new object, but we whitelist keys to be
    // sure nothing extra sneaks into state.pages).
    const tplPage = tpl.build();
    page = {
      id: uniqueId(slugId(tplPage.id || key)),
      title: tplPage.title || key.charAt(0).toUpperCase() + key.slice(1),
      parent: tplPage.parent || "",
      items: Array.isArray(tplPage.items) ? tplPage.items.map(it => ({
        name: it.name || "Unnamed",
        type: it.type || "category",
        entity: it.entity || "",
        target_page: it.target_page || "",
        unit: it.unit || "",
        editable: !!it.editable,
        min: typeof it.min === "number" ? it.min : 0,
        max: typeof it.max === "number" ? it.max : 100,
        step: typeof it.step === "number" ? it.step : 1,
        action: it.action || null,
        metadata: it.metadata || null,
      })) : [],
    };
    // If the template says parent=home but no home page exists yet, drop
    // the parent so the page doesn't validate-fail.
    if (page.parent === "home" && !state.pages.some(p => p.id === "home")) {
      page.parent = "";
    }
  }

  state.pages.push(page);
  state.selectedPage = state.pages.length - 1;
  closeNewPagePopover();
  await persistPages();
  toast(`Created "${page.title}" from ${tpl.label.toLowerCase()} template`, "success");
}

// Empty-state template cards (W-3 item 4). Wire the three hero cards to the
// TEMPLATES registry above, so the empty-state hero uses the same single
// source of truth as the sidebar popover.
function wireEmptyTemplates() {
  const root = $("empty-templates");
  if (!root) return;
  root.querySelectorAll(".tpl-card").forEach(card => {
    card.addEventListener("click", () => {
      const tpl = card.dataset.template;
      if (!tpl || !TEMPLATES[tpl]) return;
      createPageFromTemplate(tpl);
    });
  });
}

async function savePageMeta() {
  if (state.selectedPage === null) return;
  const p = state.pages[state.selectedPage];
  const newId = $("f-id").value.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_");
  if (newId && newId !== p.id && state.pages.some(x => x.id === newId)) { toast("Page ID already exists", "error"); return; }
  if (newId) p.id = newId;
  p.title = $("f-title").value.trim() || p.id;
  p.parent = $("f-parent").value;
  await persistPages();
}

/* ---------------- Item actions ---------------- */
function moveItem(i, dir) {
  const p = state.pages[state.selectedPage];
  const j = i + dir;
  if (j < 0 || j >= p.items.length) return;
  [p.items[i], p.items[j]] = [p.items[j], p.items[i]];
  persistPages();
}

function openItemModal(itemIdx) {
  const p = state.pages[state.selectedPage];
  const it = itemIdx === null ? { name: "", type: "category", entity: "", target_page: "", unit: "", editable: false, min: 0, max: 100, step: 5 } : { ...p.items[itemIdx] };
  const bg = $("item-modal-bg"), mo = $("item-modal");
  $("item-modal-title").textContent = itemIdx === null ? "New item" : "Edit item";
  const form = $("item-form");
  form.innerHTML = `
    <div class="field"><label for="m-name">Name</label><input id="m-name" type="text" value="${esc(it.name)}" autocomplete="off"></div>
    <div class="modal-2col">
      <div class="field"><label for="m-type">Type</label><select id="m-type"></select></div>
      <div class="field"><label for="m-target">Target page (category)</label><select id="m-target"></select></div>
    </div>
    <div class="field"><label for="m-entity">Entity ID</label>
      <div id="m-entity-combo" class="combo">
        <input id="m-entity" type="text" value="${esc(it.entity)}" placeholder="Search…" autocomplete="off">
        <button id="m-entity-clear" type="button" class="combo-clear ${it.entity ? "" : "hidden"}" aria-label="Clear">✕</button>
        <div id="m-entity-list" class="combo-list hidden"></div>
      </div>
      <div id="m-entity-status" class="field-hint"></div></div>
    <div class="field"><label for="m-unit">Unit (sensors)</label><input id="m-unit" type="text" value="${esc(it.unit)}" autocomplete="off"></div>
    <div class="field" style="flex-direction:row;align-items:center;gap:8px">
      <input id="m-editable" type="checkbox" ${it.editable ? "checked" : ""}><label for="m-editable" style="margin:0">Editable (slider/value)</label>
    </div>
    <div class="modal-2col">
      <div class="field"><label for="m-min">Min</label><input id="m-min" type="number" value="${it.min ?? 0}"></div>
      <div class="field"><label for="m-max">Max</label><input id="m-max" type="number" value="${it.max ?? 100}"></div>
      <div class="field"><label for="m-step">Step</label><input id="m-step" type="number" value="${it.step ?? 5}"></div>
    </div>
    <div id="m-warn" class="warn-text hidden"></div>`;
  // type options
  const tsel = form.querySelector("#m-type");
  state.itemTypes.forEach(t => { tsel.insertAdjacentHTML("beforeend", `<option value="${t}" ${t === it.type ? "selected" : ""}>${t}</option>`); });
  // target options
  const tarsel = form.querySelector("#m-target");
  tarsel.insertAdjacentHTML("beforeend", `<option value="">(none)</option>`);
  state.pages.forEach(pp => { if (pp.id !== (p.id)) tarsel.insertAdjacentHTML("beforeend", `<option value="${esc(pp.id)}" ${pp.id === it.target_page ? "selected" : ""}>${esc(pp.id)}</option>`); });
  // entity combo
  const entInput = form.querySelector("#m-entity");
  const entList = form.querySelector("#m-entity-list");
  const entClear = form.querySelector("#m-entity-clear");
  const entStatus = form.querySelector("#m-entity-status");
  const entCombo = form.querySelector("#m-entity-combo");

  const entityArray = state.entities ? Object.values(state.entities) : [];
  if (entityArray.length) {
    entStatus.className = "field-hint";
    entStatus.textContent = entityCount() + " entities loaded";
  } else {
    entStatus.className = "field-hint";
    entStatus.innerHTML = "No entities loaded — ";
    const btn = document.createElement("a");
    btn.href = "#"; btn.textContent = "Load entities";
    btn.style.color = "var(--primary)"; btn.style.fontWeight = "600";
    btn.onclick = async (e) => {
      e.preventDefault(); btn.textContent = "Loading…";
      try {
        state.settings = readSettings();
        await api("/api/config", "POST", { settings: state.settings });
        const r = await api("/api/ha/entities", "POST");
        state.entities = buildEntities(r.entities);
        entStatus.textContent = r.entities.length + " entities loaded";
        $("conn-dot").className = "conn-dot ok"; $("conn-label").textContent = `HA: ${r.entities.length} entities`;
        renderEntityList(""); entList.classList.remove("hidden");
      } catch (err) { entStatus.textContent = "Error: " + err.message; }
    };
    entStatus.appendChild(btn);
  }

  function renderEntityList(query) {
    const q = (query || "").trim().toLowerCase();
    let items = state.entities ? Object.values(state.entities) : [];
    if (q) items = items.filter(e =>
      e.entity_id.toLowerCase().includes(q) ||
      (e.name || "").toLowerCase().includes(q) ||
      e.domain.toLowerCase().includes(q));
    items = items.slice(0, 80);

    if (!items.length) {
      entList.classList.remove("hidden");
      entList.innerHTML = `<div class="combo-empty">No entities found</div>`;
      return;
    }
    entList.innerHTML = "";
    items.forEach(e => {
      const row = document.createElement("div");
      row.className = "combo-item" + (e.entity_id === entInput.value ? " active" : "");
      row.innerHTML = `<div class="combo-name">${esc(e.name || "(no name)")}</div><div class="combo-sub">${esc(e.entity_id)}</div>`;
      row.onclick = () => { selectEntity(e); closeEntityList(); };
      entList.appendChild(row);
    });
  }

  function selectEntity(e) {
    entInput.value = e.entity_id;
    entClear.classList.remove("hidden");
    if (itemIdx === null && !$("m-name").value.trim()) {
      $("m-name").value = e.name || suggestName(e.entity_id);
    }
    if ($("m-type").value === "category") $("m-type").value = suggestType(e.entity_id);
  }
  function suggestType(id) { return (id.split(".")[0] || "sensor"); }
  function suggestName(id) {
    const parts = id.split(".");
    if (parts.length < 2) return id;
    return parts[1].replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  function openEntityList() {
    if (!entityCount()) return; // status link already offers "Load entities"
    renderEntityList(entInput.value);
    entList.classList.remove("hidden");
  }
  function closeEntityList() { entList.classList.add("hidden"); }

  entInput.addEventListener("input", () => {
    renderEntityList(entInput.value);
    entList.classList.remove("hidden");
  });
  entInput.addEventListener("focus", openEntityList);
  entInput.addEventListener("click", openEntityList);
  entInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeEntityList();
  });
  entClear.addEventListener("click", () => {
    entInput.value = "";
    entClear.classList.add("hidden");
    entList.classList.add("hidden");
    entInput.focus();
  });
  entInput.addEventListener("change", () => {
    if (entInput.value) entClear.classList.remove("hidden");
  });
  // close list when clicking outside the combo
  document.addEventListener("mousedown", function comboClose(e) {
    if (entCombo && !entCombo.contains(e.target)) {
      if (entList && !entList.classList.contains("hidden")) closeEntityList();
    }
  });

  // initial: show a populated dropdown when opening with an existing entity
  if (entInput.value) {
    const cur = (state.entities || {})[entInput.value];
    if (cur) {
      entStatus.textContent = `${esc(cur.name || entInput.value)}` + ` (${entityCount()} loaded)`;
    }
  }
  // warning on editable for unsupported types
  const editable = form.querySelector("#m-editable"), warn = form.querySelector("#m-warn");
  function updWarn() {
    const ty = tsel.value;
    if (editable.checked && !editTypes.has(ty)) {
      warn.classList.remove("hidden");
      warn.textContent = `Type "${ty}" has no edit mode on the pad (treated as toggle/press). Use "number" or "media_player" for a working slider.`;
    } else warn.classList.add("hidden");
  }
  tsel.onchange = updWarn; editable.onchange = updWarn; updWarn();

  state._itemIdx = itemIdx;
  bg.classList.remove("hidden"); mo.classList.remove("hidden");
}

function closeItemModal() { $("item-modal-bg").classList.add("hidden"); $("item-modal").classList.add("hidden"); }

function saveItemFromModal() {
  const p = state.pages[state.selectedPage];
  const item = {
    name: $("m-name").value.trim() || "Unnamed",
    type: $("m-type").value,
    entity: $("m-entity").value.trim(),
    target_page: $("m-target").value,
    unit: $("m-unit").value.trim(),
    editable: $("m-editable").checked,
    min: parseFloat($("m-min").value) || 0,
    max: parseFloat($("m-max").value) || 100,
    step: parseFloat($("m-step").value) || 1,
  };
  if (state._itemIdx === null) p.items.push(item);
  else p.items[state._itemIdx] = item;
  closeItemModal();
  persistPages();
}

/* ---------------- 07 HA actions ---------------- */
async function testConn(ev) {
  const btn = ev && ev.currentTarget;
  try {
    setBusy(btn, true, "Testing…");
    state.settings = readSettings();
    await api("/api/config", "POST", { settings: state.settings });
    const r = await api("/api/ha/test", "POST");
    toast(r.message || "Connection OK", "success");
    updateConnDot();
  } catch (e) { toast(e.message, "error"); }
  finally { setBusy(btn, false); }
}

async function fetchEntities(ev) {
  const btn = ev && ev.currentTarget;
  // W-3 item 8: if the entity combo in the item modal is open at the moment
  // we hit the blocking backend request, swap its content for a 3-bar
  // shimmer so the user knows the action did register (HA may take 2-4s).
  const entList = document.querySelector("#m-entity-list");
  const entStatus = document.querySelector("#m-entity-status");
  const showSkeleton = !!entList && !entList.classList.contains("hidden");
  if (showSkeleton) {
    entList.innerHTML = `<div class="skeleton" aria-live="polite" aria-label="Loading entities">
      <span class="skeleton-bar w-90"></span>
      <span class="skeleton-bar w-70"></span>
      <span class="skeleton-bar w-55"></span>
    </div>`;
    if (entStatus) entStatus.textContent = "Loading entities…";
  }
  try {
    setBusy(btn, true, "Loading…");
    state.settings = readSettings();
    await api("/api/config", "POST", { settings: state.settings });
    const r = await api("/api/ha/entities", "POST");
    state.entities = buildEntities(r.entities);
    toast(`${r.entities.length} entities loaded`, "success");
    $("conn-dot").className = "conn-dot ok"; $("conn-label").textContent = `HA: ${r.entities.length} entities`;
    // If skeleton was shown and list still visible, restore the list.
    if (showSkeleton && !entList.classList.contains("hidden")) {
      // The list render lives in openItemModal's closure via the input event;
      // the simplest, dependency-free way to restore content is to fire an
      // input event on the entity input which triggers re-render.
      const entInput = document.querySelector("#m-entity");
      if (entInput) entInput.dispatchEvent(new Event("input"));
    }
  } catch (e) { toast(e.message, "error"); }
  finally { setBusy(btn, false); }
}

async function generate() {
  if (!state.pages.length) { toast("No pages defined", "error"); return; }
  try {
    setBusy($("btn-generate"), true, "Generating…");
    const r = await api("/api/generate", "POST", { pages: state.pages, keymap: currentKeymap() });
    if (!r.ok) { toast((r.errors || []).join("\n"), "error"); return; }
    state.genResult = r;
    _initGenToolbar();
    // Reset format toggle to YAML on every fresh generate.
    $("gen-format")?.querySelectorAll("button[data-format]").forEach(b => {
      b.classList.toggle("on", b.dataset.format === "yaml");
    });
    _setGenFormat("yaml");
    $("gen-overlay").classList.remove("hidden");
  } catch (e) { toast(e.message, "error"); }
  finally { setBusy($("btn-generate"), false); }
}

async function upload() {
  if (!state.pages.length) { toast("No pages defined", "error"); return; }
  const ok = await confirmModal({
    title: "Upload to Home Assistant",
    body: "Upload config to Home Assistant and reload it automatically?",
    yesLabel: "Upload",
    hint: "Existing automations.yaml entries named micropad_controller will be replaced.",
  });
  if (!ok) return;
  try {
    setBusy($("btn-upload"), true, "Uploading…");
    const r = await api("/api/upload/api", "POST", { pages: state.pages, keymap: currentKeymap() });
    toast(r.message || "Uploaded", "success");
  } catch (e) { toast(e.message, "error"); }
  finally { setBusy($("btn-upload"), false); }
}

async function download() {
  const ok = await confirmModal({
    title: "Load from Home Assistant",
    body: "Load current MicroPad automation from HA (replaces local pages)?",
    yesLabel: "Load",
    danger: true,
    hint: "Any local pages you haven't uploaded yet will be lost.",
  });
  if (!ok) return;
  try {
    setBusy($("btn-download"), true, "Loading…");
    const r = await api("/api/download/api", "POST");
    state.pages = r.pages; state.selectedPage = 0;
    await api("/api/config", "POST", { pages: state.pages });
    toast(`${r.pages.length} pages loaded from HA`, "success");
    renderAll();
  } catch (e) { toast(e.message, "error"); }
  finally { setBusy($("btn-download"), false); }
}

async function loadCurrentPage() {
  try {
    const r = await api("/api/load-current-page", "POST");
    const pid = r.page_id || r.target_page;
    const idx = state.pages.findIndex(p => p.id === pid);
    if (idx >= 0) { selectPage(idx); toast(`Current page: ${pid}`, "info"); }
    else toast(`Pad shows: ${pid || "unknown"} (action: ${r.state || "?"})`, "info");
  } catch (e) {
    if (e.hint) toast(e.message + " " + e.hint, "info");
    else toast(e.message, "error");
  }
}

/* ---------------- 08 key map editor ---------------- */
// Which actions need an extra entity field (mirrors core.KEY_ACTIONS_NEED_ENTITY).
const keyNeedsEntity = new Set(["toggle", "on", "off", "press",
  "volume_up", "volume_down", "media_next", "media_prev"]);
const keyNeedsPage = new Set(["navigate"]);

const keyActionLabels = {
  none: "No action", enter: "Activate", back: "Back", home: "Home",
  settings: "Wi-Fi setup", scroll: "Scroll", scroll_up: "Up selection",
  scroll_down: "Down selection", navigate: "Open page", toggle: "Toggle",
  on: "Turn on", off: "Turn off", press: "Script/Button/Scene",
  volume_up: "Volume up", volume_down: "Volume down",
  media_next: "Next track", media_prev: "Previous track",
};
function actionLabel(a) { return keyActionLabels[a] || a; }

// Short text printed on the keycap itself.
const keyActionShort = {
  none: "", enter: "Activate", back: "Back", home: "Home", settings: "Wi-Fi",
  scroll: "Scroll", scroll_up: "↑ Up", scroll_down: "↓ Down",
  navigate: "Page", toggle: "Toggle", on: "On", off: "Off",
  press: "Run",
  volume_up: "Vol+", volume_down: "Vol-",
  media_next: "Next", media_prev: "Prev",
};

const keyActionGroups = [
  { title: "Navigation", actions: ["enter", "back", "home", "navigate"] },
  { title: "Scrolling", actions: ["scroll", "scroll_up", "scroll_down"] },
  { title: "Device", actions: ["toggle", "on", "off", "press"] },
  { title: "Media", actions: ["volume_up", "volume_down", "media_next", "media_prev"] },
  { title: "System", actions: ["settings", "none"] },
];

const MATRIX_IDS = ["r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2", "r1c3", "r2c0", "r2c1", "r2c2", "r2c3"];

function keyInfo(id) {
  return (state.keymapKeys || []).find(k => k.id === id) || { id: id, label: id };
}

function shortEntity(e) {
  if (!e) return "";
  const ent = (state.entities || {})[e];
  const name = ent ? (ent.name || e) : e;
  return name.length > 16 ? name.slice(0, 15) + "…" : name;
}

// ---------- Per-page key map (tree inheritance) ----------
// The effective map of a page = global map, overridden by every page on the
// parent chain (root first). Mirrors core.effective_keymap_for_page().
function pageKeymapOverrides(page) {
  return (page && typeof page.keymap === "object" && page.keymap) ? page.keymap : {};
}
function parentChain(pageId, seen) {
  seen = seen || new Set();
  if (seen.has(pageId)) return [];
  seen.add(pageId);
  const p = state.pages.find(x => x.id === pageId);
  if (!p) return [];
  return [p, ...parentChain(p.parent, seen)];
}
function effectiveKeymapForPage(page) {
  const chain = parentChain(page.id).reverse();   // root → … → page
  // start from the global map, then apply page overrides root-first
  let map = {};
  Object.keys(state.keymapDefaults || {}).forEach(k => { map[k] = Object.assign({ action: "none" }, state.keymapDefaults[k]); });
  Object.keys(state.keymap || {}).forEach(k => { map[k] = Object.assign({ action: "none" }, state.keymap[k]); });
  chain.forEach(p => {
    const ov = pageKeymapOverrides(p);
    Object.keys(ov).forEach(k => {
      const entry = ov[k];
      map[k] = (entry && typeof entry === "object")
        ? Object.assign({ action: "none" }, entry) : { action: "none" };
    });
  });
  // always complete: every key present (mirrors normalize_keymap defaults)
  (state.keymapKeys || []).forEach(k => { if (!map[k.id]) map[k.id] = { action: "none" }; });
  return map;
}

// Which map the editor currently edits: the global one, or the effective map
// of the selected page (changes become overrides on that page).
function activeKeymap() {
  if (isPageScope() && state.selectedPage !== null) {
    const p = state.pages[state.selectedPage];
    if (p) return effectiveKeymapForPage(p);
  }
  return state.keymap || {};
}
// True when the editor is currently editing a single page's overrides.
function isPageScope() {
  if (state.keymapScope === "global") return false;
  if (state.selectedPage === null) return false;
  const p = state.pages[state.selectedPage];
  return !!p && state.keymapScope === p.id;
}
function keymapScopeTitle() {
  if (isPageScope()) {
    const p = state.pages[state.selectedPage];
    return p ? (p.title || p.id) : "Page";
  }
  return "global";
}
// Reset an override back to "inherited": remove the key from the page map.
function clearPageKeyOverride(id) {
  const p = state.pages[state.selectedPage];
  if (!p || !p.keymap) return;
  delete p.keymap[id];
  if (Object.keys(p.keymap).length === 0) delete p.keymap;
  persistPages();
  renderKeymap();
}

// What a key currently does, as one readable line ("" = nothing bound).
function bindingSummary(id) {
  const map = activeKeymap();
  const b = map[id] || { action: "none" };
  const act = b.action || "none";
  if (act === "none") return "";
  if (keyNeedsEntity.has(act)) return keyActionShort[act] + (b.entity ? ": " + shortEntity(b.entity) : "");
  if (keyNeedsPage.has(act)) return keyActionShort[act] + (b.target_page ? ": " + b.target_page : "");
  return keyActionShort[act] || act;
}

function pageOptions(sel) {
  const out = ['<option value="">(none)</option>'];
  state.pages.forEach(p => {
    out.push(`<option value="${esc(p.id)}"${p.id === sel ? " selected" : ""}>${esc(p.title || p.id)}</option>`);
  });
  return out.join("");
}

function renderKeymap() {
  const grid = $("pad-grid");
  if (!grid) return;
  if (!state.keymapSel) state.keymapSel = "r0c0";
  const sel = state.keymapSel;
  const inPageScope = isPageScope();
  // Scope UI
  const scopeBtn = $("btn-keymap-scope");
  const scopeHint = $("keymap-scope-hint");
  if (scopeBtn) {
    scopeBtn.textContent = inPageScope
      ? "Switch to: global"
      : "Edit a single page…";
  }
  if (scopeHint) {
    scopeHint.textContent = inPageScope
      ? `Key layout for page "${esc(state.pages[state.selectedPage]?.title || state.pages[state.selectedPage]?.id || "")}". Changes are saved as overrides on this page; keys you don't touch here continue to use the global binding.`
      : `Global key layout — the default binding for every key on every page. Edit values freely for any key, or use "Edit a single page…" to override keys on one specific page.`;
  }

  grid.innerHTML = MATRIX_IDS.map(id => {
    const info = keyInfo(id);
    const sum = bindingSummary(id);
    const bound = sum !== "";
    const slot = esc(String(info.label || id).split(" (")[0]);
    return `<button class="pad-key${bound ? " bound" : ""}${id === sel ? " sel" : ""}" data-key="${esc(id)}"
              title="${esc(info.label || id)}${bound ? " — " + esc(sum) : ""}">
        <span class="pk-slot">${slot}</span>
        <span class="pk-action">${bound ? esc(sum) : "—"}</span>
      </button>`;
  }).join("");

  // Encoder knob + both directions, same highlight rules.
  document.querySelectorAll("#pad-grid .pad-key, .enc-dir, .enc-knob").forEach(el => {
    const id = el.dataset.key;
    if (!id) return;
    const bound = bindingSummary(id) !== "";
    if (el.classList.contains("enc-dir") || el.classList.contains("enc-knob")) {
      el.classList.toggle("bound", bound);
      el.classList.toggle("sel", id === sel);
    }
    el.onclick = () => { state.keymapSel = id; renderKeymap(); };
  });

  renderKeyDetail();

  // Entity suggestions for the key bindings (same list the item editor uses).
  const dl = $("keymap-entities");
  if (dl) {
    dl.innerHTML = Object.values(state.entities || {}).slice(0, 600).map(e =>
      `<option value="${esc(e.entity_id)}">${esc(e.name || "")}</option>`).join("");
  }
}

// Right-hand panel: shows every available action for the selected key.
function renderKeyDetail() {
  const el = $("key-detail");
  if (!el) return;
  const id = state.keymapSel || "r0c0";
  const info = keyInfo(id);
  const map = activeKeymap();
  const b = map[id] || { action: "none" };
  const act = b.action || "none";
  const inPageScope = isPageScope();
  const hasOverride = inPageScope && !!(state.pages[state.selectedPage]?.keymap || {})[id];

  // Status chip: when editing a single page, every key falls into exactly one
  // of two cases — the page has its own override (chip: "differs from
  // global", override class), or it has no override (chip: "inherits from
  // global", inherits class). In global scope there is no inheritance so the
  // chip is hidden — every key here IS the global binding.
  let statusChip = "";
  if (inPageScope) {
    statusChip = hasOverride
      ? `<span class="keychip-status override" title="This page defines its own binding for this key.">⚠ differs from global</span>`
      : `<span class="keychip-status inherits" title="No override on this page — the global binding is used.">inherits from global</span>`;
  }
  const extra = [b.entity, b.target_page].filter(Boolean).map(esc).join(" · ");
  el.innerHTML = `
    <div class="kd-head">
      <div class="kd-title">${esc(info.label || id)}</div>
      <div class="kd-sub">${inPageScope ? `Page: ${esc(keymapScopeTitle())}` : "Global"} · Bound to: ${esc(actionLabel(act))}${extra ? " · " + extra : ""}</div>
      ${statusChip ? `<div class="kd-status-row">${statusChip}</div>` : ""}
    </div>
    ${hasOverride ? `<button class="btn btn-ghost btn-sm kd-inherit">↺ Reset to global map's binding</button>` : ""}
    ${keyActionGroups.map(g => `
      <div class="kd-group">
        <div class="kd-group-title">${g.title}</div>
        <div class="kd-chips">
          ${g.actions.map(a => `<button class="chip${a === act ? " on" : ""}" data-action="${a}">${esc(actionLabel(a))}</button>`).join("")}
        </div>
      </div>`).join("")}
    ${keyNeedsEntity.has(act) ? `
      <div class="kd-field">
        <label>Entity</label>
        <input id="kd-entity" list="keymap-entities" value="${esc(b.entity || "")}"
               placeholder="e.g. switch.socket" autocomplete="off">
      </div>` : ""}
    ${keyNeedsPage.has(act) ? `
      <div class="kd-field">
        <label>Target page</label>
        <select id="kd-target">${pageOptions(b.target_page)}</select>
      </div>` : ""}
  `;

  const inh = el.querySelector(".kd-inherit");
  if (inh) inh.onclick = () => clearPageKeyOverride(id);

  el.querySelectorAll(".chip").forEach(ch => {
    ch.onclick = () => setKeyAction(id, ch.dataset.action);
  });
  const ent = el.querySelector("#kd-entity");
  if (ent) {
    const commit = () => setKeyEntity(id, ent.value.trim());
    ent.addEventListener("change", commit);
    ent.addEventListener("blur", commit);
  }
  const tgt = el.querySelector("#kd-target");
  if (tgt) tgt.addEventListener("change", () => setKeyTarget(id, tgt.value));
}

function setKeyAction(id, action) {
  if (isPageScope()) {
    const p = state.pages[state.selectedPage];
    if (!p.keymap) p.keymap = {};
    const prev = Object.assign({ action: "none" }, p.keymap[id] || {});
    const entry = { action };
    if (keyNeedsEntity.has(action) && prev.entity) entry.entity = prev.entity;
    if (keyNeedsPage.has(action) && prev.target_page) entry.target_page = prev.target_page;
    p.keymap[id] = entry;
    renderKeymap();
    persistPages();
    return;
  }
  if (!state.keymap) state.keymap = {};
  const prev = state.keymap[id] || {};
  const entry = { action };
  if (keyNeedsEntity.has(action) && prev.entity) entry.entity = prev.entity;
  if (keyNeedsPage.has(action) && prev.target_page) entry.target_page = prev.target_page;
  state.keymap[id] = entry;
  renderKeymap();
  saveKeymap();
}
function setKeyEntity(id, v) {
  const target = isPageScope()
    ? (state.pages[state.selectedPage].keymap = state.pages[state.selectedPage].keymap || {})
    : (state.keymap = state.keymap || {});
  const e = Object.assign({ action: "none" }, target[id] || {});
  e.entity = v;
  target[id] = e;
  renderKeymap();
  if (isPageScope()) persistPages(); else saveKeymap();
}
function setKeyTarget(id, v) {
  const target = isPageScope()
    ? (state.pages[state.selectedPage].keymap = state.pages[state.selectedPage].keymap || {})
    : (state.keymap = state.keymap || {});
  const e = Object.assign({ action: "none" }, target[id] || {});
  e.target_page = v;
  target[id] = e;
  renderKeymap();
  if (isPageScope()) persistPages(); else saveKeymap();
}

async function saveKeymap() {
  try { await api("/api/config", "POST", { keymap: state.keymap || {} }); }
  catch (e) { toast("Could not save key layout: " + e.message, "error"); }
}

function currentKeymap() { return state.keymap || {}; }

function resetKeymap() {
  if (isPageScope()) {
    const p = state.pages[state.selectedPage];
    delete p.keymap;                 // forget overrides -> inherit everything
    renderKeymap();
    persistPages();
    toast("Key layout of this page reset (inherits everything)", "info");
    return;
  }
  state.keymap = JSON.parse(JSON.stringify(state.keymapDefaults || {}));
  renderKeymap();
  saveKeymap();
  toast("Default layout restored", "info");
}

function toggleKeymapScope() {
  if (isPageScope()) {
    state.keymapScope = "global";
  } else if (state.selectedPage !== null) {
    state.keymapScope = state.pages[state.selectedPage].id;
  } else {
    state.keymapScope = "global";
  }
  renderKeymap();
}

function openPageKeymap() {
  if (state.selectedPage === null) { toast("Select a page first", "info"); return; }
  state.keymapScope = state.pages[state.selectedPage].id;
  showKeys();
}

/* ---------------- 09 shortcuts + wire-up ---------------- */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("settings-drawer").classList.contains("hidden") || $("settings-drawer").classList.contains("open")) closeDrawer();
  if (!$("item-modal").classList.contains("hidden")) closeItemModal();
  if (!$("new-page-popover").classList.contains("hidden")) closeNewPagePopover();
  if (!$("confirm-modal").classList.contains("hidden")) _closeConfirm(false);
  if (!$("gen-overlay").classList.contains("hidden")) $("gen-overlay").classList.add("hidden");
  closeSidebar();
});

document.addEventListener("DOMContentLoaded", () => {
  init();

  $("btn-add-page").onclick = addPage;
  $("btn-add-page-hero").onclick = addPage;
  $("btn-settings").onclick = openDrawer;
  $("btn-close-drawer").onclick = closeDrawer;
  $("drawer-overlay").onclick = closeDrawer;
  $("btn-save-settings").onclick = saveSettings;
  $("btn-test").onclick = testConn;
  $("btn-fetch-entities").onclick = fetchEntities;
  $("btn-generate").onclick = generate;
  $("btn-upload").onclick = upload;
  $("btn-upload-hero").onclick = download;
  $("btn-download").onclick = download;
  $("btn-load-current").onclick = loadCurrentPage;
  $("btn-add-item").onclick = () => openItemModal(null);

  $("btn-keys").onclick = showKeys;
  $("btn-keymap-back").onclick = showPages;
  $("btn-keymap-defaults").onclick = resetKeymap;
  $("btn-keymap-scope").onclick = toggleKeymapScope;
  $("btn-page-keys").onclick = openPageKeymap;

  $("f-id").addEventListener("change", savePageMeta);
  $("f-title").addEventListener("change", savePageMeta);
  $("f-parent").addEventListener("change", savePageMeta);

  $("btn-item-save").onclick = saveItemFromModal;
  $("btn-item-cancel").onclick = closeItemModal;
  $("btn-close-modal").onclick = closeItemModal;
  $("item-modal-bg").onclick = closeItemModal;
  $("btn-gen-close").onclick = () => $("gen-overlay").classList.add("hidden");
  $("gen-overlay").addEventListener("click", e => { if (e.target === e.currentTarget) $("gen-overlay").classList.add("hidden"); });

  // Mobile sidebar
  $("btn-menu").onclick = openSidebar;
  $("btn-sidebar-close").onclick = closeSidebar;
  $("sidebar-overlay").onclick = closeSidebar;
  document.querySelector(".sidebar .page-list").addEventListener("click", e => {
    if (e.target.classList.contains("p-del")) return;
    closeSidebar();
  });

  // "+ New page" popover wiring.
  $("popover-overlay").onclick = closeNewPagePopover;
  $("btn-popover-close").onclick = closeNewPagePopover;
  document.querySelectorAll("#new-page-popover .tp-card").forEach(card => {
    card.onclick = () => createPageFromTemplate(card.dataset.template);
    // Mouse-down on the card to avoid the click being swallowed by the
    // overlay mousedown handler that the item modal also listens on.
    card.addEventListener("mousedown", e => e.stopPropagation());
  });
  // Mousedown anywhere outside the popover closes it.
  document.addEventListener("mousedown", (e) => {
    const pop = $("new-page-popover");
    if (!pop || pop.classList.contains("hidden")) return;
    if (pop.contains(e.target)) return;
    if (e.target === $("btn-add-page") || e.target === $("btn-add-page-hero")) return;
    closeNewPagePopover();
  });

  // W-3: empty-state template cards (item 4).
  wireEmptyTemplates();

  // W-3: confirm modal wiring (item 6).
  $("btn-confirm-close").onclick = () => _closeConfirm(false);
  $("btn-confirm-no").onclick = () => _closeConfirm(false);
  $("btn-confirm-yes").onclick = () => _closeConfirm(true);
  $("confirm-modal-bg").onclick = () => _closeConfirm(false);
  const confirmInp = $("confirm-input");
  if (confirmInp) confirmInp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); _closeConfirm(true); }
    else if (e.key === "Escape") { e.preventDefault(); _closeConfirm(false); }
  });

  // W-3: Result modal toolbar (item 2).
  _initGenToolbar();
});