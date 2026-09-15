// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Panel preview: draws the firmware's prim model as SVG.
//
// The prim list is produced by the *shipped* core (firmware/micropad_core.cpp via
// tools/micropad_sim.cpp) and fetched through POST /api/simulate, so this module
// contains no layout knowledge at all: no row height, no clip width, no status
// geometry. If the firmware moves a row, the picture moves. That is the whole
// point of routing the preview through the core instead of re-drawing the panel in
// JavaScript — a preview that is "close enough" is worse than none.
//
// The panel is monochrome (1 = white, 0 = black), 296x128, mirrored in the CSS
// so the SVG viewBox matches the hardware exactly.

import { simulate } from './api.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
// Mirrors CANVAS_WIDTH/CANVAS_HEIGHT in firmware/micropad_core.h; used only for
// the SVG viewBox, never for geometry of drawn content.
const PANEL_WIDTH = 296;
const PANEL_HEIGHT = 128;
const REQUEST_DEBOUNCE_MS = 250;

function element(name, attributes) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function colorOf(prim) {
  // color 0 is black ink, 1 is white (the panel is 1-bit).
  return prim.color === 1 ? '#ffffff' : '#000000';
}

function primToSvg(prim) {
  switch (prim.kind) {
    case 'line':
      return element('line', {
        x1: prim.x0, y1: prim.y0, x2: prim.x1, y2: prim.y1,
        stroke: colorOf(prim), 'stroke-width': 1,
      });
    case 'rect':
      return element('rect', {
        x: prim.x0, y: prim.y0, width: prim.w, height: prim.h,
        fill: 'none', stroke: colorOf(prim), 'stroke-width': 1,
      });
    case 'fillrect':
      return element('rect', {
        x: prim.x0, y: prim.y0, width: prim.w, height: prim.h, fill: colorOf(prim),
      });
    case 'circle':
      return element('circle', {
        cx: prim.x0, cy: prim.y0, r: prim.radius, fill: 'none',
        stroke: colorOf(prim), 'stroke-width': 1,
      });
    case 'fillcircle':
      return element('circle', {
        cx: prim.x0, cy: prim.y0, r: prim.radius, fill: colorOf(prim),
      });
    case 'filltriangle':
      return element('polygon', {
        points: `${prim.x0},${prim.y0} ${prim.x1},${prim.y1} ${prim.w},${prim.h}`,
        fill: colorOf(prim),
      });
    case 'text': {
      const node = element('text', {
        x: prim.x0,
        y: prim.y0 + Math.max(8, prim.h - 6),
        fill: colorOf(prim),
        // 'left' anchors at the prim's x, 'right' at the box's right edge: the
        // firmware's TextAlign maps onto SVG text-anchor directly.
        'text-anchor': prim.align === 'right' ? 'end' : 'start',
      });
      node.textContent = prim.text || '';
      return node;
    }
    default:
      return null;
  }
}

function buildSvg(model, { portal }) {
  const svg = element('svg', {
    viewBox: `0 0 ${PANEL_WIDTH} ${PANEL_HEIGHT}`,
    width: PANEL_WIDTH,
    height: PANEL_HEIGHT,
    role: 'img',
    'aria-label': portal ? 'Setup portal as drawn on the panel' : 'Page as drawn on the panel',
    class: 'panel-svg',
  });
  svg.appendChild(element('rect', {
    x: 0, y: 0, width: PANEL_WIDTH, height: PANEL_HEIGHT, fill: '#ffffff',
  }));
  (model.prims || []).forEach((prim) => {
    const node = primToSvg(prim);
    if (node) svg.appendChild(node);
  });
  return svg;
}

