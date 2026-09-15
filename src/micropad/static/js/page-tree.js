// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Accessible page tree + page commands. ES module; window-free at import.
// DOM renders from the store state only; every mutation flows through the store.

import {
  createPage,
  deletePage,
  duplicatePage,
  movePage,
  renamePage,
  uniquePageId,
} from './page-model.js';

// Flattened visible tree order (depth-first) used by ArrowUp/ArrowDown roving selection.
function flatten(pages) {
  const ordered = [];
  const walk = parentId => pages.filter(page => page.parent === parentId)
    .forEach(page => { ordered.push(page.page_id); walk(page.page_id); });
  walk('');
  return ordered;
}

export function mountPageTree(element, store) {
  // The toolbar is rebuilt from scratch on every render, so the title field is a fresh
  // element each time. Anything typed therefore has to be remembered outside the DOM:
  // without this, a re-render between typing and the rename click silently discarded the
  // value - a save landing from the previous action was enough - and the rename became a
  // no-op that still looked like a successful click (found on a slow CI runner, where the
  // duplicate was then named after the old title).
  let pendingTitle = null;
  let titlePageId = null;
  function openDeleteDialog(title, pageId, parentId) {
    const dialog = document.createElement('dialog');
    const message = document.createElement('p');
    message.textContent = `Delete ${title}?`;
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = 'Cancel';
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.textContent = `Delete ${title}`;
    confirmBtn.addEventListener('click', () => {
      const current = store.getState();
      const next = deletePage(current.config, pageId);
      store.replaceConfig(next);
      store.dispatch({type:'select-page', pageId: parentId});
      dialog.close();
    });
    cancel.addEventListener('click', () => dialog.close());
    dialog.appendChild(message);
    dialog.appendChild(cancel);
    dialog.appendChild(confirmBtn);
    dialog.addEventListener('close', () => dialog.remove());
    element.appendChild(dialog);
    dialog.showModal();
  }

  // Drop-zone controls (Before / Make child / After) for one page node. They are
  // always present (so native DnD has a real hit target) as invisible hairline
  // strips; they are highlighted only while a drag is active.
  function makeDropZone(pageId, position, label) {
    const zone = document.createElement('button');
    zone.type = 'button';
    zone.className = 'drop-zone';
    zone.dataset.dropPosition = position;
    zone.setAttribute('aria-label', label);
    zone.tabIndex = -1;
    zone.addEventListener('dragover', event => event.preventDefault());
    zone.addEventListener('drop', event => {
      event.preventDefault();
      const draggedId = event.dataTransfer.getData('text/plain');
      try {
        store.replaceConfig(movePage(store.getState().config, draggedId, pageId, position));
        store.dispatch({type:'select-page', pageId: draggedId});
      } catch (error) {
        store.dispatch({type:'status', status:{kind:'error', message:error.message}});
      }
    });
    return zone;
  }

  function setDragActive(active) {
    element.querySelectorAll('.drop-zone').forEach(zone => zone.classList.toggle('drop-target', active));
  }

  function render(state) {
    const pages = state.config.pages;
    const selected = state.selectedPageId;
    const selectedPage = pages.find(page => page.page_id === selected) || null;
    element.replaceChildren();
    element.setAttribute('aria-label', 'Pages');

    // --- Command toolbar ---
    const toolbar = document.createElement('div');
    toolbar.className = 'page-commands';

    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.textContent = 'Add child page';
    addButton.addEventListener('click', () => {
      const current = store.getState();
      const pageId = uniquePageId(current.config.pages, 'New page');
      store.replaceConfig(createPage(current.config, current.selectedPageId, {
        maxItemsPerPage: current.meta?.caps?.max_items_per_page,
      }));
      store.dispatch({type:'select-page', pageId});
    });
    toolbar.appendChild(addButton);

    // A different page selected means a new editing session: forget the typed value.
    if (state.selectedPageId !== titlePageId) {
      titlePageId = state.selectedPageId;
      pendingTitle = null;
    }
    const titleInput = document.createElement('input');
    titleInput.type = 'text';
    titleInput.setAttribute('aria-label', 'Page title');
    titleInput.value = pendingTitle ?? '';
    titleInput.addEventListener('input', () => {
      pendingTitle = titleInput.value;
    });
    toolbar.appendChild(titleInput);

    const renameButton = document.createElement('button');
    renameButton.type = 'button';
    renameButton.textContent = 'Rename page';
    renameButton.addEventListener('click', () => {
      const current = store.getState();
      const next = renamePage(current.config, current.selectedPageId, titleInput.value);
      if (next !== current.config) {
        store.replaceConfig(next);
        pendingTitle = null;  // the field is done, the next render starts clean
      }
    });
    toolbar.appendChild(renameButton);

    const duplicateButton = document.createElement('button');
    duplicateButton.type = 'button';
    duplicateButton.textContent = 'Duplicate page';
    duplicateButton.disabled = selected === 'home' || !selectedPage;
    duplicateButton.addEventListener('click', () => {
      const current = store.getState();
      const page = current.config.pages.find(item => item.page_id === current.selectedPageId);
      if (!page || page.page_id === 'home') return;
      const pageId = uniquePageId(current.config.pages, `${page.title} copy`);
      store.replaceConfig(duplicatePage(current.config, current.selectedPageId));
      store.dispatch({type:'select-page', pageId});
    });
    toolbar.appendChild(duplicateButton);

    const deleteButton = document.createElement('button');
    deleteButton.type = 'button';
    deleteButton.textContent = 'Delete page';
    deleteButton.disabled = selected === 'home' || !selectedPage;
    deleteButton.addEventListener('click', () => {
      const current = store.getState();
      const page = current.config.pages.find(item => item.page_id === current.selectedPageId);
      if (!page || page.page_id === 'home') return;
      openDeleteDialog(page.title, page.page_id, page.parent);
    });
    toolbar.appendChild(deleteButton);

    // --- ARIA tree ---
    const tree = document.createElement('ul');
    tree.setAttribute('role', 'tree');

    const handleKey = (event, pageId) => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const ordered = flatten(store.getState().config.pages);
        const index = ordered.indexOf(pageId);
        const nextIndex = event.key === 'ArrowDown' ? index + 1 : index - 1;
        if (nextIndex < 0 || nextIndex >= ordered.length) return;
        const nextId = ordered[nextIndex];
        store.dispatch({type:'select-page', pageId: nextId});
        const node = element.querySelector(`[data-page-id="${nextId}"]`);
        if (node) node.focus();
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        store.dispatch({type:'select-page', pageId});
      }
    };

    const buildBranch = (parentId, level) => {
      const branch = pages.filter(page => page.parent === parentId);
      if (branch.length === 0) return null;
      const group = document.createElement('ul');
      group.setAttribute('role', 'group');
      branch.forEach(page => {
        // Non-interactive wrapper carrying the node id; holds ONLY this node's
        // drop controls so that `[data-page-node-id] [data-drop-position]`
        // does not match nested children's zones.
        const item = document.createElement('li');
        const wrapper = document.createElement('div');
        wrapper.className = 'page-node';
        wrapper.dataset.pageNodeId = page.page_id;
        const before = makeDropZone(page.page_id, 'before', 'Before');
        const inside = makeDropZone(page.page_id, 'inside', 'Make child');
        const after = makeDropZone(page.page_id, 'after', 'After');
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('role', 'treeitem');
        button.dataset.pageId = page.page_id;
        button.setAttribute('aria-level', String(level));
        button.draggable = true;
        const isSelected = page.page_id === selected;
        button.setAttribute('aria-selected', String(isSelected));
        if (isSelected) button.setAttribute('aria-current', 'page');
        button.tabIndex = isSelected ? 0 : -1;
        button.textContent = page.title;
        button.addEventListener('click', () => store.dispatch({type:'select-page', pageId: page.page_id}));
        button.addEventListener('keydown', event => handleKey(event, page.page_id));
        button.addEventListener('dragstart', event => {
          event.dataTransfer.setData('text/plain', page.page_id);
          event.dataTransfer.effectAllowed = 'move';
          button.setAttribute('aria-grabbed', 'true');
          setDragActive(true);
        });
        button.addEventListener('dragend', () => {
          button.removeAttribute('aria-grabbed');
          setDragActive(false);
        });
        wrapper.appendChild(before);
        wrapper.appendChild(button);
        wrapper.appendChild(inside);
        wrapper.appendChild(after);
        item.appendChild(wrapper);
        const childGroup = buildBranch(page.page_id, level + 1);
        if (childGroup) item.appendChild(childGroup);
        group.appendChild(item);
      });
      return group;
    };

    const root = buildBranch('', 1);
    if (root) tree.appendChild(root);
    element.appendChild(toolbar);
    element.appendChild(tree);
  }

  return {render};
}