"""Browser Automation Data Models and Action Specifications."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class BrowserActionType(str, Enum):
    """Supported browser interaction primitives."""
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    TYPE = "type"
    SCROLL = "scroll"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_ATTRIBUTES = "extract_attributes"
    EXTRACT_PAGE = "extract_page"
    SCREENSHOT = "screenshot"
    EVALUATE = "evaluate"
    WAIT_FOR_SELECTOR = "wait_for_selector"


class BrowserSessionConfig(BaseModel):
    """Configuration for an isolated headless browser session."""
    model_config = ConfigDict(frozen=True)

    headless: bool = True
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=720, ge=240, le=2160)
    user_agent: Optional[str] = None
    storage_state_path: Optional[str] = None  # Persistent cookies / session path
    ignore_https_errors: bool = True


class BrowserAction(BaseModel):
    """Single declarative interaction instruction."""
    model_config = ConfigDict(frozen=True)

    action_type: BrowserActionType
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    attribute_name: Optional[str] = None
    expression: Optional[str] = None
    timeout_ms: Optional[int] = None
    scroll_pixels: int = 500
    options: Dict[str, Any] = Field(default_factory=dict)


class BrowserActionResult(BaseModel):
    """Outcome of a single browser interaction."""
    model_config = ConfigDict(frozen=True)

    success: bool
    action_type: BrowserActionType
    data: Optional[Any] = None
    error_message: Optional[str] = None
    screenshot_bytes: Optional[bytes] = None
    detected_challenge: Optional[str] = None  # e.g. "captcha", "cloudflare", "mfa"


class PageExtractionResult(BaseModel):
    """Extracted semantic information from a loaded webpage."""
    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    text_content: str
    elements: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)
    has_captcha: bool = False
    has_mfa: bool = False
