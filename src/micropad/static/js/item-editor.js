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
    // Which types may be a slider row (the contract's `adjustable` flag): a type
    // without an adjustable value would give the operator a slider that cannot
    // move, so the editor does not offer one there.
    adjustableTypes: new Set(
      descriptors.filter((row) => row.adjustable).map((row) => row.id)
    ),
    // The controls the backend knows, with the contract's own labels.
    controlOptions: Array.isArray(meta?.control_meta) && meta.control_meta.length
      ? meta.control_meta
      : [{ id: 'button', label: 'Button', description: '' }],
  };
}

function draftErrors(draft, meta) {
  const { entityTypes, targetPageTypes, adjustableTypes } = itemRules(meta);
  const errors = [];
  if (!draft.name || !String(draft.name).trim()) errors.push('Name is required.');
  if (entityTypes.has(draft.type) && !draft.entity.trim()) {
    errors.push(`Entity is required for type "${draft.type}".`);
  }
  if (targetPageTypes.has(draft.type) && !draft.target_page) {
    errors.push(`Target page is required for type "${draft.type}".`);
  }
  if (draft.control === 'slider' && !adjustableTypes.has(draft.type)) {
    errors.push(`A "${draft.type}" row has no adjustable value, so it cannot be a slider.`);
  }
  return errors;
}

// The two-valued states the pad draws as a checkbox (a box with a tick, never a
// word). The editor offers the matching switch for exactly this vocabulary and for
// an empty state, so toggling can never overwrite a real reading like 23.4 - and
// because the vocabulary is pair-based, a cover or a lock gets a switch that writes
// `closed`/`open` or `unlocked`/`locked` instead of forcing `on`/`off` onto it.
const BOOLEAN_PAIRS = {
  on: 'off', off: 'on',
  true: 'false', false: 'true',
  open: 'closed', closed: 'open',
  locked: 'unlocked', unlocked: 'locked',
  home: 'away', away: 'home',
  yes: 'no', no: 'yes',
  active: 'inactive', inactive: 'active',
  enabled: 'disabled', disabled: 'enabled',
};

// The "on" side of every pair: the state whose checkbox is ticked.
const BOOLEAN_ON_STATES = new Set([
  'on', 'true', 'open', 'locked', 'home', 'yes', 'active', 'enabled',
]);

// The pair a state belongs to, as [onValue, offValue]. Looking either member up
// gives the same pair (the map is symmetric), and an empty or unknown state gets
// the default on/off pair.
function booleanPairOf(state) {
  const value = String(state == null ? '' : state).trim().toLowerCase();
  const other = BOOLEAN_PAIRS[value];
  if (other === undefined) return ['on', 'off'];
  return BOOLEAN_ON_STATES.has(value) ? [value, other] : [other, value];
}

