// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Frontend module entry. ES module; does not touch the global `window` at import time.
//
// Admin authentication: on 401 the UI invites the operator to supply the admin
// secret, which is stored only in sessionStorage (never in the URL, config,
// logs, or generated files) and sent as an Authorization header on later calls.

import { assertMeta } from './contracts.js';
import {
  loadConfig, loadMeta, saveConfig, setAdminSecret, generate, upload,
} from './api.js';
import { createStore } from './store.js';
import { mountPageTree } from './page-tree.js';
import { mountItemEditor } from './item-editor.js';
import { mountKeymapEditor } from './keymap-editor.js';
import { mountTemplatePicker } from './template-picker.js';
import { mountOperations } from './operations.js';
import { mountPanelPreview } from './panel-preview.js';
import { mountAnalysisPanel } from './analysis-panel.js';

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
let panelPreview = null;
let analysisPanel = null;

function render(state) {
  renderSaveStatus(state);
  renderAppStatus(state);
  if (pageTree) pageTree.render(state);
  if (itemEditor) itemEditor.render(state);
  if (keymapEditor) keymapEditor.render(state);
  if (templatePicker) templatePicker.render(state);
  if (operations) operations.render(state);
  if (panelPreview) panelPreview.render(state);
  if (analysisPanel) analysisPanel.render(state);
}

const authDialog = () => document.getElementById('auth-dialog');
const authSecret = () => document.getElementById('auth-secret');
const authError = () => document.getElementById('auth-error');
const appStatus = () => document.getElementById('app-status');

function showAuthDialog(secretRejected = false) {
  const dialog = authDialog();
  if (!dialog || !dialog.hidden) return;
  if (secretRejected) {
    const error = authError();
    if (error) {
      error.textContent = 'Administrator secret was not accepted.';
      error.hidden = false;
    }
  }
  dialog.hidden = false;
  const input = authSecret();
  if (input) input.focus();
}

function hideAuthDialog() {
  const dialog = authDialog();
  if (dialog) dialog.hidden = true;
  const error = authError();
  if (error) error.hidden = true;
}

function startApp() {
  renderSaveStatus({ saveState: 'Loading' });
  Promise.all([loadConfig(), loadMeta()])
    .then(([config, meta]) => {
      assertMeta(meta);
      hideAuthDialog();
      const store = createStore({ config, meta, persist: saveConfig });
      pageTree = mountPageTree(document.getElementById('page-tree'), store);
      itemEditor = mountItemEditor(document.getElementById('item-editor'), store);
      keymapEditor = mountKeymapEditor(document.getElementById('keymap-editor'), store);
      templatePicker = mountTemplatePicker(document.getElementById('template-picker'), store);
      operations = mountOperations(document.getElementById('operations'), { store, api: { generate, upload } });
      panelPreview = mountPanelPreview(document.getElementById('panel-preview'), store);
      analysisPanel = mountAnalysisPanel(document.getElementById('analysis-panel'), store);
      store.subscribe(render);
    })
    .catch((error) => {
      if (appStatus()) appStatus().textContent = error.message;
    });
}

window.addEventListener('micropad:unauthorized', () => {
  const alreadyShown = authDialog() && !authDialog().hidden;
  showAuthDialog(alreadyShown);
});

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('auth-form');
  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const value = (authSecret()?.value || '').trim();
      if (!value) return;
      setAdminSecret(value);
      hideAuthDialog();
      if (appStatus()) appStatus().textContent = '';
      // Rejected secrets still produce a 401, which re-opens the dialog.
      startApp();
    });
  }
  startApp();
});