/* MicroPad Config Generator - frontend logic */
const $ = (id) => document.getElementById(id);

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
  keymapScope: "global",  // "global" | "page"
  keymapSel: "r0c0",      // currently selected key in the pad view
};

const icons = {
  category:"▦", light:"💡", switch:"🔌", script:"⚙", button:"🔘",
  sensor:"📊", media_player:"🎬", number:"#", settings:"⚙", back:"↩",
};

const editTypes = new Set(["number","media_player"]);

// ---------------- API helpers ----------------
async function api(path, method="GET", body=null){
  const opt = { method, headers:{} };
  if (body){ opt.headers["Content-Type"]="application/json"; opt.body=JSON.stringify(body); }
  const r = await fetch(path, opt);
  const data = await r.json().catch(()=>({}));
  if (!r.ok && !data.ok){ throw new Error(data.error || data.message || `HTTP ${r.status}`); }
  return data;
}

function toast(msg, type="info"){
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("toast-wrap").appendChild(el);
  setTimeout(()=>{ el.style.opacity="0"; el.style.transition="opacity .3s"; setTimeout(()=>el.remove(),300); }, 4000);
}

// ---------------- Init ----------------
async function init(){
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
  } catch(e){ toast("Kann Config nicht laden: "+e.message, "error"); }
}

// entities kommen aus dem Backend als Array von {entity_id,name,state,domain}
function buildEntities(arr){
  const map = {};
  (arr||[]).forEach(e=>{ map[e.entity_id] = e; });
  return map;
}

