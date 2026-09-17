// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Observable store and pure UI-state reducer. ES module; window-free and owns all mutations.
// Rollback (P1.12) always restores the LAST CONFIRMED server state, and draft
// items (P1.8) stay out of the persisted config until they are complete.

export function makeDraftItem() {
  return {
    name: '', type: 'sensor', entity: '', state: '',
    value: 0, min: 0, max: 100, step: 1, unit: '', editable: false,
    control: 'button', target_page: '',
  };
}

export function reduceUiState(state, action) {
  if (action.type === 'select-page') return {...state, selectedPageId:action.pageId};
  if (action.type === 'select-key') return {...state, selectedKeyId:action.keyId};
  if (action.type === 'set-scope') return {...state, keyScope:action.scope};
  if (action.type === 'status') return {...state, status:action.status};
  if (action.type === 'draft-add') return {...state, drafts:[...state.drafts, makeDraftItem()]};
  if (action.type === 'draft-update') return {
    ...state,
    drafts: state.drafts.map((draft, index) => index === action.index ? {...draft, ...action.patch} : draft),
  };
  if (action.type === 'draft-remove') return {
    ...state,
    drafts: state.drafts.filter((_, index) => index !== action.index),
  };
  if (action.type === 'draft-commit') return {
    ...state,
    drafts: state.drafts.filter((_, index) => index !== action.index),
  };
  return state;
}

export function createStore({config, meta, persist}) {
  let state = {config, meta, selectedPageId:meta.home_page_id, selectedKeyId:'r0c0', keyScope:'global', entities:[], entitiesState:'idle', saveState:'saved', operationState:'idle', status:null, drafts:[]};
  // The last state the server CONFIRMED; failed saves roll back to this, never
  // to an unconfirmed local intermediate (P1.12).
  let confirmedConfig = config;
  const listeners = new Set();
  let saveChain = Promise.resolve();
  let revision = 0;
  const emit = () => listeners.forEach(listener => listener({...state}));
  return {
    getState: () => ({...state}),
    subscribe(listener) { listeners.add(listener); listener({...state}); return () => listeners.delete(listener); },
    dispatch(action) { state = reduceUiState(state, action); emit(); },
    replaceConfig(nextConfig) {
      const ownRevision = ++revision;
      state = {...state, config:nextConfig, saveState:'saving'}; emit();
      saveChain = saveChain.then(() => persist(nextConfig)).then(saved => {
        if (ownRevision === revision) {
          confirmedConfig = saved;
          state = {...state, config:saved, saveState:'saved'}; emit();
        }
      }).catch(error => {
        if (ownRevision === revision) {
          state = {...state, config:confirmedConfig, saveState:'error', status:{kind:'error', message:error.message}}; emit();
        }
      });
      return saveChain;
    }
  };
}