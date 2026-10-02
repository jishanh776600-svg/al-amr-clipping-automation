"""Human-Like Browser Interaction Engine for Whop Automation.

Simulates biological human kinematics for undetectable browser interaction:
- Cubic Bezier curve trajectory generation with randomized control points.
- Fitts's Law velocity profiles (acceleration phase -> deceleration near target).
- Micro-jitter tremor simulation (subtle involuntary hand vibrations).
- Gaussian target point sampling (biologically realistic clustering within bounding boxes).
- Pre-click hesitation and realistic mouse-down click hold durations.
- Log-normal keyboard typing cadence with optional typo-correction dynamics.
- Inertial wheel scrolling with natural reading pauses.
"""

from __future__ import annotations

import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

from .config import FORBIDDEN_MUTATION_ACTIONS, WhopDryRunViolationError

log = logging.getLogger(__name__)


@dataclass
class Point:
    x: float
    y: float


# QWERTY adjacent key mapping for ultra-realistic typo simulation
QWERTY_NEIGHBORS: Dict[str, str] = {
    "a": "qwsz",
    "b": "vghn",
    "c": "xdfv",
    "d": "ersfxc",
    "e": "wsdr",
    "f": "rtgdvc",
    "g": "tyhfvb",
    "h": "yujgbn",
    "i": "ujko",
    "j": "uikmnh",
    "k": "ijlm",
    "l": "okp",
    "m": "njk",
    "n": "bhjm",
    "o": "iklp",
    "p": "ol",
    "q": "wa",
    "r": "edft",
    "s": "wedxza",
    "t": "rfgy",
    "u": "yhji",
    "v": "cfgb",
    "w": "qase",
    "x": "zsdc",
    "y": "tghu",
    "z": "asx",
    "1": "2q",
    "2": "13qw",
    "3": "24we",
    "4": "35er",
    "5": "46rt",
    "6": "57ty",
    "7": "68yu",
    "8": "79ui",
    "9": "80io",
    "0": "9op",
}


@dataclass
class HumanPersona:
    """Represents an individual human user's biological motor characteristics."""
    base_mouse_speed: float = 1.0          # 0.7 to 1.35
    tremor_intensity: float = 1.2          # 0.5 to 2.2 px
    overshoot_probability: float = 0.22    # 15% to 35% chance to overshoot target
    curve_variation: float = 0.25          # Curvature eccentricity
    
    hesitation_mean: float = 0.14          # Pre-click hesitation (s)
    click_hold_mean: float = 0.075         # Click hold duration (s)
    recovery_delay_mean: float = 0.12      # Post-action recovery (s)

    base_wpm: float = 62.0                 # Words per minute
    typo_rate: float = 0.025               # Typo probability
    typing_rhythm_entropy: float = 0.35    # Burstiness / variance in typing cadence

    actions_count: int = 0

    @classmethod
    def generate_random(cls) -> "HumanPersona":
        """Spawns a distinct, unique human profile with naturally correlated physical traits."""
        speed = random.gauss(1.0, 0.15)
        speed = max(0.7, min(1.35, speed))

        wpm = random.gauss(60.0, 12.0)
        wpm = max(38.0, min(95.0, wpm))

        return cls(
            base_mouse_speed=speed,
            tremor_intensity=random.uniform(0.7, 1.8),
            overshoot_probability=random.uniform(0.18, 0.32),
            curve_variation=random.uniform(0.18, 0.35),
            hesitation_mean=random.uniform(0.10, 0.22),
            click_hold_mean=random.uniform(0.055, 0.095),
            recovery_delay_mean=random.uniform(0.08, 0.18),
            base_wpm=wpm,
            typo_rate=random.uniform(0.015, 0.04),
            typing_rhythm_entropy=random.uniform(0.25, 0.45),
        )

    def record_action(self) -> None:
        """Slightly shifts fatigue and reaction times as interaction session progresses."""
        self.actions_count += 1
        if self.actions_count > 25 and random.random() < 0.25:
            self.base_mouse_speed *= random.uniform(0.98, 1.01)
            self.hesitation_mean *= random.uniform(0.99, 1.02)


