// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Complete page item editor (add / edit / delete / reorder). ES module; window-free at import.
// Renders from the store state only; every mutation flows through store.replaceConfig.
// New items are UI DRAFTS (P1.8): they live in store state and reach the persisted
// config only when every required field is set, so the backend never sees a
// placeholder/invalid item. After a failed save the editor re-renders from the
// last confirmed server state (P1.12).

import { insertItem, moveItem, removeItem, updateItem } from './page-model.js';
import { loadEntities } from './api.js';
import { attachEntityAutocomplete } from './entity-autocomplete.js';

function commitChange(store, pageId, index, patch) {
  if (!patch) return;
  store.replaceConfig(updateItem(store.getState().config, pageId, index, patch));
}

// Client-side mirror of the backend's required-field rules (P1.8): the save
// button only enables when the draft could round-trip through parse_config.
// Which types need an entity and which need a destination page comes from
// /api/meta's item_type_meta, the same descriptor rows the firmware's
// ITEM_TYPE_DESCRIPTORS table and the backend model use — hard-coding them here
// is how the editor would come to accept a config the pad rejects.
function itemRules(meta) {
  const descriptors = Array.isArray(meta?.item_type_meta) ? meta.item_type_meta : [];
  return {
    entityTypes: new Set(
      descriptors.filter((row) => (row.ha_domains || []).length > 0).map((row) => row.id)
    ),
    targetPageTypes: new Set(
      descriptors.filter((row) => row.needs_target_page).map((row) => row.id)
    ),
  };
}

function draftErrors(draft, meta) {
  const { entityTypes, targetPageTypes } = itemRules(meta);
  const errors = [];
  if (!draft.name || !String(draft.name).trim()) errors.push('Name is required.');
  if (entityTypes.has(draft.type) && !draft.entity.trim()) {
    errors.push(`Entity is required for type "${draft.type}".`);
  }
  if (targetPageTypes.has(draft.type) && !draft.target_page) {
    errors.push(`Target page is required for type "${draft.type}".`);
  }
  return errors;
}

// A string that could still grow into a finite number after more keystrokes — a
// transient prefix of values like "-20", "0.5", or "1e5". Used to distinguish
// in-progress typing from a definitively non-numeric entry.
const PARTIAL_NUMBER = /^[-+]?(\d*\.?\d*)([eE][-+]?\d*)?$/;

// Validate a numeric field. Finite input yields a patch. A non-finite value is rejected
// (field reverts, error announced, nothing persists) on a stable commit boundary
// (change/blur) or for a string that can never form a number. During live typing a value
// that is still a plausible partial number remains uncommitted so the user can keep
// typing; it is only validated once it resolves or is committed.
function numericPatch(field, input, item, store, boundary) {
  const value = Number(input.value);
  if (Number.isFinite(value)) return { [field]: value };
  // Not finite yet but a credible mid-typing prefix: leave the field as typed,
  // do not revert or announce an error; commit once it becomes finite.
  if (!boundary && PARTIAL_NUMBER.test(input.value)) return null;
  input.value = String(item[field]);
  store.dispatch({ type: 'status', status: { kind: 'error', message: 'Enter a finite number.' } });
  return null;
}

function makeTextInput(label, value, onChange) {
  const input = document.createElement('input');
  input.type = 'text';
  input.value = value == null ? '' : String(value);
  input.setAttribute('aria-label', label);
  // Commit on `input` so browser automation (fill fires `input`, not `change`) and
  // every living keystroke land in the store; `change`/blur re-commits the same value.
  input.addEventListener('input', () => onChange(input.value));
  input.addEventListener('change', () => onChange(input.value));
  return input;
}

function appendLabeledControl(fieldset, labelText, control) {
  const label = document.createElement('label');
  label.appendChild(control);
  label.appendChild(document.createTextNode(labelText));
  fieldset.appendChild(label);
}

