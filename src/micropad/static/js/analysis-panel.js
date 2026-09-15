// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Budget meters and "what the device changes" findings.
//
// Both come from POST /api/lint, which uses the contract's ceilings and caps (the
// same numbers the pad reports on its retained micropad/device payload) plus the
// real generator for the byte sizes. The UI only formats numbers here — it never
// invents a limit, so a contract change moves the meters automatically.
//
// Why this matters: a page over its ceiling or a catalog over the 16 KiB MQTT
// buffer is silently ignored by the pad (PubSubClient drops the payload), and an
// over-long state simply appears truncated on the panel. Both used to be invisible
// until someone noticed the pad showing the wrong thing.

import { analyzeConfig } from './api.js';

const REQUEST_DEBOUNCE_MS = 250;

function meter(label, value, limit, unit = 'B') {
  const wrapper = document.createElement('div');
  wrapper.className = 'meter';
  const ratio = limit > 0 ? Math.min(1, value / limit) : 0;
  wrapper.classList.add(ratio >= 1 ? 'meter-over' : ratio >= 0.8 ? 'meter-warn' : 'meter-ok');
  const name = document.createElement('span');
  name.className = 'meter-label';
  name.textContent = label;
  const numbers = document.createElement('span');
  numbers.className = 'meter-value';
  numbers.textContent = `${value} / ${limit} ${unit}`.trim();
  const bar = document.createElement('span');
  bar.className = 'meter-bar';
  const fill = document.createElement('span');
  fill.className = 'meter-fill';
  fill.style.width = `${Math.round(ratio * 100)}%`;
  bar.appendChild(fill);
  wrapper.append(name, numbers, bar);
  return wrapper;
}

export function mountAnalysisPanel(element, store, { api = { analyzeConfig } } = {}) {
  if (element) {
    element.textContent = '';
    const heading = document.createElement('h2');
    heading.textContent = 'Budget and device changes';
    const status = document.createElement('p');
    status.className = 'analysis-status';
    status.setAttribute('role', 'status');
    const meters = document.createElement('div');
    meters.className = 'meters';
    const list = document.createElement('ul');
    list.className = 'findings';
    element.append(heading, meters, status, list);

    let timer = null;
    let lastKey = '';
    let lastState = null;

    const schedule = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(refresh, REQUEST_DEBOUNCE_MS);
    };

    function renderMeters(analysis, pageId) {
      meters.textContent = '';
      const limits = analysis.limits || {};
      const caps = analysis.caps || {};
      const pageBytes = (analysis.page_bytes || {})[pageId];
      if (typeof pageBytes === 'number') {
        meters.appendChild(meter(`Page "${pageId}"`, pageBytes, limits.page_bytes));
      }
      meters.appendChild(meter('Catalog', analysis.catalog_bytes || 0, limits.catalog_bytes));
      meters.appendChild(
        meter('MQTT buffer', analysis.catalog_bytes || 0, limits.mqtt_buffer_bytes)
      );
      const config = (lastState && lastState.config) || {};
      const pages = Array.isArray(config.pages) ? config.pages : [];
      meters.appendChild(meter('Pages', pages.length, caps.max_pages || 0, ''));
      const page = pages.find((entry) => entry.page_id === pageId) || pages[0];
      const items = page && Array.isArray(page.items) ? page.items.length : 0;
      meters.appendChild(meter('Items on page', items, caps.max_items_per_page || 0, ''));
    }

    function renderFindings(analysis) {
      list.textContent = '';
      const findings = analysis.findings || [];
      if (!findings.length) {
        const item = document.createElement('li');
        item.className = 'finding finding-ok';
        item.textContent = 'Nothing to change: the pad stores and shows this configuration as written.';
        list.appendChild(item);
        return;
      }
      findings.forEach((finding) => {
        const item = document.createElement('li');
        item.className = `finding finding-${finding.severity}`;
        const where = finding.where ? ` (${finding.where})` : '';
        item.textContent = `${finding.severity === 'error' ? 'Blocks publish' : 'Device changes it'}: ${finding.message}${where}`;
        list.appendChild(item);
      });
    }

    async function refresh() {
      if (!lastState) return;
      const config = lastState.config || {};
      const key = JSON.stringify([config, lastState.selectedPageId]);
      if (key === lastKey) return;
      lastKey = key;
      status.textContent = 'Analysing…';
      try {
        const analysis = await api.analyzeConfig(config);
        status.textContent = analysis.ok
          ? 'Publishable as configured.'
          : 'This configuration cannot be published until the errors above are fixed.';
        renderMeters(analysis, lastState.selectedPageId);
        renderFindings(analysis);
      } catch (error) {
        status.textContent = `Analysis unavailable: ${error.message}`;
        meters.textContent = '';
        list.textContent = '';
      }
    }

    return { render(state) {
      lastState = state;
      schedule();
    } };
  }
  return { render() {} };
}
