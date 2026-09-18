// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Player queue editor: the list of videos the pad's `player` action can stream.
// ES module; window-free at import.
//
// Incomplete rows (missing name or URL) are kept as local drafts and never
// persisted — the config schema requires both fields, so persisting a half
// typed row would fail the save for the whole configuration. A row becomes
// part of the configuration the moment both fields are filled in.

const MAX_ROWS_FALLBACK = 32;

export function mountPlayerEditor(element, store) {
  const notice = document.createElement('p');
  notice.className = 'player-notice';
  notice.setAttribute('role', 'status');

  const list = document.createElement('div');
  list.className = 'player-rows';

  const addButton = document.createElement('button');
  addButton.type = 'button';
  addButton.className = 'player-add';
  addButton.textContent = 'Add video';

  let rows = null;      // [{name, url}] — the on-screen rows, drafts included
  let noticeText = '';
  let saveTimer = null;

  element.replaceChildren(notice, list, addButton);

  function maxRows() {
    return store.getState().meta?.caps?.max_player_videos ?? MAX_ROWS_FALLBACK;
  }

  // Typing persists through a short debounce: one save for a burst of keys
  // instead of one request per character.
  function persistSoon() {
    if (saveTimer !== null) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      persist();
    }, 400);
  }

  function configRows() {
    const state = store.getState();
    return Array.isArray(state.config.player_videos) ? state.config.player_videos : [];
  }

  function complete(row) {
    return Boolean(row.name && row.name.trim() && row.url && row.url.trim());
  }

  function persist() {
    const next = rows.filter(complete).map(row => ({
      name: row.name.trim(),
      url: row.url.trim(),
    }));
    const current = configRows();
    const unchanged = next.length === current.length
      && next.every((row, index) => row.name === current[index].name && row.url === current[index].url);
    if (!unchanged) store.replaceConfig({...store.getState().config, player_videos: next});
  }

  function move(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= rows.length) return;
    const copy = rows.slice();
    [copy[index], copy[target]] = [copy[target], copy[index]];
    rows = copy;
    persist();
    render(store.getState());
  }

  function render(state) {
    // Rows are rebuilt from the configuration only when no local draft is in
    // flight; otherwise a re-render (typing elsewhere) would drop the draft.
    const stored = Array.isArray(state.config.player_videos) ? state.config.player_videos : [];
    if (rows === null || rows.length === stored.length
        && rows.every(row => complete(row))) {
      rows = stored.map(row => ({name: row.name, url: row.url}));
    }

    notice.textContent = noticeText;
    list.replaceChildren();
    rows.forEach((row, index) => {
      const line = document.createElement('div');
      line.className = 'player-row';
      line.dataset.index = String(index);

      const name = document.createElement('input');
      name.type = 'text';
      name.placeholder = 'Name (shown on the pad)';
      name.setAttribute('aria-label', 'Video name');
      name.maxLength = 64;
      name.value = row.name;
      name.addEventListener('input', () => {
        rows[index] = {...rows[index], name: name.value};
        persistSoon();
      });

      const url = document.createElement('input');
      url.type = 'text';
      url.placeholder = 'https://www.youtube.com/watch?v=...';
      url.setAttribute('aria-label', 'Video URL');
      url.maxLength = 512;
      url.value = row.url;
      url.addEventListener('input', () => {
        rows[index] = {...rows[index], url: url.value};
        persistSoon();
      });

      const up = document.createElement('button');
      up.type = 'button';
      up.textContent = 'Up';
      up.disabled = index === 0;
      up.addEventListener('click', () => move(index, -1));

      const down = document.createElement('button');
      down.type = 'button';
      down.textContent = 'Down';
      down.disabled = index === rows.length - 1;
      down.addEventListener('click', () => move(index, +1));

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Remove';
      remove.addEventListener('click', () => {
        rows.splice(index, 1);
        persist();
        render(store.getState());
      });

      line.append(name, url, up, down, remove);
      list.appendChild(line);
    });

    addButton.disabled = rows.length >= maxRows();
  }

  addButton.addEventListener('click', () => {
    if (rows.length >= maxRows()) {
      noticeText = `At most ${maxRows()} videos are supported.`;
      render(store.getState());
      return;
    }
    noticeText = 'Fill in name and URL — the entry is saved once both are set.';
    rows.push({name: '', url: ''});
    render(store.getState());
  });

  return {render};
}