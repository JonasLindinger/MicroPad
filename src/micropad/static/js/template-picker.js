// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Template picker fed by API-supplied `meta.templates`. ES module; window-free at import.
// Cards render in metadata order; clicking one creates a child page under the selected
// page, deep-copies the template items, selects the new page, and never mutates the
// template object. A persistent live region announces the created page.

import { createPageFromTemplate } from './page-model.js';

export function mountTemplatePicker(element, store) {
  // Persistent live region, kept outside the re-rendered card grid so announcements
  // survive render() clearing and rebuilding the cards.
  const notice = document.createElement('p');
  notice.className = 'template-notice';
  notice.setAttribute('role', 'status');

  const cards = document.createElement('div');
  cards.className = 'template-cards';

  element.replaceChildren(notice, cards);

  function render(state) {
    cards.replaceChildren();
    for (const template of state.meta.templates || []) {
      const card = document.createElement('article');
      card.className = 'template-card';
      card.dataset.templateId = template.id;

      const title = document.createElement('h2');
      title.className = 'template-title';
      title.textContent = template.label;

      const description = document.createElement('p');
      description.className = 'template-description';
      description.textContent = template.description;

      const create = document.createElement('button');
      create.type = 'button';
      create.textContent = `Create ${template.label} page`;
      create.addEventListener('click', () => {
        const current = store.getState();
        const {config: nextConfig, pageId} = createPageFromTemplate(current.config, current.selectedPageId, template);
        store.replaceConfig(nextConfig);
        store.dispatch({type:'select-page', pageId});
        const titleInput = document.querySelector('#page-tree [aria-label="Page title"]');
        if (titleInput) titleInput.focus();
        notice.textContent = `${template.label} page created.`;
      });

      card.appendChild(title);
      card.appendChild(description);
      card.appendChild(create);
      cards.appendChild(card);
    }
  }

  return {render};
}