# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Complete effective keymaps with deterministic page inheritance."""

from micropad.constants import KEY_IDS
from micropad.models import AppConfig, Binding
from micropad.page_graph import ancestor_chain


def effective_keymap(config: AppConfig, page_id: str) -> dict[str, Binding]:
    """Return a detached full keymap after applying root-to-page overrides."""
    merged = {
        key_id: config.global_keymap[key_id].model_copy(deep=True)
        for key_id in KEY_IDS
    }
    for page in ancestor_chain(config.pages, page_id):
        for key_id, binding in page.keymap.items():
            merged[key_id] = binding.model_copy(deep=True)
    return {key_id: merged[key_id] for key_id in KEY_IDS}


def clear_page_override(config: AppConfig, page_id: str, key_id: str) -> AppConfig:
    """Return a detached configuration without one page-local key override."""
    if key_id not in KEY_IDS:
        raise ValueError(f"unknown key ID: {key_id}")
    result = config.model_copy(deep=True)
    page = next((candidate for candidate in result.pages if candidate.page_id == page_id), None)
    if page is None:
        raise ValueError(f"unknown page_id: {page_id}")
    page.keymap.pop(key_id, None)
    return result
