"""Unit tests for Central Configuration."""

import os
from clipping.config.settings import Settings


def test_default_settings():
    settings = Settings()
    assert settings.PROJECT_NAME == "Clipping Automation"
    assert settings.STORAGE_DRIVER == "local"
    assert settings.API_PORT == 8000
    assert settings.is_production is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STORAGE_DRIVER", "gdrive")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")

    settings = Settings()
    assert settings.ENVIRONMENT == "production"
    assert settings.is_production is True
    assert settings.STORAGE_DRIVER == "gdrive"

    # Verify secret is wrapped and not leaked in repr
    assert settings.TELEGRAM_BOT_TOKEN is not None
    assert settings.TELEGRAM_BOT_TOKEN.get_secret_value() == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    assert "123456:ABC-DEF" not in str(settings)
