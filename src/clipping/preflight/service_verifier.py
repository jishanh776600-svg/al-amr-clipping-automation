"""Real Service Verification Engine for AL AMR CLIPPING.

Safely and non-destructively validates live external integrations against real APIs:
- Whop: Validates API token against /campaigns endpoint. Never fabricates campaigns.
- YouTube: Validates OAuth2 token refresh and verifies authenticated channel identity without uploading.
- Instagram: Validates Graph API token against /me endpoint. Never publishes publicly.
- Telegram: Validates Bot token against getMe endpoint. Never spams chats.

Strictly preserves zero-secret leakage: all tokens are redacted in log and result structures.
"""

import os
from typing import Any, Dict, Optional
import httpx
from pydantic import BaseModel, Field

from clipping.approval.transport import mask_bot_token
from clipping.config.settings import get_settings
from clipping.logging.logger import get_logger

logger = get_logger("clipping.preflight.service_verifier")


class ServiceVerificationResult(BaseModel):
    """Result of validating an external service integration."""
    service: str
    configured: bool
    verified: bool
    status_code: Optional[int] = None
    account_identity: Optional[str] = None
    message: str
    why_required: str
    configuration_requirement: str
    blocks_dry_run: bool = False
    blocks_live_operation: bool = True
    details: Dict[str, Any] = Field(default_factory=dict)


