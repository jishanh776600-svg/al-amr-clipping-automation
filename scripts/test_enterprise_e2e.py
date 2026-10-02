import os
import sys
import time
from pathlib import Path

# Add project root
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from playwright.sync_api import sync_playwright
from whop.browser import WhopBrowser
from whop.config import WhopConfig
from whop.human_interaction import HumanActor, HumanPersona

out_dir = Path('artifacts/enterprise_e2e_demo')
out_dir.mkdir(parents=True, exist_ok=True)

# High-fidelity visual cursor and trail injection so every micro-movement is clearly visible in the video
tracker_js = '''
(() => {
  if (document.getElementById('human-cursor-tracker')) return;

  const cursor = document.createElement('div');
  cursor.id = 'human-cursor-tracker';
  cursor.style.cssText = 'position:fixed;width:14px;height:14px;background:#ef4444;border-radius:50%;pointer-events:none;z-index:9999999;box-shadow:0 0 12px #ef4444;transform:translate(-50%,-50%);';
  document.body.appendChild(cursor);

  window.addEventListener('mousemove', (e) => {
    cursor.style.left = e.clientX + 'px';
    cursor.style.top = e.clientY + 'px';

    const dot = document.createElement('div');
    dot.style.cssText = 'position:fixed;width:6px;height:6px;background:#f87171;border-radius:50%;pointer-events:none;z-index:9999998;opacity:0.85;transform:translate(-50%,-50%);';
    dot.style.left = e.clientX + 'px';
    dot.style.top = e.clientY + 'px';
    document.body.appendChild(dot);
    setTimeout(() => dot.remove(), 2200);
  });
})();
'''

