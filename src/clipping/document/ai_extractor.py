"""Structured AI / LLM Extraction Layer for Campaign Briefs (Step 2/5)."""

import json
import os
from typing import Any, Dict, Optional
import httpx
from pydantic import ValidationError

from clipping.config.settings import get_settings
from clipping.contracts.requirements import CampaignRequirements
from clipping.logging.logger import get_logger

logger = get_logger("clipping.document.ai_extractor")

SYSTEM_EXTRACTION_PROMPT = """You are an expert AI media operations analyst extracting campaign requirements from a campaign brief.
Analyze the provided campaign brief text and produce a valid JSON object strictly adhering to the CampaignRequirements schema.

Extraction rules:
1. NEVER invent missing requirements. If a requirement is not explicitly stated, leave it null/empty.
2. Distinguish modality: REQUIRED, OPTIONAL, PREFERRED, PROHIBITED, UNKNOWN.
3. Extract clip count, duration ranges (min, max, preferred in seconds), aspect ratio, resolution, and fps.
4. Extract allowed and prohibited topics, talking points, and claims.
5. Extract required and prohibited hashtags (e.g. #shorts), captions, and calls to action.
6. Extract platforms (e.g. youtube_shorts, instagram_reels, tiktok).
7. Extract submission rules, deadline, CPM, payout, and budget.
8. Output ONLY the JSON object, with no markdown code fences and no preamble.
"""


class BriefAIExtractor:
    """
    Interfaces with configured LLM (Local LLM or Gemini) to perform structured output extraction.
    Enforces strict Pydantic validation and fails closed without hallucination.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ):
        settings = get_settings()
        self.base_url = base_url or getattr(settings, "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        self.model = model or getattr(settings, "LOCAL_LLM_MODEL", "qwen2.5:7b-instruct-q4_K_M")
        self.timeout = timeout_seconds

    async def extract_structured_requirements(
        self,
        raw_text: str,
        source_filename: Optional[str] = None,
    ) -> Optional[CampaignRequirements]:
        """
        Attempts structured LLM extraction. Returns validated CampaignRequirements, or None if unavailable/invalid.
        """
        if not raw_text or not raw_text.strip():
            return None

        # Check if local or remote LLM can be invoked
        try:
            prompt_content = f"Campaign Brief ({source_filename or 'brief'}):\n\n{raw_text[:8000]}"
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_EXTRACTION_PROMPT},
                    {"role": "user", "content": prompt_content},
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code != 200:
                    logger.info("AI extraction provider unavailable or returned non-200", status=response.status_code)
                    return None

                data = response.json()
                raw_json_str = data["choices"][0]["message"]["content"]
                parsed = json.loads(raw_json_str)
                reqs = CampaignRequirements.model_validate(parsed)
                reqs.metadata.engine = f"llm_validated:{self.model}"
                reqs.metadata.source_filename = source_filename
                return reqs

        except (httpx.RequestError, httpx.TimeoutException) as exc:
            logger.info("AI extraction endpoint unreachable, falling back to deterministic parser", error=str(exc))
            return None
        except (ValidationError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("AI extraction output failed schema validation", error=str(exc))
            return None
        except Exception as exc:
            logger.warning("Unexpected error during AI extraction", error=str(exc))
            return None
