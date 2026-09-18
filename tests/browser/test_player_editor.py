# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Browser video-player editor: queue rows persist once complete, drafts stay local."""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect


def _last_write(config_writes, timeout: float = 5.0) -> dict:
    """Return the newest recorded /api/config write once one exists.

    The editor saves through a 400 ms debounce, so the save-status text never
    leaves the initial "Saved" state and to_have_text cannot gate on the write
    completing; poll the recorder instead.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        writes = config_writes()
        if writes:
            return writes[-1]
        time.sleep(0.05)
    raise AssertionError("no config write recorded within timeout")


@pytest.mark.browser
def test_player_editor_adds_and_persists_a_video(page, app_url, config_writes):
    page.goto(app_url)
    page.get_by_role("button", name="Add video").click()
    rows = page.locator(".player-row")
    expect(rows).to_have_count(1)
    # Incomplete rows are local drafts: a name alone must not be persisted.
    rows.nth(0).get_by_label("Video name").fill("Bad Apple")
    assert config_writes() == []

    rows.nth(0).get_by_label("Video URL").fill(
        "https://www.youtube.com/watch?v=FtutLA63Cp8"
    )
    write = _last_write(config_writes)
    assert write["json"]["player_videos"] == [
        {"name": "Bad Apple", "url": "https://www.youtube.com/watch?v=FtutLA63Cp8"}
    ]


@pytest.mark.browser
def test_player_editor_removes_and_reorders(page, app_url, config_writes):
    page.goto(app_url)
    page.get_by_role("button", name="Add video").click()
    page.get_by_role("button", name="Add video").click()
    rows = page.locator(".player-row")
    rows.nth(0).get_by_label("Video name").fill("A")
    rows.nth(0).get_by_label("Video URL").fill("https://example.com/a")
    rows.nth(1).get_by_label("Video name").fill("B")
    rows.nth(1).get_by_label("Video URL").fill("https://example.com/b")
    write = _last_write(config_writes)
    assert [v["name"] for v in write["json"]["player_videos"]] == ["A", "B"]

    # Move B above A, then remove it: the queue ends with only A and stays saved.
    rows.nth(1).get_by_role("button", name="Up").click()
    write = _last_write(config_writes)
    assert [v["name"] for v in write["json"]["player_videos"]] == ["B", "A"]
    rows.nth(0).get_by_role("button", name="Remove").click()
    write = _last_write(config_writes)
    assert write["json"]["player_videos"] == [{"name": "A", "url": "https://example.com/a"}]


@pytest.mark.browser
def test_player_editor_caps_rows_at_contract_limit(page, app_url):
    page.goto(app_url)
    page.get_by_role("button", name="Add video").click()
    for _ in range(31):
        page.get_by_role("button", name="Add video").click()
    expect(page.get_by_role("button", name="Add video")).to_be_disabled()
    expect(page.locator(".player-row")).to_have_count(32)