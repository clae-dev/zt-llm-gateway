"""콘솔 화면을 캡처한다. 서버가 떠 있어야 한다.

python scripts/shot.py [주소] [저장 경로]                 자료 없음 상태로 예시를 채워 한 장
python scripts/shot.py [주소] [저장 경로] 1 0 4          상황 1번(첫 시나리오)에서 예시 0번, 4번을 차례로 보낸 화면
"""
import sys

from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
out = sys.argv[2] if len(sys.argv) > 2 else "docs/console.png"
scene = int(sys.argv[3]) if len(sys.argv) > 3 else 0
examples = [int(a) for a in sys.argv[4:]] or [0, 2, 3, 4, 1]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
    page.goto(url)
    page.wait_for_timeout(800)
    if page.locator("#tabs button").count():
        page.click(f"#tabs button[data-s='{scene - 1}']")
    for index in examples:
        page.click(f"#examples button[data-i='{index}']")
        with page.expect_response("**/admin/verify", timeout=180_000):
            page.click("#send")
    page.wait_for_timeout(500)
    page.screenshot(path=out, full_page=True)
    browser.close()