class RealServiceVerifier:
    """Safely probes live external service APIs using real configured credentials."""

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout = timeout_seconds

    async def verify_whop(self, api_key: Optional[str] = None) -> ServiceVerificationResult:
        """
        Validates Whop API token against live Whop API endpoint if configured.
        Non-destructive GET request to /v5/campaigns.
        """
        token = api_key or os.getenv("WHOP_API_KEY") or os.getenv("WHOP_API_TOKEN")
        if not token:
            return ServiceVerificationResult(
                service="whop",
                configured=False,
                verified=False,
                message="WHOP_API_KEY is not configured (optional: browser discovery active)",
                why_required="Live campaign discovery, CPM payout rules, and source video URL ingestion from Whop",
                configuration_requirement="Optional: Set WHOP_API_KEY if enterprise REST API access is enabled",
                blocks_dry_run=False,
                blocks_live_operation=False,
            )

        masked_token = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "***"
        base_url = os.getenv("WHOP_BASE_URL", "https://api.whop.com/v5")
        target_url = f"{base_url}/campaigns"

        try:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "AlAmrClippingBot/2.0",
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(target_url, headers=headers, params={"limit": 1, "type": "clipping"})
                status = resp.status_code

                if status == 200:
                    data = resp.json()
                    campaign_count = len(data.get("data", data.get("campaigns", [])))
                    return ServiceVerificationResult(
                        service="whop",
                        configured=True,
                        verified=True,
                        status_code=status,
                        message=f"Whop API connectivity verified successfully ({campaign_count} sample campaigns retrieved)",
                        why_required="Live campaign discovery, CPM payout rules, and source video URL ingestion from Whop",
                        configuration_requirement="WHOP_API_KEY",
                        blocks_dry_run=False,
                        blocks_live_operation=False,
                        details={"sample_count": campaign_count, "token_prefix": masked_token},
                    )
                elif status in (401, 403):
                    return ServiceVerificationResult(
                        service="whop",
                        configured=True,
                        verified=False,
                        status_code=status,
                        message=f"Whop API token authentication failed ({status}): token is invalid or unauthorized",
                        why_required="Live campaign discovery, CPM payout rules, and source video URL ingestion from Whop",
                        configuration_requirement="Verify WHOP_API_KEY has valid permissions for Whop creator rewards API",
                        blocks_dry_run=False,
                        blocks_live_operation=False,
                        details={"status": status},
                    )
                elif status == 429:
                    return ServiceVerificationResult(
                        service="whop",
                        configured=True,
                        verified=False,
                        status_code=status,
                        message="Whop API rate limit encountered (429); retry after window clears",
                        why_required="Live campaign discovery",
                        configuration_requirement="Ensure API call rates stay within Whop tier limits",
                        blocks_dry_run=False,
                        blocks_live_operation=False,
                    )
                else:
                    return ServiceVerificationResult(
                        service="whop",
                        configured=True,
                        verified=False,
                        status_code=status,
                        message=f"Whop API returned unexpected HTTP status: {status}",
                        why_required="Live campaign discovery",
                        configuration_requirement="Verify Whop API service status and endpoint availability",
                        blocks_dry_run=False,
                        blocks_live_operation=False,
                    )
        except Exception as e:
            return ServiceVerificationResult(
                service="whop",
                configured=True,
                verified=False,
                message=f"Whop API network connectivity error: {str(e)}",
                why_required="Live campaign discovery",
                configuration_requirement="Verify internet access and DNS resolution for api.whop.com",
                blocks_dry_run=False,
                blocks_live_operation=False,
                details={"error": str(e)},
            )

    async def verify_youtube(self, credentials: Optional[Dict[str, Any]] = None) -> ServiceVerificationResult:
        """
        Validates YouTube OAuth credentials non-destructively:
        1. Obtains access token via refresh token.
        2. Queries authenticated channel via /channels?part=snippet&mine=true.
        Zero video upload is executed.
        """
        creds = credentials or {}
        settings = get_settings()
        if "YOUTUBE_CLIENT_ID" in os.environ:
            client_id = creds.get("client_id") or os.environ["YOUTUBE_CLIENT_ID"]
        else:
            client_id = creds.get("client_id") or settings.YOUTUBE_CLIENT_ID

        if "YOUTUBE_CLIENT_SECRET" in os.environ:
            client_secret = creds.get("client_secret") or os.environ["YOUTUBE_CLIENT_SECRET"]
        else:
            client_secret = (
                creds.get("client_secret")
                or (settings.YOUTUBE_CLIENT_SECRET.get_secret_value() if settings.YOUTUBE_CLIENT_SECRET else None)
            )

        if "YOUTUBE_REFRESH_TOKEN" in os.environ:
            refresh_token = creds.get("refresh_token") or os.environ["YOUTUBE_REFRESH_TOKEN"]
        else:
            refresh_token = (
                creds.get("refresh_token")
                or (settings.YOUTUBE_REFRESH_TOKEN.get_secret_value() if settings.YOUTUBE_REFRESH_TOKEN else None)
            )

        if not (client_id and client_secret and refresh_token):
            missing = []
            if not client_id:
                missing.append("client_id")
            if not client_secret:
                missing.append("client_secret")
            if not refresh_token:
                missing.append("refresh_token")
            return ServiceVerificationResult(
                service="youtube",
                configured=False,
                verified=False,
                message=f"YouTube OAuth2 credentials missing: {', '.join(missing)}",
                why_required="Automated YouTube Shorts video upload and post ID reconciliation via YouTube Data API v3",
                configuration_requirement="Configure Google Cloud OAuth2 Client ID, Secret, and Refresh Token in vault or environment",
                blocks_dry_run=False,
                blocks_live_operation=True,
            )

        try:
            from pydantic import SecretStr
            from clipping.publishing.oauth import OAuthCredentials, OAuthTokenManager

            secret_str = client_secret if isinstance(client_secret, SecretStr) else SecretStr(str(client_secret))
            refresh_str = refresh_token if isinstance(refresh_token, SecretStr) else SecretStr(str(refresh_token))

            token_mgr = OAuthTokenManager(
                credentials=OAuthCredentials(
                    client_id=str(client_id),
                    client_secret=secret_str,
                    refresh_token=refresh_str,
                )
            )

            # 1. Attempt token refresh
            access_token = await token_mgr.get_access_token()
            if not access_token:
                raise RuntimeError("Access token returned empty from Google OAuth2 endpoint")

            # 2. Query authenticated channel identity
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails&mine=true",
                    headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        channel_title = items[0].get("snippet", {}).get("title", "Unknown")
                        channel_id = items[0].get("id", "Unknown")
                        return ServiceVerificationResult(
                            service="youtube",
                            configured=True,
                            verified=True,
                            status_code=200,
                            account_identity=f"{channel_title} ({channel_id})",
                            message=f"YouTube OAuth2 verified: Authenticated as '{channel_title}' (Channel ID: {channel_id})",
                            why_required="Automated YouTube Shorts video upload",
                            configuration_requirement="Google Cloud OAuth2 credentials",
                            blocks_dry_run=False,
                            blocks_live_operation=False,
                            details={"channel_id": channel_id, "channel_title": channel_title},
                        )
                    else:
                        return ServiceVerificationResult(
                            service="youtube",
                            configured=True,
                            verified=False,
                            status_code=200,
                            message="Google account authenticated but has no associated YouTube channel",
                            why_required="Automated YouTube Shorts video upload",
                            configuration_requirement="Create a YouTube channel for the authenticated Google account",
                            blocks_dry_run=False,
                            blocks_live_operation=True,
                        )
                else:
                    return ServiceVerificationResult(
                        service="youtube",
                        configured=True,
                        verified=False,
                        status_code=resp.status_code,
                        message=f"YouTube Data API query failed ({resp.status_code}): Ensure YouTube Data API v3 is enabled in Google Cloud Console",
                        why_required="Automated YouTube Shorts video upload",
                        configuration_requirement="Enable YouTube Data API v3 in Google Cloud project",
                        blocks_dry_run=False,
                        blocks_live_operation=True,
                    )
        except Exception as e:
            return ServiceVerificationResult(
                service="youtube",
                configured=True,
                verified=False,
                message=f"YouTube OAuth verification failed: {str(e)}",
                why_required="Automated YouTube Shorts video upload",
                configuration_requirement="Verify OAuth2 Client ID, Secret, and Refresh Token validity",
                blocks_dry_run=False,
                blocks_live_operation=True,
                details={"error": str(e)},
            )

    async def verify_instagram(self, credentials: Optional[Dict[str, Any]] = None) -> ServiceVerificationResult:
        """
        Validates Instagram Graph API access token non-destructively:
        Queries /me or /{account_id}?fields=id,username.
        Zero video or reel upload is executed.
        """
        creds = credentials or {}
        access_token = creds.get("access_token") or creds.get("token") or os.getenv("INSTAGRAM_ACCESS_TOKEN")
        account_id = creds.get("instagram_account_id") or creds.get("user_id") or os.getenv("INSTAGRAM_ACCOUNT_ID")

        if not access_token:
            return ServiceVerificationResult(
                service="instagram",
                configured=False,
                verified=False,
                message="INSTAGRAM_ACCESS_TOKEN not configured; automated Instagram Reels publishing disabled",
                why_required="Automated Instagram Reels video publishing via Meta Graph API",
                configuration_requirement="Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID in environment or vault",
                blocks_dry_run=False,
                blocks_live_operation=True,
            )

        # Support both Instagram API tokens (IGAA...) and Meta Graph API tokens (EAAB...)
        endpoints_to_try = []
        if access_token.startswith("IG"):
            endpoints_to_try = [
                f"https://graph.instagram.com/me?fields=id,username,account_type&access_token={access_token}",
                f"https://graph.facebook.com/v19.0/{account_id or 'me'}?fields=id,username&access_token={access_token}",
            ]
        else:
            endpoints_to_try = [
                f"https://graph.facebook.com/v19.0/{account_id or 'me'}?fields=id,username&access_token={access_token}",
                f"https://graph.instagram.com/me?fields=id,username,account_type&access_token={access_token}",
            ]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                last_resp = None
                for endpoint in endpoints_to_try:
                    resp = await client.get(endpoint)
                    last_resp = resp
                    if resp.status_code == 200:
                        data = resp.json()
                        username = data.get("username", "Unknown")
                        ig_id = data.get("id", "Unknown")
                        acc_type = data.get("account_type", "Instagram Profile")
                        return ServiceVerificationResult(
                            service="instagram",
                            configured=True,
                            verified=True,
                            status_code=200,
                            account_identity=f"@{username} ({ig_id})",
                            message=f"Instagram API verified: Authenticated as @{username} ({acc_type})",
                            why_required="Automated Instagram Reels publishing",
                            configuration_requirement="Meta/Instagram access token",
                            blocks_dry_run=False,
                            blocks_live_operation=False,
                            details={"account_id": ig_id, "username": username, "account_type": acc_type},
                        )

                err_text = last_resp.text if last_resp else "Unknown verification error"
                return ServiceVerificationResult(
                    service="instagram",
                    configured=True,
                    verified=False,
                    status_code=last_resp.status_code if last_resp else 400,
                    message=f"Instagram token verification failed ({last_resp.status_code if last_resp else 400}): Token expired or invalid",
                    why_required="Automated Instagram Reels publishing",
                    configuration_requirement="Generate a valid Instagram Access Token",
                    blocks_dry_run=False,
                    blocks_live_operation=True,
                    details={"error_response": err_text[:200]},
                )
        except Exception as e:
            return ServiceVerificationResult(
                service="instagram",
                configured=True,
                verified=False,
                message=f"Instagram Graph API network error: {str(e)}",
                why_required="Automated Instagram Reels publishing",
                configuration_requirement="Verify network reachability for graph.facebook.com",
                blocks_dry_run=False,
                blocks_live_operation=True,
                details={"error": str(e)},
            )

    async def verify_telegram(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[int] = None,
    ) -> ServiceVerificationResult:
        """
        Validates Telegram Bot credentials non-destructively:
        Queries /getMe to confirm token validity and bot username.
        Zero messages are sent.
        """
        settings = get_settings()
        if bot_token is not None:
            token = bot_token
        elif "TELEGRAM_BOT_TOKEN" in os.environ:
            token = os.environ["TELEGRAM_BOT_TOKEN"]
        else:
            token = settings.TELEGRAM_BOT_TOKEN.get_secret_value() if settings.TELEGRAM_BOT_TOKEN else None

        if chat_id is not None:
            chat = chat_id
        elif "TELEGRAM_CHAT_ID" in os.environ:
            chat = os.environ["TELEGRAM_CHAT_ID"]
        else:
            chat = settings.TELEGRAM_CHAT_ID

        if not token:
            return ServiceVerificationResult(
                service="telegram",
                configured=False,
                verified=False,
                message="TELEGRAM_BOT_TOKEN not configured; mobile push escalation disabled (logging locally only)",
                why_required="Instant mobile push alerts for CAPTCHAs, MFA, platform blocks, and QA rejections",
                configuration_requirement="Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
                blocks_dry_run=False,
                blocks_live_operation=False,
            )

        url = f"https://api.telegram.org/bot{token}/getMe"
        masked_url = mask_bot_token(url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        bot_info = data.get("result", {})
                        bot_user = bot_info.get("username", "Unknown")
                        bot_name = bot_info.get("first_name", "Bot")
                        has_chat = bool(chat)
                        msg = f"Telegram Bot verified: @{bot_user} ('{bot_name}')"
                        if has_chat:
                            msg += f" with destination chat {chat}"
                        else:
                            msg += " (Warning: TELEGRAM_CHAT_ID not configured; bot cannot deliver alerts)"

                        return ServiceVerificationResult(
                            service="telegram",
                            configured=True,
                            verified=True,
                            status_code=200,
                            account_identity=f"@{bot_user}",
                            message=msg,
                            why_required="Mobile push alerts for critical operator escalations",
                            configuration_requirement="Telegram Bot Token and Chat ID",
                            blocks_dry_run=False,
                            blocks_live_operation=False,
                            details={"bot_username": bot_user, "chat_configured": has_chat},
                        )
                return ServiceVerificationResult(
                    service="telegram",
                    configured=True,
                    verified=False,
                    status_code=resp.status_code,
                    message=f"Telegram bot token validation failed ({resp.status_code}): Invalid bot token",
                    why_required="Mobile push alerts for critical operator escalations",
                    configuration_requirement="Verify TELEGRAM_BOT_TOKEN created via @BotFather",
                    blocks_dry_run=False,
                    blocks_live_operation=False,
                )
        except Exception as e:
            return ServiceVerificationResult(
                service="telegram",
                configured=True,
                verified=False,
                message=f"Telegram API network error: {mask_bot_token(str(e))}",
                why_required="Mobile push alerts for critical operator escalations",
                configuration_requirement="Verify network reachability for api.telegram.org",
                blocks_dry_run=False,
                blocks_live_operation=False,
                details={"error": mask_bot_token(str(e))},
            )

    async def verify_all(self) -> Dict[str, ServiceVerificationResult]:
        """Runs non-destructive verification across all supported external platform integrations."""
        return {
            "whop": await self.verify_whop(),
            "youtube": await self.verify_youtube(),
            "instagram": await self.verify_instagram(),
            "telegram": await self.verify_telegram(),
        }
