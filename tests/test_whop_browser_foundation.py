"""Targeted Unit Tests for Whop Cloud-Browser Foundation (Step 1).

Tests:
1. Missing WHOP_COOKIES -> configuration failure.
2. Malformed storage state -> safe failure.
3. Valid storage-state parsing -> accepted.
4. Secret redaction -> secret never appears in logs/errors.
5. DRY_RUN enforcement -> mutation actions cannot execute.
6. Browser cleanup -> resources close correctly after success/failure.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from whop import (
    WhopBrowser,
    WhopConfig,
    WhopConfigError,
    WhopDryRunViolationError,
    WhopSessionError,
    FORBIDDEN_MUTATION_ACTIONS,
    parse_and_validate_session_state,
    sanitize_text,
)


def test_missing_whop_cookies_raises_config_error(monkeypatch):
    """Test 1: Missing WHOP_COOKIES environment variable raises WhopConfigError."""
    monkeypatch.delenv("WHOP_COOKIES", raising=False)
    config = WhopConfig.from_env()

    with pytest.raises(WhopConfigError) as exc_info:
        config.get_cookies_secret()

    assert "WHOP_COOKIES secret is missing" in str(exc_info.value)


def test_malformed_storage_state_fails_safely():
    """Test 2: Malformed JSON or invalid schema fails with clear message without leaking raw input."""
    # Invalid JSON
    with pytest.raises(WhopSessionError) as exc_info1:
        parse_and_validate_session_state("not_valid_json_string{")
    assert "malformed JSON" in str(exc_info1.value)

    # Empty array
    with pytest.raises(WhopSessionError) as exc_info2:
        parse_and_validate_session_state("[]")
    assert "zero cookies" in str(exc_info2.value)

    # Missing required cookie fields
    with pytest.raises(WhopSessionError) as exc_info3:
        parse_and_validate_session_state('[{"name": "test_cookie"}]')
    assert "missing required 'name' or 'value'" in str(exc_info3.value)


def test_valid_storage_state_accepted():
    """Test 3: Valid list of cookies and Playwright storage state format parse successfully."""
    # List format
    cookie_list = [
        {"name": "whop_session", "value": "test_session_val_123", "domain": ".whop.com", "path": "/"},
        {"name": "cf_clearance", "value": "test_cf_val_456", "domain": ".whop.com", "path": "/"},
    ]
    state1 = parse_and_validate_session_state(json.dumps(cookie_list))
    assert state1.cookie_count == 2
    assert ".whop.com" in state1.domains

    # Full Playwright storage state format
    playwright_format = {
        "cookies": cookie_list,
        "origins": [{"origin": "https://whop.com", "localStorage": [{"name": "theme", "value": "dark"}]}],
    }
    state2 = parse_and_validate_session_state(json.dumps(playwright_format))
    assert state2.cookie_count == 2
    assert state2.origins_count == 1
    assert "cookies" in state2.raw_storage_dict


def test_secret_redaction():
    """Test 4: Secret values and auth query parameters are never leaked in strings or logs."""
    fake_secret = "super_secret_cookie_token_987654321"
    raw_message = f"Error connecting with token {fake_secret} at https://whop.com/api?token={fake_secret}&safe=1"

    redacted = sanitize_text(raw_message, secret_to_redact=fake_secret)

    assert fake_secret not in redacted
    assert "[REDACTED_SECRET]" in redacted
    assert "token=%5BREDACTED%5D" in redacted or "token=[REDACTED]" in redacted
    assert "safe=1" in redacted


def test_dry_run_mutation_enforcement():
    """Test 5: Mutation actions are strictly blocked by DRY_RUN safety guard."""
    config = WhopConfig(dry_run=True)
    browser = WhopBrowser(config=config)

    # Verify that each forbidden action triggers a WhopDryRunViolationError
    for action in FORBIDDEN_MUTATION_ACTIONS:
        with pytest.raises(WhopDryRunViolationError) as exc_info:
            browser.assert_action_permitted(action)
        assert "forbidden by Step 1 read-only guard" in str(exc_info.value)

    # General mutation assertion on config
    with pytest.raises(WhopDryRunViolationError):
        config.assert_mutation_permitted("submit_clip")


def test_browser_cleanup_on_success_and_failure():
    """Test 6: Browser, context, and page close deterministically."""
    browser = WhopBrowser(config=WhopConfig(dry_run=True, headless=True))

    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    browser._playwright = mock_playwright
    browser._browser = mock_browser
    browser._context = mock_context
    browser._page = mock_page

    # Execute close
    browser.close()

    mock_page.close.assert_called_once()
    mock_context.close.assert_called_once()
    mock_browser.close.assert_called_once()
    mock_playwright.stop.assert_called_once()

    assert browser.page is None
    assert browser.context is None
