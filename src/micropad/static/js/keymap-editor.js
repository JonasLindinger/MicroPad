// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Fourteen-input keymap editor with a Global/Page scope chooser. ES module; window-free at import.
// Global scope edits the shared defaults; page scope edits page-local overrides that inherit
// from global → ancestors → the current page. Editing in page scope writes an override (never
// the inherited binding); a Clear override button restores the inherited value.

import {loadEntities} from './api.js';
import {attachEntityAutocomplete} from './entity-autocomplete.js';
import {
  bindingsEqual,
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

// What a key face shows: the key id and the *effective* action, plus where that
// action comes from. One place, so the board, its legend and the binding panel
// cannot describe the same binding differently.
function keyFace(keyId, state, actionLabels, store, selectedKeyId, suffix) {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.keyId = keyId;
  button.setAttribute('aria-pressed', String(selectedKeyId === keyId));

  const label = keyId.startsWith('enc_') ? `ENC ${suffix}` : keyId.toUpperCase();
  const idSpan = document.createElement('span');
  idSpan.className = 'key-id';
  idSpan.textContent = label;

  const actionSpan = document.createElement('span');
  actionSpan.className = 'key-action';

  let origin = 'global';
  let binding = null;
  if (state.keyScope === 'global') {
    binding = normalizeBinding(state.config.global_keymap && state.config.global_keymap[keyId]);
  } else {
    const resolved = resolveBinding(state.config, state.keyScope, keyId);
    binding = resolved.binding;
    origin = resolved.inherited ? 'inherited' : 'override';
  }
  const actionLabel = binding && binding.action !== 'none'
    ? (actionLabels.get(binding.action) || binding.action)
    : 'No action';
  actionSpan.textContent = actionLabel;
  button.dataset.bindingOrigin = origin;
  button.dataset.action = binding ? binding.action : 'none';
  // The gestures of this key, as a badge and in the tooltip: the board is the
  // only place that shows all fourteen keys at once, so "this key also has a
  // hold" has to be readable without clicking it.
  const gestures = [];
  if (binding && binding.hold && binding.hold.action !== 'none') gestures.push('hold');
  if (binding && binding.double && binding.double.action !== 'none') gestures.push('double');
  if (gestures.length) {
    const badge = document.createElement('span');
    badge.className = 'key-gestures';
    badge.textContent = gestures.map(name => (name === 'hold' ? 'H' : 'D')).join('+');
    badge.title = gestures
      .map(name => `${name}: ${actionLabels.get(binding[name].action) || binding[name].action}`)
      .join(' · ');
    button.appendChild(badge);
  }
  button.dataset.gestures = gestures.join(',');
  const detail = binding && binding.entity ? `${actionLabel} — ${binding.entity}` : actionLabel;
  button.title = gestures.length ? `${detail} (+ ${gestures.join(' + ')})` : detail;

  button.appendChild(idSpan);
  button.appendChild(actionSpan);
  button.addEventListener('click', () => store.dispatch({type:'select-key', keyId}));
  return button;
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
    // The gesture *actions* belong in the signature (a select change rebuilds the
    // panel so the matching target field appears); free-text gesture entities do
    // not, for the same reason the tap entity does not: rebuilding would destroy
    // the input being typed in.
    const signature = `${complete}|${scopeComplete}|${state.keyScope}|${selectedKeyId}|${effective.action}|${effective.target_page}|${effective.hold.action}|${effective.double.action}|${pagesSignature}|${resolved.inherited}|${resolved.sourceScopeId}`;
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

    // --- the physical board: 3x4 matrix + the two encoder turns ---
    // Laid out like the hardware (landscape, three rows of four) and labelled with
    // the *effective* binding for the current scope, so a page's keymap can be read
    // at a glance instead of clicking all fourteen keys. Each face carries
    // data-binding-origin (global / inherited / override) — the same notion the
    // binding panel below shows for the selected key.
    const actionLabels = new Map((state.meta.actions || []).map(action => [action.id, action.label]));
    const board = document.createElement('div');
    board.className = 'keyboard';
    board.setAttribute('role', 'group');
    board.setAttribute('aria-label', 'MicroPad board');

    const matrix = document.createElement('div');
    matrix.className = 'keypad';
    for (const keyId of keyIds.slice(0, 12)) {
      const key = keyFace(keyId, state, actionLabels, store, selectedKeyId);
      matrix.appendChild(key);
    }
    board.appendChild(matrix);

    const encoder = document.createElement('div');
    encoder.className = 'encoder';
    const encoderRing = document.createElement('div');
    encoderRing.className = 'encoder-ring';
    // The two encoder detents sit either side of the ring, so the label reads like
    // the wheel it is driven by.
    encoderRing.appendChild(keyFace('enc_up', state, actionLabels, store, selectedKeyId, 'Left'));
    encoderRing.appendChild(keyFace('enc_down', state, actionLabels, store, selectedKeyId, 'Right'));
    encoder.appendChild(encoderRing);
    board.appendChild(encoder);

    const legend = document.createElement('p');
    legend.className = 'keyboard-legend';
    legend.textContent = state.keyScope === 'global'
      ? 'Global scope: every key shows the shared default. Page scope shows overrides.'
      : 'Solid = override on this page · dimmed = inherited · the binding panel edits the selected key.';
    element.appendChild(board);
    element.appendChild(legend);

    // --- effective binding for the active scope ---
    const selectedBinding = resolved.binding;
    const origin = state.keyScope === 'global' ? 'global' : resolved.inherited ? 'ancestor' : 'override';

    // The binding as it is stored *right now*.
    //
    // Free-text typing deliberately does not re-render the panel (a rebuild would
    // destroy the field mid-keystroke), so a handler captured at render time can
    // hold a stale binding: "type the entity, then pick a hold action" would write
    // the binding back with the empty entity and silently undo the typing. Every
    // partial update therefore re-reads the live value instead of closing over the
    // render-time snapshot.
    const liveBinding = () => {
      const current = store.getState();
      const source = current.keyScope === 'global'
        ? current.config.global_keymap?.[selectedKeyId]
        : resolveBinding(current.config, current.keyScope, selectedKeyId).binding;
      return normalizeBinding(source);
    };

    // A mutation in global scope rewrites the shared default; in page scope it writes a
    // page-local override (which then overrides the inherited value in this scope). In page
    // scope, re-picking the effective (inherited) value is a no-op: writing a redundant
    // override would push a pointless pages[].keymap entry onto the wire.
    const writeBinding = binding => {
      const normalized = normalizeBinding(binding);
      if (state.keyScope !== 'global' && bindingsEqual(normalized, liveBinding())) return;
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
          // Re-picking the tap action must not silently drop the key's gestures:
          // they are a separate part of the same binding.
          writeBinding({
            ...liveBinding(),
            action: action.id, entity: '', target_page: '',
          });
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
          writeBinding({...liveBinding(), entity: value});
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
        onSelect: entityId => writeBinding({...liveBinding(), entity: entityId}),
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
      select.addEventListener('change', () => writeBinding({...liveBinding(), target_page: select.value}));
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

    // --- gestures: hold and double press on the same key -----------------
    // Each gesture is a full target of its own (action, entity, target page), so
    // a hold can turn a different device than the tap. Unset means "no gesture":
    // the key then behaves exactly as it did before gestures existed, which is
    // why the panel says so explicitly instead of hiding the block.
    const gestureMeta = new Map(
      (Array.isArray(state.meta.gesture_meta) ? state.meta.gesture_meta : [])
        .map(entry => [entry.id, entry])
    );
    const caps = state.meta.caps || {};
    const writeGesture = (gestureId, patch) => {
      const current = liveBinding();
      const next = {...current[gestureId], ...patch};
      // An unset gesture carries nothing: clearing the action clears the fields
      // that belonged to the action before it, so no stale entity is published.
      if (next.action === 'none') {
        next.entity = '';
        next.target_page = '';
      }
      writeBinding({...current, [gestureId]: next});
    };

    const gesturePanel = document.createElement('div');
    gesturePanel.className = 'gesture-panel';
    const gestureHeading = document.createElement('h3');
    gestureHeading.textContent = 'Gestures on this key';
    gesturePanel.appendChild(gestureHeading);

    for (const gestureId of ['hold', 'double']) {
      const definition = gestureMeta.get(gestureId);
      const label = definition ? definition.label : gestureId;
      const threshold = gestureId === 'hold' ? caps.hold_ms : caps.double_press_ms;
      const gesture = liveBinding()[gestureId];
      const bound = gesture.action !== 'none';

      const block = document.createElement('div');
      block.className = 'gesture-block';
      block.dataset.gesture = gestureId;

      const blockLabel = document.createElement('span');
      blockLabel.className = 'gesture-label';
      blockLabel.textContent = threshold ? `${label} (${threshold} ms)` : label;
      if (definition && definition.description) blockLabel.title = definition.description;
      block.appendChild(blockLabel);

      // Action picker, grouped the same way the tap chips are. A plain select
      // (not fourteen chips per gesture) keeps the panel readable at 768 px.
      const select = document.createElement('select');
      select.setAttribute('aria-label', `${label} action`);
      const noneOption = document.createElement('option');
      noneOption.value = 'none';
      noneOption.textContent = 'No action';
      select.appendChild(noneOption);
      for (const groupName of GROUPS) {
        const groupActions = actionsMeta.filter(action => action.group === groupName);
        if (!groupActions.length) continue;
        const group = document.createElement('optgroup');
        group.label = groupName;
        groupActions.forEach(action => {
          const option = document.createElement('option');
          option.value = action.id;
          option.textContent = action.label;
          group.appendChild(option);
        });
        select.appendChild(group);
      }
      select.value = gesture.action;
      select.addEventListener('change', () => writeGesture(gestureId, {action: select.value}));
      block.appendChild(select);

      const gestureArgument = (actionsMeta.find(action => action.id === gesture.action) || {}).argument;
      if (gestureArgument === 'entity') {
        const input = document.createElement('input');
        input.type = 'text';
        input.setAttribute('aria-label', `${label} entity ID`);
        input.value = gesture.entity;
        const hint = document.createElement('span');
        hint.className = 'binding-hint';
        const refreshHint = () => {
          const value = input.value.trim();
          if (!value) { hint.textContent = 'Choose an entity.'; hint.classList.remove('binding-error'); return; }
          if (isValidEntity(value)) { hint.textContent = ''; hint.classList.remove('binding-error'); }
          else { hint.textContent = 'Invalid entity id (use domain.entity).'; hint.classList.add('binding-error'); }
        };
        refreshHint();
        const commit = () => {
          const value = input.value.trim();
          // Invalid free text stays local (visible with its hint) instead of
          // provoking a server rejection per keystroke.
          if (isValidEntity(value)) writeGesture(gestureId, {entity: value});
          refreshHint();
        };
        input.addEventListener('input', commit);
        input.addEventListener('change', commit);
        input.addEventListener('blur', commit);
        block.appendChild(input);
        block.appendChild(hint);
      } else if (gestureArgument === 'target_page') {
        const selectPage = document.createElement('select');
        selectPage.setAttribute('aria-label', `${label} target page`);
        const emptyOption = document.createElement('option');
        emptyOption.value = '';
        emptyOption.textContent = '';
        selectPage.appendChild(emptyOption);
        (state.config.pages || []).forEach(page => {
          const option = document.createElement('option');
          option.value = page.page_id;
          option.textContent = page.title;
          selectPage.appendChild(option);
        });
        selectPage.value = gesture.target_page;
        selectPage.addEventListener('change', () => writeGesture(gestureId, {target_page: selectPage.value}));
        block.appendChild(selectPage);
      }

      const state_ = document.createElement('span');
      state_.className = 'gesture-state';
      state_.textContent = bound ? 'Bound' : 'Not used';
      block.appendChild(state_);

      if (bound) {
        const clearGesture = document.createElement('button');
        clearGesture.type = 'button';
        clearGesture.textContent = `Clear ${label.toLowerCase()}`;
        clearGesture.setAttribute('aria-label', `Clear ${label} gesture`);
        clearGesture.addEventListener('click', () => writeGesture(gestureId, {action: 'none'}));
        block.appendChild(clearGesture);
      }

      gesturePanel.appendChild(block);
    }

    const gestureNote = document.createElement('p');
    gestureNote.className = 'gesture-note';
    gestureNote.textContent = state.keyScope === 'global'
      ? 'Gestures are part of the binding. A key without a gesture keeps its single-press behaviour.'
      : 'Gestures are part of the binding and follow the same override/inherit rule as the tap action.';
    gesturePanel.appendChild(gestureNote);
    element.appendChild(gesturePanel);
  }

  return {render};
}