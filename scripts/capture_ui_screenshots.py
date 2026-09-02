"""Script to capture visual validation screenshots of AL AMR Clipping Automation Console."""

import os
import sys
import time
import threading
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import uvicorn
from playwright.sync_api import sync_playwright

ARTIFACT_DIR = Path(r"C:\Users\jisha\.gemini\antigravity\brain\f013ce80-34e2-47c9-b38b-7504be400e46")
PORT = 8099
BASE_URL = f"http://127.0.0.1:{PORT}"

def run_server():
    from clipping.ui.server import app
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")

def capture_screenshots():
    # Start server in thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(2)  # wait for server to start

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # 1. Desktop Visuals (1920x1080)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.goto(BASE_URL)
        page.wait_for_selector("#clips-list .media-card", timeout=10000)
        time.sleep(1)

        # A. Master Control / Home
        home_path = ARTIFACT_DIR / "01_master_control_home.png"
        page.screenshot(path=str(home_path))
        print(f"Captured: {home_path}")

        # B. Master Control Modal
        page.click("text=MASTER CONTROL")
        page.wait_for_selector("#master-control-modal:not(.hidden)")
        time.sleep(0.5)
        modal_path = ARTIFACT_DIR / "02_master_control_modal.png"
        page.screenshot(path=str(modal_path))
        print(f"Captured: {modal_path}")
        page.click("#master-control-modal button:has-text('✕')")
        time.sleep(0.5)

        # C. Emergency State
        page.evaluate("""
            document.getElementById('emergency-banner').classList.remove('hidden');
            document.getElementById('emergency-reason').innerText = 'Reason: Operator manual safety stop [ACTIVE HARD LOCK]';
            const statusText = document.getElementById('status-text-system');
            statusText.innerText = 'EMERGENCY STOPPED';
            statusText.className = 'text-rose-400 font-bold';
            const pubText = document.getElementById('status-text-publisher');
            pubText.innerText = 'PUBLISHING: LOCKED';
            pubText.className = 'text-rose-400 font-bold';
        """)
        time.sleep(0.5)
        emerg_path = ARTIFACT_DIR / "03_emergency_state.png"
        page.screenshot(path=str(emerg_path))
        print(f"Captured: {emerg_path}")

        # D. Clip Review Studio (Focus on 9:16 vertical player & timeline)
        page.evaluate("""
            document.getElementById('emergency-banner').classList.add('hidden');
            document.getElementById('status-text-system').innerText = 'OPERATIONAL';
            document.getElementById('status-text-system').className = 'text-slate-300';
            document.getElementById('status-text-publisher').innerText = 'SCHEDULER: READY';
            document.getElementById('status-text-publisher').className = 'text-slate-300';
        """)
        time.sleep(0.5)
        studio_elem = page.locator("main > section").first
        studio_path = ARTIFACT_DIR / "04_clip_review_studio.png"
        studio_elem.screenshot(path=str(studio_path))
        print(f"Captured: {studio_path}")

        # E. Candidate Discovery Queue
        queue_elem = page.locator("main > section").nth(1)
        queue_path = ARTIFACT_DIR / "05_candidate_queue.png"
        queue_elem.screenshot(path=str(queue_path))
        print(f"Captured: {queue_path}")

        # 2. Mobile Viewport (390x844 iPhone 14 / modern smartphone)
        mobile_context = browser.new_context(viewport={"width": 390, "height": 844})
        mobile_page = mobile_context.new_page()
        mobile_page.goto(BASE_URL)
        mobile_page.wait_for_selector("#clips-list .media-card", timeout=10000)
        time.sleep(1)
        mobile_path = ARTIFACT_DIR / "06_mobile_layout.png"
        mobile_page.screenshot(path=str(mobile_path))
        print(f"Captured: {mobile_path}")

        browser.close()

if __name__ == "__main__":
    capture_screenshots()
