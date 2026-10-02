"""Focused regression tests for HumanActor intrinsic mutation safety guard.

Verifies:
1. dry_run + read-only click -> ALLOWED
2. dry_run + claim -> BLOCKED
3. dry_run + join -> BLOCKED
4. dry_run + submit -> BLOCKED
5. dry_run + upload -> BLOCKED
6. blocked mutation -> NO mouse.down()
7. blocked mutation -> NO mouse.up()
8. browser-level assert_action_permitted() -> still works
9. HumanActor-level protection -> works independently of caller-side checks
10. non-dry-run -> existing behavior is not globally disabled
"""

import pytest
from unittest.mock import MagicMock
from playwright.sync_api import sync_playwright

from whop.browser import WhopBrowser
from whop.config import WhopConfig, WhopDryRunViolationError, FORBIDDEN_MUTATION_ACTIONS
from whop.human_interaction import HumanActor


def test_dry_run_readonly_click_allowed():
    """1. Read-only interactions are allowed under dry_run."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<div id="card" style="margin:50px;width:100px;height:50px;">Campaign Card</div>')
        actor = HumanActor(page, dry_run=True)

        # Should complete without error
        tx, ty = actor.human_click("#card", action_name="read_only")
        assert tx > 0 and ty > 0
        browser.close()


@pytest.mark.parametrize("mutation_action", ["claim", "join", "submit", "upload", "apply", "delete"])
def test_dry_run_explicit_mutation_blocked(mutation_action):
    """2-5. Explicit mutation actions are strictly blocked under dry_run."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(f'<button id="btn" style="margin:50px;width:100px;height:50px;">{mutation_action.title()}</button>')
        actor = HumanActor(page, dry_run=True)

        with pytest.raises(WhopDryRunViolationError) as exc_info:
            actor.human_click("#btn", action_name=mutation_action)
        assert mutation_action in str(exc_info.value).lower()
        browser.close()


def test_blocked_mutation_never_dispatches_mouse_down_or_up():
    """6-7. Verifies NO mouse.down() and NO mouse.up() are ever called when mutation is blocked."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<button id="claim-btn" style="margin:50px;width:100px;height:50px;">Claim Reward</button>')

        # Spy on page.mouse.down and page.mouse.up
        real_down = page.mouse.down
        real_up = page.mouse.up
        mock_down = MagicMock(side_effect=real_down)
        mock_up = MagicMock(side_effect=real_up)
        page.mouse.down = mock_down
        page.mouse.up = mock_up

        actor = HumanActor(page, dry_run=True)

        # Attempt forbidden click
        with pytest.raises(WhopDryRunViolationError):
            actor.human_click("#claim-btn")

        # Prove mouse.down and mouse.up were NEVER called
        assert mock_down.call_count == 0, "Security breach: mouse.down() was called on blocked mutation!"
        assert mock_up.call_count == 0, "Security breach: mouse.up() was called on blocked mutation!"

        browser.close()


def test_browser_level_assert_action_permitted_still_works():
    """8. WhopBrowser.assert_action_permitted() continues to function as expected."""
    cfg = WhopConfig(dry_run=True, headless=True)
    browser = WhopBrowser(cfg)

    # Allowed read-only action
    browser.assert_action_permitted("inspect")

    # Blocked mutations
    for verb in FORBIDDEN_MUTATION_ACTIONS:
        with pytest.raises(WhopDryRunViolationError):
            browser.assert_action_permitted(verb)


def test_human_actor_intrinsic_protection_without_browser():
    """9. HumanActor protects independently even without a WhopBrowser instance."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<button id="submit-btn" style="margin:50px;width:100px;height:50px;">Submit Application</button>')

        # Direct instantiation with no WhopBrowser wrapper
        actor = HumanActor(page, dry_run=True)

        # Automatic semantic detection blocks without explicit action_name
        with pytest.raises(WhopDryRunViolationError) as exc_info:
            actor.human_click("#submit-btn")
        assert "submit" in str(exc_info.value).lower()

        browser.close()


def test_non_dry_run_allows_mutations():
    """10. When dry_run is False, mutations are permitted and mouse events are dispatched."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<button id="btn" style="margin:50px;width:100px;height:50px;">Join</button>')

        mock_down = MagicMock()
        mock_up = MagicMock()
        page.mouse.down = mock_down
        page.mouse.up = mock_up

        # Explicitly configure dry_run=False
        actor = HumanActor(page, dry_run=False)

        # Should complete and dispatch mouse events
        actor.human_click("#btn", action_name="join")
        assert mock_down.call_count == 1
        assert mock_up.call_count == 1

        browser.close()
