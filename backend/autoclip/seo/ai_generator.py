"""AI SEO Generator for AL AMR Clipping Automation.

Multi-Tier Resilient High-CTR Short SEO Generator:
1. Tier 1: Google Gemini API (gemini-3.5-flash-lite) for accurate viral reasoning.
2. Tier 2: Groq Cloud API (qwen/qwen3.8-27b) for ultra-fast sub-second (0.3s) synthesis.
3. Tier 3: NVIDIA NIM Cloud (deepseek-ai/deepseek-v4.1-flash) with 3-key round-robin rotation.
4. Tier 4: OpenRouter Universal API Gateway (meta-llama/llama-3.1-8b-instruct).
5. Tier 5: Local Ollama (DeepSeek-R1 / Qwen2.5) on Oracle VPS (http://localhost:11434) [Preserved offline].
6. Tier 6: Local rule-based heuristic synthesis fallback.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

_nvidia_counter = itertools.count()


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
    """Generate viral high-CTR Short title and description using multi-cloud failover."""
    clean_slice = (transcript or "").strip()
    if len(clean_slice) > 1200:
        clean_slice = clean_slice[:1200].rsplit(" ", 1)[0] + "..."

    brand = (brand_name or "").strip()
    if brand.lower() in ("alamr", "autoclip", "normalized campaign"):
        brand = ""

    # Tier 1: Google Gemini Flash API
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
                log.info("Generated SEO metadata via Tier 1: Gemini Flash API")
                return res
        except Exception as exc:
            log.warning("Tier 1 Gemini SEO failed, trying Tier 2 (Groq): %s", exc)

    # Tier 2: Groq Cloud API (0.3s lightning fast)
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        try:
            res = _call_groq(
                api_key=groq_key,
                transcript=clean_slice,
                brand=brand,
                campaign_title=campaign_title,
                required_phrases=required_phrases or [],
                prohibited_terms=prohibited_terms or [],
            )
            if res and res.get("title"):
                log.info("Generated SEO metadata via Tier 2: Groq Cloud API")
                return res
        except Exception as exc:
            log.warning("Tier 2 Groq SEO failed, trying Tier 3 (NVIDIA DeepSeek): %s", exc)

    # Tier 3: NVIDIA NIM Cloud (DeepSeek Flash)
    nv_keys = [
        k for k in [
            os.getenv("AI_COUNCIL_DEEPSEEK_KEY_1"),
            os.getenv("AI_COUNCIL_DEEPSEEK_KEY_2"),
            os.getenv("AI_COUNCIL_DEEPSEEK_KEY_3"),
            os.getenv("NVIDIA_API_KEY"),
        ] if k and k.strip()
    ]
    if nv_keys:
        try:
            selected_key = nv_keys[next(_nvidia_counter) % len(nv_keys)]
            res = _call_nvidia(
                api_key=selected_key,
                transcript=clean_slice,
                brand=brand,
                campaign_title=campaign_title,
                prohibited_terms=prohibited_terms or [],
            )
            if res and res.get("title"):
                log.info("Generated SEO metadata via Tier 3: NVIDIA Cloud NIM DeepSeek")
                return res
        except Exception as exc:
            log.warning("Tier 3 NVIDIA SEO failed, trying Tier 4 (OpenRouter): %s", exc)

    # Tier 4: OpenRouter API
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    if openrouter_key:
        try:
            res = _call_openrouter(
                api_key=openrouter_key,
                transcript=clean_slice,
                brand=brand,
                campaign_title=campaign_title,
                prohibited_terms=prohibited_terms or [],
            )
            if res and res.get("title"):
                log.info("Generated SEO metadata via Tier 4: OpenRouter API")
                return res
        except Exception as exc:
            log.warning("Tier 4 OpenRouter SEO failed, trying Tier 5 (Local Ollama): %s", exc)

    # Tier 5: Local Ollama (DeepSeek-R1 / Qwen2.5 on VPS) [Never deleted]
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
            log.info("Generated SEO metadata via Tier 5: Local Ollama (%s)", model_name)
            return res
    except Exception as exc:
        log.warning("Tier 5 Local Ollama SEO fallback failed: %s", exc)

    return {}


def _build_prompt(
    transcript: str,
    brand: str,
    campaign_title: str,
    required_phrases: list[str],
    prohibited_terms: list[str],
) -> str:
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
    return prompt


def _call_gemini(
    api_key: str,
    transcript: str,
    brand: str,
    campaign_title: str,
    required_phrases: list[str],
    prohibited_terms: list[str],
) -> dict[str, str]:
    """Call Google Gemini 3.5 Flash Lite using official google-genai SDK or direct REST."""
    prompt = _build_prompt(transcript, brand, campaign_title, required_phrases, prohibited_terms)
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
    except Exception:
        # Fallback to direct REST endpoint
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            return {
                "title": str(data.get("title", "")).strip(),
                "description": str(data.get("description", "")).strip(),
            }


def _call_groq(
    api_key: str,
    transcript: str,
    brand: str,
    campaign_title: str,
    required_phrases: list[str],
    prohibited_terms: list[str],
) -> dict[str, str]:
    """Call Groq Cloud OpenAI-compatible endpoint with JSON mode."""
    prompt = _build_prompt(transcript, brand, campaign_title, required_phrases, prohibited_terms)
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": "qwen/qwen3.8-27b",
        "messages": [
            {"role": "system", "content": "You are a professional video SEO agent. You must respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.6,
        "max_tokens": 250,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        content = res["choices"][0]["message"]["content"]
        data = json.loads(content)
        return {
            "title": str(data.get("title", "")).strip(),
            "description": str(data.get("description", "")).strip(),
        }


def _call_nvidia(
    api_key: str,
    transcript: str,
    brand: str,
    campaign_title: str,
    prohibited_terms: list[str],
) -> dict[str, str]:
    """Call NVIDIA Cloud NIM chat completions endpoint."""
    prompt = _build_prompt(transcript, brand, campaign_title, [], prohibited_terms)
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    payload = {
        "model": "deepseek-ai/deepseek-v4.1-flash",
        "messages": [
            {"role": "system", "content": "Return valid JSON with keys title and description."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 300,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        raw = res["choices"][0]["message"]["content"]
        clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        data = json.loads(clean)
        return {
            "title": str(data.get("title", "")).strip(),
            "description": str(data.get("description", "")).strip(),
        }


def _call_openrouter(
    api_key: str,
    transcript: str,
    brand: str,
    campaign_title: str,
    prohibited_terms: list[str],
) -> dict[str, str]:
    """Call OpenRouter universal AI gateway."""
    prompt = _build_prompt(transcript, brand, campaign_title, [], prohibited_terms)
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": "meta-llama/llama-3.1-8b-instruct",
        "messages": [
            {"role": "system", "content": "Return valid JSON with keys title and description only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 250,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        raw = res["choices"][0]["message"]["content"]
        clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        data = json.loads(clean)
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
