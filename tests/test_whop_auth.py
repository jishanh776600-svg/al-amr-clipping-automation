"""Unit tests for Whop auth and Gmail OTP reader."""

import pytest
from whop.gmail_otp import GmailOTPReader


def test_gmail_otp_regex_explicit():
    sample_text = "Your Whop verification code is: 482910. Use this code within 10 minutes."
    code = GmailOTPReader._parse_otp_from_text(sample_text)
    assert code == "482910"


def test_gmail_otp_regex_html():
    sample_html = "<div><p>Enter the following code to sign in:</p><h2> 930184 </h2></div>"
    code = GmailOTPReader._parse_otp_from_text(sample_html)
    assert code == "930184"


def test_gmail_otp_regex_fallback():
    sample_text = "Whop login security token 551920 expires soon."
    code = GmailOTPReader._parse_otp_from_text(sample_text)
    assert code == "551920"


def test_gmail_otp_is_configured(monkeypatch):
    monkeypatch.setenv("WHOP_EMAIL", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    reader = GmailOTPReader()
    assert reader.is_configured() is True
    assert reader.app_password == "abcdefghijklmnop"
