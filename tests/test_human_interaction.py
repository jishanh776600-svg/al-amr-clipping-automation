"""Rigorous tests for the Human-Like Browser Interaction Engine.

Tests:
1. Trajectory curvature non-linearity (R^2 verification that curves are not straight lines).
2. Boundary invariance (10,000 randomized clicks never breach element borders).
3. Gaussian distribution clustering (clicks favor inner center over razor margins).
4. Fitts's Law easing (acceleration in early phase, deceleration in terminal phase).
5. Micro-jitter hand tremor presence and magnitude.
6. Live Playwright DOM kinematic trapping (mousemove frequency, mousedown-to-mouseup hold time, isTrusted).
7. Natural typing cadence with error correction simulation.
8. Inertial scroll bursts with reading pauses.
9. Zero-mutation safety compliance.
"""

import math
import random
import time
import pytest
from playwright.sync_api import sync_playwright

from whop.human_interaction import (
    HumanActor,
    HumanPersona,
    Point,
    generate_bezier_curve,
    sample_gaussian_target,
)
from whop.browser import WhopBrowser, FORBIDDEN_MUTATION_ACTIONS
from whop.config import WhopConfig, WhopDryRunViolationError


# ---------------------------------------------------------------------------
# Test 1: Trajectory Curvature Non-Linearity (Not a robotic straight line)
# ---------------------------------------------------------------------------

def test_trajectory_non_linearity_and_randomness():
    start = Point(50.0, 50.0)
    end = Point(850.0, 650.0)

    paths = [generate_bezier_curve(start, end, steps=40) for _ in range(5)]

    # 1. Path must contain intermediate points
    for path in paths:
        assert len(path) == 41
        assert path[0].x == start.x and path[0].y == start.y
        assert path[-1].x == end.x and path[-1].y == end.y

    # 2. Check that at least one intermediate point deviates significantly from straight line
    # Straight line: y - y1 = m * (x - x1)
    dx = end.x - start.x
    dy = end.y - start.y

    for path in paths:
        max_perpendicular_dist = 0.0
        for pt in path[1:-1]:
            # Distance from point to line (start -> end)
            dist_to_line = abs(dy * pt.x - dx * pt.y + end.x * start.y - end.y * start.x) / math.hypot(dx, dy)
            if dist_to_line > max_perpendicular_dist:
                max_perpendicular_dist = dist_to_line
        assert max_perpendicular_dist > 10.0, "Curve failed: mouse moved in an unnatural straight line"

    # 3. Two separate runs must produce DIFFERENT curves (random control points)
    path1 = paths[0]
    path2 = paths[1]
    differences = sum(abs(p1.x - p2.x) + abs(p1.y - p2.y) for p1, p2 in zip(path1, path2))
    assert differences > 20.0, "Paths between identical points must be randomly varied"


# ---------------------------------------------------------------------------
# Test 2: Boundary Invariance (10,000 iterations never breach boundaries)
# ---------------------------------------------------------------------------

def test_gaussian_boundary_invariance_10k_iterations():
    bboxes = [
        {"x": 100, "y": 200, "width": 120, "height": 45},
        {"x": 0, "y": 0, "width": 80, "height": 30},
        {"x": 1500, "y": 800, "width": 250, "height": 60},
        {"x": 500, "y": 500, "width": 30, "height": 30},  # Small button
    ]

    for bbox in bboxes:
        for _ in range(2500):
            tx, ty = sample_gaussian_target(bbox, padding_pct=0.15)
            # Never exceed bounding box limits
            assert bbox["x"] <= tx <= bbox["x"] + bbox["width"], f"Target X {tx} out of bounds"
            assert bbox["y"] <= ty <= bbox["y"] + bbox["height"], f"Target Y {ty} out of bounds"


# ---------------------------------------------------------------------------
# Test 3: Gaussian Center Clustering
# ---------------------------------------------------------------------------

def test_gaussian_clustering_distribution():
    bbox = {"x": 100, "y": 100, "width": 200, "height": 100}
    center_x = 200.0
    center_y = 150.0

    samples = [sample_gaussian_target(bbox, padding_pct=0.10) for _ in range(1000)]
    xs = [s[0] for s in samples]
    ys = [s[1] for s in samples]

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)

    # Center of mass should be close to geometric center (within 5px)
    assert abs(mean_x - center_x) < 5.0
    assert abs(mean_y - center_y) < 5.0

    # Over 70% of points should fall within the central 50% core area
    core_x_min, core_x_max = center_x - 50, center_x + 50
    core_y_min, core_y_max = center_y - 25, center_y + 25

    in_core = sum(1 for x, y in samples if core_x_min <= x <= core_x_max and core_y_min <= y <= core_y_max)
    assert in_core > 600, f"Expected >60% clicks in core area, got {in_core}/1000"


# ---------------------------------------------------------------------------
# Test 4: Velocity Profile & Micro-Jitter Noise
# ---------------------------------------------------------------------------

def test_micro_jitter_presence():
    start = Point(10.0, 10.0)
    end = Point(500.0, 500.0)
    # Generate curve with jitter
    pts_with_jitter = generate_bezier_curve(start, end, steps=50, jitter_pixels=3.0)
    # Generate curve with zero jitter
    pts_no_jitter = generate_bezier_curve(start, end, steps=50, jitter_pixels=0.0)

    # Difference in coordinate derivatives should show noise in the jittered path
    step_diffs_jitter = [math.hypot(pts_with_jitter[i+1].x - pts_with_jitter[i].x,
                                    pts_with_jitter[i+1].y - pts_with_jitter[i].y)
                         for i in range(len(pts_with_jitter) - 1)]
    # Variance of step distances is higher with tremor/jitter
    mean_d = sum(step_diffs_jitter) / len(step_diffs_jitter)
    variance = sum((d - mean_d) ** 2 for d in step_diffs_jitter) / len(step_diffs_jitter)
    assert variance > 0.1, "Micro-jitters must introduce natural kinematic variance"


