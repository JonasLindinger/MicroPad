// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Thin fetch client for the MicroPad API. ES module; window-free at import.
//
// Admin authentication (P0.1): the admin secret lives only in sessionStorage and
// is sent as an Authorization header, never placed in the URL, the persisted
// config, logs, or generated files. State-changing requests additionally carry
// an X-Requested-With header as a cross-site request guard.

const SECRET_KEYS = /token|password|authorization|ssh_key/i;
const ADMIN_TOKEN_KEY = 'micropad.admin';
const XHR = 'XMLHttpRequest';

function publicMessage(payload, status) {
  if (!payload || typeof payload !== 'object') return `Request failed (${status}).`;
  const copy = Object.fromEntries(Object.entries(payload).filter(([key]) => !SECRET_KEYS.test(key)));
  if (copy.error && typeof copy.error === 'object') {
    const message = typeof copy.error.message === 'string' ? copy.error.message : `Request failed (${status}).`;
    if (Array.isArray(copy.error.details) && copy.error.details.length) {
      const first = copy.error.details[0];
      const detail = first && typeof first === 'object'
        ? `${first.path || ''}: ${first.message || ''}`.trim()
        : String(first);
      if (detail) return `${message} (${detail})`;
    }
    return message;
  }
  return typeof copy.error === 'string' ? copy.error : `Request failed (${status}).`;
}

export function getAdminSecret() {
  try { return window.sessionStorage.getItem(ADMIN_TOKEN_KEY) || ''; }
  catch { return ''; }
}

export function setAdminSecret(secret) {
  try {
    if (secret) window.sessionStorage.setItem(ADMIN_TOKEN_KEY, secret);
    else window.sessionStorage.removeItem(ADMIN_TOKEN_KEY);
  } catch { /* sessionStorage unavailable: proceed anonymously */ }
}

async function request(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const token = getAdminSecret();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (method !== 'GET' && method !== 'HEAD') headers['X-Requested-With'] = XHR;
  const response = await fetch(path, { ...options, method, headers });
  const payload = await response.json().catch(() => null);
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('micropad:unauthorized', { detail: { status: response.status } }));
  }
  if (!response.ok) throw new Error(publicMessage(payload, response.status));
  return payload;
}

export const loadConfig = () => request('/api/config');
export const loadMeta = () => request('/api/meta');
export const saveConfig = config => request('/api/config', {method:'POST', body:JSON.stringify(config)});
export const loadEntities = () => request('/api/ha/entities');
export const validateConfig = config => request('/api/validate', {method:'POST', body:JSON.stringify(config)});
export const generate = () => request('/api/generate');
export const upload = mode => request(`/api/upload/${mode}`, {method:'POST', body:'{}'});
// Preview and lint: the panel model comes from the firmware core, the budgets and
// findings from the contract caps plus the real generator.
export const analyzeConfig = config => request('/api/lint', {method:'POST', body:JSON.stringify(config)});
// Page generation: the backend owns which domains are pageable and every ceiling.
export const loadEntityGroups = () => request('/api/entity-groups');
export const buildPages = payload => request('/api/build-pages', {method:'POST', body:JSON.stringify(payload)});
export const simulate = payload => request('/api/simulate', {method:'POST', body:JSON.stringify(payload)});