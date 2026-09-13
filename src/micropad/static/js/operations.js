// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Operations region: Generate automation, Upload through Home Assistant API,
// Upload through SSH, and the Download API artifact / Download SSH script links.
// ES module; window-free at import.
//
// Every operation validates the current config first, disables ALL operation
// controls while busy, and renders only the server's `message`/`filename` or the
// API client's generic public error — never request bodies, settings, exception
// objects, or raw response JSON (which can embed tokens, passwords, or SSH keys).

import { validateConfig } from './api.js';

export function mountOperations(element, { store, api }) {
  const status = element.querySelector('#operation-status');
  const buttons = Array.from(element.querySelectorAll('button[data-operation]'));
  const downloadLinks = Array.from(element.querySelectorAll('a[data-download]'));
  const controls = [...buttons, ...downloadLinks];
  let busy = false;

  function setOperationState(kind, message) {
    status.textContent = message || '';
    status.dataset.kind = kind;
  }

  // Disable every operation control while one is running so a user cannot start
  // a second operation mid-flight. `<button>` elements get a real `disabled`
  // attribute; download `<a>` links are marked aria-disabled and refuse clicks.
  function setButtonsDisabled(disabled) {
    busy = disabled;
    for (const button of buttons) button.disabled = disabled;
    for (const link of downloadLinks) {
      if (disabled) link.setAttribute('aria-disabled', 'true');
      else link.removeAttribute('aria-disabled');
    }
  }

  async function runOperation(execute, successText) {
    setOperationState('busy', 'Working…');
    setButtonsDisabled(true);
    try {
      await validateConfig(store.getState().config);
      const result = await execute();
      setOperationState('success', successText(result));
    } catch (error) {
      setOperationState('error', error.message);
    } finally {
      setButtonsDisabled(false);
    }
  }

  element.querySelector('[data-operation="generate"]').addEventListener('click', () => {
    runOperation(() => api.generate(), (result) => `Automation generated: ${result.filename}`);
  });

  element.querySelector('[data-operation="upload-api"]').addEventListener('click', () => {
    runOperation(() => api.upload('api'), (result) => result.message);
  });

  element.querySelector('[data-operation="upload-ssh"]').addEventListener('click', () => {
    runOperation(() => api.upload('ssh'), (result) => result.message);
  });

  for (const link of downloadLinks) {
    link.addEventListener('click', (event) => {
      if (busy) event.preventDefault();
    });
  }

  // The operations' controls and status are event-driven and read config live
  // from the store at click time, so there is nothing store-driven to re-render
  // here. render() exists for the shared mount interface and is deliberately
  // non-destructive so transient result states are never clobbered.
  function render() {}

  return { render };
}