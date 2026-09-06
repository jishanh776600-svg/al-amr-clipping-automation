"""Strict Pre-Production Compatibility Gate.

Enforces: SOURCE + CAMPAIGN REQUIREMENTS + TARGET PLATFORM + TARGET ACCOUNT = VALID PRODUCTION JOB.
Fails closed with detailed diagnostic blockers on violations.
Marks unverifiable items as NEEDS_REVIEW without fabricating passes.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.contracts.requirements import CampaignRequirements
from clipping.contracts.source import SourceAccessStatus, SourceResolutionResult
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.compatibility_gate")


class GateCheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SKIPPED = "SKIPPED"


class CompatibilityGateResult(BaseModel):
    """Result of strict pre-production validation gate."""
    model_config = ConfigDict(frozen=True)

    is_valid: bool = Field(..., description="True only if all critical checks pass and zero blockers exist")
    blockers: List[str] = Field(default_factory=list, description="Strict fatal blockers preventing job launch")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings or items needing operator review")
    checks: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Detailed sub-check results")
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def requires_operator_action(self) -> bool:
        return len(self.blockers) > 0 or any(c.get("status") == GateCheckStatus.NEEDS_REVIEW.value for c in self.checks.values())


class PreProductionCompatibilityGate:
    """
    Evaluates end-to-end compatibility between source media, campaign brief requirements,
    destination platform, and target account credentials before initiating video production.
    """

    def evaluate(
        self,
        source_result: SourceResolutionResult,
        requirements: Optional[CampaignRequirements],
        target_platform: str,
        target_account: Optional[AccountMetadata],
    ) -> CompatibilityGateResult:
        blockers: List[str] = []
        warnings: List[str] = []
        checks: Dict[str, Dict[str, Any]] = {}

        # -------------------------------------------------------------
        # 1. SOURCE INTEGRITY & ACCESSIBILITY CHECK
        # -------------------------------------------------------------
        source_status = GateCheckStatus.PASS
        source_details: Dict[str, Any] = {
            "source_type": source_result.source_type,
            "access_status": source_result.source_access_status.value,
        }

        if source_result.source_access_status != SourceAccessStatus.ACCESSIBLE:
            source_status = GateCheckStatus.FAIL
            err = source_result.failure_reason or f"Source is not accessible (status: {source_result.source_access_status.value})"
            blockers.append(f"SOURCE INACCESSIBLE: {err}")
            source_details["error"] = err
        elif source_result.failure_reason:
            source_status = GateCheckStatus.FAIL
            blockers.append(f"SOURCE FAILED: {source_result.failure_reason}")
            source_details["error"] = source_result.failure_reason
        else:
            source_details["duration"] = source_result.duration
            source_details["resolution"] = f"{source_result.width}x{source_result.height}" if source_result.width and source_result.height else "unknown"
            source_details["checksum"] = source_result.checksum

        checks["source"] = {"status": source_status.value, "details": source_details}

        # -------------------------------------------------------------
        # 2. CAMPAIGN REQUIREMENTS & DURATION CHECK
        # -------------------------------------------------------------
        req_status = GateCheckStatus.PASS
        req_details: Dict[str, Any] = {}

        if requirements:
            # A. Source restrictions & permitted URLs
            if requirements.source:
                s_req = requirements.source
                cand_uri = source_result.original_uri.lower()
                prohibited_list = s_req.prohibited_content or getattr(s_req, "prohibited_topics", [])
                for prob in prohibited_list:
                    if prob and prob.lower() in cand_uri:
                        req_status = GateCheckStatus.FAIL
                        blockers.append(f"CAMPAIGN RESTRICTION: Source matches prohibited content '{prob}'")

                restrictions_list = s_req.source_restrictions or s_req.source_footage_restrictions
                for restr in restrictions_list:
                    if restr and restr.lower() in cand_uri:
                        req_status = GateCheckStatus.FAIL
                        blockers.append(f"CAMPAIGN RESTRICTION: Source violates restriction '{restr}'")

                permitted_list = s_req.permitted_source_urls or s_req.permitted_source_videos or s_req.source_urls
                if s_req.specific_footage_required and permitted_list:
                    permitted = [u.strip().lower() for u in permitted_list if u.strip()]
                    matched = any(p in cand_uri or cand_uri in p for p in permitted)
                    if not matched:
                        req_status = GateCheckStatus.FAIL
                        blockers.append(
                            f"CAMPAIGN RESTRICTION: Campaign requires specific footage. Provided source does not match {permitted_list}"
                        )

            # B. Duration requirements
            if requirements.clips and source_result.duration:
                min_dur = requirements.clips.min_duration_seconds or 10.0
                if source_result.duration < min_dur:
                    req_status = GateCheckStatus.FAIL
                    blockers.append(
                        f"DURATION MISMATCH: Source duration ({source_result.duration:.1f}s) is shorter than required minimum clip duration ({min_dur:.1f}s)"
                    )
                else:
                    req_details["duration_check"] = "PASS"

            # C. Resolution requirements
            if requirements.clips and requirements.clips.resolution_min and source_result.height:
                if "1080" in requirements.clips.resolution_min and source_result.height < 720:
                    warnings.append(
                        f"RESOLUTION WARNING: Source resolution ({source_result.width}x{source_result.height}) is lower than target ({requirements.clips.resolution_min})"
                    )

            # D. Campaign Deadline check
            sub_dl = requirements.submission and (requirements.submission.submission_deadline or requirements.submission.deadline)
            if sub_dl:
                dl_str = sub_dl
                try:
                    dl_dt = datetime.fromisoformat(dl_str.replace("Z", "+00:00"))
                    if dl_dt < datetime.now(timezone.utc):
                        req_status = GateCheckStatus.FAIL
                        blockers.append(f"CAMPAIGN EXPIRED: Submission deadline ({dl_str}) has already passed")
                except Exception:
                    # Non-ISO date string
                    warnings.append(f"DEADLINE FORMAT: Could not parse deadline '{dl_str}' into ISO timestamp")

            # E. Unverifiable items marked NEEDS_REVIEW
            if requirements.content and requirements.content.required_talking_points:
                req_details["talking_points"] = "NEEDS_REVIEW (Checked in perception/discovery stage)"
            if requirements.branding and (requirements.branding.required_watermark or requirements.branding.watermark_requirements):
                req_details["watermark"] = "NEEDS_REVIEW (Checked in render stage)"
        else:
            req_status = GateCheckStatus.NEEDS_REVIEW
            warnings.append("No structured campaign requirements supplied; running with default constraints")

        checks["requirements"] = {"status": req_status.value, "details": req_details}

        # -------------------------------------------------------------
        # 3. DESTINATION PLATFORM COMPATIBILITY
        # -------------------------------------------------------------
        dest_status = GateCheckStatus.PASS
        dest_details: Dict[str, Any] = {"platform": target_platform}

        clean_plat = target_platform.lower()
        supported_platforms = {"youtube_shorts", "youtube", "instagram_reels", "instagram"}
        if clean_plat not in supported_platforms:
            dest_status = GateCheckStatus.FAIL
            blockers.append(f"UNSUPPORTED PLATFORM: '{target_platform}' is not supported for publishing")
        elif requirements and requirements.platform:
            p_list = requirements.platform.target_platforms or requirements.platform.platforms
            if p_list:
                allowed_p = [p.lower() for p in p_list]
                if not any(clean_plat in p or p in clean_plat for p in allowed_p):
                    dest_status = GateCheckStatus.FAIL
                    blockers.append(
                        f"PLATFORM MISMATCH: Target platform '{target_platform}' is not in campaign allowed platforms: {p_list}"
                    )


        checks["destination"] = {"status": dest_status.value, "details": dest_details}

        # -------------------------------------------------------------
        # 4. TARGET ACCOUNT VALIDATION
        # -------------------------------------------------------------
        acc_status = GateCheckStatus.PASS
        acc_details: Dict[str, Any] = {}

        if not target_account:
            acc_status = GateCheckStatus.FAIL
            blockers.append(f"NO TARGET ACCOUNT: No account specified for destination platform '{target_platform}'")
        else:
            acc_details["account_id"] = target_account.account_id
            acc_details["username"] = target_account.username
            acc_details["account_status"] = target_account.status.value

            if target_account.status != AccountStatus.ACTIVE:
                acc_status = GateCheckStatus.FAIL
                blockers.append(
                    f"ACCOUNT NOT ACTIVE: Target account '{target_account.username}' ({target_account.account_id}) status is '{target_account.status.value}'. Must be ACTIVE to publish."
                )

            # Platform alignment
            acc_p_str = target_account.platform.value.lower()
            if "youtube" in clean_plat and "youtube" not in acc_p_str:
                acc_status = GateCheckStatus.FAIL
                blockers.append(
                    f"ACCOUNT PLATFORM MISMATCH: Selected account platform '{acc_p_str}' does not match job platform '{target_platform}'"
                )
            elif "instagram" in clean_plat and "instagram" not in acc_p_str:
                acc_status = GateCheckStatus.FAIL
                blockers.append(
                    f"ACCOUNT PLATFORM MISMATCH: Selected account platform '{acc_p_str}' does not match job platform '{target_platform}'"
                )

        checks["account"] = {"status": acc_status.value, "details": acc_details}

        # Overall validity
        is_valid = len(blockers) == 0

        return CompatibilityGateResult(
            is_valid=is_valid,
            blockers=blockers,
            warnings=warnings,
            checks=checks,
        )
