"""Test suite for Hybrid AI SEO Generator (Gemini + Local DeepSeek fallback)."""

import pytest
from unittest.mock import patch, MagicMock
from autoclip.seo.ai_generator import generate_ai_metadata
from autoclip.seo.engine import SEOEngine
from autoclip.seo.models import CampaignSEORequirements
from autoclip.db.models import Clip


def test_ai_generator_gemini_success():
    """Verify Gemini Flash generates high-CTR title and description."""
    mock_response = MagicMock()
    mock_response.text = '{"title": "The Brutal Startup Reality 💀", "description": "Why founders fail without validation."}'

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        res = generate_ai_metadata(
            transcript="Founders spend 6 months building something nobody wants.",
            brand_name="Future Founders",
            gemini_api_key="fake-test-key",
        )
        assert res["title"] == "The Brutal Startup Reality 💀"
        assert "founders fail" in res["description"].lower()


def test_ai_generator_fallback_to_ollama_when_gemini_fails():
    """Verify fallback to local Ollama (DeepSeek-R1) when Gemini fails."""
    mock_ollama_return = {
        "title": "Stop Wasting Time on Apps 🛑",
        "description": "Talk to your customers before you build anything.",
    }

    with patch("autoclip.seo.ai_generator._call_gemini", side_effect=Exception("Gemini quota/network error")):
        with patch("autoclip.seo.ai_generator._call_ollama", return_value=mock_ollama_return):
            res = generate_ai_metadata(
                transcript="Building software before selling is a trap.",
                brand_name="Future Founders",
                gemini_api_key="fake-key",
            )
            assert res["title"] == "Stop Wasting Time on Apps 🛑"
            assert "talk to your customers" in res["description"].lower()


def test_seo_engine_uses_ai_generated_title():
    """Verify SEOEngine integrates AI title cleanly with #Shorts and Title Casing."""
    engine = SEOEngine(requirements=CampaignSEORequirements(brand_name="Future Founders"))
    clip = Clip(id="c1", job_id="j1", start_s=0.0, end_s=30.0, rank=1, score=95)

    mock_ai = {
        "title": "Why Most Startups Fail Instantly 💀",
        "description": "The harsh truth about product market fit.",
    }

    with patch("autoclip.seo.engine.generate_ai_metadata", return_value=mock_ai):
        title = engine._synthesize_title(
            hook="",
            topic="",
            slice_text="Founders fail because they build before they validate with customers.",
        )
        assert "Why Most Startups Fail Instantly 💀" in title
        assert "#Shorts" in title


def test_ai_generator_fallback_to_groq_when_gemini_fails():
    """Verify Groq Cloud is called when Gemini fails."""
    mock_groq_return = {
        "title": "Build Fast Or Die Trying ⚡",
        "description": "How speed determines startup survival in 2026.",
    }

    with patch("autoclip.seo.ai_generator._call_gemini", side_effect=Exception("Gemini quota error")), \
         patch("autoclip.seo.ai_generator._call_groq", return_value=mock_groq_return), \
         patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test123"}):
        res = generate_ai_metadata(
            transcript="Speed is everything in early stage startups.",
            brand_name="Future Founders",
            gemini_api_key="fake-key",
        )
        assert res["title"] == "Build Fast Or Die Trying ⚡"
        assert "speed determines startup survival" in res["description"].lower()


def test_ai_generator_fallback_to_nvidia_deepseek():
    """Verify NVIDIA NIM DeepSeek is called when Gemini and Groq fail."""
    mock_nv_return = {
        "title": "The DeepSeek AI Revolution 🧠",
        "description": "Why reasoning models change automated video editing.",
    }

    with patch("autoclip.seo.ai_generator._call_gemini", side_effect=Exception("Gemini error")), \
         patch("autoclip.seo.ai_generator._call_groq", side_effect=Exception("Groq error")), \
         patch("autoclip.seo.ai_generator._call_nvidia", return_value=mock_nv_return), \
         patch.dict("os.environ", {
             "GROQ_API_KEY": "gsk_test",
             "AI_COUNCIL_DEEPSEEK_KEY_1": "nvapi-test1",
         }):
        res = generate_ai_metadata(
            transcript="DeepSeek R1 reasoning on the fly.",
            brand_name="Future Founders",
            gemini_api_key="fake-key",
        )
        assert res["title"] == "The DeepSeek AI Revolution 🧠"

