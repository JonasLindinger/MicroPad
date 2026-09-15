// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Page generator: turn Home Assistant entities into pages (B5).
//
// One button per domain that the backend reports as pageable ("Lights (12)",
// "Scenes (4)", …). The backend owns every rule that matters — which domains are
// pageable, which item type they map to, the page/item ceilings, and the firmware's
// field caps — so this module only asks, merges the returned drafts into the open
// configuration and reports what changed. Saving stays the operator's action.
//
// The route itself saves nothing: it returns drafts. They land in the store, which
// autosaves every edit like the rest of the UI — so generating a page is an ordinary
// configuration change, reverted by undo/save like any other.

import { buildPages, loadEntityGroups, loadEntities } from './api.js';

export function mountPageGenerator(element, store, { api = { buildPages, loadEntityGroups, loadEntities } } = {}) {
  if (!element) return { render() {} };

  const heading = document.createElement('h2');
  heading.textContent = 'Generate pages from Home Assistant';

  const hint = document.createElement('p');
  hint.className = 'generator-hint';
  hint.textContent =
    'Pages are built from the cached entities, added to the open configuration and saved like any other edit.';

  const refresh = document.createElement('button');
  refresh.type = 'button';
  refresh.className = 'generator-refresh';
  refresh.textContent = 'Refresh entity cache';

  const buttons = document.createElement('div');
  buttons.className = 'generator-groups';

  const notice = document.createElement('p');
  notice.className = 'generator-notice';
  notice.setAttribute('role', 'status');

  const notes = document.createElement('ul');
  notes.className = 'generator-notes';

  element.replaceChildren(heading, hint, refresh, buttons, notice, notes);

  let groups = [];
  let busy = false;

  function setNotice(text) {
    notice.textContent = text;
  }

  function renderNotes(messages) {
    notes.replaceChildren();
    messages.forEach((message) => {
      const item = document.createElement('li');
      item.className = 'finding finding-warning';
      item.textContent = message;
      notes.appendChild(item);
    });
  }

  function renderGroups() {
    buttons.replaceChildren();
    if (!groups.length) {
      const empty = document.createElement('p');
      empty.textContent = 'No entities cached yet — refresh the entity cache first.';
      buttons.appendChild(empty);
      return;
    }
    groups.forEach((group) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'generator-group';
      button.dataset.domain = group.domain;
      const label = group.item_type ? group.item_type : group.domain;
      button.textContent = `${label} (${group.count})`;
      if (!group.pageable) {
        button.disabled = true;
        button.title = 'No item type matches this domain yet.';
      }
      button.addEventListener('click', () => generate([group.domain]));
      buttons.appendChild(button);
    });
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'generator-group generator-all';
    all.textContent = 'All pageable domains';
    all.addEventListener('click', () => generate(null));
    buttons.appendChild(all);
  }

  async function refreshGroups() {
    try {
      const response = await api.loadEntityGroups();
      groups = (response && response.groups) || [];
      renderGroups();
    } catch (error) {
      setNotice(`Could not read the entity cache: ${error.message}`);
    }
  }

  async function generate(domains) {
    if (busy) return;
    busy = true;
    setNotice('Building pages…');
    try {
      const result = await api.buildPages(domains ? { domains } : {});
      const drafts = (result && result.pages) || [];
      if (!drafts.length) {
        setNotice('Nothing to generate: no cached entity matches a pageable domain.');
        renderNotes((result && result.notes) || []);
        return;
      }
      const state = store.getState();
      const existing = new Set((state.config.pages || []).map((page) => page.page_id));
      const fresh = drafts.filter((page) => !existing.has(page.page_id));
      store.replaceConfig({ ...state.config, pages: [...(state.config.pages || []), ...fresh] });
      const first = fresh[0];
      if (first) store.dispatch({ type: 'select-page', pageId: first.page_id });
      // The store autosaves every edit, so the pages are persisted immediately;
      // publishing to the pad still happens through the normal deploy action.
      setNotice(`${fresh.length} page(s) added and saved — review them before deploying.`);
      renderNotes((result && result.notes) || []);
    } catch (error) {
      setNotice(`Page generation failed: ${error.message}`);
    } finally {
      busy = false;
    }
  }

  refresh.addEventListener('click', async () => {
    setNotice('Refreshing entity cache…');
    try {
      await api.loadEntities();
      await refreshGroups();
      setNotice('Entity cache refreshed.');
    } catch (error) {
      setNotice(`Could not refresh entities: ${error.message}`);
    }
  });

  return {
    render() {
      // Rendered once from the backend; re-reading on every store change would
      // hammer the API while the operator types.
      if (!buttons.childElementCount) refreshGroups();
    },
  };
}