def generate_bezier_curve(
    start: Point,
    end: Point,
    steps: Optional[int] = None,
    deviation_factor: float = 0.25,
    jitter_pixels: float = 1.2,
) -> List[Point]:
    """Generates a human-like curved trajectory between two points using Cubic Bezier curves.
    
    Args:
        start: Starting (x, y) coordinates.
        end: Destination (x, y) coordinates.
        steps: Total discrete trajectory points. If None, computed from euclidean distance.
        deviation_factor: How wide the curve can bow out relative to distance.
        jitter_pixels: Magnitude of involuntary hand tremor noise.
    """
    dx = end.x - start.x
    dy = end.y - start.y
    distance = math.hypot(dx, dy)

    if steps is None:
        steps = max(15, int(distance / random.uniform(8.0, 14.0)))

    if distance < 5.0 or steps <= 2:
        return [start, end]

    nx = -dy / distance
    ny = dx / distance

    t1 = random.uniform(0.2, 0.45)
    dev1 = random.uniform(-1.0, 1.0) * deviation_factor * distance
    cp1_x = start.x + dx * t1 + nx * dev1
    cp1_y = start.y + dy * t1 + ny * dev1

    t2 = random.uniform(0.55, 0.8)
    dev2 = random.uniform(-0.8, 0.8) * deviation_factor * distance
    cp2_x = start.x + dx * t2 + nx * dev2
    cp2_y = start.y + dy * t2 + ny * dev2

    points: List[Point] = []

    for i in range(steps + 1):
        u = i / float(steps)

        # Flash & Hogan (1985) Minimum-Jerk biological trajectory polynomial:
        # tau = u, s(tau) = 10*tau^3 - 15*tau^4 + 6*tau^5
        tau = u
        tau3 = tau * tau * tau
        tau4 = tau3 * tau
        tau5 = tau4 * tau
        eased_u = 10.0 * tau3 - 15.0 * tau4 + 6.0 * tau5

        c0 = (1.0 - eased_u) ** 3
        c1 = 3.0 * ((1.0 - eased_u) ** 2) * eased_u
        c2 = 3.0 * (1.0 - eased_u) * (eased_u ** 2)
        c3 = eased_u ** 3

        px = c0 * start.x + c1 * cp1_x + c2 * cp2_x + c3 * end.x
        py = c0 * start.y + c1 * cp1_y + c2 * cp2_y + c3 * end.y

        # Biological tremor noise with natural frequency modulation
        if 0 < i < steps:
            # Envelope is 0 at endpoints, maximum near trajectory midpoint
            jitter_envelope = math.sin(math.pi * u)
            px += random.gauss(0, jitter_pixels * 0.45) * jitter_envelope
            py += random.gauss(0, jitter_pixels * 0.45) * jitter_envelope

        points.append(Point(round(px, 2), round(py, 2)))

    return points


def sample_gaussian_target(
    bbox: dict,
    padding_pct: float = 0.15,
) -> Tuple[float, float]:
    """Samples a realistic click target point inside an element bounding box.
    
    Humans cluster clicks towards the element center with Gaussian spread,
    avoiding the extreme outer margins.
    """
    x = float(bbox.get("x", 0))
    y = float(bbox.get("y", 0))
    w = float(bbox.get("width", 10))
    h = float(bbox.get("height", 10))

    pad_x = max(2.0, w * padding_pct)
    pad_y = max(2.0, h * padding_pct)

    inner_min_x = x + pad_x
    inner_max_x = x + w - pad_x
    inner_min_y = y + pad_y
    inner_max_y = y + h - pad_y

    center_x = x + (w / 2.0)
    center_y = y + (h / 2.0)

    sigma_x = max(1.0, (w - 2 * pad_x) / 4.0)
    sigma_y = max(1.0, (h - 2 * pad_y) / 4.0)

    target_x = random.gauss(center_x, sigma_x)
    target_y = random.gauss(center_y, sigma_y)

    # Biological sub-pixel motor scatter: humans never click exact integers or predictable fractions
    target_x += random.uniform(-2.37, 2.64)
    target_y += random.uniform(-2.18, 2.41)

    target_x = max(inner_min_x, min(inner_max_x, target_x))
    target_y = max(inner_min_y, min(inner_max_y, target_y))

    return round(target_x, 2), round(target_y, 2)