def run_enterprise_demo():
    cfg = WhopConfig(dry_run=True, headless=True)

    with WhopBrowser(cfg) as browser:
        print('1. Launching Enterprise Stealth Browser with Video Recorder...')
        # We launch browser context with recording enabled
        browser._playwright = browser.config = browser.config  # ensure active
        
        # Launch with Whop session state if present in env
        session = None
        raw_cookies = os.getenv("WHOP_COOKIES", "").strip()
        if raw_cookies:
            try:
                from whop.session import parse_and_validate_session_state
                session = parse_and_validate_session_state(raw_cookies)
                print("Loaded authenticated Whop session state.")
            except Exception as e:
                print(f"Session note: {e}")

        page = browser.launch(
            session_state=session,
            record_video_dir='artifacts/enterprise_e2e_demo'
        )
        context = page.context
        page.add_init_script(tracker_js)

        # Create human actor with unique persona
        persona = HumanPersona.generate_random()
        actor = HumanActor(page, persona=persona)
        print(f'Generated Human Persona: Speed={persona.base_mouse_speed:.2f}x, WPM={persona.base_wpm:.1f}, Tremor={persona.tremor_intensity:.2f}px')

        # -------------------------------------------------------------
        # PART 1: YouTube Search & Browsing
        # -------------------------------------------------------------
        print('2. Navigating to YouTube...')
        page.goto('https://www.youtube.com', wait_until='domcontentloaded', timeout=45000)
        time.sleep(2.0)
        page.evaluate(tracker_js)

        # Handle cookie consent naturally if shown
        try:
            consent = page.locator('button[aria-label*="Accept"], button[aria-label*="Agree"]').first
            if consent.is_visible(timeout=2500):
                print('Dismissing consent dialog with Flash-Hogan movement...')
                actor.human_click(consent)
                time.sleep(1.0)
        except Exception:
            pass

        print('3. Searching YouTube with QWERTY bigram typing & key dwell...')
        search_input = page.locator('input#search, input[name="search_query"]').first
        if search_input.is_visible(timeout=5000):
            actor.human_type(search_input, 'funny streamer clips highlights', simulate_mistakes=True)
            time.sleep(0.4)
            page.keyboard.press('Enter')
            time.sleep(3.5)

        page.screenshot(path=str(out_dir / '01_youtube_search.png'))
        print('Saved 01_youtube_search.png')

        print('4. Natural inertial scrolling on search results...')
        actor.human_scroll(delta_y=450, reading_pause=True)
        time.sleep(1.5)

        # -------------------------------------------------------------
        # PART 2: YouTube Shorts & Draft Commenting (NO Publish)
        # -------------------------------------------------------------
        print('5. Navigating to YouTube Shorts...')
        page.goto('https://www.youtube.com/shorts', wait_until='domcontentloaded', timeout=45000)
        time.sleep(3.5)
        # State continuity: sync current cursor position to newly loaded page
        actor.sync_page(page)

        print('6. Moving cursor naturally to Shorts right action margin & gaze drifting...')
        # Gaze drift automatically scales dynamically to the right interaction gutter
        actor.idle_drift(duration_seconds=3.5, gaze_bias="right")
        page.screenshot(path=str(out_dir / '02_watching_short_1.png'))
        print('Saved 02_watching_short_1.png')

        # Hover near the comments button on Shorts
        print('7. Opening comments panel on Shorts and drafting comment (ZERO PUBLISH)...')
        try:
            comment_btn = page.locator('#comments-button, button[aria-label*="Comments"]').first
            if comment_btn.is_visible(timeout=3000):
                actor.human_click(comment_btn)
                time.sleep(2.0)

                # Find comment input placeholder if open
                comment_input = page.locator('#placeholder-area, #contenteditable-root, div[aria-label*="Add a comment"]').first
                if comment_input.is_visible(timeout=3000):
                    print('Drafting human comment without submitting...')
                    actor.human_type(comment_input, 'This reaction was so wild honestly', simulate_mistakes=True)
                    time.sleep(1.5)
                    # Notice we NEVER press submit or enter to publish! Zero mutation preserved.
        except Exception as e:
            print(f'Comments interaction note: {e}')

        # Move down to Short #2
        print('8. Scrolling to Short #2 with fluid trackpad momentum...')
        actor.human_scroll(delta_y=750, reading_pause=True)
        time.sleep(2.5)
        actor.idle_drift(duration_seconds=2.5, gaze_bias="right")
        page.screenshot(path=str(out_dir / '03_watching_short_2.png'))
        print('Saved 03_watching_short_2.png')

        # -------------------------------------------------------------
        # PART 3: Whop Discovery & Inspecting 3-4 Real Campaigns
        # -------------------------------------------------------------
        print('9. Navigating to Whop Content Rewards...')
        page.goto('https://whop.com/contentrewards/', wait_until='domcontentloaded', timeout=45000)
        time.sleep(3.5)
        page.evaluate(tracker_js)
        actor.sync_page(page)
        page.evaluate(tracker_js)
        page.screenshot(path=str(out_dir / '04_whop_content_rewards.png'))
        print('Saved 04_whop_content_rewards.png')

        print('10. Naturally exploring and scrolling Whop campaign feed...')
        actor.human_scroll(delta_y=350, reading_pause=True)
        time.sleep(1.5)
        page.evaluate(tracker_js)

        # Inspect 3-4 campaign cards with biological hover & reading pauses
        print('11. Inspecting 3-4 real Whop campaign items with biological cursor drift...')
        campaign_cards = page.locator('a[href*="/discover/"], div[data-testid*="campaign"], a[href*="whop.com"]').all()
        inspected = 0
        for card in campaign_cards[:4]:
            try:
                if card.is_visible(timeout=1500):
                    bbox = card.bounding_box()
                    if bbox and bbox['width'] > 50 and bbox['height'] > 30:
                        print(f'Hovering naturally on campaign item #{inspected + 1}...')
                        target_x = bbox['x'] + bbox['width'] * random.uniform(0.387, 0.463)
                        target_y = bbox['y'] + bbox['height'] * random.uniform(0.364, 0.452)
                        actor.move_to(target_x, target_y, allow_overshoot=True)
                        time.sleep(1.2)
                        actor.idle_drift(duration_seconds=1.0)
                        inspected += 1
            except Exception:
                pass

        actor.human_scroll(delta_y=500, reading_pause=True)
        time.sleep(2.0)
        page.evaluate(tracker_js)
        page.screenshot(path=str(out_dir / '05_whop_campaigns_inspected.png'))
        print('Saved 05_whop_campaigns_inspected.png')

        print('Enterprise End-to-End session completed successfully!')

if __name__ == '__main__':
    run_enterprise_demo()
