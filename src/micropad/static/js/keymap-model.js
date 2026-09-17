// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Immutable keymap operations for the clean-room MicroPad frontend. ES module; window-free.

// One gesture target (hold or double): its own action, entity and target page.
// Absent means unbound, which is exactly what a keymap written before gestures
// existed carries — such a key keeps its single-press behaviour.
export function normalizeGesture(gesture = {}) {
  const source = gesture && typeof gesture === 'object' ? gesture : {};
  return {
    action: typeof source.action === 'string' ? source.action : 'none',
    entity: typeof source.entity === 'string' ? source.entity : '',
    target_page: typeof source.target_page === 'string' ? source.target_page : ''
  };
}

export function normalizeBinding(binding = {}) {
  return {
    action: typeof binding.action === 'string' ? binding.action : 'none',
    entity: typeof binding.entity === 'string' ? binding.entity : '',
    target_page: typeof binding.target_page === 'string' ? binding.target_page : '',
    hold: normalizeGesture(binding.hold),
    double: normalizeGesture(binding.double)
  };
}

// True when both bindings address exactly the same thing, gestures included.
// The page-scope writer uses this to skip a redundant override: writing one
// would push a pointless pages[].keymap entry onto the wire.
const TARGET_FIELDS = ['action', 'entity', 'target_page'];

export function bindingsEqual(a, b) {
  const left = normalizeBinding(a);
  const right = normalizeBinding(b);
  return TARGET_FIELDS.every(field => left[field] === right[field])
    && ['hold', 'double'].every(gesture =>
      TARGET_FIELDS.every(field => left[gesture][field] === right[gesture][field]));
}

// True when at least one gesture is bound on this key (used for the key face
// badge, so the board shows at a glance where a hold or double press exists).
export function hasGestures(binding) {
  const normalized = normalizeBinding(binding);
  return normalized.hold.action !== 'none' || normalized.double.action !== 'none';
}

export function setGlobalBinding(config, keyId, binding) {
  return {...config, global_keymap:{...config.global_keymap, [keyId]:normalizeBinding(binding)}};
}

// Exact set match: the global keymap must contain precisely the metadata key IDs,
// no more and no fewer, and every metadata key ID must be unique before key editing
// is safe. Returns true only when the keymap is renderable.
export function keymapMetadataComplete(config, meta) {
  if (!meta || !Array.isArray(meta.key_ids)) return false;
  if (new Set(meta.key_ids).size !== meta.key_ids.length) return false;
  const global = config && config.global_keymap;
  if (!global || typeof global !== 'object') return false;
  const keys = Object.keys(global);
  if (keys.length !== meta.key_ids.length) return false;
  return meta.key_ids.every(keyId => global[keyId] && typeof global[keyId] === 'object');
}

// Root-to-current ancestry for a page, including the page itself. Follows parent
// links up the chain (unshifting each id) and throws on a missing parent or a cycle,
// mirroring the backend page_graph contract.
export function ancestorIds(pages, pageId) {
  const index = {};
  for (const page of pages || []) index[page.page_id] = page;
  const page = index[pageId];
  if (!page) throw new Error(`unknown page_id: ${pageId}`);
  const chain = [];
  const seen = new Set();
  let current = page;
  while (true) {
    if (seen.has(current.page_id)) throw new Error(`cycle at page_id: ${current.page_id}`);
    seen.add(current.page_id);
    chain.push(current.page_id);
    if (!current.parent) break;
    if (!index[current.parent]) throw new Error(`unknown parent: ${current.parent}`);
    current = index[current.parent];
  }
  chain.reverse();
  return chain;
}

// Resolve the effective binding for a key on a page: global defaults first, then
// root→parent ancestor overrides, then this page's own override. Overrides live in
// each page's `keymap` map (the canonical AppConfig shape: models.Page.keymap), keyed
// by page_id within config.pages. `inherited` is true unless the binding comes from
// the current page's own override.
export function resolveBinding(config, pageId, keyId) {
  const pageForId = id => (config.pages || []).find(page => page.page_id === id);
  let binding = normalizeBinding(config.global_keymap && config.global_keymap[keyId]);
  let sourceScopeId = 'global';
  for (const id of ancestorIds(config.pages, pageId)) {
    const override = pageForId(id)?.keymap?.[keyId];
    if (override) { binding = normalizeBinding(override); sourceScopeId = id; }
  }
  return {binding, inherited: sourceScopeId !== pageId, sourceScopeId};
}

// Write (or replace) a page-local override for one key by setting it inside the page's
// canonical `keymap` map. Never touches the global or ancestor bindings.
export function setPageOverride(config, pageId, keyId, binding) {
  const pages = (config.pages || []).map(page => {
    if (page.page_id !== pageId) return page;
    return {...page, keymap: {...(page.keymap || {}), [keyId]: normalizeBinding(binding)}};
  });
  return {...config, pages};
}

// Remove a page-local override for one key from the page's `keymap` map, omitting the
// map entirely when the page becomes empty so the public_config round-trip stays clean
// and the pages list stays valid. Returns a new config; never mutates the input.
export function clearPageOverride(config, pageId, keyId) {
  const pages = (config.pages || []).map(page => {
    if (page.page_id !== pageId) return page;
    const keymap = {...(page.keymap || {})};
    delete keymap[keyId];
    const copy = {...page};
    if (Object.keys(keymap).length) copy.keymap = keymap;
    else delete copy.keymap;
    return copy;
  });
  return {...config, pages};
}

// A guard-railed reset of the whole key layout: restore the global keymap to the
// fourteen canonical defaults (mirroring backend default_keymap/KEY_IDS semantics) and
// clear every page-local key override, leaving pages, items, settings, and the entity
// cache untouched. Guards against a partial/default map from a stale or malformed
// server so a bogus default can never be blindly written. Returns a new config.
export function restoreKeymapDefaults(config, meta) {
  if (!meta || !meta.default_keymap || Object.keys(meta.default_keymap).length !== 14) {
    throw new Error('Default keymap is incomplete.');
  }
  const pages = (config.pages || []).map(page => {
    const keymap = page.keymap || {};
    if (Object.keys(keymap).length === 0) return page;
    const copy = {...page};
    // Drop the empty override map so the public_config round-trip stays clean, matching
    // the clearPageOverride convention of omitting an empty pages[].keymap.
    delete copy.keymap;
    return copy;
  });
  return {...config, global_keymap: structuredClone(meta.default_keymap), pages};
}

// True when every metadata key resolves to a binding for the requested page scope.
// Because global defaults always cover all keys, a complete global map implies every
// page's effective map is complete; a page may not be edited until this holds.
export function effectiveKeymapComplete(config, pageId, meta) {
  if (!meta || !Array.isArray(meta.key_ids)) return false;
  if (pageId === 'global') return keymapMetadataComplete(config, meta);
  try {
    return meta.key_ids.every(keyId => {
      const resolved = resolveBinding(config, pageId, keyId);
      return resolved.binding && typeof resolved.binding === 'object';
    });
  } catch {
    return false;
  }
}

// Display title for a page, or a neutral placeholder when unknown.
export function pageTitle(config, pageId) {
  const page = (config.pages || []).find(candidate => candidate.page_id === pageId);
  return page && page.title ? page.title : pageId;
}