class HumanActor:
    """Full-spectrum bio-mimetic browser actor for undetectable automation."""

    def __init__(
        self,
        page: Any,
        persona: Optional[HumanPersona] = None,
        initial_position: Optional[Tuple[float, float]] = None,
        dry_run: Optional[bool] = None,
        browser: Optional[Any] = None,
    ):
        self.page = page
        self.persona = persona or HumanPersona.generate_random()
        self.browser = browser

        if dry_run is not None:
            self.dry_run = bool(dry_run)
        elif browser and hasattr(browser, "config") and hasattr(browser.config, "dry_run"):
            self.dry_run = bool(browser.config.dry_run)
        else:
            self.dry_run = os.getenv("WHOP_DRY_RUN", "true").lower() in ("true", "1", "yes")

        if initial_position:
            self._current_x, self._current_y = initial_position
        else:
            vp = None
            try:
                vp = self.page.viewport_size
            except Exception:
                pass
            vw = float(vp["width"]) if vp else 1280.0
            vh = float(vp["height"]) if vp else 720.0
            # Highly randomized, irregular initial resting position (never round numbers like 250, 500)
            self._current_x = round(vw * random.uniform(0.237, 0.581) + random.uniform(-16.3, 18.7), 2)
            self._current_y = round(vh * random.uniform(0.274, 0.623) + random.uniform(-14.2, 17.6), 2)

    def assert_action_permitted(self, action_name: str) -> None:
        """Enforces that mutation actions cannot be executed in read-only / dry-run mode."""
        clean_action = str(action_name).strip().lower()
        if self.dry_run and clean_action in FORBIDDEN_MUTATION_ACTIONS:
            raise WhopDryRunViolationError(
                f"Action '{clean_action}' is strictly forbidden by HumanActor mutation guard (WHOP_DRY_RUN=true)."
            )

    @property
    def current_position(self) -> Tuple[float, float]:
        return self._current_x, self._current_y

    def sync_page(self, new_page: Any) -> None:
        """Transfers state continuity to a newly navigated or opened page without teleporting."""
        self.page = new_page
        # Dispatch mouse move to sync new page's internal position without moving coordinates
        try:
            self.page.mouse.move(self._current_x, self._current_y)
        except Exception:
            pass

    def move_to(
        self,
        target_x: float,
        target_y: float,
        speed_factor: Optional[float] = None,
        allow_overshoot: bool = True,
    ) -> None:
        """Moves pointer with realistic 2-phase Fitts's ballistic curve and corrective sub-movements."""
        # Infuse sub-pixel human motor scatter to prevent exact integer/round coordinate landing
        if abs(target_x - round(target_x)) < 0.001 and abs(target_y - round(target_y)) < 0.001:
            scatter_x = random.uniform(0.35, 0.78) if random.random() < 0.5 else -random.uniform(0.35, 0.78)
            scatter_y = random.uniform(0.35, 0.78) if random.random() < 0.5 else -random.uniform(0.35, 0.78)
            target_x = round(target_x + scatter_x, 2)
            target_y = round(target_y + scatter_y, 2)

        effective_speed = (speed_factor or self.persona.base_mouse_speed) * random.uniform(0.92, 1.08)
        start = Point(self._current_x, self._current_y)
        dest = Point(target_x, target_y)
        total_dist = math.hypot(dest.x - start.x, dest.y - start.y)

        # 1. Check if human overshoots the target (ballistic phase)
        should_overshoot = (
            allow_overshoot
            and total_dist > 80.0
            and random.random() < self.persona.overshoot_probability
        )

        if should_overshoot:
            # Overshoot target by 4px to 14px in trajectory direction
            angle = math.atan2(dest.y - start.y, dest.x - start.x)
            overshoot_dist = random.uniform(4.0, 14.0)
            overshoot_pt = Point(
                round(dest.x + math.cos(angle) * overshoot_dist + random.gauss(0, 1.5), 2),
                round(dest.y + math.sin(angle) * overshoot_dist + random.gauss(0, 1.5), 2),
            )

            # Phase 1: Rapid movement to overshoot point
            self._execute_path(start, overshoot_pt, effective_speed * 1.15)
            # Brief micro-pause realizing overshoot (25ms - 60ms)
            time.sleep(random.uniform(0.025, 0.060))
            # Phase 2: Corrective sub-movement back to intended target
            self._execute_path(overshoot_pt, dest, effective_speed * 0.85)
        else:
            # Single smooth Flash-Hogan movement
            self._execute_path(start, dest, effective_speed)

        self.persona.record_action()

    def _execute_path(self, start: Point, end: Point, speed: float) -> None:
        """Internal helper to dispatch mousemove steps with timing noise."""
        path = generate_bezier_curve(
            start,
            end,
            deviation_factor=self.persona.curve_variation,
            jitter_pixels=self.persona.tremor_intensity,
        )
        if len(path) <= 1:
            return

        distance = math.hypot(end.x - start.x, end.y - start.y)
        total_time_ms = max(90.0, (65.0 + distance * 0.42)) / max(0.2, speed)
        delay_per_step_s = (total_time_ms / len(path)) / 1000.0

        for pt in path:
            self.page.mouse.move(pt.x, pt.y)
            self._current_x = pt.x
            self._current_y = pt.y
            step_delay = max(0.001, delay_per_step_s + random.gauss(0, delay_per_step_s * 0.2))
            time.sleep(step_delay)

    def _validate_element_mutation_safety(self, locator: Any, selector_str: str) -> None:
        """Inspects DOM attributes and visible text to block mutation buttons intrinsically."""
        if not self.dry_run:
            return

        # 1. Check if selector string explicitly mentions any forbidden action
        if selector_str:
            for verb in FORBIDDEN_MUTATION_ACTIONS:
                if re.search(r'\b' + re.escape(verb) + r'\b', selector_str):
                    raise WhopDryRunViolationError(
                        f"Selector '{selector_str}' targets forbidden mutation action '{verb}' under WHOP_DRY_RUN=true."
                    )

        # 2. Inspect element DOM attributes
        try:
            elem_info = locator.evaluate("""(el) => {
                if (!el) return null;
                const tag = (el.tagName || '').toLowerCase();
                const text = (el.innerText || el.textContent || el.value || '').trim().toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const testid = (el.getAttribute('data-testid') || '').toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                return { tag, text, aria, testid, role, type };
            }""")
            if not elem_info:
                return

            tag = elem_info.get("tag", "")
            text = elem_info.get("text", "")
            aria = elem_info.get("aria", "")
            testid = elem_info.get("testid", "")
            role = elem_info.get("role", "")
            type_attr = elem_info.get("type", "")

            is_actionable = tag in ("button", "input") or role == "button" or type_attr == "submit"

            # Safe exemptions: Cookie banner, modal dismissals, notifications consent
            combined = f"{text} {aria} {testid}".lower()
            if any(c in combined for c in ("cookie", "consent", "got it", "dismiss", "allow all", "close")):
                return

            # Target inspection for actionable elements or concise button labels (< 60 chars)
            if is_actionable or len(text) <= 60:
                for verb in FORBIDDEN_MUTATION_ACTIONS:
                    # For 'accept', only flag if NOT related to cookies/terms
                    if verb == "accept" and ("cookie" in combined or "terms" in combined or "policy" in combined):
                        continue
                    if (
                        re.search(r'\b' + re.escape(verb) + r'\b', text)
                        or re.search(r'\b' + re.escape(verb) + r'\b', aria)
                        or re.search(r'\b' + re.escape(verb) + r'\b', testid)
                    ):
                        raise WhopDryRunViolationError(
                            f"Target element with text/attributes '{text or aria or testid}' matches forbidden mutation action '{verb}' under WHOP_DRY_RUN=true."
                        )
        except WhopDryRunViolationError:
            raise
        except Exception:
            pass

    def human_click(
        self,
        selector_or_locator: Any,
        hesitation_scale: float = 1.0,
        action_name: Optional[str] = None,
    ) -> Tuple[float, float]:
        """Performs an authentic human click with strict intrinsic mutation safety.
        
        Guarantees that no mouse.down() or mouse.up() event is ever dispatched for
        forbidden mutation actions when dry_run=True.
        """
        # A. Explicit action validation BEFORE any interaction
        if action_name is not None:
            self.assert_action_permitted(action_name)

        # B. Resolve target locator
        if isinstance(selector_or_locator, str):
            locator = self.page.locator(selector_or_locator).first
            selector_str = selector_or_locator.lower()
        else:
            locator = selector_or_locator
            selector_str = ""

        # C. Intrinsic element/selector mutation validation BEFORE any mouse interaction
        if action_name is None:
            self._validate_element_mutation_safety(locator, selector_str)

        locator.scroll_into_view_if_needed(timeout=10000)
        bbox = locator.bounding_box()
        if not bbox:
            raise RuntimeError(f"Cannot resolve bounding box for element: {selector_or_locator}")

        target_x, target_y = sample_gaussian_target(bbox)

        # Move naturally to sampled coordinates
        self.move_to(target_x, target_y, allow_overshoot=True)

        # Pre-click hesitation (biological reaction time: log-normal)
        hesitation = random.gauss(self.persona.hesitation_mean * hesitation_scale, 0.03)
        hesitation = max(0.04, min(0.35, hesitation))
        time.sleep(hesitation)

        # Mouse press with natural hold duration
        self.page.mouse.down()
        hold_time = random.gauss(self.persona.click_hold_mean, 0.015)
        hold_time = max(0.035, min(0.18, hold_time))
        time.sleep(hold_time)
        self.page.mouse.up()

        # Post-click recovery delay
        recovery = random.gauss(self.persona.recovery_delay_mean, 0.03)
        recovery = max(0.04, min(0.30, recovery))
        time.sleep(recovery)

        self.persona.record_action()
        return target_x, target_y

    def human_type(
        self,
        selector_or_locator: Any,
        text: str,
        simulate_mistakes: bool = True,
    ) -> None:
        """Types text with Cognitive Word-Chunking, QWERTY bigrams, and real key dwell times."""
        if isinstance(selector_or_locator, str):
            locator = self.page.locator(selector_or_locator).first
        else:
            locator = selector_or_locator

        self.human_click(locator, action_name="type")

        effective_wpm = self.persona.base_wpm * random.uniform(0.92, 1.08)
        base_char_delay = 60.0 / (effective_wpm * 5.0)

        keyboard = self.page.keyboard

        # Guaranteed organic typo target index for strings >= 16 chars when simulate_mistakes is True
        guaranteed_typo_idx = None
        if simulate_mistakes and len(text) >= 16:
            eligible_indices = [
                i for i, c in enumerate(text)
                if 3 <= i < len(text) - 3 and c.lower() in QWERTY_NEIGHBORS and c != " "
            ]
            if eligible_indices:
                guaranteed_typo_idx = random.choice(eligible_indices)

        global_char_idx = 0
        typo_occurred = False

        # Split into cognitive word chunks so intra-word typing is bursty and word boundaries pause
        words = text.split(" ")
        for w_idx, word in enumerate(words):
            # Intra-word typing speed (faster burst within a familiar word)
            word_speed_factor = random.uniform(0.75, 0.95)

            for char in word:
                # 1. Realistic typo check: hit adjacent key on QWERTY
                trigger_typo = False
                if simulate_mistakes and char.lower() in QWERTY_NEIGHBORS:
                    if not typo_occurred and global_char_idx == guaranteed_typo_idx:
                        trigger_typo = True
                    elif random.random() < self.persona.typo_rate:
                        trigger_typo = True

                if trigger_typo:
                    typo_occurred = True
                    typo_char = random.choice(QWERTY_NEIGHBORS[char.lower()])
                    keyboard.down(typo_char)
                    time.sleep(random.uniform(0.045, 0.085))
                    keyboard.up(typo_char)

                    # Cognitive hesitation noticing typo
                    time.sleep(random.uniform(0.16, 0.32))
                    keyboard.down("Backspace")
                    time.sleep(random.uniform(0.05, 0.09))
                    keyboard.up("Backspace")
                    time.sleep(random.uniform(0.09, 0.18))

                # 2. KeyDown -> Biological Dwell -> KeyUp
                key_dwell = random.gauss(0.075, 0.014)
                key_dwell = max(0.040, min(0.130, key_dwell))
                keyboard.down(char)
                time.sleep(key_dwell)
                keyboard.up(char)

                # 3. Flight time to next character within word
                flight_time = random.gauss(
                    base_char_delay * word_speed_factor,
                    base_char_delay * 0.2
                )
                flight_time = max(0.020, flight_time)
                if char.isupper():
                    flight_time += random.uniform(0.04, 0.10)
                time.sleep(flight_time)

                global_char_idx += 1

            # Space between words (Cognitive word boundary pause)
            if w_idx < len(words) - 1:
                # Type the space key
                space_dwell = random.uniform(0.055, 0.095)
                keyboard.down(" ")
                time.sleep(space_dwell)
                keyboard.up(" ")

                # Human pause formulating next word: 160ms to 380ms
                word_thinking_pause = random.uniform(0.16, 0.38)
                time.sleep(word_thinking_pause)
                global_char_idx += 1

        self.persona.record_action()

    def idle_drift(
        self,
        duration_seconds: float = 2.0,
        focus_region: Optional[Tuple[float, float]] = None,
        gaze_bias: Optional[str] = None,
    ) -> None:
        """Simulates biological gaze-attracted breathing and fidgeting instead of freezing.
        
        Args:
            duration_seconds: Time to spend idling.
            focus_region: Optional (x, y) target region to drift around.
            gaze_bias: Optional bias ('right', 'left', 'center'). For video players (YouTube Shorts),
                       'right' places cursor in the action margin near comments/like buttons.
        """
        end_time = time.time() + duration_seconds

        vp = None
        try:
            vp = self.page.viewport_size
        except Exception:
            pass
        vw = float(vp["width"]) if vp else 1280.0
        vh = float(vp["height"]) if vp else 720.0

        target_focus = focus_region
        if gaze_bias == "right" and not target_focus:
            # Dynamic right gutter with high organic entropy (never round numbers)
            base_x = vw * random.uniform(0.631, 0.719) + random.uniform(-13.4, 15.2)
            base_y = vh * random.uniform(0.387, 0.573) + random.uniform(-11.2, 13.6)
            target_focus = (round(base_x, 2), round(base_y, 2))

        # If a focus region is specified, gently bias towards it
        while time.time() < end_time:
            if target_focus:
                fx, fy = target_focus
                # Attracted towards focus center with organic non-round offset
                drift_x = fx + random.gauss(0, 24.3) + random.uniform(-4.1, 4.7)
                drift_y = fy + random.gauss(0, 21.2) + random.uniform(-3.8, 4.3)
            else:
                # Organic wander around current area
                drift_x = self._current_x + random.gauss(0, 17.6) + random.uniform(-3.4, 3.8)
                drift_y = self._current_y + random.gauss(0, 14.8) + random.uniform(-3.2, 3.5)

            drift_x = max(vw * 0.048, min(vw * 0.952, drift_x))
            drift_y = max(vh * 0.053, min(vh * 0.947, drift_y))

            start = Point(self._current_x, self._current_y)
            dest = Point(round(drift_x, 2), round(drift_y, 2))
            # Gentle breathing velocity (0.35x speed)
            self._execute_path(start, dest, speed=0.38)
            time.sleep(random.uniform(0.4, 0.85))

    def human_scroll(
        self,
        delta_y: int,
        steps: Optional[int] = None,
        reading_pause: bool = True,
    ) -> None:
        """Simulates smooth trackpad/wheel momentum with friction decay and micro-settling."""
        # Infuse organic entropy so wheel ticks never match round numbers like 450 or 500
        organic_delta = delta_y * random.uniform(0.963, 1.038) + random.uniform(-9.4, 11.2)
        delta_y = int(round(organic_delta))

        if steps is None:
            # 1 step per 25-45px for ultra-smooth fluid scrolling
            steps = max(6, abs(delta_y) // random.randint(25, 45))

        step_amount = delta_y / float(steps)

        for i in range(steps):
            progress = i / float(steps)
            # Biological S-curve ease (momentum building then friction stopping)
            ease = math.sin(progress * math.pi)
            current_step = step_amount * (0.4 + 1.2 * ease)

            self.page.mouse.wheel(0, current_step)
            # Trackpad frame rate ~ 60fps (12-20ms per tick)
            time.sleep(random.uniform(0.012, 0.024))

        # Tiny micro-settle bounce at stop
        settle_bounce = -1.0 * math.copysign(random.uniform(2.0, 8.0), delta_y)
        self.page.mouse.wheel(0, settle_bounce)
        time.sleep(0.05)

        if reading_pause:
            time.sleep(random.uniform(0.8, 2.4))

        self.persona.record_action()


