// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Fourteen-input keymap editor with a Global/Page scope chooser. ES module; window-free at import.
// Global scope edits the shared defaults; page scope edits page-local overrides that inherit
// from global → ancestors → the current page. Editing in page scope writes an override (never
// the inherited binding); a Clear override button restores the inherited value.

import {loadEntities} from './api.js';
import {attachEntityAutocomplete} from './entity-autocomplete.js';
import {
  clearPageOverride,
  effectiveKeymapComplete,
  keymapMetadataComplete,
  normalizeBinding,
  pageTitle,
  resolveBinding,
  restoreKeymapDefaults,
  setGlobalBinding,
  setPageOverride,
} from './keymap-model.js';

const GROUPS = ['Navigation', 'Scrolling', 'Device', 'Media', 'System'];

function renderEncoderTurn(keyId, label) {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.keyId = keyId;
  button.textContent = label;
  return button;
}

// Pages in pre-order (parents before children, siblings in config order) so the scope
// chooser reads as a tree. The graph contract is enforced server-side; this traversal
// gracefully appends any stray page rather than dropping it.
function treeOrder(config) {
  const children = new Map();
  for (const page of config.pages || []) {
    const parent = page.parent || '';
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(page);
  }
  const ordered = [];
  const walk = list => list.forEach(page => {
    ordered.push(page);
    walk(children.get(page.page_id) || []);
  });
  walk(children.get('') || []);
  const seen = new Set(ordered.map(page => page.page_id));
  for (const page of config.pages || []) if (!seen.has(page.page_id)) ordered.push(page);
  return ordered;
}

