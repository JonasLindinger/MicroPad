// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Home Assistant entity friendly-name autocomplete. ES module; window-free at import.

function label(entity) {
  return `${entity.friendly_name} — ${entity.entity_id}`;
}

export function matchEntities(entities, query, limit = 20) {
  const needle = query.trim().toLowerCase();
  if (!needle) return entities.slice(0, limit);
  const rank = entity => {
    const id = entity.entity_id.toLowerCase();
    const name = entity.friendly_name.toLowerCase();
    if (id === needle) return 0;
    if (id.startsWith(needle) || name.startsWith(needle)) return 1;
    if (id.includes(needle) || name.includes(needle)) return 2;
    return 3;
  };
  return entities.filter(entity => rank(entity) < 3)
    .sort((a, b) => rank(a) - rank(b) || a.friendly_name.localeCompare(b.friendly_name) || a.entity_id.localeCompare(b.entity_id))
    .slice(0, limit);
}

// Attach a combobox listbox to an entity input. Selection always writes entity_id
// (never the friendly name). On fetch failure the input stays free-text and only the
// safe API client message is surfaced. Returns { destroy } to detach listeners.
export function attachEntityAutocomplete(input, {getEntities, onSelect, onError}) {
  const root = input.closest('label') || input.parentElement;
  let listbox = null;
  let entities = null;
  let activeIndex = -1;
  let matches = [];

  function closeListbox() {
    if (!listbox) return;
    listbox.remove();
    listbox = null;
    activeIndex = -1;
    input.removeAttribute('aria-activedescendant');
    input.setAttribute('aria-expanded', 'false');
  }

  function ensureListbox() {
    if (listbox) return;
    listbox = document.createElement('ul');
    listbox.id = `${input.id || 'autocomplete'}-listbox-${Math.random().toString(36).slice(2)}`;
    listbox.setAttribute('role', 'listbox');
    listbox.setAttribute('aria-label', 'Matching Home Assistant entities');
    listbox.className = 'entity-listbox';
    // A combobox listbox is not phrasing content; inserting it inside a <label>
    // would fold it into the label's text and break label activation/accessibility.
    // Always place it as a SIBLING of the input's wrapping label (or container),
    // never as a child of the label. It stays scrollable and horizontally contained.
    if (root) {
      root.insertAdjacentElement('afterend', listbox);
    } else {
      input.after(listbox);
    }
    input.setAttribute('aria-controls', listbox.id);
    input.setAttribute('aria-expanded', 'true');
  }

  function renderOptions() {
    ensureListbox();
    listbox.replaceChildren();
    if (matches.length === 0) {
      const empty = document.createElement('li');
      empty.textContent = 'No matching Home Assistant entities.';
      empty.setAttribute('role', 'presentation');
      listbox.appendChild(empty);
      return;
    }
    matches.forEach((entity, index) => {
      const option = document.createElement('li');
      option.setAttribute('role', 'option');
      option.id = `${listbox.id}-opt-${index}`;
      option.setAttribute('aria-selected', String(index === activeIndex));
      option.textContent = label(entity);
      option.addEventListener('click', () => {
        select(entity.entity_id);
      });
      listbox.appendChild(option);
    });
    if (activeIndex >= 0 && activeIndex < matches.length) {
      input.setAttribute('aria-activedescendant', `${listbox.id}-opt-${activeIndex}`);
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function select(entityId) {
    input.value = entityId;
    onSelect(entityId);
    closeListbox();
  }

  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');

  const onInput = () => {
    if (!entities) return; // entities still loading or failed
    matches = matchEntities(entities, input.value);
    activeIndex = matches.length ? 0 : -1;
    renderOptions();
  };

  const onFocus = () => {
    if (entities === null) {
      Promise.resolve().then(() => getEntities()).then(list => {
        entities = list || [];
        onInput();
      }).catch(error => {
        entities = null;
        if (typeof onError === 'function') onError(error);
      });
    } else {
      onInput();
    }
  };

  const onKeydown = event => {
    if (event.key === 'ArrowDown' && matches.length) {
      event.preventDefault();
      activeIndex = Math.min(activeIndex + 1, matches.length - 1);
      renderOptions();
    } else if (event.key === 'ArrowUp' && matches.length) {
      event.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      renderOptions();
    } else if (event.key === 'Escape') {
      closeListbox();
    } else if (event.key === 'Enter' && matches[activeIndex]) {
      event.preventDefault();
      select(matches[activeIndex].entity_id);
    }
  };

  input.addEventListener('focus', onFocus);
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKeydown);
  document.addEventListener('click', onDocumentClick);

  function onDocumentClick(event) {
    if (event.target !== input && !(listbox && listbox.contains(event.target))) closeListbox();
  }

  return {
    destroy() {
      input.removeAttribute('role');
      input.removeAttribute('aria-autocomplete');
      input.removeAttribute('aria-expanded');
      input.removeAttribute('aria-controls');
      input.removeAttribute('aria-activedescendant');
      input.removeEventListener('focus', onFocus);
      input.removeEventListener('input', onInput);
      input.removeEventListener('keydown', onKeydown);
      document.removeEventListener('click', onDocumentClick);
      closeListbox();
    }
  };
}