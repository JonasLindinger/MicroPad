// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// Observable store and pure UI-state reducer. ES module; window-free and owns all mutations.

export function reduceUiState(state, action) {
  if (action.type === 'select-page') return {...state, selectedPageId:action.pageId};
  if (action.type === 'select-key') return {...state, selectedKeyId:action.keyId};
  if (action.type === 'set-scope') return {...state, keyScope:action.scope};
  if (action.type === 'status') return {...state, status:action.status};
  return state;
}

export function createStore({config, meta, persist}) {
  let state = {config, meta, selectedPageId:meta.home_page_id, selectedKeyId:'r0c0', keyScope:'global', entities:[], entitiesState:'idle', saveState:'saved', operationState:'idle', status:null};
  const listeners = new Set();
  let saveChain = Promise.resolve();
  let revision = 0;
  const emit = () => listeners.forEach(listener => listener({...state}));
  return {
    getState: () => ({...state}),
    subscribe(listener) { listeners.add(listener); listener({...state}); return () => listeners.delete(listener); },
    dispatch(action) { state = reduceUiState(state, action); emit(); },
    replaceConfig(nextConfig) {
      const previous = state.config;
      const ownRevision = ++revision;
      state = {...state, config:nextConfig, saveState:'saving'}; emit();
      saveChain = saveChain.then(() => persist(nextConfig)).then(saved => {
        if (ownRevision === revision) { state = {...state, config:saved, saveState:'saved'}; emit(); }
      }).catch(error => {
        if (ownRevision === revision) { state = {...state, config:previous, saveState:'error', status:{kind:'error', message:error.message}}; emit(); }
      });
      return saveChain;
    }
  };
}