export function mountKeymapEditor(element, store) {
  let autocomplete = null;
  // Rebuild only on structural change (scope/key/action/target/origin). Free-text
  // entity typing must never destroy the input mid-keystroke (P1.10), and a
  // failed save forces a full rebuild from the confirmed state (P1.12).
  let lastSignature = null;

  // Client-side mirror of the Binding entity validator (models.py): valid Home
  // Assistant entity ids, or Super-Productivity virtual task entities. Invalid
  // free-text stays local until it becomes valid — it never provokes a server
  // rejection per keystroke (P1.10 + P1.12).
  const ENTITY_ID_PATTERN = /^[a-z_][a-z0-9_]*\.[a-z0-9_]+$/;
  const SP_TASK_PREFIX = 'script.sp_start_';
  const isValidEntity = value => {
    if (!value) return true;
    if (ENTITY_ID_PATTERN.test(value)) return true;
    if (value.startsWith(SP_TASK_PREFIX) && /^[A-Za-z0-9_-]+$/.test(value.slice(SP_TASK_PREFIX.length))) return true;
    return false;
  };

  // Guard-railed restore of the whole key layout. Opening the modal writes nothing;
  // only the explicit "Restore defaults now" confirms it, so it cannot be triggered by
  // accident. Confirmation persists once, returns the scope to global, selects the home
  // key, closes, and announces the result. Mirrors the page-tree confirm dialog pattern.
  function openRestoreDialog() {
    const dialog = document.createElement('dialog');
    const title = document.createElement('h2');
    title.id = 'restore-title';
    title.textContent = 'Restore keymap defaults';
    dialog.setAttribute('aria-labelledby', 'restore-title');
    const message = document.createElement('p');
    message.textContent = 'Reset the global keymap to its defaults and clear every page override. Other configuration is kept.';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = 'Cancel restore';
    const confirmRestore = document.createElement('button');
    confirmRestore.type = 'button';
    confirmRestore.textContent = 'Restore defaults now';
    confirmRestore.addEventListener('click', async () => {
      const current = store.getState();
      await store.replaceConfig(restoreKeymapDefaults(current.config, current.meta));
      store.dispatch({type:'set-scope', scope:'global'});
      store.dispatch({type:'select-key', keyId:'r0c0'});
      dialog.close();
      if (store.getState().saveState === 'saved') {
        store.dispatch({type:'status', status:{kind:'success', message:'Keymap defaults restored.'}});
      }
    });
    cancel.addEventListener('click', () => dialog.close());
    dialog.appendChild(title);
    dialog.appendChild(message);
    dialog.appendChild(cancel);
    dialog.appendChild(confirmRestore);
    dialog.addEventListener('close', () => dialog.remove());
    element.appendChild(dialog);
    dialog.showModal();
  }

  function render(state) {
    const complete = keymapMetadataComplete(state.config, state.meta);
    const selectedKeyId = state.selectedKeyId;
    const keyIds = Array.isArray(state.meta.key_ids) ? state.meta.key_ids : [];
    const scopeComplete = effectiveKeymapComplete(state.config, state.keyScope, state.meta);
    const resolved = state.keyScope === 'global'
      ? {binding: normalizeBinding(state.config.global_keymap[selectedKeyId]), inherited: false, sourceScopeId: 'global'}
      : resolveBinding(state.config, state.keyScope, selectedKeyId);
    const effective = normalizeBinding(resolved.binding);
    const pagesSignature = (state.config.pages || []).map(
        page => `${page.page_id}:${page.parent}:${page.title}`
      ).join('|');
    const signature = `${complete}|${scopeComplete}|${state.keyScope}|${selectedKeyId}|${effective.action}|${effective.target_page}|${pagesSignature}|${resolved.inherited}|${resolved.sourceScopeId}`;
    if (lastSignature === signature && state.saveState !== 'error') return;
    lastSignature = signature;
    if (autocomplete) { autocomplete.destroy(); autocomplete = null; }
    element.replaceChildren();
    element.setAttribute('aria-label', 'Keymap editor');

    const metaHeading = document.createElement('h2');
    metaHeading.textContent = 'Keymap';
    element.appendChild(metaHeading);

    if (!complete) {
      const notice = document.createElement('p');
      notice.textContent = 'Keymap metadata is incomplete.';
      element.appendChild(notice);
      return;
    }

    // --- Global / page scope chooser ---
    const scopeLabel = document.createElement('label');
    scopeLabel.className = 'keymap-scope';
    const scopeText = document.createElement('span');
    scopeText.className = 'keymap-scope-label';
    scopeText.textContent = 'Keymap scope';
    const scope = document.createElement('select');
    scope.setAttribute('aria-label', 'Keymap scope');
    const globalOption = document.createElement('option');
    globalOption.value = 'global';
    globalOption.textContent = 'Global defaults';
    scope.appendChild(globalOption);
    treeOrder(state.config).forEach(page => {
      const option = document.createElement('option');
      option.value = page.page_id;
      // Indent with CSS padding, not literal NBSP text: a screen reader (and a
      // label-based <select> mutation) must read the option's accessible label as
      // the real page title, not '\u00A0\u00A0Downstairs lights'.
      option.textContent = page.title;
      option.style.paddingInlineStart = '1.5em';
      scope.appendChild(option);
    });
    scope.value = state.keyScope;
    scope.addEventListener('change', () => store.dispatch({type:'set-scope', scope: scope.value}));
    scopeLabel.appendChild(scopeText);
    scopeLabel.appendChild(scope);
    element.appendChild(scopeLabel);

    const restoreButton = document.createElement('button');
    restoreButton.type = 'button';
    restoreButton.className = 'restore-defaults';
    restoreButton.textContent = 'Restore keymap defaults';
    restoreButton.addEventListener('click', () => openRestoreDialog());
    element.appendChild(restoreButton);

    if (!scopeComplete) {
      const notice = document.createElement('p');
      notice.textContent = 'Effective keymap for this page is incomplete.';
      element.appendChild(notice);
      return;
    }

    // --- 3x4 matrix + two encoder turns ---
    const matrix = document.createElement('div');
    matrix.className = 'keypad';
    for (const keyId of keyIds.slice(0, 12)) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.keyId = keyId;
      button.setAttribute('aria-pressed', String(selectedKeyId === keyId));
      button.textContent = keyId === 'r0c3' ? 'Encoder push' : keyId.toUpperCase();
      button.addEventListener('click', () => store.dispatch({type:'select-key', keyId}));
      matrix.appendChild(button);
    }
    const encUp = renderEncoderTurn('enc_up', 'Encoder left');
    encUp.setAttribute('aria-pressed', String(selectedKeyId === 'enc_up'));
    encUp.addEventListener('click', () => store.dispatch({type:'select-key', keyId:'enc_up'}));
    const encDown = renderEncoderTurn('enc_down', 'Encoder right');
    encDown.setAttribute('aria-pressed', String(selectedKeyId === 'enc_down'));
    encDown.addEventListener('click', () => store.dispatch({type:'select-key', keyId:'enc_down'}));
    matrix.appendChild(encUp);
    matrix.appendChild(encDown);
    element.appendChild(matrix);

    // --- effective binding for the active scope ---
    const selectedBinding = resolved.binding;
    const origin = state.keyScope === 'global' ? 'global' : resolved.inherited ? 'ancestor' : 'override';

    // A mutation in global scope rewrites the shared default; in page scope it writes a
    // page-local override (which then overrides the inherited value in this scope). In page
    // scope, re-picking the effective (inherited) value is a no-op: writing a redundant
    // override would push a pointless pages[].keymap entry onto the wire.
    const writeBinding = binding => {
      const normalized = normalizeBinding(binding);
      const effective = normalizeBinding(resolved.binding);
      if (state.keyScope !== 'global'
          && normalized.action === effective.action
          && normalized.entity === effective.entity
          && normalized.target_page === effective.target_page) return;
      const current = store.getState();
      store.replaceConfig(state.keyScope === 'global'
        ? setGlobalBinding(current.config, selectedKeyId, binding)
        : setPageOverride(current.config, state.keyScope, selectedKeyId, binding));
    };

    // --- binding panel ---
    const bindingPanel = document.createElement('div');
    bindingPanel.className = 'binding-panel';
    bindingPanel.dataset.bindingOrigin = origin;
    const title = document.createElement('h3');
    title.textContent = selectedKeyId === 'r0c3'
      ? 'Encoder push' : selectedKeyId === 'enc_up' ? 'Encoder left'
        : selectedKeyId === 'enc_down' ? 'Encoder right' : selectedKeyId.toUpperCase();
    bindingPanel.appendChild(title);

    const originLabel = document.createElement('p');
    originLabel.className = 'binding-origin';
    originLabel.textContent = origin === 'global' ? 'Global binding'
      : origin === 'override'
        ? `Override on ${pageTitle(state.config, state.keyScope)}`
        : `Inherited from ${resolved.sourceScopeId === 'global' ? 'Global defaults' : pageTitle(state.config, resolved.sourceScopeId)}`;
    bindingPanel.appendChild(originLabel);

    const actionsMeta = Array.isArray(state.meta.actions) ? state.meta.actions : [];
    for (const groupName of GROUPS) {
      const groupActions = actionsMeta.filter(action => action.group === groupName);
      const fieldset = document.createElement('fieldset');
      fieldset.setAttribute('role', 'group');
      fieldset.setAttribute('aria-label', groupName);
      const legend = document.createElement('legend');
      legend.textContent = groupName;
      fieldset.appendChild(legend);
      groupActions.forEach(action => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.setAttribute('aria-pressed', String(selectedBinding.action === action.id));
        chip.textContent = action.label;
        chip.addEventListener('click', () => {
          writeBinding({action: action.id, entity: '', target_page: ''});
        });
        fieldset.appendChild(chip);
      });
      bindingPanel.appendChild(fieldset);
    }

    const actionMeta = actionsMeta.find(candidate => candidate.id === selectedBinding.action);
    const argument = actionMeta ? actionMeta.argument : 'none';

    if (argument === 'entity') {
      const label = document.createElement('label');
      label.className = 'binding-target';
      const text = document.createElement('span');
      text.textContent = 'Entity ID for binding';
      const input = document.createElement('input');
      input.type = 'text';
      input.setAttribute('aria-label', 'Entity ID for binding');
      input.value = selectedBinding.entity;
      label.appendChild(text);
      label.appendChild(input);
      const hint = document.createElement('span');
      hint.className = 'binding-hint';
      label.appendChild(hint);
      // P1.10: persist free-text entity on input/change/blur, not only on
      // autocomplete selection; discovery stays an optional convenience.
      const refreshHint = () => {
        const value = input.value.trim();
        if (!value) { hint.textContent = 'Choose an entity.'; hint.classList.remove('binding-error'); return; }
        if (isValidEntity(value)) { hint.textContent = ''; hint.classList.remove('binding-error'); }
        else { hint.textContent = 'Invalid entity id (use domain.entity).'; hint.classList.add('binding-error'); }
      };
      refreshHint();
      const commitEntity = () => {
        const value = input.value.trim();
        if (isValidEntity(value)) {
          writeBinding({...normalizeBinding(selectedBinding), entity: value});
        }
        // Invalid text stays local (and visible with the hint) and never reaches
        // the server, so typing never triggers a per-keystroke rollback.
        refreshHint();
      };
      input.addEventListener('input', commitEntity);
      input.addEventListener('change', commitEntity);
      input.addEventListener('blur', commitEntity);
      bindingPanel.appendChild(label);
      autocomplete = attachEntityAutocomplete(input, {
        getEntities: () => loadEntities(),
        onSelect: entityId => writeBinding({...normalizeBinding(selectedBinding), entity: entityId}),
        onError: error => store.dispatch({type:'status', status:{kind:'error', message:error.message}})
      });
    } else if (argument === 'target_page') {
      const label = document.createElement('label');
      label.className = 'binding-target';
      const text = document.createElement('span');
      text.textContent = 'Target page for binding';
      const select = document.createElement('select');
      select.setAttribute('aria-label', 'Target page for binding');
      const emptyOption = document.createElement('option');
      emptyOption.value = '';
      emptyOption.textContent = '';
      select.appendChild(emptyOption);
      (state.config.pages || []).forEach(page => {
        const option = document.createElement('option');
        option.value = page.page_id;
        option.textContent = page.title;
        select.appendChild(option);
      });
      select.value = selectedBinding.target_page;
      select.addEventListener('change', () => writeBinding({...normalizeBinding(selectedBinding), target_page: select.value}));
      label.appendChild(text);
      label.appendChild(select);
      if (!selectedBinding.target_page) {
        const hint = document.createElement('span');
        hint.className = 'binding-hint';
        hint.textContent = 'Choose a target page.';
        label.appendChild(hint);
      }
      bindingPanel.appendChild(label);
    }

    if (origin === 'override') {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.textContent = 'Clear override';
      clear.addEventListener('click', () => {
        store.replaceConfig(clearPageOverride(store.getState().config, state.keyScope, selectedKeyId));
      });
      bindingPanel.appendChild(clear);
    }

    element.appendChild(bindingPanel);
  }

  return {render};
}