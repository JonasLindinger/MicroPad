// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Shared frontend contracts with the MicroPad backend. ES module; window-free at import.

export const KEY_IDS = Object.freeze([
  'r0c0','r0c1','r0c2','r0c3','r1c0','r1c1','r1c2','r1c3',
  'r2c0','r2c1','r2c2','r2c3','enc_up','enc_down'
]);

export function assertMeta(meta) {
  if (meta.home_page_id !== 'home' || JSON.stringify(meta.key_ids) !== JSON.stringify(KEY_IDS)) {
    throw new Error('Server metadata does not match the fourteen-input MicroPad contract.');
  }
}