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
import random
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

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

        eased_u = u * u * (3.0 - 2.0 * u)

        c0 = (1.0 - eased_u) ** 3
        c1 = 3.0 * ((1.0 - eased_u) ** 2) * eased_u
        c2 = 3.0 * (1.0 - eased_u) * (eased_u ** 2)
        c3 = eased_u ** 3

        px = c0 * start.x + c1 * cp1_x + c2 * cp2_x + c3 * end.x
        py = c0 * start.y + c1 * cp1_y + c2 * cp2_y + c3 * end.y

        if 0 < i < steps:
            jitter_weight = math.sin(math.pi * u)
            px += random.gauss(0, jitter_pixels * 0.5) * jitter_weight
            py += random.gauss(0, jitter_pixels * 0.5) * jitter_weight

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

    target_x = max(inner_min_x, min(inner_max_x, target_x))
    target_y = max(inner_min_y, min(inner_max_y, target_y))

    return round(target_x, 2), round(target_y, 2)


class HumanActor:
    """Full-spectrum bio-mimetic browser actor for undetectable automation."""

    def __init__(self, page: Any, persona: Optional[HumanPersona] = None):
        self.page = page
        self.persona = persona or HumanPersona.generate_random()
        self._current_x: float = random.uniform(150.0, 500.0)
        self._current_y: float = random.uniform(150.0, 400.0)

    @property
    def current_position(self) -> Tuple[float, float]:
        return self._current_x, self._current_y

    def move_to(
        self,
        target_x: float,
        target_y: float,
        speed_factor: Optional[float] = None,
        allow_overshoot: bool = True,
    ) -> None:
        """Moves pointer with realistic 2-phase Fitts's ballistic curve and corrective sub-movements."""
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
            # Single smooth Bezier movement
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

    def human_click(
        self,
        selector_or_locator: Any,
        hesitation_scale: float = 1.0,
    ) -> Tuple[float, float]:
        """Performs an authentic human click:
        - Resolves target coordinates via Gaussian distribution.
        - Moves with potential overshoot & micro-correction.
        - Hesitates based on persona reaction time.
        - Natural mouse-down hold duration.
        - Post-click recovery delay.
        """
        if isinstance(selector_or_locator, str):
            locator = self.page.locator(selector_or_locator).first
        else:
            locator = selector_or_locator

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
        """Types text with QWERTY bigram mechanics, realistic typo corrections, and burstiness."""
        if isinstance(selector_or_locator, str):
            locator = self.page.locator(selector_or_locator).first
        else:
            locator = selector_or_locator

        # Natural click into input field
        self.human_click(locator)

        effective_wpm = self.persona.base_wpm * random.uniform(0.9, 1.1)
        base_char_delay = 60.0 / (effective_wpm * 5.0)

        keyboard = self.page.keyboard
        prev_char = ""

        for char in text:
            # 1. Realistic typo check: hit adjacent key on QWERTY keyboard
            if (
                simulate_mistakes
                and char.lower() in QWERTY_NEIGHBORS
                and random.random() < self.persona.typo_rate
            ):
                typo_char = random.choice(QWERTY_NEIGHBORS[char.lower()])
                keyboard.press(typo_char)
                time.sleep(random.uniform(0.12, 0.28))
                keyboard.press("Backspace")
                time.sleep(random.uniform(0.08, 0.16))

            # 2. Press actual character
            keyboard.press(char)

            # 3. Dynamic delay calculation (bigram alternation and punctuation)
            char_delay = random.gauss(
                base_char_delay,
                base_char_delay * self.persona.typing_rhythm_entropy
            )
            char_delay = max(0.02, char_delay)

            # Same character or space double-tap takes slightly longer
            if prev_char and prev_char.lower() == char.lower():
                char_delay += random.uniform(0.03, 0.07)

            # Punctuation / word-boundary pauses (thinking pauses)
            if char in " .!?,:\n":
                char_delay += random.uniform(0.08, 0.24)
            elif char.isupper():
                char_delay += random.uniform(0.04, 0.11)

            time.sleep(char_delay)
            prev_char = char

        self.persona.record_action()

    def idle_drift(self, duration_seconds: float = 1.5) -> None:
        """Simulates passive cursor drifting and fidgeting while user reads or waits."""
        end_time = time.time() + duration_seconds
        while time.time() < end_time:
            drift_x = self._current_x + random.gauss(0, 18)
            drift_y = self._current_y + random.gauss(0, 15)
            drift_x = max(10.0, min(1800.0, drift_x))
            drift_y = max(10.0, min(950.0, drift_y))

            start = Point(self._current_x, self._current_y)
            dest = Point(drift_x, drift_y)
            self._execute_path(start, dest, speed=0.45)
            time.sleep(random.uniform(0.2, 0.6))

    def human_scroll(
        self,
        delta_y: int,
        steps: Optional[int] = None,
        reading_pause: bool = True,
    ) -> None:
        """Inertial wheel scrolling with natural acceleration bursts and content inspection pauses."""
        if steps is None:
            steps = max(3, abs(delta_y) // random.randint(80, 130))

        step_amount = delta_y / float(steps)

        for i in range(steps):
            progress = i / float(steps)
            ease = math.sin(progress * math.pi)
            current_step = step_amount * (0.55 + 0.9 * ease)

            self.page.mouse.wheel(0, current_step)
            time.sleep(random.uniform(0.025, 0.075))

        if reading_pause:
            time.sleep(random.uniform(0.6, 2.2))

        self.persona.record_action()


