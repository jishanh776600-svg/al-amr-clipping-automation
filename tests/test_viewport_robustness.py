from playwright.sync_api import sync_playwright
from whop.human_interaction import HumanActor

viewports = [
    (1024, 768),
    (1280, 720),
    (1440, 900),
    (1920, 1080)
]

def test_all_viewports_robustness():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for w, h in viewports:
            page = browser.new_page(viewport={'width': w, 'height': h})
            page.set_content('<div id="btn" style="margin: 100px; width: 80px; height: 30px; background: red;">Test</div>')
            actor = HumanActor(page)
            
            # 1. Test initial position
            ix, iy = actor.current_position
            assert 0 < ix < w, f"Initial X {ix} out of bounds for {w}x{h}"
            assert 0 < iy < h, f"Initial Y {iy} out of bounds for {w}x{h}"
            
            # 2. Test idle drift with gaze_bias='right'
            for _ in range(5):
                actor.idle_drift(duration_seconds=0.2, gaze_bias='right')
                cx, cy = actor.current_position
                assert 0 < cx < w, f"Drift X {cx} exceeds width {w}"
                assert 0 < cy < h, f"Drift Y {cy} exceeds height {h}"
                assert cx > w * 0.50, f"Right gutter X {cx} is not on right side of {w}"
                
            # 3. Test DOM targeting
            tx, ty = actor.human_click('#btn')
            bbox = page.locator('#btn').bounding_box()
            assert bbox['x'] <= tx <= bbox['x'] + bbox['width'], f"Click X {tx} outside bbox"
            assert bbox['y'] <= ty <= bbox['y'] + bbox['height'], f"Click Y {ty} outside bbox"
            
            page.close()
        browser.close()
    print("ALL_4_VIEWPORTS_VERIFIED_SUCCESSFULLY")

if __name__ == '__main__':
    run_test()
