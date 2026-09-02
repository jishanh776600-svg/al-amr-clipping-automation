"""Unit tests for Structured Logging and Secret Redaction."""

from clipping.logging.logger import mask_sensitive_keys, configure_logging, get_logger


def test_mask_sensitive_keys():
    event_dict = {
        "event": "user_logged_in",
        "telegram_bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "api_key": "sk-secret-99999",
        "normal_field": "hello world",
        "campaign_id": "CAMP_01",
    }

    processed = mask_sensitive_keys(None, "info", event_dict)

    assert processed["telegram_bot_token"] != "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    assert "REDACTED" in processed["telegram_bot_token"]
    assert "REDACTED" in processed["api_key"]
    assert processed["normal_field"] == "hello world"
    assert processed["campaign_id"] == "CAMP_01"


def test_get_logger_binding():
    configure_logging(log_level="INFO", log_format="json")
    logger = get_logger("test_pipeline")
    bound = logger.bind(campaign_id="CAMP_01", job_id="JOB_01")
    assert bound is not None
