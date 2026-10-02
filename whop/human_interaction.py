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
    """Wraps a Playwright Page with biologically authentic human interaction dynamics."""

    def __init__(self, page: Any):
        self.page = page
        self._current_x: float = random.uniform(100.0, 400.0)
        self._current_y: float = random.uniform(100.0, 300.0)

    @property
    def current_position(self) -> Tuple[float, float]:
        return self._current_x, self._current_y

    def move_to(
        self,
        target_x: float,
        target_y: float,
        speed_factor: float = 1.0,
    ) -> None:
        """Moves mouse pointer along a natural Bezier curve to target coordinates."""
        start = Point(self._current_x, self._current_y)
        end = Point(target_x, target_y)

        path = generate_bezier_curve(start, end)
        if len(path) <= 1:
            return

        distance = math.hypot(end.x - start.x, end.y - start.y)
        total_time_ms = max(120.0, (80.0 + distance * 0.45)) / max(0.2, speed_factor)
        delay_per_step_s = (total_time_ms / len(path)) / 1000.0

        for pt in path:
            self.page.mouse.move(pt.x, pt.y)
            self._current_x = pt.x
            self._current_y = pt.y
            jittered_delay = max(0.001, delay_per_step_s + random.uniform(-0.001, 0.001))
            time.sleep(jittered_delay)

    def human_click(
        self,
        selector_or_locator: Any,
        pre_delay_range: Tuple[float, float] = (0.08, 0.22),
        hold_time_range: Tuple[float, float] = (0.045, 0.115),
        post_delay_range: Tuple[float, float] = (0.08, 0.25),
    ) -> Tuple[float, float]:
        """Executes a human-physics click on an element."""
        if isinstance(selector_or_locator, str):
            locator = self.page.locator(selector_or_locator).first
        else:
            locator = selector_or_locator

        locator.scroll_into_view_if_needed(timeout=10000)
        bbox = locator.bounding_box()
        if not bbox:
            raise RuntimeError(f"Cannot resolve bounding box for element: {selector_or_locator}")

        target_x, target_y = sample_gaussian_target(bbox)

        self.move_to(target_x, target_y)

        pre_delay = random.uniform(*pre_delay_range)
        time.sleep(pre_delay)

        self.page.mouse.down()
        hold_time = random.uniform(*hold_time_range)
        time.sleep(hold_time)
        self.page.mouse.up()

        post_delay = random.uniform(*post_delay_range)
        time.sleep(post_delay)

        return target_x, target_y

    def human_type(
        self,
        selector_or_locator: Any,
        text: str,
        wpm_range: Tuple[int, int] = (45, 75),
        simulate_mistakes: bool = True,
        mistake_probability: float = 0.02,
    ) -> None:
        """Types text into an input field with realistic inter-keystroke cadence."""
        if isinstance(selector_or_locator, str):
            locator = self.page.locator(selector_or_locator).first
        else:
            locator = selector_or_locator

        self.human_click(locator)

        min_wpm, max_wpm = wpm_range
        avg_wpm = random.uniform(min_wpm, max_wpm)
        avg_char_delay = 60.0 / (avg_wpm * 5.0)

        keyboard = self.page.keyboard

        for char in text:
            if simulate_mistakes and random.random() < mistake_probability and char.isalnum():
                typo_char = random.choice("abcdefghijklmnopqrstuvwxyz")
                keyboard.press(typo_char)
                time.sleep(random.uniform(0.12, 0.28))
                keyboard.press("Backspace")
                time.sleep(random.uniform(0.08, 0.18))

            keyboard.press(char)

            char_delay = random.gauss(avg_char_delay, avg_char_delay * 0.3)
            char_delay = max(0.025, char_delay)

            if char in " .!?,:\\n":
                char_delay += random.uniform(0.08, 0.22)
            elif char.isupper():
                char_delay += random.uniform(0.04, 0.10)

            time.sleep(char_delay)

    def human_scroll(
        self,
        delta_y: int,
        steps: Optional[int] = None,
        reading_pause: bool = True,
    ) -> None:
        """Scrolls the page with realistic mouse-wheel bursts and reading inertia."""
        if steps is None:
            steps = max(3, abs(delta_y) // 100)

        step_amount = delta_y / float(steps)

        for i in range(steps):
            progress = i / float(steps)
            ease = math.sin(progress * math.pi)
            current_step = step_amount * (0.6 + 0.8 * ease)

            self.page.mouse.wheel(0, current_step)
            time.sleep(random.uniform(0.03, 0.08))

        if reading_pause:
            time.sleep(random.uniform(0.5, 1.8))

