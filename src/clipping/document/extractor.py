"""Deterministic Campaign Rule and Constraint Extractor."""

import re
from typing import List, Optional, Tuple
from clipping.contracts.campaign import (
    CampaignRule,
    CampaignRuleCategory,
    CampaignRuleSeverity,
    CampaignSpec,
    BoundingBox,
)
from clipping.document.base import DocumentExtractionResult, ExtractedBlock, ExtractedTable


class DeterministicRuleExtractor:
    """Extracts structured campaign rules and bounds from document extraction AST without LLM."""

    # Regex patterns for duration extraction
    DURATION_RANGE_PATTERN = re.compile(
        r"(?:duration|length):\s*(\d+)\s*(?:s|sec|seconds)?\s*(?:-|to)\s*(\d+)\s*(?:s|sec|seconds)?",
        re.IGNORECASE,
    )
    DURATION_SINGLE_PATTERN = re.compile(
        r"(?:duration|length):\s*(\d+)\s*(?:s|sec|seconds)?",
        re.IGNORECASE,
    )

    # Patterns for audience
    AUDIENCE_PATTERN = re.compile(
        r"(?:target audience|audience|demographic):\s*([^\n\r.]+)",
        re.IGNORECASE,
    )

    # Patterns for CTA
    CTA_PATTERN = re.compile(
        r"(?:call to action|cta|required cta|closing phrase):\s*[\"']?([^\"'\n\r]+)[\"']?",
        re.IGNORECASE,
    )

    @classmethod
    def extract_spec(
        cls,
        doc_result: DocumentExtractionResult,
        campaign_id: str,
        raw_pdf_storage_key: Optional[str] = None
    ) -> CampaignSpec:
        """Transforms DocumentExtractionResult into a validated CampaignSpec."""
        campaign_name = doc_result.title or f"Campaign {campaign_id}"
        target_audience = "General"
        min_duration = 30.0
        max_duration = 60.0
        required_cta = None
        rules: List[CampaignRule] = []
        rule_counter = 1

        # 1. First pass: extract metadata and rules from text blocks
        for block in doc_result.blocks:
            text = block.text.strip()
            if not text:
                continue

            # Check title if not yet set
            if not doc_result.title and block.is_heading and block.heading_level == 1:
                campaign_name = text

            # Extract Audience
            aud_match = cls.AUDIENCE_PATTERN.search(text)
            if aud_match:
                target_audience = aud_match.group(1).strip()

            # Extract Durations
            dur_range = cls.DURATION_RANGE_PATTERN.search(text)
            if dur_range:
                min_duration = float(dur_range.group(1))
                max_duration = float(dur_range.group(2))
            else:
                dur_single = cls.DURATION_SINGLE_PATTERN.search(text)
                if dur_single:
                    val = float(dur_single.group(1))
                    min_duration = max(15.0, val - 10.0)
                    max_duration = min(90.0, val + 10.0)

            # Extract CTA
            cta_match = cls.CTA_PATTERN.search(text)
            if cta_match:
                required_cta = cta_match.group(1).strip()

            # Extract Prohibited Words / Topics
            cls._extract_prohibited_rules(block, rules, rule_counter)
            rule_counter = len(rules) + 1

            # Extract Required Themes
            cls._extract_theme_rules(block, rules, rule_counter)
            rule_counter = len(rules) + 1

            # Extract Brand Voice / Tone
            cls._extract_brand_rules(block, rules, rule_counter)
            rule_counter = len(rules) + 1

        # 2. Second pass: extract structured rules from tables
        for table in doc_result.tables:
            cls._extract_table_rules(table, rules, rule_counter)
            rule_counter = len(rules) + 1

        # Ensure min_duration <= max_duration
        if min_duration > max_duration:
            min_duration, max_duration = max_duration, min_duration

        return CampaignSpec(
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            target_audience=target_audience,
            min_duration_seconds=min_duration,
            max_duration_seconds=max_duration,
            rules=rules,
            required_cta_text=required_cta,
            raw_pdf_storage_key=raw_pdf_storage_key,
        )

    @classmethod
    def _extract_prohibited_rules(
        cls,
        block: ExtractedBlock,
        rules: List[CampaignRule],
        start_idx: int
    ) -> None:
        text = block.text
        prohibited_triggers = ["prohibited:", "do not mention:", "avoid:", "taboo words:", "forbidden:"]
        for trigger in prohibited_triggers:
            if trigger in text.lower():
                idx = text.lower().find(trigger)
                content = text[idx + len(trigger):].split("\n")[0].strip()
                items = [item.strip().strip("\"'") for item in re.split(r"[,;]", content) if item.strip()]
                if items:
                    rules.append(
                        CampaignRule(
                            rule_id=f"RULE_PROHIBITED_{start_idx:02d}",
                            category=CampaignRuleCategory.PROHIBITED_WORD,
                            severity=CampaignRuleSeverity.CRITICAL,
                            description=f"Do not mention: {', '.join(items)}",
                            exact_match_patterns=items,
                            provenance=block.bbox,
                        )
                    )

    @classmethod
    def _extract_theme_rules(
        cls,
        block: ExtractedBlock,
        rules: List[CampaignRule],
        start_idx: int
    ) -> None:
        text = block.text
        theme_triggers = ["required theme:", "key topic:", "must include:", "core theme:"]
        for trigger in theme_triggers:
            if trigger in text.lower():
                idx = text.lower().find(trigger)
                content = text[idx + len(trigger):].split("\n")[0].strip()
                if content:
                    rules.append(
                        CampaignRule(
                            rule_id=f"RULE_THEME_{start_idx:02d}",
                            category=CampaignRuleCategory.REQUIRED_THEME,
                            severity=CampaignRuleSeverity.CRITICAL,
                            description=f"Required theme: {content}",
                            provenance=block.bbox,
                        )
                    )

    @classmethod
    def _extract_brand_rules(
        cls,
        block: ExtractedBlock,
        rules: List[CampaignRule],
        start_idx: int
    ) -> None:
        text = block.text
        brand_triggers = ["tone:", "brand voice:", "style:"]
        for trigger in brand_triggers:
            if trigger in text.lower():
                idx = text.lower().find(trigger)
                content = text[idx + len(trigger):].split("\n")[0].strip()
                if content:
                    rules.append(
                        CampaignRule(
                            rule_id=f"RULE_BRAND_{start_idx:02d}",
                            category=CampaignRuleCategory.BRAND_VOICE,
                            severity=CampaignRuleSeverity.WARNING,
                            description=f"Brand Voice / Tone: {content}",
                            provenance=block.bbox,
                        )
                    )

    @classmethod
    def _extract_table_rules(
        cls,
        table: ExtractedTable,
        rules: List[CampaignRule],
        start_idx: int
    ) -> None:
        """Parses tables with columns such as [Category, Rule / Description, Severity]."""
        headers = [h.lower().strip() for h in table.headers]
        cat_idx = -1
        desc_idx = -1
        sev_idx = -1

        for i, h in enumerate(headers):
            if "category" in h or "type" in h:
                cat_idx = i
            elif "rule" in h or "description" in h or "requirement" in h:
                desc_idx = i
            elif "severity" in h or "priority" in h:
                sev_idx = i

        if desc_idx == -1 and len(headers) >= 2:
            desc_idx = 1
            cat_idx = 0

        if desc_idx != -1:
            for r_idx, row in enumerate(table.rows):
                if len(row) > desc_idx:
                    desc = row[desc_idx].strip()
                    if not desc:
                        continue
                    cat_str = row[cat_idx].lower().strip() if cat_idx != -1 and len(row) > cat_idx else "required_theme"
                    
                    if "prohibit" in cat_str or "avoid" in cat_str or "taboo" in cat_str:
                        category = CampaignRuleCategory.PROHIBITED_WORD
                    elif "brand" in cat_str or "tone" in cat_str or "voice" in cat_str:
                        category = CampaignRuleCategory.BRAND_VOICE
                    elif "cta" in cat_str or "action" in cat_str:
                        category = CampaignRuleCategory.CALL_TO_ACTION
                    elif "visual" in cat_str or "logo" in cat_str:
                        category = CampaignRuleCategory.VISUAL_REQUIREMENT
                    elif "duration" in cat_str:
                        category = CampaignRuleCategory.DURATION
                    else:
                        category = CampaignRuleCategory.REQUIRED_THEME

                    severity = (
                        CampaignRuleSeverity.WARNING
                        if sev_idx != -1 and len(row) > sev_idx and "warn" in row[sev_idx].lower()
                        else CampaignRuleSeverity.CRITICAL
                    )

                    rules.append(
                        CampaignRule(
                            rule_id=f"RULE_TBL_{start_idx + r_idx:02d}",
                            category=category,
                            severity=severity,
                            description=desc,
                            provenance=table.bbox,
                        )
                    )
