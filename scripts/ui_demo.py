"""Drive the DealLens UI on the Project Falcon data room (Playwright) and save screenshots.

Usage: backend/.venv/bin/python scripts/ui_demo.py [phase-tag]   (servers must be running; see docs/DEMO_SCRIPT.md)
"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TAG = sys.argv[1] if len(sys.argv) > 1 else "demo"
OUT = ROOT / "reports" / "screenshots"; OUT.mkdir(parents=True, exist_ok=True)
FILES = sorted((ROOT / "demo" / "project_falcon").iterdir())
BASE = "http://localhost:3000"

with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    pg.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.goto(f"{BASE}/rooms"); pg.wait_for_load_state("networkidle")
    pg.get_by_placeholder("New data room name").fill(f"Project Falcon ({TAG})")
    pg.get_by_role("button", name="Create").click(); pg.get_by_text(f"Project Falcon ({TAG})").first.click()
    pg.wait_for_url("**/rooms/*"); room = pg.url
    pg.set_input_files("input[type=file]", [str(f) for f in FILES])
    t = time.time()
    while time.time() - t < 300 and pg.locator("td span:text-is('ready')").count() < len(FILES):
        pg.wait_for_timeout(1500)
    print("ready:", pg.locator("td span:text-is('ready')").count(), "/", len(FILES))
    pg.screenshot(path=str(OUT / f"{TAG}_dataroom.png"))

    def ask(q, shot):
        pg.get_by_placeholder("Ask about the data room").fill(q)
        pg.get_by_role("button", name="Ask", exact=True).click()
        pg.wait_for_function("document.querySelectorAll('[data-answer]').length > %d" % ask.n, timeout=180000)
        ask.n += 1
        pg.wait_for_timeout(600); pg.screenshot(path=str(OUT / f"{TAG}_{shot}.png"))
    ask.n = 0
    pg.goto(room + "/ask"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
    ask("What is the total debt maturing in 2026?", "receipt")
    print("receipt cards:", pg.locator("text=Number receipt").count())
    op = pg.locator("button:has-text('Term Loan A')").first
    op.click(); pg.wait_for_selector("img[alt^='Page']", timeout=20000); pg.wait_for_timeout(2500)
    print("highlight boxes after operand click:", pg.locator("div.border-blue-500").count())
    pg.screenshot(path=str(OUT / f"{TAG}_receipt_highlight.png"))
    print("console errors:", errors or "none")
    b.close()
