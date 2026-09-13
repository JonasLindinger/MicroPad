// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Thin fetch client for the MicroPad API. ES module; window-free at import.

const SECRET_KEYS = /token|password|authorization|ssh_key/i;

function publicMessage(payload, status) {
  if (!payload || typeof payload !== 'object') return `Request failed (${status}).`;
  const copy = Object.fromEntries(Object.entries(payload).filter(([key]) => !SECRET_KEYS.test(key)));
  return typeof copy.error === 'string' ? copy.error : `Request failed (${status}).`;
}

async function request(path, options = {}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const payload = await response.json().catch(() => null);
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