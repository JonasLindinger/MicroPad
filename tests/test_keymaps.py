# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
import pytest

from micropad.constants import KEY_IDS
from micropad.keymaps import clear_page_override, effective_keymap
from micropad.models import Binding, Page, default_config


def test_effective_map_merges_global_root_parent_and_current() -> None:
    config = default_config().model_copy(deep=True)
    config.pages = [
        Page(page_id="home", title="Home", keymap={"r1c0": Binding(action="settings")}),
        Page(page_id="room", title="Room", parent="home", keymap={"r1c0": Binding(action="back")}),
        Page(
            page_id="lamp",
            title="Lamp",
            parent="room",
            keymap={"r2c0": Binding(action="toggle", entity="light.desk")},
        ),
    ]

    result = effective_keymap(config, "lamp")

    assert tuple(result) == KEY_IDS
    assert result["r0c0"].action == "home"
    assert result["r1c0"].action == "back"
    assert result["r2c0"] == Binding(action="toggle", entity="light.desk")


def test_clearing_override_restores_inherited_binding_without_mutation() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].keymap["r0c0"] = Binding(action="settings")

    cleared = clear_page_override(config, "home", "r0c0")

    assert "r0c0" not in cleared.pages[0].keymap
    assert effective_keymap(cleared, "home")["r0c0"].action == "home"
    assert config.pages[0].keymap["r0c0"].action == "settings"


@pytest.mark.parametrize(
    ("page_id", "key_id", "message"),
    [("missing", "r0c0", "unknown page_id"), ("home", "r9c9", "unknown key ID")],
)
def test_clear_override_rejects_unknown_identifiers(
    page_id: str, key_id: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        clear_page_override(default_config(), page_id, key_id)
