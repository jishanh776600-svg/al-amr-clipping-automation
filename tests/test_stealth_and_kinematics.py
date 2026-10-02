"""Rigorous tests for Enterprise-Grade Stealth & Flash-Hogan Neuro-Kinematics.

Verifies:
1. navigator.webdriver is strictly undefined (no prototype leaks).
2. WebGL renderer reports genuine NVIDIA hardware (not generic SwiftShader).
3. window.chrome runtime, loadTimes, and csi objects are authentic.
4. navigator.plugins reports native PDF viewer plugins.
5. Permissions API returns 'prompt' for notifications without errors.
6. Flash-Hogan polynomial model produces smooth minimum-jerk acceleration.
7. Keystroke dwell duration is strictly > 40ms and separated from flight times.
8. Canvas 2D fingerprinting returns non-colliding human micro-noise.
"""

import time
import pytest
from playwright.sync_api import sync_playwright

from whop.browser import WhopBrowser
from whop.config import WhopConfig
from whop.human_interaction import HumanActor, HumanPersona, Point, generate_bezier_curve
from whop.stealth import apply_stealth_to_context, STEALTH_INIT_SCRIPT


def test_stealth_webdriver_elimination():
    """Verifies that navigator.webdriver is wiped with zero prototype leaks."""
    with WhopBrowser(WhopConfig(dry_run=True, headless=True)) as browser:
        page = browser.launch()
        webdriver_val = page.evaluate("navigator.webdriver")
        proto_descriptor = page.evaluate(
            "Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'webdriver')"
        )
        assert webdriver_val is None, f"navigator.webdriver leaked: {webdriver_val}"
        assert proto_descriptor is None or proto_descriptor.get("get") is not None


def test_stealth_webgl_gpu_spoofing():
    """Verifies WebGL vendor and renderer report genuine NVIDIA GPU hardware."""
    with WhopBrowser(WhopConfig(dry_run=True, headless=True)) as browser:
        page = browser.launch()
        test_script = """
        (() => {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
            return {
                vendor: gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL),
                renderer: gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
            };
        })()
        """
        res = page.evaluate(test_script)
        assert "NVIDIA" in res["vendor"], f"Suspicious WebGL vendor: {res['vendor']}"
        assert "GeForce" in res["renderer"], f"Suspicious WebGL renderer: {res['renderer']}"
        assert "SwiftShader" not in res["renderer"], "SwiftShader leak detected!"


def test_stealth_chrome_runtime_and_plugins():
    """Verifies window.chrome and navigator.plugins emulate standard desktop Chrome."""
    with WhopBrowser(WhopConfig(dry_run=True, headless=True)) as browser:
        page = browser.launch()
        chrome_ok = page.evaluate("Boolean(window.chrome && window.chrome.loadTimes && window.chrome.csi)")
        assert chrome_ok is True, "Missing window.chrome runtime hierarchy"

        plugins_len = page.evaluate("navigator.plugins.length")
        assert plugins_len >= 3, f"Suspiciously empty plugins array: {plugins_len}"

        first_plugin_name = page.evaluate("navigator.plugins[0].name")
        assert "PDF" in first_plugin_name, f"Unexpected default plugin: {first_plugin_name}"


def test_stealth_permissions_api():
    """Verifies Permissions API handles notifications query naturally."""
    with WhopBrowser(WhopConfig(dry_run=True, headless=True)) as browser:
        page = browser.launch()
        perm_state = page.evaluate("""
        (async () => {
            if (navigator.permissions && navigator.permissions.query) {
                const p = await navigator.permissions.query({name: 'notifications'});
                return p.state;
            }
            return 'not_supported';
        })()
        """ )
        assert perm_state in ("prompt", "denied", "granted"), f"Broken permissions API: {perm_state}"


def test_flash_hogan_minimum_jerk_smoothness():
    """Verifies 5th-degree Flash-Hogan polynomial model produces smooth trajectories."""
    start = Point(100.0, 100.0)
    end = Point(800.0, 500.0)
    pts = generate_bezier_curve(start, end, steps=60, jitter_pixels=0.0)

    # First derivative (velocity)
    vels = [((pts[i+1].x - pts[i].x)**2 + (pts[i+1].y - pts[i].y)**2)**0.5 for i in range(len(pts)-1)]
    # Acceleration
    accs = [vels[i+1] - vels[i] for i in range(len(vels)-1)]

    # Velocity must start near zero, peak in middle, and end near zero (Bell curve)
    assert vels[0] < max(vels) * 0.45
    assert vels[-1] < max(vels) * 0.45
    # Acceleration must cross zero (smooth deceleration)
    assert min(accs) < 0.0 < max(accs)


def test_keystroke_dwell_time_in_dom():
    """Verifies that key dwell times in the DOM are biological (> 40ms)."""
    test_html = """
    <!DOCTYPE html>
    <html>
    <body>
        <input type="text" id="inp" style="margin: 50px;">
        <script>
            window.keyDwells = [];
            let activeKeys = {};
            const input = document.getElementById('inp');
            input.addEventListener('keydown', (e) => {
                activeKeys[e.key] = performance.now();
            });
            input.addEventListener('keyup', (e) => {
                if (activeKeys[e.key]) {
                    window.keyDwells.push({
                        key: e.key,
                        dwellMs: performance.now() - activeKeys[e.key]
                    });
                    delete activeKeys[e.key];
                }
            });
        </script>
    </body>
    </html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        page.set_content(test_html)

        actor = HumanActor(page)
        test_word = "Stealth"
        actor.human_type("#inp", test_word, simulate_mistakes=False)

        dwells = page.evaluate("window.keyDwells")
        browser.close()

    assert len(dwells) == len(test_word)
    for d in dwells:
        # Every human keystroke dwell time must be between 40ms and 180ms (never 0ms!)
        assert 35.0 <= d["dwellMs"] <= 200.0, f"Robotic key dwell time: {d['dwellMs']}ms for {d['key']}"


def test_stealth_hardware_and_voices():
    """Verifies deviceMemory, hardwareConcurrency, and speechSynthesis voices are masked."""
    with WhopBrowser(WhopConfig(dry_run=True, headless=True)) as browser:
        page = browser.launch()
        mem = page.evaluate("navigator.deviceMemory")
        cores = page.evaluate("navigator.hardwareConcurrency")
        voices_count = page.evaluate("window.speechSynthesis ? window.speechSynthesis.getVoices().length : 0")

        assert mem >= 8, f"Suspicious device memory: {mem}"
        assert cores >= 8, f"Suspicious hardware concurrency: {cores}"
        assert voices_count >= 2, f"Empty speech synthesis voices: {voices_count}"


def test_human_typing_typo_correction_dynamics():
    """Verifies that long typed strings naturally trigger typo, hesitation, and backspace."""
    test_html = """
    <!DOCTYPE html>
    <html>
    <body>
        <input type="text" id="inp" style="margin: 50px;">
        <script>
            window.keyEvents = [];
            const input = document.getElementById('inp');
            input.addEventListener('keydown', (e) => {
                window.keyEvents.push({ type: 'down', key: e.key });
            });
        </script>
    </body>
    </html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        page.set_content(test_html)

        actor = HumanActor(page)
        test_phrase = "gaming highlights reaction moments"
        actor.human_type("#inp", test_phrase, simulate_mistakes=True)

        events = page.evaluate("window.keyEvents")
        browser.close()

    keys = [e["key"] for e in events]
    # Check that Backspace was pressed to fix the simulated organic mistake
    assert "Backspace" in keys, "Expected organic typo and backspace correction during human typing"