export function mountItemEditor(element, store) {
  // Only rebuild the DOM when the page scope or the item *structure* changes
  // (page switch, add, remove, reorder, type). Value edits must not destroy the
  // very inputs being edited — that would clobber in-flight `change` events from
  // browser automation and drop focus for human typing. A failed save (rollback,
  // P1.12) always forces a full rebuild from the confirmed server state.
  let structureSignature = null;
  let autocompletes = [];

  function render(state) {
    const page = state.config.pages.find(p => p.page_id === state.selectedPageId);
    if (!page) {
      autocompletes.forEach(ac => ac.destroy());
      autocompletes = [];
      element.replaceChildren();
      structureSignature = null;
      return;
    }
    const itemSig = page.items.map((item, i) => `${i}:${item.type}`).join(',');
    const draftSig = state.drafts.map((draft, i) => `${i}:${draft.type}`).join(',');
    const signature = `${state.selectedPageId}|${itemSig}|drafts:${draftSig}`;
    const forceRebuild = state.saveState === 'error';
    if (structureSignature === signature && !forceRebuild) return;
    structureSignature = signature;
    autocompletes.forEach(ac => ac.destroy());
    autocompletes = [];
    element.replaceChildren();
    element.setAttribute('aria-label', 'Item editor');

    const typeOptions = Array.isArray(state.meta.item_types) ? state.meta.item_types : [];
    const pageOptions = state.config.pages.map(p => p.page_id);

    page.items.forEach((item, index) => {
      const fieldset = document.createElement('fieldset');
      fieldset.dataset.itemIndex = String(index);

      const nameInput = makeTextInput('Name', item.name, value => commitChange(store, page.page_id, index, { name: value }));
      appendLabeledControl(fieldset, 'Name', nameInput);

      const typeSelect = document.createElement('select');
      typeSelect.setAttribute('aria-label', 'Type');
      typeOptions.forEach(type => {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        typeSelect.appendChild(option);
      });
      typeSelect.value = item.type;
      typeSelect.addEventListener('change', () => commitChange(store, page.page_id, index, { type: typeSelect.value }));
      appendLabeledControl(fieldset, 'Type', typeSelect);

      const entityInput = makeTextInput('Entity ID', item.entity, value => commitChange(store, page.page_id, index, { entity: value }));
      appendLabeledControl(fieldset, 'Entity ID', entityInput);
      autocompletes.push(attachEntityAutocomplete(entityInput, {
        getEntities: () => loadEntities(),
        onSelect: entityId => commitChange(store, page.page_id, index, { entity: entityId }),
        onError: error => store.dispatch({ type: 'status', status: { kind: 'error', message: error.message } })
      }));

      const stateInput = makeTextInput('State', item.state, value => commitChange(store, page.page_id, index, { state: value }));
      appendLabeledControl(fieldset, 'State', stateInput);

      const numeric = (label, field) => {
        const input = document.createElement('input');
        input.type = 'text';
        input.value = String(item[field]);
        input.setAttribute('aria-label', label);
        // `input` fires per keystroke (and from automation that fills the whole value);
        // `change` fires on blur — the stable commit boundary for a hard reject.
        input.addEventListener('input', () => applyNumeric(field, input, false));
        input.addEventListener('change', () => applyNumeric(field, input, true));
        appendLabeledControl(fieldset, label, input);
      };
      const applyNumeric = (field, input, boundary) => {
        const savedItem = store.getState().config.pages.find(p => p.page_id === page.page_id).items[index];
        commitChange(store, page.page_id, index, numericPatch(field, input, savedItem, store, boundary));
      };
      numeric('Value', 'value');
      numeric('Minimum', 'min');
      numeric('Maximum', 'max');
      numeric('Step', 'step');

      const unitInput = makeTextInput('Unit', item.unit, value => commitChange(store, page.page_id, index, { unit: value }));
      appendLabeledControl(fieldset, 'Unit', unitInput);

      const editableInput = document.createElement('input');
      editableInput.type = 'checkbox';
      editableInput.checked = Boolean(item.editable);
      editableInput.setAttribute('aria-label', 'Editable');
      editableInput.addEventListener('change', () => commitChange(store, page.page_id, index, { editable: editableInput.checked }));
      appendLabeledControl(fieldset, 'Editable', editableInput);

      const targetSelect = document.createElement('select');
      targetSelect.setAttribute('aria-label', 'Target page');
      const emptyOption = document.createElement('option');
      emptyOption.value = '';
      emptyOption.textContent = '';
      targetSelect.appendChild(emptyOption);
      pageOptions.forEach(pageId => {
        const option = document.createElement('option');
        option.value = pageId;
        option.textContent = pageId;
        targetSelect.appendChild(option);
      });
      targetSelect.value = item.target_page;
      targetSelect.addEventListener('change', () => commitChange(store, page.page_id, index, { target_page: targetSelect.value }));
      appendLabeledControl(fieldset, 'Target page', targetSelect);

      const moveUp = document.createElement('button');
      moveUp.type = 'button';
      moveUp.textContent = 'Move item up';
      moveUp.setAttribute('aria-label', 'Move item up');
      moveUp.addEventListener('click', () => {
        const current = store.getState();
        store.replaceConfig(moveItem(current.config, page.page_id, index, -1));
      });
      fieldset.appendChild(moveUp);

      const moveDown = document.createElement('button');
      moveDown.type = 'button';
      moveDown.textContent = 'Move item down';
      moveDown.setAttribute('aria-label', 'Move item down');
      moveDown.addEventListener('click', () => {
        const current = store.getState();
        store.replaceConfig(moveItem(current.config, page.page_id, index, 1));
      });
      fieldset.appendChild(moveDown);

      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.textContent = 'Remove item';
      removeBtn.setAttribute('aria-label', 'Remove item');
      removeBtn.addEventListener('click', () => {
        const current = store.getState();
        store.replaceConfig(removeItem(current.config, page.page_id, index));
      });
      fieldset.appendChild(removeBtn);

      element.appendChild(fieldset);
    });

    // --- Draft area (P1.8): nothing here reaches the persisted config until valid ---
    state.drafts.forEach((draft, dIndex) => {
      const fieldset = document.createElement('fieldset');
      fieldset.dataset.draftIndex = String(dIndex);
      fieldset.className = 'item-draft';

      const heading = document.createElement('legend');
      heading.textContent = 'New item (not saved yet)';
      fieldset.appendChild(heading);

      const refreshDraft = () => {
        const current = store.getState().drafts[dIndex] || draft;
        const errors = draftErrors(current, store.getState().meta);
        errorsEl.textContent = errors.join(' ');
        errorsEl.hidden = errors.length === 0;
        saveBtn.disabled = errors.length !== 0;
      };

      const nameInput = makeTextInput('Draft name', draft.name, value => {
        store.dispatch({ type: 'draft-update', index: dIndex, patch: { name: value } });
        refreshDraft();
      });
      appendLabeledControl(fieldset, 'Name', nameInput);

      const typeSelect = document.createElement('select');
      typeSelect.setAttribute('aria-label', 'Draft type');
      typeOptions.forEach(type => {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        typeSelect.appendChild(option);
      });
      typeSelect.value = draft.type;
      typeSelect.addEventListener('change', () => {
        store.dispatch({ type: 'draft-update', index: dIndex, patch: { type: typeSelect.value } });
        refreshDraft();
      });
      appendLabeledControl(fieldset, 'Type', typeSelect);

      const entityInput = makeTextInput('Draft entity ID', draft.entity, value => {
        store.dispatch({ type: 'draft-update', index: dIndex, patch: { entity: value } });
        refreshDraft();
      });
      appendLabeledControl(fieldset, 'Entity ID', entityInput);

      const targetSelect = document.createElement('select');
      targetSelect.setAttribute('aria-label', 'Draft target page');
      const targetEmpty = document.createElement('option');
      targetEmpty.value = '';
      targetEmpty.textContent = '';
      targetSelect.appendChild(targetEmpty);
      pageOptions.forEach(pageId => {
        const option = document.createElement('option');
        option.value = pageId;
        option.textContent = pageId;
        targetSelect.appendChild(option);
      });
      targetSelect.value = draft.target_page;
      targetSelect.addEventListener('change', () => {
        store.dispatch({ type: 'draft-update', index: dIndex, patch: { target_page: targetSelect.value } });
        refreshDraft();
      });
      appendLabeledControl(fieldset, 'Target page', targetSelect);

      const errorsEl = document.createElement('p');
      errorsEl.className = 'draft-errors';
      errorsEl.setAttribute('role', 'alert');
      errorsEl.hidden = true;
      fieldset.appendChild(errorsEl);

      const saveBtn = document.createElement('button');
      saveBtn.type = 'button';
      saveBtn.textContent = 'Save item';
      saveBtn.setAttribute('aria-label', 'Save draft item');
      saveBtn.disabled = true;
      saveBtn.addEventListener('click', () => {
        const current = store.getState().drafts[dIndex];
        if (!current || draftErrors(current, store.getState().meta).length !== 0) return;
        store.dispatch({ type: 'draft-commit', index: dIndex });
        store.replaceConfig(insertItem(store.getState().config, page.page_id, current));
      });
      fieldset.appendChild(saveBtn);

      const discardBtn = document.createElement('button');
      discardBtn.type = 'button';
      discardBtn.textContent = 'Discard draft';
      discardBtn.setAttribute('aria-label', 'Discard draft item');
      discardBtn.addEventListener('click', () => store.dispatch({ type: 'draft-remove', index: dIndex }));
      fieldset.appendChild(discardBtn);

      element.appendChild(fieldset);
      refreshDraft();
    });

    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.textContent = 'Add item';
    addButton.addEventListener('click', () => store.dispatch({ type: 'draft-add' }));
    element.appendChild(addButton);
  }

  return { render };
}