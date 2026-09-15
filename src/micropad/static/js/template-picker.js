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
        const parentId = current.selectedPageId;
        const parent = current.config.pages.find(page => page.page_id === parentId);
        const {config: nextConfig, pageId, linked} = createPageFromTemplate(
          current.config, parentId, template,
          {maxItemsPerPage: current.meta?.caps?.max_items_per_page},
        );
        store.replaceConfig(nextConfig);
        store.dispatch({type:'select-page', pageId});
        const titleInput = document.querySelector('#page-tree [aria-label="Page title"]');
        if (titleInput) titleInput.focus();
        // Say where the new page hangs: on the pad it is only reachable through the
        // parent's menu entry, so "created" alone would be misleading (and a page that
        // fits nowhere must not look like a success).
        notice.textContent = linked
          ? `${template.label} page created and linked from ${parent ? parent.title : parentId}.`
          : `${template.label} page created, but ${parent ? parent.title : parentId} is at the item limit — link it from another page.`;
      });

      card.appendChild(title);
      card.appendChild(description);
      card.appendChild(create);
      cards.appendChild(card);
    }
  }

  return {render};
}