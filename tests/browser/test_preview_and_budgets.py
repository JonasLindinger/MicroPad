# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Browser coverage for the panel preview and the budget/device-change panel.

The preview draws the firmware core's prim model (recorded in
``tests/fixtures/frontend_panel.json`` from the real ``micropad_sim`` binary), so
these tests prove the browser renders a genuine model — including the selection
cursor — and that the meters come from the API rather than from client-side
guesses.
"""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect


@pytest.mark.browser
def test_panel_preview_draws_the_firmware_prim_model(page, app_url):
    page.goto(app_url)
    svg = page.locator("#panel-preview svg.panel-svg")
    expect(svg).to_be_visible()
    # Hardware resolution, monochrome panel: the viewBox is the panel geometry.
    assert svg.get_attribute("viewBox") == "0 0 296 128"
    # The recorded core output for the fixture page: title text, the selection
    # cursor triangle and the item's name.
    texts = [node.text_content() for node in page.locator("#panel-preview svg text").all()]
    assert "Home" in texts
    assert "Living room" in texts
    assert page.locator("#panel-preview svg polygon").count() == 1
    expect(page.locator("#panel-preview .preview-status")).to_contain_text("firmware 1.1.0")


@pytest.mark.browser
def test_panel_preview_can_show_the_setup_portal(page, app_url, captured_requests):
    page.goto(app_url)
    expect(page.locator("#panel-preview svg.panel-svg")).to_be_visible()
    page.get_by_label("show setup portal").check()
    # The toggle re-requests the model after the render debounce, so wait for the
    # second /api/simulate instead of racing it.
    deadline = time.time() + 5
    while time.time() < deadline:
        calls = [call for call in captured_requests if call["path"] == "/api/simulate"]
        if any("portal" in call["json"] for call in calls):
            break
        page.wait_for_timeout(100)
    calls = [call for call in captured_requests if call["path"] == "/api/simulate"]
    assert any("portal" in call["json"] for call in calls), calls
    # A portal request carries no page: the pad shows the portal instead of one.
    portal_call = next(call for call in calls if "portal" in call["json"])
    assert "page" not in portal_call["json"]
    expect(page.locator("#panel-preview svg.panel-svg")).to_be_visible()


@pytest.mark.browser
def test_budget_meters_and_findings_come_from_the_api(page, app_url, captured_requests):
    page.goto(app_url)
    meters = page.locator("#analysis-panel .meter")
    expect(meters.first).to_be_visible()
    labels = [node.text_content() for node in page.locator("#analysis-panel .meter-label").all()]
    assert 'Page "home"' in labels
    assert "Catalog" in labels
    assert "MQTT buffer" in labels
    assert "Pages" in labels
    values = [node.text_content() for node in page.locator("#analysis-panel .meter-value").all()]
    assert "500 / 8192 B" in values
    assert "1300 / 16000 B" in values
    expect(page.locator("#analysis-panel .analysis-status")).to_contain_text("Publishable")
    expect(page.locator("#analysis-panel .finding-ok")).to_be_visible()
    lint_calls = [call for call in captured_requests if call["path"] == "/api/lint"]
    assert lint_calls, "the meters must be driven by /api/lint"
    assert "pages" in lint_calls[-1]["json"]