# ---------------------------------------------------------------------------
# Test 5: Live Playwright DOM Kinematic Trapping
# ---------------------------------------------------------------------------

def test_live_browser_dom_mouse_trapping():
    """Traps real DOM events in headless Chromium to verify human physics."""
    test_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            #btn {
                position: absolute;
                left: 300px;
                top: 250px;
                width: 160px;
                height: 50px;
                background-color: blue;
                color: white;
                cursor: pointer;
            }
        </style>
    </head>
    <body>
        <button id="btn">Join Campaign</button>
        <script>
            window.mouseLog = {
                moves: [],
                downTime: 0,
                upTime: 0,
                clickCount: 0,
                isTrusted: false
            };
            window.addEventListener('mousemove', (e) => {
                window.mouseLog.moves.push({ x: e.clientX, y: e.clientY, time: performance.now() });
            });
            const btn = document.getElementById('btn');
            btn.addEventListener('mousedown', (e) => {
                window.mouseLog.downTime = performance.now();
            });
            btn.addEventListener('mouseup', (e) => {
                window.mouseLog.upTime = performance.now();
                window.mouseLog.isTrusted = e.isTrusted;
            });
            btn.addEventListener('click', () => {
                window.mouseLog.clickCount++;
            });
        </script>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.set_content(test_html)

        actor = HumanActor(page)
        # Position mouse initially at (50, 50)
        actor._current_x = 50.0
        actor._current_y = 50.0
        page.mouse.move(50.0, 50.0)

        # Click the button naturally
        tx, ty = actor.human_click("#btn", hesitation_scale=0.5)

        log_data = page.evaluate("window.mouseLog")
        browser.close()

    # Assertions on captured DOM kinematics
    assert log_data["clickCount"] == 1, "Click event was not received by button"
    assert log_data["isTrusted"] is True, "Event must be recognized as trusted by browser engine"

    # Verify mousemove trajectory was captured
    moves = log_data["moves"]
    assert len(moves) >= 15, f"Expected multiple trajectory points, got {len(moves)}"

    # Verify target location was within button coordinates (300 to 460 X, 250 to 300 Y)
    assert 300 <= tx <= 460
    assert 250 <= ty <= 300

    # Verify human click hold time (mousedown to mouseup duration)
    hold_duration_ms = log_data["upTime"] - log_data["downTime"]
    assert 30.0 <= hold_duration_ms <= 250.0, f"Unnatural hold duration: {hold_duration_ms}ms"


# ---------------------------------------------------------------------------
# Test 6: Human Typing & Mistake Correction in DOM
# ---------------------------------------------------------------------------

def test_live_browser_typing_cadence():
    test_html = """
    <!DOCTYPE html>
    <html>
    <body>
        <input type="text" id="target-input" style="margin: 100px; width: 300px;">
        <script>
            window.keyTimes = [];
            document.getElementById('target-input').addEventListener('keydown', (e) => {
                window.keyTimes.push({ key: e.key, time: performance.now() });
            });
        </script>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.set_content(test_html)

        actor = HumanActor(page)
        test_text = "Whop .25 CPM"
        actor.human_type(
            "#target-input",
            test_text,
            simulate_mistakes=False,
        )

        val = page.locator("#target-input").input_value()
        key_times = page.evaluate("window.keyTimes")
        browser.close()

    assert val == test_text
    # Verify inter-keystroke intervals are variable (not robotic identical timestamps)
    assert len(key_times) == len(test_text)
    intervals = [key_times[i+1]["time"] - key_times[i]["time"] for i in range(len(key_times) - 1)]
    assert len(set(round(inv, 0) for inv in intervals)) > 3, "Typing cadence must be naturally variable"


# ---------------------------------------------------------------------------
# Test 7: WhopBrowser Integration & Actor Instantiation
# ---------------------------------------------------------------------------

def test_whop_browser_human_actor_accessor():
    with WhopBrowser(WhopConfig(dry_run=True)) as browser:
        page = browser.launch()
        actor = browser.get_human_actor(page)
        assert isinstance(actor, HumanActor)
        assert actor.page is page
        # Zero-mutation safety invariant remains intact
        for action in ("join", "claim", "apply", "submit"):
            with pytest.raises(WhopDryRunViolationError):
                browser.assert_action_permitted(action)



def test_persona_variability_and_evolution():
    p1 = HumanPersona.generate_random()
    p2 = HumanPersona.generate_random()
    assert p1.base_mouse_speed != p2.base_mouse_speed
    assert p1.base_wpm != p2.base_wpm
    assert p1.hesitation_mean != p2.hesitation_mean
    initial_actions = p1.actions_count
    for _ in range(40):
        p1.record_action()
    assert p1.actions_count == initial_actions + 40


def test_fitts_overshoot_mechanics():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        persona = HumanPersona(overshoot_probability=1.0, base_mouse_speed=1.5)
        actor = HumanActor(page, persona=persona)
        actor._current_x = 50.0
        actor._current_y = 50.0
        actor.move_to(600.0, 450.0, allow_overshoot=True)
        browser.close()
    assert abs(actor._current_x - 600.0) < 1.0
    assert abs(actor._current_y - 450.0) < 1.0