export function mountPanelPreview(element, store, { api = { simulate } } = {}) {
  if (element) {
    element.textContent = '';
    const heading = document.createElement('h2');
    heading.textContent = 'Panel preview';
    const note = document.createElement('p');
    note.className = 'preview-note';
    note.textContent = 'Rendered by the firmware core (micropad_sim), not re-drawn in the browser.';
    const host = document.createElement('div');
    host.className = 'panel-host';
    const status = document.createElement('p');
    status.className = 'preview-status';
    status.setAttribute('role', 'status');
    const findings = document.createElement('ul');
    findings.className = 'preview-findings';
    element.append(heading, note, host, status, findings);

    let selected = 0;
    let portal = false;
    let timer = null;
    let lastKey = '';
    let lastState = null;

    const portalToggle = document.createElement('label');
    portalToggle.className = 'preview-toggle';
    const portalBox = document.createElement('input');
    portalBox.type = 'checkbox';
    portalToggle.append(portalBox, document.createTextNode(' show setup portal'));
    element.insertBefore(portalToggle, host);
    portalBox.addEventListener('change', () => {
      portal = portalBox.checked;
      lastKey = '';
      schedule();
    });

    function currentPage(state) {
      const config = state.config || {};
      const pages = Array.isArray(config.pages) ? config.pages : [];
      return pages.find((page) => page.page_id === state.selectedPageId) || pages[0] || null;
    }

    function payloadFor(state) {
      if (portal) {
        // The device generates its own portal password from hardware entropy and
        // shows it on the panel; the browser never knows it, so the preview uses
        // the documented placeholder (release/allowed-public-values.json).
        return { portal: { ssid: 'MicroPad-Setup', password: 'replace-me', address: '192.168.4.1' } };
      }
      const page = currentPage(state);
      if (!page) return null;
      return {
        page,
        state: { selected: Math.min(selected, Math.max(0, (page.items || []).length - 1)), first_visible: 0 },
        network: 4,
        usb_host: true,
        key_id: (state.meta && state.meta.key_ids ? state.meta.key_ids[3] : null) || null,
        keys: Object.entries(page.keymap || {}).map(([key_id, binding]) => ({ key_id, ...binding })),
      };
    }

    function schedule() {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(refresh, REQUEST_DEBOUNCE_MS);
    }

    async function refresh() {
      if (!lastState) return;
      const payload = payloadFor(lastState);
      if (!payload) return;
      const key = JSON.stringify(payload);
      if (key === lastKey) return; // nothing changed since the last render
      lastKey = key;
      status.textContent = 'Rendering…';
      try {
        const result = await api.simulate(payload);
        if (result && result.ok === false) {
          status.textContent = result.error || 'The firmware core rejected this page.';
          host.textContent = '';
          return;
        }
        const model = { prims: result.prims || [] };
        host.textContent = '';
        host.appendChild(buildSvg(model, { portal }));
        const firmware = result.firmware ? ` (firmware ${result.firmware})` : '';
        status.textContent = result.prims_truncated
          ? `Preview truncated at the prim budget${firmware}.`
          : `Preview from the firmware core${firmware}.`;
        findings.textContent = '';
        (result.findings || []).forEach((finding) => {
          const item = document.createElement('li');
          item.className = `finding finding-${finding.severity}`;
          item.textContent = `${finding.field}: ${finding.code} (cap ${finding.cap}, got ${finding.chars})`;
          findings.appendChild(item);
        });
        if (result.would_reject) {
          const item = document.createElement('li');
          item.className = 'finding finding-error';
          item.textContent = 'The pad would reject this payload (identifier too long).';
          findings.appendChild(item);
        }
      } catch (error) {
        // Never fake a preview: say the model is unavailable.
        host.textContent = '';
        status.textContent = `Preview unavailable: ${error.message}`;
      }
    }

    return {
      render(state) {
        lastState = state;
        schedule();
      },
      // Row clicks move the cursor so the preview can show the selected row and
      // what a press would do there.
      selectRow(index) {
        selected = index;
        lastKey = '';
        schedule();
      },
    };
  }
  return { render() {} };
}
