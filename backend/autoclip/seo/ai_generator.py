"""AI SEO Generator for AL AMR Clipping Automation.

Hybrid High-CTR Short SEO Generator:
1. Primary Provider: Google Gemini API (gemini-3.5-flash-lite) for ultra-fast (sub-second) reasoning.
2. Fallback Provider: Local Ollama (DeepSeek-R1 / Qwen2.5) running on Oracle VPS (http://localhost:11434).
3. Deterministic Fallback: Local rule-based heuristic synthesis if all LLMs are unreachable or offline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


def generate_ai_metadata(
    transcript: str,
    brand_name: str = "",
    campaign_title: str = "",
    required_phrases: list[str] | None = None,
    prohibited_terms: list[str] | None = None,
    gemini_api_key: str | None = None,
    ollama_url: str | None = None,
    ollama_model: str | None = None,
) -> dict[str, str]:
    """Generate viral high-CTR Short title and description using Gemini with local DeepSeek fallback."""
    clean_slice = (transcript or "").strip()
    if len(clean_slice) > 1200:
        clean_slice = clean_slice[:1200].rsplit(" ", 1)[0] + "..."

    brand = (brand_name or "").strip()
    if brand.lower() in ("alamr", "autoclip", "normalized campaign"):
        brand = ""

    # 1. Try Primary: Google Gemini API
    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    if api_key:
        try:
            res = _call_gemini(
                api_key=api_key,
                transcript=clean_slice,
                brand=brand,
                campaign_title=campaign_title,
                required_phrases=required_phrases or [],
                prohibited_terms=prohibited_terms or [],
            )
            if res and res.get("title"):
                log.info("Successfully generated SEO metadata via Gemini Flash API")
                return res
        except Exception as exc:
            log.warning("Gemini SEO generation failed, falling back to local Ollama/DeepSeek: %s", exc)

    # 2. Try Fallback: Local Ollama (DeepSeek-R1 or Qwen2.5 on VPS)
    base_ollama = ollama_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    model_name = ollama_model or os.getenv("OLLAMA_SEO_MODEL", "deepseek-r1:8b")
    try:
        res = _call_ollama(
            ollama_url=base_ollama,
            model=model_name,
            transcript=clean_slice,
            brand=brand,
            prohibited_terms=prohibited_terms or [],
        )
        if res and res.get("title"):
            log.info("Successfully generated SEO metadata via local Ollama (%s)", model_name)
            return res
    except Exception as exc:
        log.warning("Local Ollama SEO fallback failed: %s", exc)

    return {}


def _call_gemini(
    api_key: str,
    transcript: str,
    brand: str,
    campaign_title: str,
    required_phrases: list[str],
    prohibited_terms: list[str],
) -> dict[str, str]:
    """Call Google Gemini 3.5 Flash Lite using official google-genai SDK or direct REST."""
    prompt = (
        "You are an elite viral YouTube Shorts & Instagram Reels SEO strategist.\n"
        f"Transcript excerpt: \"{transcript}\"\n"
        f"Brand / Channel: \"{brand or 'Future Founders'}\"\n"
        f"Campaign Context: \"{campaign_title}\"\n"
    )
    if required_phrases:
        prompt += f"Mandatory concepts to emphasize: {', '.join(required_phrases)}\n"
    if prohibited_terms:
        prompt += f"Strictly BANNED words (do NOT use): {', '.join(prohibited_terms)}\n"

    prompt += (
        "\nGenerate strictly valid JSON with:\n"
        "1. \"title\": High-CTR, curiosity-driven viral Short title (35-70 characters, Title Case, punchy, ending with a relevant emoji, do not include #Shorts).\n"
        "2. \"description\": 2-3 engaging sentences summarizing the core revelation/lesson to maximize viewer watch time.\n"
    )

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = json.loads(response.text)
        return {
            "title": str(data.get("title", "")).strip(),
            "description": str(data.get("description", "")).strip(),
        }
    except Exception as exc:
        # Fallback to direct REST endpoint if SDK client encounters issues
        model = "gemini-3.5-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            return {
                "title": str(data.get("title", "")).strip(),
                "description": str(data.get("description", "")).strip(),
            }


def _call_ollama(
    ollama_url: str,
    model: str,
    transcript: str,
    brand: str,
    prohibited_terms: list[str],
) -> dict[str, str]:
    """Call local Ollama server running on VPS with JSON enforcement."""
    prompt = (
        "You are an elite viral Short title creator.\n"
        f"Transcript: \"{transcript}\"\n"
        f"Brand: \"{brand or 'Future Founders'}\"\n"
    )
    if prohibited_terms:
        prompt += f"BANNED WORDS: {', '.join(prohibited_terms)}\n"

    prompt += (
        "\nReturn STRICTLY valid JSON with keys:\n"
        "{\"title\": \"Viral punchy title under 65 chars\", \"description\": \"2 sentence hook description\"}"
    )

    url = f"{ollama_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.6,
            "num_predict": 250,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        raw_text = res.get("response", "")
        # Strip DeepSeek thinking tags <think>...</think> if present
        clean_json = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
        data = json.loads(clean_json)
        return {
            "title": str(data.get("title", "")).strip(),
            "description": str(data.get("description", "")).strip(),
        }