// ---------------- Settings drawer ----------------
function populateSettings(){
  $("s-ha-url").value = state.settings.ha_url || "";
  $("s-ha-token").value = state.settings.ha_token || "";
  $("s-mqtt").value = state.settings.mqtt_broker || "";
  $("s-ssh-host").value = state.settings.ssh_host || "";
  $("s-ssh-user").value = state.settings.ssh_user || "";
  $("s-ssh-key").value = state.settings.ssh_key || "";
  $("s-remote").value = state.settings.remote_path || "";
}
function readSettings(){
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
async function saveSettings(){
  state.settings = readSettings();
  await api("/api/config","POST",{ settings: state.settings });
  toast("Einstellungen gespeichert","success");
  closeDrawer();
  updateConnDot();
}
function openDrawer(){ $("drawer-overlay").classList.remove("hidden"); $("settings-drawer").classList.add("open"); }
function closeDrawer(){ $("drawer-overlay").classList.add("hidden"); $("settings-drawer").classList.remove("open"); }

function updateConnDot(){
  const dot = $("conn-dot");
  if (state.settings.ha_token){ dot.className="conn-dot ok"; $("conn-label").textContent="HA-Token gesetzt"; }
  else { dot.className="conn-dot err"; $("conn-label").textContent="HA nicht verbunden"; }
}

// ---------------- Persist pages ----------------
async function persistPages(){
  await api("/api/config","POST",{ pages: state.pages });
  renderAll();
}

// ---------------- Render ----------------
function renderAll(){
  renderPageList();
  renderKeymap();
  applyView();
  updateConnDot();
}

// The main column either shows the page editor or the key map editor.
function applyView(){
  const keys = state.view === "keys";
  $("keymap-view").classList.toggle("hidden", !keys);
  if (keys) {
    $("editor").classList.add("hidden");
    $("empty-state").classList.add("hidden");
  } else {
    renderEditor();          // restores empty-state / editor visibility
  }
}

function showKeys(){ state.view = "keys"; renderAll(); }
function showPages(){ state.view = "pages"; renderAll(); }
function selectPage(idx){
  state.selectedPage = idx;
  renderAll();
}

// ---------- Page tree ----------
// Sort pages so parents come before children; render with indentation so the
// tree structure (home → ha → licht ...) is visible and editable.
function pageChildren(parentId){
  return state.pages.filter(p => (p.parent || "") === parentId);
}
function pageDepth(id, seen){
  seen = seen || new Set();
  if(seen.has(id)) return 0;          // cycle guard
  seen.add(id);
  const p = state.pages.find(x => x.id === id);
  if(!p || !p.parent) return 0;
  return 1 + pageDepth(p.parent, seen);
}
function renderPageList(){
  const list = $("page-list");
  list.innerHTML = "";
  const roots = pageChildren("");
  const ordered = [];
  function walk(items){
    items.forEach(p => {
      ordered.push(p);
      walk(pageChildren(p.id));
    });
  }
  walk(roots);
  // Fallback: pages whose parent is missing from state still show at root
  const known = new Set(state.pages.map(p => p.id));
  state.pages.forEach(p => {
    if(!known.has(p.parent || "") && !ordered.includes(p)) ordered.push(p);
  });

  ordered.forEach(p => {
    const i = state.pages.indexOf(p);
    const depth = pageDepth(p.id);
    const row = document.createElement("div");
    row.className = "page-item" + (i===state.selectedPage? " active":"");
    row.style.paddingLeft = (12 + depth * 16) + "px";
    const prefix = depth > 0 ? "└ " : "";
    row.innerHTML = `
      <div class="p-icon">${icons[p.items[0]?.type]||"▦"}</div>
      <div class="p-text"><div class="p-name">${esc(prefix + (p.title||p.id))}</div>
      <div class="p-id">${esc(p.id)}</div></div>
      <button class="p-del" title="Seite löschen">✕</button>`;
    row.onclick = (e)=>{ if(e.target.classList.contains("p-del"))return; selectPage(i); };
    row.querySelector(".p-del").onclick = async (e)=>{
      e.stopPropagation();
      if(!confirm(`Seite "${p.id}" löschen?`))return;
      state.pages.splice(i,1);
      if(state.selectedPage>=state.pages.length) state.selectedPage=state.pages.length-1;
      await persistPages();
    };
    list.appendChild(row);
  });
}

function renderEditor(){
  const hasPages = state.pages.length>0;
  $("empty-state").classList.toggle("hidden", hasPages);
  $("editor").classList.toggle("hidden", !hasPages || state.selectedPage===null);
  if(!hasPages || state.selectedPage===null) return;
  const p = state.pages[state.selectedPage];
  $("page-title-placeholder").textContent = p.title||p.id||"Seite";
  $("page-id-hint").textContent = "ID: "+p.id+(p.parent?"  ·  Übergeordnet: "+p.parent:"");
  $("f-id").value = p.id||"";
  $("f-title").value = p.title||"";
  // parent select
  const ps = $("f-parent");
  ps.innerHTML = '<option value="">(keine)</option>';
  state.pages.forEach((pp,i)=>{ if(i!==state.selectedPage){ ps.insertAdjacentHTML("beforeend",`<option value="${esc(pp.id)}">${esc(pp.id)}</option>`); } });
  ps.value = p.parent||"";
  // page key map summary
  const pks = $("page-key-summary");
  if(pks){
    const ov = pageKeymapOverrides(p);
    const n = Object.keys(ov).length;
    const parentTitle = p.parent ? (state.pages.find(x=>x.id===p.parent)?.title || p.parent) : "global";
    pks.textContent = n === 0
      ? `Erbt komplett von „${esc(parentTitle)}“ – keine eigenen Belegungen.`
      : `${n} Taste(n) überschrieben · Rest erbt von „${esc(parentTitle)}“.`;
  }
  // items
  $("item-count").textContent = p.items.length;
  $("item-empty").classList.toggle("hidden", p.items.length>0);
  const il = $("item-list");
  il.innerHTML = "";
  p.items.forEach((it, j)=>{
    const row = document.createElement("div");
    row.className="item-row";
    row.innerHTML = `
      <div class="item-arrows">
        <button class="move-btn" data-m="-1" ${j===0?"disabled":""}>▲</button>
        <button class="move-btn" data-m="1" ${j===p.items.length-1?"disabled":""}>▼</button>
      </div>
      <div class="item-main">
        <div class="item-ico">${icons[it.type]||"•"}</div>
        <div><div class="item-name">${esc(it.name||"Unnamed")}</div>
        <div class="item-sub">${esc(it.entity||(it.target_page?"→ "+it.target_page:""))}</div></div>
      </div>
      <span class="item-type">${esc(it.type)}</span>
      <button class="item-edit">Bearbeiten</button>
      <button class="item-del">🗑</button>`;
    row.querySelectorAll(".move-btn").forEach(b=>b.onclick=()=>moveItem(j, +b.dataset.m));
    row.querySelector(".item-main").onclick=()=>openItemModal(j);
    row.querySelector(".item-edit").onclick=()=>openItemModal(j);
    row.querySelector(".item-del").onclick=async (e)=>{e.stopPropagation(); if(!confirm("Item löschen?"))return; p.items.splice(j,1); await persistPages(); };
    il.appendChild(row);
  });
}

function esc(s){ return String(s??"").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

// ---------------- Page actions ----------------
async function addPage(){
  const id = prompt("Seiten-ID (z.B. 'home', 'lamps'):");
  if(!id) return;
  const clean = id.trim().toLowerCase().replace(/[^a-z0-9_]/g,"_");
  if(state.pages.some(p=>p.id===clean)){ toast(`Seite "${clean}" existiert schon`,"error"); return; }
  state.pages.push({ id:clean, title:clean.charAt(0).toUpperCase()+clean.slice(1), parent:"", items:[] });
  state.selectedPage = state.pages.length-1;
  await persistPages();
}

async function savePageMeta(){
  if(state.selectedPage===null) return;
  const p = state.pages[state.selectedPage];
  const newId = $("f-id").value.trim().toLowerCase().replace(/[^a-z0-9_]/g,"_");
  if(newId && newId!==p.id && state.pages.some(x=>x.id===newId)){ toast("Seiten-ID existiert schon","error"); return; }
  if(newId) p.id = newId;
  p.title = $("f-title").value.trim() || p.id;
  p.parent = $("f-parent").value;
  await persistPages();
}

// ---------------- Item actions ----------------
function moveItem(i,dir){
  const p = state.pages[state.selectedPage];
  const j=i+dir;
  if(j<0||j>=p.items.length)return;
  [p.items[i],p.items[j]]=[p.items[j],p.items[i]];
  persistPages();
}

function openItemModal(itemIdx){
  const p = state.pages[state.selectedPage];
  const it = itemIdx===null ? {name:"",type:"category",entity:"",target_page:"",unit:"",editable:false,min:0,max:100,step:5} : {...p.items[itemIdx]};
  const bg = $("item-modal-bg"), mo = $("item-modal");
  $("item-modal-title").textContent = itemIdx===null ? "Neues Item" : "Item bearbeiten";
  const form = $("item-form");
  form.innerHTML = `
    <div class="field"><label>Name</label><input id="m-name" type="text" value="${esc(it.name)}"></div>
    <div class="modal-2col">
      <div class="field"><label>Typ</label><select id="m-type"></select></div>
      <div class="field"><label>Zielseite (Kategorie)</label><select id="m-target"></select></div>
    </div>
    <div class="field"><label>Entity ID</label>
      <div id="m-entity-combo" class="combo">
        <input id="m-entity" type="text" value="${esc(it.entity)}" placeholder="Suche…" autocomplete="off">
        <button id="m-entity-clear" type="button" class="combo-clear ${it.entity?"":"hidden"}">✕</button>
        <div id="m-entity-list" class="combo-list hidden"></div>
      </div>
      <div id="m-entity-status" class="field-hint"></div></div>
    <div class="field"><label>Einheit (Sensoren)</label><input id="m-unit" type="text" value="${esc(it.unit)}"></div>
    <div class="field" style="flex-direction:row;align-items:center;gap:8px">
      <input id="m-editable" type="checkbox" ${it.editable?"checked":""}><label style="margin:0">Editable (Slider/Wert)</label>
    </div>
    <div class="modal-2col">
      <div class="field"><label>Min</label><input id="m-min" type="number" value="${it.min??0}"></div>
      <div class="field"><label>Max</label><input id="m-max" type="number" value="${it.max??100}"></div>
      <div class="field"><label>Step</label><input id="m-step" type="number" value="${it.step??5}"></div>
    </div>
    <div id="m-warn" class="warn-text hidden"></div>`;
  // type options
  const tsel = form.querySelector("#m-type");
  state.itemTypes.forEach(t=>{ tsel.insertAdjacentHTML("beforeend",`<option value="${t}" ${t===it.type?"selected":""}>${t}</option>`); });
  // target options
  const tarsel = form.querySelector("#m-target");
  tarsel.insertAdjacentHTML("beforeend",`<option value="">(keine)</option>`);
  state.pages.forEach(pp=>{ if(pp.id!==(p.id)) tarsel.insertAdjacentHTML("beforeend",`<option value="${esc(pp.id)}" ${pp.id===it.target_page?"selected":""}>${esc(pp.id)}</option>`); });
  // datalist of entities
  const entInput = form.querySelector("#m-entity");
  const entList = form.querySelector("#m-entity-list");
  const entClear = form.querySelector("#m-entity-clear");
  const entStatus = form.querySelector("#m-entity-status");
  const entCombo = form.querySelector("#m-entity-combo");

  const entityArray = state.entities ? Object.values(state.entities) : [];
  if(entityArray.length){
    entStatus.className = "field-hint";
    entStatus.textContent = entityCount() + " Entities geladen";
  } else {
    entStatus.className = "field-hint";
    entStatus.innerHTML = "Keine Entities geladen — ";
    const btn = document.createElement("a");
    btn.href="#"; btn.textContent = "Entities laden";
    btn.style.color = "var(--primary)"; btn.style.fontWeight="600";
    btn.onclick = async (e)=>{ e.preventDefault(); btn.textContent="Lade…";
      try{
        state.settings = readSettings();
        await api("/api/config","POST",{settings:state.settings});
        const r = await api("/api/ha/entities","POST");
        state.entities = buildEntities(r.entities);
        entStatus.textContent = r.entities.length + " Entities geladen";
        $("conn-dot").className="conn-dot ok"; $("conn-label").textContent=`HA: ${r.entities.length} Entities`;
        renderEntityList(""); entList.classList.remove("hidden");
      }catch(err){ entStatus.textContent = "Fehler: "+err.message; }
    };
    entStatus.appendChild(btn);
  }

  function renderEntityList(query){
    const q = (query||"").trim().toLowerCase();
    let items = state.entities ? Object.values(state.entities) : [];
    if(q) items = items.filter(e =>
      e.entity_id.toLowerCase().includes(q) ||
      (e.name||"").toLowerCase().includes(q) ||
      e.domain.toLowerCase().includes(q));
    items = items.slice(0,80);

    if(!items.length){
      entList.classList.remove("hidden");
      entList.innerHTML = `<div class="combo-empty">Keine Entities gefunden</div>`;
      return;
    }
    entList.innerHTML = "";
    items.forEach(e=>{
      const row = document.createElement("div");
      row.className = "combo-item" + (e.entity_id===entInput.value?" active":"");
      row.innerHTML = `<div class="combo-name">${esc(e.name||"(ohne Name)")}</div><div class="combo-sub">${esc(e.entity_id)}</div>`;
      row.onclick = ()=>{ selectEntity(e); closeEntityList(); };
      entList.appendChild(row);
    });
  }

  function selectEntity(e){
    entInput.value = e.entity_id;
    entClear.classList.remove("hidden");
    if(itemIdx===null && !$("m-name").value.trim()){
      $("m-name").value = e.name || suggestName(e.entity_id);
    }
    if($("m-type").value==="category") $("m-type").value = suggestType(e.entity_id);
  }
  function suggestType(id){ return (id.split(".")[0] || "sensor"); }
  function suggestName(id){
    const parts = id.split(".");
    if(parts.length < 2) return id;
    return parts[1].replace(/_/g," ").replace(/\b\w/g, c=>c.toUpperCase());
  }

  function openEntityList(){
    if(!entityCount()) return; // status link already offers "Entities laden"
    renderEntityList(entInput.value);
    entList.classList.remove("hidden");
  }
  function closeEntityList(){ entList.classList.add("hidden"); }

  entInput.addEventListener("input", ()=>{
    renderEntityList(entInput.value);
    entList.classList.remove("hidden");
  });
  entInput.addEventListener("focus", openEntityList);
  entInput.addEventListener("click", openEntityList);
  entInput.addEventListener("keydown", (ev)=>{
    if(ev.key==="Escape") closeEntityList();
  });
  entClear.addEventListener("click", ()=>{
    entInput.value="";
    entClear.classList.add("hidden");
    entList.classList.add("hidden");
    entInput.focus();
  });
  entInput.addEventListener("change", ()=>{
    if(entInput.value) entClear.classList.remove("hidden");
  });
  // close list and restore if clicked outside combo
  document.addEventListener("mousedown", function comboClose(e){
    if(entCombo && !entCombo.contains(e.target)){
      if(entList && !entList.classList.contains("hidden")) closeEntityList();
    }
  });

  // initial: show a populated dropdown when opening with an existing entity
  if(entInput.value){
    const cur = (state.entities||{})[entInput.value];
    if(cur){
      entStatus.textContent = `${esc(cur.name||entInput.value)}` + ` (${entCount()} geladen)`;
    }
  }
  function entCount(){ return Object.keys(state.entities).length; }
  // warning on editable for unsupported types
  const editable = form.querySelector("#m-editable"), warn = form.querySelector("#m-warn");
  function updWarn(){
    const ty = tsel.value;
    if(editable.checked && !editTypes.has(ty)){
      warn.classList.remove("hidden");
      warn.textContent = `Typ "${ty}" unterstützt auf dem Pad keinen Edit-Modus (wird als Toggle/Press behandelt). Nutze "number" oder "media_player" für einen funktionierenden Slider.`;
    } else warn.classList.add("hidden");
  }
  tsel.onchange=updWarn; editable.onchange=updWarn; updWarn();

  state._itemIdx = itemIdx;
  bg.classList.remove("hidden"); mo.classList.remove("hidden");
}

function closeItemModal(){ $("item-modal-bg").classList.add("hidden"); $("item-modal").classList.add("hidden"); }

function saveItemFromModal(){
  const p = state.pages[state.selectedPage];
  const item = {
    name: $("m-name").value.trim() || "Unnamed",
    type: $("m-type").value,
    entity: $("m-entity").value.trim(),
    target_page: $("m-target").value,
    unit: $("m-unit").value.trim(),
    editable: $("m-editable").checked,
    min: parseFloat($("m-min").value)||0,
    max: parseFloat($("m-max").value)||100,
    step: parseFloat($("m-step").value)||1,
  };
  if(state._itemIdx===null) p.items.push(item);
  else p.items[state._itemIdx] = item;
  closeItemModal();
  persistPages();
}

// ---------------- HA actions ----------------
async function testConn(){
  try {
    state.settings = readSettings();
    await api("/api/config","POST",{settings:state.settings});
    const r = await api("/api/ha/test","POST");
    toast(r.message||"Verbindung OK","success");
    updateConnDot();
  } catch(e){ toast(e.message,"error"); }
}

async function fetchEntities(){
  try {
    state.settings = readSettings();
    await api("/api/config","POST",{settings:state.settings});
    const r = await api("/api/ha/entities","POST");
    state.entities = buildEntities(r.entities);
    toast(`${r.entities.length} Entities geladen`,"success");
    $("conn-dot").className="conn-dot ok"; $("conn-label").textContent=`HA: ${r.entities.length} Entities`;
  } catch(e){ toast(e.message,"error"); }
}

function entityCount(){ return Object.keys(state.entities).length; }

async function generate(){
  if(!state.pages.length){ toast("Keine Seiten definiert","error"); return; }
  try {
    const r = await api("/api/generate","POST",{pages:state.pages, keymap:currentKeymap()});
    if(!r.ok){ toast(r.errors.join("\n"),"error"); return; }
    $("gen-output").textContent = r.yaml_automations;
    $("gen-overlay").classList.remove("hidden");
  } catch(e){ toast(e.message,"error"); }
}

async function upload(){
  if(!state.pages.length){ toast("Keine Seiten definiert","error"); return; }
  if(!confirm("Config zu Home Assistant hochladen und automatisch neu laden?"))return;
  try {
    const r = await api("/api/upload/api","POST",{pages:state.pages, keymap:currentKeymap()});
    toast(r.message||"Hochgeladen","success");
  } catch(e){ toast(e.message,"error"); }
}

async function download(){
  if(!confirm("Aktuelle MicroPad-Automation von HA laden (ersetzt lokale Seiten)?"))return;
  try {
    const r = await api("/api/download/api","POST");
    state.pages = r.pages; state.selectedPage = 0;
    await api("/api/config","POST",{pages:state.pages});
    toast(`${r.pages.length} Seiten von HA geladen`,"success");
    renderAll();
  } catch(e){ toast(e.message,"error"); }
}

async function loadCurrentPage(){
  try {
    const r = await api("/api/load-current-page","POST");
    const pid = r.page_id||r.target_page;
    const idx = state.pages.findIndex(p=>p.id===pid);
    if(idx>=0){ selectPage(idx); toast(`Aktuelle Seite: ${pid}`,"info"); }
    else toast(`Pad zeigt: ${pid||"unbekannt"} (Aktion: ${r.state||"?"})`,"info");
  } catch(e){
    if(e.hint) toast(e.message+" "+e.hint,"info");
    else toast(e.message,"error");
  }
}

// ---------------- Key map editor ----------------
// Which actions need an extra field.
const keyNeedsEntity = new Set(["toggle","on","off","press"]);
const keyNeedsPage   = new Set(["navigate"]);

const keyActionLabels = {
  none:"Keine Aktion", enter:"Aktivieren", back:"Zurück", home:"Home",
  settings:"WLAN-Einrichtung", scroll:"Scrollen", scroll_up:"Auswahl hoch",
  scroll_down:"Auswahl runter", navigate:"Seite öffnen", toggle:"Umschalten",
  on:"Einschalten", off:"Ausschalten", press:"Skript/Button/Szene",
};
function actionLabel(a){ return keyActionLabels[a] || a; }

// Short text printed on the keycap itself.
const keyActionShort = {
  none:"", enter:"Aktivieren", back:"Zurück", home:"Home", settings:"WLAN",
  scroll:"Scrollen", scroll_up:"↑ Auswahl", scroll_down:"↓ Auswahl",
  navigate:"Seite", toggle:"Umschalten", on:"Einschalten", off:"Ausschalten",
  press:"Starten",
};

const keyActionGroups = [
  {title:"Navigation",     actions:["enter","back","home","navigate"]},
  {title:"Scrollen",       actions:["scroll","scroll_up","scroll_down"]},
  {title:"Gerät schalten", actions:["toggle","on","off","press"]},
  {title:"System",         actions:["settings","none"]},
];

const MATRIX_IDS = ["r0c0","r0c1","r0c2","r0c3","r1c0","r1c1","r1c2","r1c3","r2c0","r2c1","r2c2","r2c3"];

function keyInfo(id){
  return (state.keymapKeys||[]).find(k=>k.id===id) || {id:id, label:id};
}

function shortEntity(e){
  if(!e) return "";
  const ent = (state.entities||{})[e];
  const name = ent ? (ent.name || e) : e;
  return name.length > 16 ? name.slice(0,15)+"…" : name;
}

// ---------- Per-page key map (tree inheritance) ----------
// The effective map of a page = global map, overridden by every page on the
// parent chain (root first). Mirrors core.effective_keymap_for_page().
function pageKeymapOverrides(page){
  return (page && typeof page.keymap === "object" && page.keymap) ? page.keymap : {};
}
function parentChain(pageId, seen){
  seen = seen || new Set();
  if(seen.has(pageId)) return [];
  seen.add(pageId);
  const p = state.pages.find(x => x.id === pageId);
  if(!p) return [];
  return [p, ...parentChain(p.parent, seen)];
}
function effectiveKeymapForPage(page){
  const chain = parentChain(page.id).reverse();   // root → … → page
  // start from the global map, then apply page overrides root-first
  let map = {};
  Object.keys(state.keymapDefaults||{}).forEach(k=>{ map[k] = Object.assign({action:"none"}, state.keymapDefaults[k]); });
  Object.keys(state.keymap||{}).forEach(k=>{ map[k] = Object.assign({action:"none"}, state.keymap[k]); });
  chain.forEach(p=>{
    const ov = pageKeymapOverrides(p);
    Object.keys(ov).forEach(k=>{
      const entry = ov[k];
      map[k] = (entry && typeof entry === "object")
        ? Object.assign({action:"none"}, entry) : {action:"none"};
    });
  });
  // always complete: every key present (mirrors normalize_keymap defaults)
  (state.keymapKeys||[]).forEach(k=>{ if(!map[k.id]) map[k.id] = {action:"none"}; });
  return map;
}

// Which map the editor currently edits: the global one, or the effective map
// of the selected page (changes become overrides on that page).
function activeKeymap(){
  if(state.keymapScope === "page" && state.selectedPage !== null){
    const p = state.pages[state.selectedPage];
    if(p) return effectiveKeymapForPage(p);
  }
  return state.keymap || {};
}
function keymapScopeTitle(){
  if(state.keymapScope === "page" && state.selectedPage !== null){
    const p = state.pages[state.selectedPage];
    return p ? (p.title || p.id) : "Seite";
  }
  return "global";
}
// Reset an override back to "inherited": remove the key from the page map.
function clearPageKeyOverride(id){
  const p = state.pages[state.selectedPage];
  if(!p || !p.keymap) return;
  delete p.keymap[id];
  if(Object.keys(p.keymap).length === 0) delete p.keymap;
  persistPages();
  renderKeymap();
}

// What a key currently does, as one readable line ("" = nothing bound).
function bindingSummary(id){
  const map = activeKeymap();
  const b = map[id] || {action:"none"};
  const act = b.action || "none";
  if(act === "none") return "";
  if(keyNeedsEntity.has(act)) return keyActionShort[act] + (b.entity ? ": " + shortEntity(b.entity) : "");
  if(keyNeedsPage.has(act))   return keyActionShort[act] + (b.target_page ? ": " + b.target_page : "");
  return keyActionShort[act] || act;
}

function pageOptions(sel){
  const out = ['<option value="">(keine)</option>'];
  state.pages.forEach(p=>{
    out.push(`<option value="${esc(p.id)}"${p.id===sel?" selected":""}>${esc(p.title||p.id)}</option>`);
  });
  return out.join("");
}

function renderKeymap(){
  const grid = $("pad-grid");
  if(!grid) return;
  if(!state.keymapSel) state.keymapSel = "r0c0";
  const sel = state.keymapSel;
  const inPageScope = state.keymapScope === "page" && state.selectedPage !== null;

  // Scope UI
  const scopeBtn = $("btn-keymap-scope");
  const scopeHint = $("keymap-scope-hint");
  if(scopeBtn){
    scopeBtn.textContent = inPageScope
      ? "Wechseln zu: global"
      : "Wechseln zu: Seite (" + (state.selectedPage !== null ? esc(state.pages[state.selectedPage]?.title || state.pages[state.selectedPage]?.id || "?") : "–") + ")";
  }
  if(scopeHint){
    scopeHint.textContent = inPageScope
      ? "Tastenbelegung der Seite „" + (state.pages[state.selectedPage]?.title || state.pages[state.selectedPage]?.id || "") + "“ – erbt von der übergeordneten Seite und überschreibt sie hier."
      : "Globale Belegung – gilt für alle Seiten, die keine eigene Belegung haben. Klicke „Wechseln zu: Seite“, um die Belegung einer einzelnen Seite anzupassen.";
  }

  grid.innerHTML = MATRIX_IDS.map(id=>{
    const info = keyInfo(id);
    const sum = bindingSummary(id);
    const bound = sum !== "";
    const slot = esc(String(info.label||id).split(" (")[0]);
    return `<button class="pad-key${bound? " bound":""}${id===sel? " sel":""}" data-key="${esc(id)}"
              title="${esc(info.label||id)}${bound? " — "+esc(sum):""}">
        <span class="pk-slot">${slot}</span>
        <span class="pk-action">${bound ? esc(sum) : "—"}</span>
      </button>`;
  }).join("");

  // Encoder knob + both directions, same highlight rules.
  document.querySelectorAll("#pad-grid .pad-key, .enc-dir, .enc-knob").forEach(el=>{
    const id = el.dataset.key;
    if(!id) return;
    const bound = bindingSummary(id) !== "";
    if(el.classList.contains("enc-dir") || el.classList.contains("enc-knob")){
      el.classList.toggle("bound", bound);
      el.classList.toggle("sel", id === sel);
    }
    el.onclick = ()=>{ state.keymapSel = id; renderKeymap(); };
  });

  renderKeyDetail();

  // Entity suggestions for the key bindings (same list the item editor uses).
  const dl = $("keymap-entities");
  if(dl){
    dl.innerHTML = Object.values(state.entities||{}).slice(0,600).map(e=>
      `<option value="${esc(e.entity_id)}">${esc(e.name||"")}</option>`).join("");
  }
}

// Right-hand panel: shows every available action for the selected key.
function renderKeyDetail(){
  const el = $("key-detail");
  if(!el) return;
  const id = state.keymapSel || "r0c0";
  const info = keyInfo(id);
  const map = activeKeymap();
  const b = map[id] || {action:"none"};
  const act = b.action || "none";
  const inPageScope = state.keymapScope === "page" && state.selectedPage !== null;
  const hasOverride = inPageScope && !!(state.pages[state.selectedPage]?.keymap || {})[id];

  const extra = [b.entity, b.target_page].filter(Boolean).map(esc).join(" · ");
  el.innerHTML = `
    <div class="kd-head">
      <div class="kd-title">${esc(info.label || id)}</div>
      <div class="kd-sub">${inPageScope ? `Seite: ${esc(keymapScopeTitle())}` : "Global"} · Belegt mit: ${esc(actionLabel(act))}${extra ? " · "+extra : ""}</div>
    </div>
    ${hasOverride ? `<button class="btn btn-ghost btn-sm kd-inherit">↺ Erben von übergeordneter Seite</button>` : ""}
    ${keyActionGroups.map(g=>`
      <div class="kd-group">
        <div class="kd-group-title">${g.title}</div>
        <div class="kd-chips">
          ${g.actions.map(a=>`<button class="chip${a===act?" on":""}" data-action="${a}">${esc(actionLabel(a))}</button>`).join("")}
        </div>
      </div>`).join("")}
    ${keyNeedsEntity.has(act) ? `
      <div class="kd-field">
        <label>Entität</label>
        <input id="kd-entity" list="keymap-entities" value="${esc(b.entity||"")}"
               placeholder="z. B. switch.steckdose">
      </div>` : ""}
    ${keyNeedsPage.has(act) ? `
      <div class="kd-field">
        <label>Zielseite</label>
        <select id="kd-target">${pageOptions(b.target_page)}</select>
      </div>` : ""}
  `;

  const inh = el.querySelector(".kd-inherit");
  if(inh) inh.onclick = ()=> clearPageKeyOverride(id);

  el.querySelectorAll(".chip").forEach(ch=>{
    ch.onclick = ()=> setKeyAction(id, ch.dataset.action);
  });
  const ent = el.querySelector("#kd-entity");
  if(ent){
    const commit = ()=> setKeyEntity(id, ent.value.trim());
    ent.addEventListener("change", commit);
    ent.addEventListener("blur", commit);
  }
  const tgt = el.querySelector("#kd-target");
  if(tgt) tgt.addEventListener("change", ()=> setKeyTarget(id, tgt.value));
}

function setKeyAction(id, action){
  if(state.keymapScope === "page" && state.selectedPage !== null){
    const p = state.pages[state.selectedPage];
    if(!p.keymap) p.keymap = {};
    const prev = Object.assign({action:"none"}, p.keymap[id] || {});
    const entry = {action};
    if(keyNeedsEntity.has(action) && prev.entity) entry.entity = prev.entity;
    if(keyNeedsPage.has(action) && prev.target_page) entry.target_page = prev.target_page;
    p.keymap[id] = entry;
    renderKeymap();
    persistPages();
    return;
  }
  if(!state.keymap) state.keymap = {};
  const prev = state.keymap[id] || {};
  const entry = {action};
  if(keyNeedsEntity.has(action) && prev.entity) entry.entity = prev.entity;
  if(keyNeedsPage.has(action) && prev.target_page) entry.target_page = prev.target_page;
  state.keymap[id] = entry;
  renderKeymap();
  saveKeymap();
}
function setKeyEntity(id, v){
  const target = (state.keymapScope === "page" && state.selectedPage !== null)
    ? (state.pages[state.selectedPage].keymap = state.pages[state.selectedPage].keymap || {})
    : (state.keymap = state.keymap || {});
  const e = Object.assign({action:"none"}, target[id] || {});
  e.entity = v;
  target[id] = e;
  renderKeymap();
  if(state.keymapScope === "page") persistPages(); else saveKeymap();
}
function setKeyTarget(id, v){
  const target = (state.keymapScope === "page" && state.selectedPage !== null)
    ? (state.pages[state.selectedPage].keymap = state.pages[state.selectedPage].keymap || {})
    : (state.keymap = state.keymap || {});
  const e = Object.assign({action:"none"}, target[id] || {});
  e.target_page = v;
  target[id] = e;
  renderKeymap();
  if(state.keymapScope === "page") persistPages(); else saveKeymap();
}

async function saveKeymap(){
  try { await api("/api/config","POST",{keymap:state.keymap||{}}); }
  catch(e){ toast("Tastenbelegung speichern fehlgeschlagen: "+e.message,"error"); }
}

function currentKeymap(){ return state.keymap || {}; }

function resetKeymap(){
  if(state.keymapScope === "page" && state.selectedPage !== null){
    const p = state.pages[state.selectedPage];
    delete p.keymap;                 // forget overrides -> inherit everything
    renderKeymap();
    persistPages();
    toast("Tastenbelegung dieser Seite zurückgesetzt (erbt alles)","info");
    return;
  }
  state.keymap = JSON.parse(JSON.stringify(state.keymapDefaults||{}));
  renderKeymap();
  saveKeymap();
  toast("Standardbelegung wiederhergestellt","info");
}

function toggleKeymapScope(){
  state.keymapScope = (state.keymapScope === "page") ? "global" : "page";
  renderKeymap();
}

function openPageKeymap(){
  if(state.selectedPage === null){ toast("Erst eine Seite auswählen","info"); return; }
  state.keymapScope = "page";
  showKeys();
}

// ---------------- Wire up ----------------
document.addEventListener("DOMContentLoaded", ()=>{
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
  $("btn-add-item").onclick = ()=>openItemModal(null);

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
  $("btn-gen-close").onclick = ()=>$("gen-overlay").classList.add("hidden");
  $("gen-overlay").addEventListener("click", e=>{ if(e.target===e.currentTarget) $("gen-overlay").classList.add("hidden"); });

  // CSS: drawer open
  const style = document.createElement("style");
  style.textContent = `.drawer{transform:translateX(110%)}.drawer.open{transform:translateX(0)}`;
  document.head.appendChild(style);
});