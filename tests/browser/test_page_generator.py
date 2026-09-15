# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Browser coverage for the page generator (B5).

The buttons come from ``/api/entity-groups`` (which domains are pageable and how
many entities each holds) and the drafts from ``/api/build-pages``. The mock builds
both with the real backend helpers, so the test proves the UI wiring — button →
request → merge into the open configuration — against the payload shapes the server
actually produces.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect


@pytest.mark.browser
def test_page_generator_offers_cached_domains(page, app_url):
    page.goto(app_url)
    expect(page.locator("#page-tree")).to_be_visible()
    button = page.locator("#page-generator .generator-group[data-domain='light']")
    expect(button).to_be_visible()
    # Two light entities sit in the fixture cache.
    assert button.text_content().strip() == "light (2)"


@pytest.mark.browser
def test_page_generator_adds_drafts_to_the_open_configuration(page, app_url, captured_requests):
    page.goto(app_url)
    expect(page.locator("#page-tree")).to_be_visible()
    page.locator("#page-generator .generator-group[data-domain='light']").click()
    expect(page.locator("#page-generator .generator-notice")).to_contain_text("page(s) added and saved")
    # The drafted page is in the tree and its items are editable, i.e. it became a
    # normal part of the open configuration rather than a separate preview.
    node = page.locator("#page-tree [data-page-id='light']")
    expect(node).to_be_visible()
    # The drafted page is selected and its items are real, editable cards: the first
    # card's name field carries the entity's friendly name.
    card = page.locator("#item-editor [data-item-index='0']")
    expect(card).to_be_visible()
    expect(card.locator("input").first).to_have_value("Desk Lamp")
    # The store autosaves edits, so the generated page must be in the saved payload:
    # a page that only exists in the browser would vanish on reload.
    saved = [call for call in captured_requests if call["path"] == "/api/config" and call["json"]]
    assert saved, captured_requests
    assert "light" in [page["page_id"] for page in saved[-1]["json"]["pages"]]


@pytest.mark.browser
def test_page_generator_can_refresh_the_entity_cache(page, app_url, captured_requests):
    page.goto(app_url)
    expect(page.locator("#page-tree")).to_be_visible()
    page.locator("#page-generator .generator-refresh").click()
    # The notice only appears after the refresh round-trip succeeded, and the group
    # buttons are rebuilt from the new cache.
    expect(page.locator("#page-generator .generator-notice")).to_contain_text("refreshed")
    expect(page.locator("#page-generator .generator-group[data-domain='light']")).to_be_visible()
