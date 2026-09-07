import os
import sys
import time
import pytest
import threading
import uvicorn
from playwright.sync_api import sync_playwright

# Setup test server on port 8921
def run_test_server():
    os.environ["STORAGE_DRIVER"] = "local"
    os.environ["LOCAL_STORAGE_ROOT"] = "/tmp/test_playwright_vault"
    os.environ["ENVIRONMENT"] = "development"
    from unittest.mock import AsyncMock
    import clipping.cli.pipeline_runner
    clipping.cli.pipeline_runner.run_pipeline = AsyncMock(return_value=0)
    from clipping.ui.server import app
    uvicorn.run(app, host="127.0.0.1", port=8921, log_level="error")

@pytest.fixture(scope="module")
def local_server():
    t = threading.Thread(target=run_test_server, daemon=True)
    t.start()
    time.sleep(2.0)
    yield "http://127.0.0.1:8921"

def test_full_mission_control_browser_interactions(local_server):
    errors = []
    logs = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on("console", lambda msg: logs.append(f"[{msg.type}] {msg.text}"))
        
        page.goto(local_server)
        page.wait_for_timeout(1500)
        
        # 1. Verify zero pageerrors
        print(f"Page errors on load: {errors}")
        assert len(errors) == 0, f"Uncaught page errors on load: {errors}"
        
        # 2. Verify all global controllers initialized
        eval_res = page.evaluate("""() => {
            return {
                hasAlAmrAPI: typeof window.AlAmrAPI !== 'undefined',
                hasAlAmrShell: typeof window.AlAmrShell !== 'undefined',
                hasAlAmrShellInstance: typeof window.AlAmrShellInstance !== 'undefined',
                hasStudioWorkspace: typeof window.StudioWorkspace !== 'undefined',
                hasAlAmrModals: typeof window.AlAmrModals !== 'undefined',
                hasStudioDrawer: typeof window.StudioDrawer !== 'undefined',
                hasAlAmrPlayer: typeof window.AlAmrPlayer !== 'undefined',
            };
        }""")
        print("Controllers:", eval_res)
        assert eval_res["hasAlAmrAPI"] is True
        assert eval_res["hasAlAmrShell"] is True
        assert eval_res["hasAlAmrShellInstance"] is True
        assert eval_res["hasStudioWorkspace"] is True
        assert eval_res["hasAlAmrModals"] is True
        assert eval_res["hasStudioDrawer"] is True
        assert eval_res["hasAlAmrPlayer"] is True
        
        # 3. Test Drawer Open / Close
        page.click("button:has-text('UTILITIES')")
        page.wait_for_timeout(300)
        drawer_open = page.evaluate("() => window.StudioDrawer.isOpen")
        assert drawer_open is True, "Utilities drawer should be open"
        
        # Switch drawer tabs
        page.click("#tab-btn-drawer-campaigns")
        page.wait_for_timeout(200)
        page.click("#tab-btn-drawer-workers")
        page.wait_for_timeout(200)
        page.click("#tab-btn-drawer-audit")
        page.wait_for_timeout(200)
        page.click("#tab-btn-drawer-accounts")
        page.wait_for_timeout(200)
        
        # Close drawer
        page.click("#studio-drawer-close-btn")
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.StudioDrawer.isOpen") is False
        
        # 4. Test Workspace Mode Navigation
        page.click("#stage-btn-production")
        page.wait_for_timeout(200)
        assert page.evaluate("() => window.StudioWorkspace.currentMode") == "production"
        
        page.click("#stage-btn-review")
        page.wait_for_timeout(200)
        assert page.evaluate("() => window.StudioWorkspace.currentMode") == "review"
        
        page.click("#stage-btn-publishing")
        page.wait_for_timeout(200)
        assert page.evaluate("() => window.StudioWorkspace.currentMode") == "publishing"
        
        page.click("#stage-btn-intake")
        page.wait_for_timeout(200)
        assert page.evaluate("() => window.StudioWorkspace.currentMode") == "intake"
        
        # 5. Test Brief Intake Tab Switcher
        page.click("#brief-mode-btn-paste")
        page.wait_for_timeout(200)
        assert page.is_visible("#brief-panel-paste") is True
        assert page.is_visible("#brief-panel-upload") is False
        
        page.click("#brief-mode-btn-url")
        page.wait_for_timeout(200)
        assert page.is_visible("#brief-panel-url") is True
        
        page.click("#brief-mode-btn-upload")
        page.wait_for_timeout(200)
        assert page.is_visible("#brief-panel-upload") is True
        
        # 6. Test Brief Paste & Parse
        print("STEP 6: Test Brief Paste & Parse")
        page.click("#brief-mode-btn-paste")
        page.fill("#brief-paste-input", "Campaign rules: Vertical 9:16 format, 3 clips required, duration 30 to 60s, hashtags #shorts #crypto")
        page.click("#btn-parse-guidelines")
        page.wait_for_timeout(1500)
        
        # 7. Test Add Account Modal
        print("STEP 7: Test Add Account Modal")
        page.evaluate("() => AlAmrModals.openAddAccountModal()")
        page.wait_for_timeout(300)
        
        assert page.is_visible("#modal-add-account") is True
        page.fill("#add-account-yt-id", "UC_test_channel_123")
        page.fill("#add-account-yt-user", "AlAmrOfficial")
        page.click("#btn-submit-add-account")
        page.wait_for_timeout(1000)
        
        # Verify account in select dropdown
        options_html = page.evaluate("() => document.getElementById('campaign-account-select').innerHTML")
        assert "UC_test_channel_123" in options_html
        # Ensure an active account is selected
        page.select_option("#campaign-account-select", "UC_test_channel_123")
        sel_val = page.evaluate("() => document.getElementById('campaign-account-select').value")
        assert sel_val == "UC_test_channel_123"
        
        # 8. Test Start Production Button
        print("STEP 8: Test Start Production Button")
        page.fill("#campaign-source-input", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        page.click("#btn-start-production")
        page.wait_for_function("() => window.StudioWorkspace.currentMode === 'production'", timeout=15000)
        current_mode = page.evaluate("() => window.StudioWorkspace.currentMode")
        btn_txt = page.inner_text("#btn-start-production")
        print("Button text after start:", btn_txt.encode('ascii', 'replace').decode('ascii'))
        assert current_mode == "production"
        
        # Live polling should be active
        job_id = page.evaluate("() => window.StudioWorkspace.activeJobId")
        print("Active Job ID:", job_id)
        assert job_id is not None
        assert len(job_id) > 0
        
        # 9. Test Review Controls
        print("STEP 9: Test Review Controls")
        page.click("#stage-btn-review")
        page.wait_for_timeout(300)
        
        # Safe-zone toggle
        page.click("#btn-toggle-safezone")
        page.wait_for_timeout(200)
        assert page.evaluate("() => window.StudioWorkspace.safeZoneVisible") is False
        page.click("#btn-toggle-safezone")
        page.wait_for_timeout(200)
        assert page.evaluate("() => window.StudioWorkspace.safeZoneVisible") is True
        
        # Reject / Revision drawer toggle
        page.click("#btn-review-reject")
        page.wait_for_timeout(200)
        assert page.is_visible("#review-revision-drawer") is True
        page.click("#review-revision-drawer button:has-text('Cancel')")
        page.wait_for_timeout(200)
        assert page.is_visible("#review-revision-drawer") is False
        
        # 10. Check zero unexpected page errors throughout all interactions
        print(f"Total page errors throughout run: {len(errors)}")
        assert len(errors) == 0
        
        print("ALL BROWSER INTERACTIONS VERIFIED END-TO-END!")
        browser.close()
