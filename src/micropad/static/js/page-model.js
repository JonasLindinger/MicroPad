// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Immutable page-tree operations for the clean-room MicroPad frontend. ES module; window-free.
// The graph contract (exactly one home root, every non-home page has a parent, no cycles) is
// enforced by the backend; these helpers keep client edits within that contract: home can be
// renamed but never duplicated, deleted, or reparented.

export const childrenOf = (pages, parentId) => pages.filter(page => page.parent === parentId);

export function uniquePageId(pages, title) {
  const base = title.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'page';
  const used = new Set(pages.map(page => page.page_id));
  let candidate = base;
  for (let suffix = 2; used.has(candidate); suffix += 1) candidate = `${base}-${suffix}`;
  return candidate;
}

// The pad renders a menu from the *items* of a page, never from the page list: a page
// that no parent links to exists in the configurator and can never be opened on the
// device. Reported symptom: "the website says there is a Spotify page, the board does
// not show it". Every page created here therefore also becomes a category item on its
// parent — unless that parent is already at the item cap, because silently making a
// valid page invalid is worse than an unlinked page the lint reports.
const categoryItem = (name, targetPage) => ({
  name,
  type: 'category',
  entity: '',
  state: '',
  value: 0,
  min: 0,
  max: 100,
  step: 1,
  unit: '',
  editable: false,
  target_page: targetPage,
});

function withChildLink(pages, parentId, name, childId, maxItemsPerPage) {
  const parent = pages.find(page => page.page_id === parentId);
  if (!parent || parent.items.length >= maxItemsPerPage) return {pages, linked: false};
  return {
    pages: pages.map(page => page.page_id === parentId
      ? {...page, items: [...page.items, categoryItem(name, childId)]}
      : page),
    linked: true,
  };
}

const linkLimit = options => (Number.isFinite(options.maxItemsPerPage) ? options.maxItemsPerPage : Infinity);

export function createPage(config, parentId, options = {}) {
  const page_id = uniquePageId(config.pages, 'New page');
  const pages = [...config.pages, {page_id, title:'New page', parent:parentId, items:[]}];
  return {...config, pages: withChildLink(pages, parentId, 'New page', page_id, linkLimit(options)).pages};
}

// Create one child page from an API-supplied template. Items are deep-copied with
// structuredClone so the template object is never mutated; the new page gets a unique
// id derived from the template title, is appended under parentId and is linked from it
// (see withChildLink). ``linked`` reports whether that link fit on the parent page.
export function createPageFromTemplate(config, parentId, template, options = {}) {
  const pageId = uniquePageId(config.pages, template.page.title);
  const page = {page_id:pageId, title:template.page.title, parent:parentId, items:structuredClone(template.page.items)};
  const {pages, linked} = withChildLink([...config.pages, page], parentId, template.label, pageId, linkLimit(options));
  return {config:{...config, pages}, pageId, linked};
}

export function duplicatePage(config, pageId) {
  const page = config.pages.find(item => item.page_id === pageId);
  // Home is never duplicated (the brief's graph contract is enforced client-side too).
  if (!page || page.page_id === 'home') return config;
  const title = `${page.title} copy`;
  const page_id = uniquePageId(config.pages, title);
  const copy = {...page, page_id, title, items: page.items.map(item => ({...item}))};
  return {...config, pages:[...config.pages, copy]};
}

export function renamePage(config, pageId, title) {
  const trimmed = (typeof title === 'string' ? title : '').trim();
  if (!trimmed) return config; // empty titles are rejected, never applied
  return {...config, pages: config.pages.map(page => page.page_id === pageId ? {...page, title: trimmed} : page)};
}

export function deletePage(config, pageId) {
  // Home is never deleted (the brief's graph contract is enforced client-side too).
  if (pageId === 'home') return config;
  const index = config.pages.findIndex(page => page.page_id === pageId);
  if (index === -1) return config;
  const removed = config.pages[index];
  // Reparent direct children to the removed page's parent at the removed position.
  const children = config.pages.filter(page => page.parent === pageId)
    .map(page => ({...page, parent: removed.parent}));
  const others = config.pages.filter(page => page.page_id !== pageId && page.parent !== pageId);
  // The removed page (and its canonical `keymap` overrides) leaves the pages list with it;
  // direct children are reparented and keep only their own keymap, never the removed page's.
  return {...config, pages:[...others.slice(0, index), ...children, ...others.slice(index)]};
}

// Ordered, cycle-safe movement. Throws without mutating when the move would break the
// graph contract: home can never be moved, and a page can never be placed inside itself
// or one of its descendants. Position is 'before' | 'inside' | 'after' relative to targetId.
export function movePage(config, draggedId, targetId, position) {
  if (draggedId === 'home') throw new Error('Home cannot be moved.');
  const byId = new Map(config.pages.map(page => [page.page_id, page]));
  const descendants = new Set();
  const visit = id => config.pages.filter(page => page.parent === id).forEach(page => { descendants.add(page.page_id); visit(page.page_id); });
  visit(draggedId);
  if (draggedId === targetId || descendants.has(targetId)) throw new Error('A page cannot be moved into itself or its descendants.');
  const target = byId.get(targetId);
  const parent = position === 'inside' ? targetId : target.parent;
  const moved = {...byId.get(draggedId), parent};
  const remaining = config.pages.filter(page => page.page_id !== draggedId);
  const targetIndex = remaining.findIndex(page => page.page_id === targetId);
  const insertAt = position === 'after' ? targetIndex + 1 : position === 'before' ? targetIndex : remaining.length;
  remaining.splice(insertAt, 0, moved);
  return {...config, pages:remaining};
}

// The default shape for a freshly added page item (matches the backend PageItem model).
export const NEW_ITEM = Object.freeze({
  name: 'New item', type: 'sensor', entity: '', state: '',
  value: 0, min: 0, max: 100, step: 1, unit: '', editable: false, target_page: '',
});

const patchPage = (config, pageId, update) => ({...config, pages: config.pages.map(page => page.page_id !== pageId ? page : update(page))});

// Insert a new item at the top of a page (index 0), cloning the template so every
// item owns its own plain object. This is the only item add-path; nothing empties items.
export function addItem(config, pageId) {
  return patchPage(config, pageId, page => ({...page, items: [{...NEW_ITEM}, ...page.items.map(item => ({...item}))]}));
}

// Insert a COMPLETE item (validated by the caller) at the top of a page.
// Used by the draft save path (P1.8): only fully valid items reach the config.
export function insertItem(config, pageId, item) {
  return patchPage(config, pageId, page => ({...page, items: [{...item}, ...page.items.map(existing => ({...existing}))]}));
}

export function updateItem(config, pageId, index, patch) {
  return patchPage(config, pageId, page => ({
    ...page,
    items: page.items.map((item, itemIndex) => itemIndex === index ? {...item, ...patch} : item)
  }));
}

export function removeItem(config, pageId, index) {
  return patchPage(config, pageId, page => ({
    ...page,
    items: page.items.filter((item, itemIndex) => itemIndex !== index)
  }));
}

// Swap only adjacent entries; return the unchanged config at array boundaries.
export function moveItem(config, pageId, index, direction) {
  const target = index + direction;
  return patchPage(config, pageId, page => {
    if (target < 0 || target >= page.items.length) return page;
    const items = page.items.map(item => ({...item}));
    const moving = items[index];
    items[index] = items[target];
    items[target] = moving;
    return {...page, items};
  });
}