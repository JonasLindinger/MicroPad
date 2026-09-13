# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
import pytest
from pydantic import ValidationError

from micropad.constants import KEY_IDS
from micropad.models import AppConfig, Page, PageItem, default_config, parse_config, public_config
from micropad.page_graph import PageGraphError, ancestor_chain, index_pages, validate_page_graph


def test_default_config_has_one_home_and_full_global_map() -> None:
    config = default_config()
    assert [page.page_id for page in config.pages] == ["home"]
    assert tuple(config.global_keymap) == KEY_IDS
    assert config.settings.verify_tls is True
    assert config.settings.ha_token == ""


def test_parse_config_normalizes_ids_and_fills_missing_global_bindings() -> None:
    raw = default_config().model_dump(mode="json", by_alias=True)
    raw["pages"].append({
        "page_id": " Living Room ", "title": " Living Room ", "parent": "home",
        "items": [], "keymap": {},
    })
    raw["global_keymap"] = {"r0c0": {"action": "home"}}
    config = parse_config(raw)
    assert config.pages[1].page_id == "living-room"
    assert config.pages[1].title == "Living Room"
    assert tuple(config.global_keymap) == KEY_IDS


def test_public_config_redacts_token_and_private_key_path() -> None:
    config = default_config().model_copy(deep=True)
    config.settings.ha_token = "secret-token"  # noqa: S105 - synthetic redaction sentinel
    config.settings.ssh_key = "/home/private/id_ed25519"
    result = public_config(config)
    assert result["settings"]["ha_token"] == ""
    assert result["settings"]["ha_token_configured"] is True
    assert result["settings"]["ssh_key"] == ""
    assert result["settings"]["ssh_key_configured"] is True
    assert "secret-token" not in str(result)
    assert "/home/private/id_ed25519" not in str(result)


def test_unknown_action_is_rejected() -> None:
    raw = default_config().model_dump(mode="json", by_alias=True)
    raw["global_keymap"]["r0c0"]["action"] = "call_anything"
    with pytest.raises(ValidationError, match="action"):
        AppConfig.model_validate(raw)


def test_twenty_five_pages_are_rejected() -> None:
    raw = default_config().model_dump(mode="json", by_alias=True)
    raw["pages"] = [
        {"page_id": f"page-{index}", "title": f"Page {index}"}
        for index in range(25)
    ]
    with pytest.raises(ValidationError, match="pages"):
        AppConfig.model_validate(raw)


@pytest.mark.parametrize(
    "global_keymap",
    [
        {key_id: {"action": "none"} for key_id in KEY_IDS[:-1]},
        {"bogus": {"action": "none"}},
    ],
    ids=["incomplete", "unknown"],
)
def test_direct_validation_requires_exact_global_key_ids(
    global_keymap: dict[str, dict[str, str]],
) -> None:
    raw = default_config().model_dump(mode="json", by_alias=True)
    raw["global_keymap"] = global_keymap
    with pytest.raises(ValidationError, match="global_keymap"):
        AppConfig.model_validate(raw)


def test_parse_config_rejects_unknown_supplied_global_key() -> None:
    raw = default_config().model_dump(mode="json", by_alias=True)
    raw["global_keymap"] = {
        "r0c0": {"action": "home"},
        "bogus": {"action": "none"},
    }
    with pytest.raises(ValidationError, match="global_keymap"):
        parse_config(raw)


def test_graph_preserves_list_and_sibling_order() -> None:
    pages = [
        Page(page_id="home", title="Home"),
        Page(page_id="lights", title="Lights", parent="home"),
        Page(page_id="media", title="Media", parent="home"),
    ]

    validate_page_graph(pages)

    assert [page.page_id for page in pages] == ["home", "lights", "media"]
    assert [page.page_id for page in ancestor_chain(pages, "lights")] == ["home", "lights"]


def test_duplicate_page_ids_are_rejected() -> None:
    pages = [Page(page_id="home", title="Home"), Page(page_id="home", title="Duplicate")]

    with pytest.raises(PageGraphError, match="duplicate page_id: home"):
        index_pages(pages)


def test_graph_requires_exactly_one_home_page() -> None:
    pages = [Page(page_id="other", title="Other")]

    with pytest.raises(PageGraphError, match="exactly one home page"):
        validate_page_graph(pages)


def test_graph_rejects_unknown_parent() -> None:
    pages = [
        Page(page_id="home", title="Home"),
        Page(page_id="x", title="X", parent="missing"),
    ]

    with pytest.raises(PageGraphError, match="unknown parent"):
        validate_page_graph(pages)


def test_graph_rejects_unparented_non_home_page() -> None:
    pages = [
        Page(page_id="home", title="Home"),
        Page(page_id="x", title="X"),
    ]

    with pytest.raises(PageGraphError, match="non-home page must have a parent"):
        validate_page_graph(pages)


def test_graph_rejects_parent_cycle() -> None:
    pages = [
        Page(page_id="home", title="Home", parent="x"),
        Page(page_id="x", title="X", parent="home"),
    ]

    with pytest.raises(PageGraphError, match="cycle"):
        validate_page_graph(pages)


def test_graph_rejects_home_parent() -> None:
    pages = [
        Page(page_id="home", title="Home", parent="x"),
        Page(page_id="x", title="X"),
    ]

    with pytest.raises(PageGraphError, match="home page must not have a parent"):
        validate_page_graph(pages)


def test_graph_rejects_unknown_target_page() -> None:
    pages = [
        Page(
            page_id="home",
            title="Home",
            items=[PageItem(name="X", type="category", target_page="missing")],
        ),
    ]

    with pytest.raises(PageGraphError, match="unknown target_page"):
        validate_page_graph(pages)


def test_app_config_rejects_invalid_page_graph() -> None:
    raw = default_config().model_dump(mode="json", by_alias=True)
    raw["pages"].append({"page_id": "x", "title": "X", "parent": "missing"})

    with pytest.raises(ValidationError, match="unknown parent"):
        AppConfig.model_validate(raw)


def test_app_config_rejects_unparented_non_home_page() -> None:
    raw = default_config().model_dump(mode="json", by_alias=True)
    raw["pages"].append({"page_id": "x", "title": "X"})

    with pytest.raises(ValidationError, match="non-home page must have a parent"):
        AppConfig.model_validate(raw)