function booleanStateOf(state) {
  const value = String(state == null ? '' : state).trim().toLowerCase();
  if (!value) return null;  // empty: nothing to overwrite, the switch starts off
  return value in BOOLEAN_PAIRS ? value : undefined;
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
  // Reorder counter: the DOM signature below is built from item *types* only
  // (so live value edits never tear down the inputs being typed in). Swapping
  // two items of the same type therefore leaves the signature unchanged and
  // the move would be invisible. Each Move click bumps this counter so the
  // rebuild happens exactly when the order changed.
  let reorderCounter = 0;

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
    const signature = `${state.selectedPageId}|${itemSig}|drafts:${draftSig}|reorder:${reorderCounter}`;
    const forceRebuild = state.saveState === 'error';
    if (structureSignature === signature && !forceRebuild) return;
    structureSignature = signature;
    autocompletes.forEach(ac => ac.destroy());
    autocompletes = [];
    element.replaceChildren();
    element.setAttribute('aria-label', 'Item editor');

    const typeOptions = Array.isArray(state.meta.item_types) ? state.meta.item_types : [];
    const pageOptions = state.config.pages.map(p => p.page_id);
    const rules = itemRules(state.meta);

    // How a row is driven: a button (a press runs its action) or a slider (the
    // encoder additionally turns the value while the cursor rests on it). Only
    // the types with an adjustable value offer the slider option, so the editor
    // can never build a row the pad would not move.
    const appendControlSelect = (fieldset, item, commit, label) => {
      const options = rules.adjustableTypes.has(item.type)
        ? rules.controlOptions
        : rules.controlOptions.filter((option) => option.id === 'button');
      const select = document.createElement('select');
      select.setAttribute('aria-label', label);
      options.forEach((option) => {
        const element = document.createElement('option');
        element.value = option.id;
        element.textContent = option.label;
        if (option.description) element.title = option.description;
        select.appendChild(element);
      });
      select.value = options.some((option) => option.id === item.control) ? item.control : 'button';
      select.disabled = options.length < 2;
      select.addEventListener('change', () => commit(select.value));
      appendLabeledControl(fieldset, label, select);
    };

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
      typeSelect.addEventListener('change', () => {
        // A control the new type cannot carry would be refused by the backend, so
        // the type switch resets it instead of persisting a contradictory row.
        const patch = rules.adjustableTypes.has(typeSelect.value)
          ? { type: typeSelect.value }
          : { type: typeSelect.value, control: 'button' };
        commitChange(store, page.page_id, index, patch);
      });
      appendLabeledControl(fieldset, 'Type', typeSelect);

      appendControlSelect(
        fieldset,
        item,
        (control) => commitChange(store, page.page_id, index, { control }),
        'Control'
      );

      const entityInput = makeTextInput('Entity ID', item.entity, value => commitChange(store, page.page_id, index, { entity: value }));
      appendLabeledControl(fieldset, 'Entity ID', entityInput);
      autocompletes.push(attachEntityAutocomplete(entityInput, {
        getEntities: () => loadEntities(),
        onSelect: entityId => commitChange(store, page.page_id, index, { entity: entityId }),
        onError: error => store.dispatch({ type: 'status', status: { kind: 'error', message: error.message } })
      }));

      const stateInput = makeTextInput('State', item.state, value => commitChange(store, page.page_id, index, { state: value }));
      appendLabeledControl(fieldset, 'State', stateInput);

      // The same two-valued state the pad draws as a checkbox: offered as a
      // switch for exactly on/off/true/false (and an empty state), so it can
      // never overwrite a reading like 23.4 with a boolean.
      const stateCheckLabel = document.createElement('label');
      const stateCheck = document.createElement('input');
      stateCheck.type = 'checkbox';
      stateCheck.setAttribute('aria-label', 'On');
      stateCheckLabel.appendChild(stateCheck);
      stateCheckLabel.appendChild(document.createTextNode('On'));
      fieldset.appendChild(stateCheckLabel);
      // The switch is shown/hidden in place instead of forcing a rebuild: a
      // rebuild would destroy the State input while it is being typed in.
      const syncStateCheck = (value) => {
        const boolean = booleanStateOf(value);
        stateCheckLabel.hidden = boolean === undefined;
        stateCheck.checked = boolean !== null && boolean !== undefined &&
          BOOLEAN_ON_STATES.has(boolean);
      };
      syncStateCheck(item.state);
      stateInput.addEventListener('input', () => syncStateCheck(stateInput.value));
      stateCheck.addEventListener('change', () => {
        // Write the member of the row's own pair, so a cover or a lock keeps its
        // vocabulary (`open`/`closed`, `locked`/`unlocked`) instead of being
        // rewritten to `on`/`off`, which Home Assistant would not report back.
        const [onValue, offValue] = booleanPairOf(stateInput.value);
        const next = stateCheck.checked ? onValue : offValue;
        // Keep the text field in step with the switch (and let it re-sync the
        // switch's own state, so the two controls can never disagree).
        stateInput.value = next;
        syncStateCheck(next);
        commitChange(store, page.page_id, index, { state: next });
      });

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
        reorderCounter += 1;  // same-type swaps keep the type signature unchanged
        store.replaceConfig(moveItem(current.config, page.page_id, index, -1));
      });
      fieldset.appendChild(moveUp);

      const moveDown = document.createElement('button');
      moveDown.type = 'button';
      moveDown.textContent = 'Move item down';
      moveDown.setAttribute('aria-label', 'Move item down');
      moveDown.addEventListener('click', () => {
        const current = store.getState();
        reorderCounter += 1;  // see moveUp: force the DOM rebuild on reorder
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
        // As in the saved-item editor: a control the chosen type cannot carry is
        // reset, so the draft can never become unsaveable.
        store.dispatch({
          type: 'draft-update', index: dIndex,
          patch: rules.adjustableTypes.has(typeSelect.value)
            ? { type: typeSelect.value }
            : { type: typeSelect.value, control: 'button' },
        });
        refreshDraft();
      });
      appendLabeledControl(fieldset, 'Type', typeSelect);

      appendControlSelect(
        fieldset,
        draft,
        (control) => {
          store.dispatch({ type: 'draft-update', index: dIndex, patch: { control } });
          refreshDraft();
        },
        'Draft control'
      );

      const entityInput = makeTextInput('Draft entity ID', draft.entity, value => {
        store.dispatch({ type: 'draft-update', index: dIndex, patch: { entity: value } });
        refreshDraft();
      });
      appendLabeledControl(fieldset, 'Entity ID', entityInput);
      autocompletes.push(attachEntityAutocomplete(entityInput, {
        getEntities: () => loadEntities(),
        onSelect: entityId => {
          store.dispatch({ type: 'draft-update', index: dIndex, patch: { entity: entityId } });
          refreshDraft();
        },
        onError: error => store.dispatch({ type: 'status', status: { kind: 'error', message: error.message } })
      }));

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