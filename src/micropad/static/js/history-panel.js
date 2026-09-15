// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Configuration history: list revisions, read the diff, restore one (B7).
//
// Every save leaves a revision beside the config file, so a bad edit is a restore
// instead of a re-typing session. The diff comes from the backend, which is also what
// decides that secrets are compared but never printed — this module renders text, it
// does not summarise configuration.
//
// Restoring overwrites the current configuration (which the store then saves), so it
// goes through a confirmation dialog, the same shape the keymap editor uses for
// "restore defaults".

import { loadHistory, loadRevision, restoreRevision } from './api.js';

function formatStamp(value) {
  if (!value) return 'unknown time';
  // The backend sends an ISO timestamp; show it as the operator's local time.
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export function mountHistoryPanel(
  element,
  store,
  { api = { loadHistory, loadRevision, restoreRevision } } = {}
) {
  if (!element) return { render() {} };

  const heading = document.createElement('h2');
  heading.textContent = 'Configuration history';

  const hint = document.createElement('p');
  hint.className = 'history-hint';
  hint.textContent = `The last revisions of this configuration, newest first. Secrets are never shown.`;

  const list = document.createElement('ul');
  list.className = 'history-list';

  const detail = document.createElement('div');
  detail.className = 'history-detail';

  const notice = document.createElement('p');
  notice.className = 'history-notice';
  notice.setAttribute('role', 'status');

  const dialog = document.createElement('dialog');
  dialog.setAttribute('aria-label', 'Restore configuration revision');
  const dialogText = document.createElement('p');
  const cancelButton = document.createElement('button');
  cancelButton.type = 'button';
  cancelButton.textContent = 'Cancel restore';
  const confirmButton = document.createElement('button');
  confirmButton.type = 'button';
  confirmButton.textContent = 'Restore now';
  dialog.append(dialogText, cancelButton, confirmButton);

  element.replaceChildren(heading, hint, notice, list, detail, dialog);

  let pending = null;
  let loaded = false;

  async function showDiff(snapshotId) {
    detail.replaceChildren();
    setNotice('Reading revision…');
    try {
      const revision = await api.loadRevision(snapshotId);
      const caption = document.createElement('p');
      caption.textContent = `Restoring ${snapshotId} would change:`;
      const changes = document.createElement('ul');
      changes.className = 'history-diff';
      (revision.diff || []).forEach((line) => {
        const item = document.createElement('li');
        item.textContent = line;
        changes.appendChild(item);
      });
      detail.append(caption, changes);
      setNotice('');
    } catch (error) {
      setNotice(`Could not read that revision: ${error.message}`);
    }
  }

  function askRestore(snapshotId) {
    pending = snapshotId;
    dialogText.textContent =
      `Restore ${snapshotId}? The current configuration is replaced by that revision.`;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function closeDialog() {
    pending = null;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  cancelButton.addEventListener('click', closeDialog);
  confirmButton.addEventListener('click', async () => {
    const snapshotId = pending;
    closeDialog();
    if (!snapshotId) return;
    setNotice(`Restoring ${snapshotId}…`);
    try {
      const result = await api.restoreRevision(snapshotId);
      if (result && result.config) store.replaceConfig(result.config);
      setNotice(
        `Restored ${snapshotId} (${(result && result.applied ? result.applied.length : 0)} change(s)).`
      );
      detail.replaceChildren();
      await refresh();
    } catch (error) {
      setNotice(`Restore failed: ${error.message}`);
    }
  });

  function setNotice(text) {
    notice.textContent = text;
  }

  async function refresh() {
    try {
      const payload = await api.loadHistory();
      const snapshots = (payload && payload.snapshots) || [];
      list.replaceChildren();
      if (!snapshots.length) {
        const empty = document.createElement('li');
        empty.textContent = 'No revisions recorded yet.';
        list.appendChild(empty);
        return;
      }
      snapshots.forEach((snapshot) => {
        const row = document.createElement('li');
        row.className = 'history-row';
        row.dataset.snapshotId = snapshot.id;

        const stamp = document.createElement('span');
        stamp.className = 'history-stamp';
        stamp.textContent = formatStamp(snapshot.created_at);

        const counts = document.createElement('span');
        counts.className = 'history-counts';
        counts.textContent = `${snapshot.pages} page(s), ${snapshot.items} item(s)`;

        const diffButton = document.createElement('button');
        diffButton.type = 'button';
        diffButton.textContent = 'Diff';
        diffButton.addEventListener('click', () => showDiff(snapshot.id));

        const restoreButton = document.createElement('button');
        restoreButton.type = 'button';
        restoreButton.textContent = 'Restore';
        restoreButton.addEventListener('click', () => askRestore(snapshot.id));

        row.append(stamp, counts, diffButton, restoreButton);
        list.appendChild(row);
      });
    } catch (error) {
      setNotice(`Could not read the history: ${error.message}`);
    }
  }

  return {
    render() {
      // Loaded once: re-reading the list on every store change would hammer the API
      // while the operator edits (and the list only changes when a save lands).
      if (!loaded) {
        loaded = true;
        refresh();
      }
    },
    refresh,
  };
}
