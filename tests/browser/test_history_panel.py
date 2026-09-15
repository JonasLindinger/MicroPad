# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Browser coverage for the configuration history panel (B7).

The panel lists the revisions the backend recorded, shows the structural diff of one
and restores it. Restoring overwrites the open configuration, so it must go through
the confirmation dialog — a stray click in a list of timestamps is easy to make.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect


@pytest.mark.browser
def test_history_panel_lists_revisions_newest_first(page, app_url):
    page.goto(app_url)
    expect(page.locator("#page-tree")).to_be_visible()
    rows = page.locator("#history-panel .history-row")
    expect(rows.first).to_be_visible()
    assert rows.count() == 2
    assert rows.first.get_attribute("data-snapshot-id") == "20260915T090000Z-aaaaaaaa"
    expect(rows.first.locator(".history-counts")).to_have_text("4 page(s), 7 item(s)")


@pytest.mark.browser
def test_history_panel_shows_the_diff_of_a_revision(page, app_url, captured_requests):
    page.goto(app_url)
    expect(page.locator("#history-panel .history-row").first).to_be_visible()
    page.locator("#history-panel .history-row").first.get_by_role("button", name="Diff").click()
    diff = page.locator("#history-panel .history-diff li")
    expect(diff.first).to_be_visible()
    assert "global keymap r0c0" in diff.nth(1).text_content()
    # Reading a revision must not save anything.
    assert not any(call["path"] == "/api/config" for call in captured_requests)


@pytest.mark.browser
def test_history_panel_restores_only_after_confirmation(page, app_url, captured_requests):
    page.goto(app_url)
    expect(page.locator("#history-panel .history-row").first).to_be_visible()
    row = page.locator("#history-panel .history-row").first
    row.get_by_role("button", name="Restore").click()
    dialog = page.get_by_role("dialog", name="Restore configuration revision")
    expect(dialog).to_be_visible()

    # Cancelling must change nothing at all.
    dialog.get_by_role("button", name="Cancel restore").click()
    expect(dialog).to_be_hidden()
    assert not any(call["path"].endswith("/restore") for call in captured_requests)

    row.get_by_role("button", name="Restore").click()
    page.get_by_role("button", name="Restore now").click()
    expect(page.locator("#history-panel .history-notice")).to_contain_text("Restored 2026")
    # The restored revision became the open configuration (the page tree re-rendered).
    expect(page.locator("#page-tree")).to_contain_text("Restored home")
