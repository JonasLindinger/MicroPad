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
    state.keymap = data.keymap || {};
    const meta = await api("/api/meta");
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
function renderPageList(){
  const list = $("page-list");
  list.innerHTML = "";
  state.pages.forEach((p, i)=>{
    const row = document.createElement("div");
    row.className = "page-item" + (i===state.selectedPage?" active":"");
    row.innerHTML = `
      <div class="p-icon">${icons[p.items[0]?.type]||"▦"}</div>
      <div class="p-text"><div class="p-name">${esc(p.title||p.id)}</div>
      <div class="p-id">${esc(p.id)}</div></div>
      <button class="p-del" title="Seite löschen">✕</button>`;
    row.onclick = (e)=>{ if(e.target.classList.contains("p-del"))return; selectPage(i); };
    row.querySelector(".p-del").onclick = async (e)=>{
      e.stopPropagation();
      if(!confirm(`Seite "${p.id}" löschen?`))return;
      const target = state.pages[i].target; // undefined - just remove
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
  none:"— nichts —", enter:"Aktivieren (Item)", back:"Zurück", home:"Home",
  settings:"WLAN-Einrichtung", scroll:"Scrollen (Encoder)", scroll_up:"Auswahl hoch",
  scroll_down:"Auswahl runter", navigate:"Seite öffnen", toggle:"Umschalten",
  on:"Einschalten", off:"Ausschalten", press:"Skript/Button/Szene starten",
};
function actionLabel(a){ return keyActionLabels[a] || a; }

function pageOptions(sel){
  const out = ['<option value="">(keine)</option>'];
  state.pages.forEach(p=>{
    out.push(`<option value="${esc(p.id)}"${p.id===sel?" selected":""}>${esc(p.title||p.id)}</option>`);
  });
  return out.join("");
}

function renderKeymap(){
  const list = $("keymap-list");
  if(!list) return;
  list.innerHTML = (state.keymapKeys||[]).map(k=>{
    const b = (state.keymap||{})[k.id] || {action:"none"};
    const act = b.action || "none";
    const opts = (state.keyActions||["none"]).map(a=>
      `<option value="${a}"${a===act?" selected":""}>${esc(actionLabel(a))}</option>`).join("");
    const showEnt = keyNeedsEntity.has(act), showPage = keyNeedsPage.has(act);
    return `<div class="keymap-row" data-key="${esc(k.id)}">
      <div class="keymap-key">${esc(k.label)}</div>
      <select class="keymap-action">${opts}</select>
      <input class="keymap-entity${showEnt?"":" hidden"}" list="keymap-entities"
             value="${esc(b.entity||"")}" placeholder="Entität, z. B. switch.steckdose">
      <select class="keymap-target${showPage?"":" hidden"}">${pageOptions(b.target_page)}</select>
    </div>`;
  }).join("");

  list.querySelectorAll(".keymap-row").forEach(row=>{
    const sel = row.querySelector(".keymap-action");
    const ent = row.querySelector(".keymap-entity");
    const tgt = row.querySelector(".keymap-target");
    sel.addEventListener("change", ()=>{
      ent.classList.toggle("hidden", !keyNeedsEntity.has(sel.value));
      tgt.classList.toggle("hidden", !keyNeedsPage.has(sel.value));
      saveKeymap();
    });
    ent.addEventListener("change", saveKeymap);
    ent.addEventListener("blur", saveKeymap);
    tgt.addEventListener("change", saveKeymap);
  });

  // Offer the known entities as suggestions (the same list the item editor
  // uses); a plain free-text field would mean typing entity ids by hand.
  const dl = $("keymap-entities");
  if(dl){
    dl.innerHTML = Object.values(state.entities||{}).slice(0,600).map(e=>
      `<option value="${esc(e.entity_id)}">${esc(e.name||"")}</option>`).join("");
  }
}

function collectKeymap(){
  const km = {};
  document.querySelectorAll(".keymap-row").forEach(row=>{
    const action = row.querySelector(".keymap-action").value;
    const ent = row.querySelector(".keymap-entity").value.trim();
    const tgt = row.querySelector(".keymap-target").value;
    const entry = {action};
    if(keyNeedsEntity.has(action) && ent) entry.entity = ent;
    if(keyNeedsPage.has(action) && tgt) entry.target_page = tgt;
    km[row.dataset.key] = entry;
  });
  return km;
}

// Read the map straight from the DOM when the editor is on screen, otherwise
// fall back to the stored copy (e.g. before it was ever opened).
function currentKeymap(){
  return document.querySelector(".keymap-row") ? collectKeymap() : (state.keymap||{});
}

async function saveKeymap(){
  state.keymap = collectKeymap();
  try { await api("/api/config","POST",{keymap:state.keymap}); }
  catch(e){ toast("Tastenbelegung speichern fehlgeschlagen: "+e.message,"error"); }
}

function resetKeymap(){
  state.keymap = JSON.parse(JSON.stringify(state.keymapDefaults||{}));
  renderKeymap();
  saveKeymap();
  toast("Standardbelegung wiederhergestellt","info");
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