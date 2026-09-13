# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

"""Browser operations: generate, upload, download with busy states and
secret-safe result rendering. Every operation validates first, disables all
operation controls while busy, and renders only the server message/filename or
the API client's generic public error — never tokens, request bodies, settings,
exception objects, or raw response JSON."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect


@pytest.mark.browser
def test_generate_and_upload_show_busy_then_success(page, app_url):
    page.goto(app_url)
    generate = page.get_by_role("button", name="Generate automation")
    generate.click()
    expect(generate).to_be_disabled()
    expect(page.locator("#operation-status")).to_have_text("Automation generated: micropad_controller.yaml")
    page.get_by_role("button", name="Upload through Home Assistant API").click()
    expect(page.locator("#operation-status")).to_have_text("Home Assistant upload verified.")
    page.get_by_role("button", name="Upload through SSH").click()
    expect(page.locator("#operation-status")).to_have_text("SSH upload verified.")
    expect(page.get_by_role("link", name="Download API artifact")).to_have_attribute("href", "/api/download/api")


@pytest.mark.browser
def test_generate_error_never_renders_token(page, app_url):
    page.route("**/api/upload/api", lambda route: route.fulfill(
        status=500,
        content_type="application/json",
        body='{"token":"super-secret-token","details":{"authorization":"Bearer super-secret-token"}}',
    ))
    page.goto(app_url)
    page.get_by_role("button", name="Upload through Home Assistant API").click()
    body = page.locator("body")
    expect(body).to_contain_text("Request failed (500).")
    expect(body).not_to_contain_text("super-secret-token")


@pytest.mark.browser
def test_validation_error_is_visible_and_prevents_generation(page, app_url):
    page.route("**/api/validate", lambda route: route.fulfill(
        status=422,
        content_type="application/json",
        body='{"error":"Page title is required."}',
    ))
    page.goto(app_url)
    page.get_by_role("button", name="Generate automation").click()
    expect(page.locator("#operation-status")).to_have_text("Page title is required.")


@pytest.mark.browser
def test_tablet_layout_has_no_horizontal_scroll_and_keeps_keymap_visible(page, app_url):
    page.set_viewport_size({"width": 768, "height": 1024})
    page.goto(app_url)
    expect(page.locator("#page-tree")).to_be_visible()
    expect(page.locator("#item-editor")).to_be_visible()
    expect(page.locator("#keymap-editor")).to_be_visible()
    key = page.locator('[data-key-id="r0c0"]')
    expect(key).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert key.bounding_box()["width"] >= 44
    # 44px touch-target gate applies to every button, not just the oversized keypad keys.
    op = page.get_by_role("button", name="Generate automation")
    assert op.bounding_box()["height"] >= 44


@pytest.mark.browser
def test_keyboard_focus_is_visible(page, app_url):
    page.goto(app_url)
    page.keyboard.press("Tab")
    focused = page.locator(":focus")
    expect(focused).to_be_visible()
    assert focused.evaluate("node => getComputedStyle(node).outlineStyle") != "none"