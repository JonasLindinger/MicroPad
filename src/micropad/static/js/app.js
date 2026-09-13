// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Frontend module entry. ES module; does not touch the global `window` at import time.

import { assertMeta } from './contracts.js';
import { loadConfig, loadMeta, saveConfig, generate, upload } from './api.js';
import { createStore } from './store.js';
import { mountPageTree } from './page-tree.js';
import { mountItemEditor } from './item-editor.js';
import { mountKeymapEditor } from './keymap-editor.js';
import { mountTemplatePicker } from './template-picker.js';
import { mountOperations } from './operations.js';

function renderSaveStatus(state) {
  const element = document.getElementById('save-status');
  if (element) {
    element.textContent = state.saveState === 'saved' ? 'Saved' : state.saveState;
  }
}

function renderAppStatus(state) {
  const element = document.getElementById('app-status');
  if (element) {
    const message = state.status && state.status.kind === 'error' ? state.status.message : '';
    element.textContent = message || '';
  }
}

let pageTree = null;
let itemEditor = null;
let keymapEditor = null;
let templatePicker = null;
let operations = null;

function render(state) {
  renderSaveStatus(state);
  renderAppStatus(state);
  if (pageTree) pageTree.render(state);
  if (itemEditor) itemEditor.render(state);
  if (keymapEditor) keymapEditor.render(state);
  if (templatePicker) templatePicker.render(state);
  if (operations) operations.render(state);
}

document.addEventListener('DOMContentLoaded', () => {
  const status = document.getElementById('app-status');
  Promise.all([loadConfig(), loadMeta()])
    .then(([config, meta]) => {
      assertMeta(meta);
      const store = createStore({ config, meta, persist: saveConfig });
      pageTree = mountPageTree(document.getElementById('page-tree'), store);
      itemEditor = mountItemEditor(document.getElementById('item-editor'), store);
      keymapEditor = mountKeymapEditor(document.getElementById('keymap-editor'), store);
      templatePicker = mountTemplatePicker(document.getElementById('template-picker'), store);
      operations = mountOperations(document.getElementById('operations'), { store, api: { generate, upload } });
      store.subscribe(render);
    })
    .catch((error) => {
      if (status) status.textContent = error.message;
    });
});