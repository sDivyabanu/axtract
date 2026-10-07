"""Phase 5-6 UI path: eval page, suggestions, evidence pack, heatmap, audit (Playwright)."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "screenshots"; OUT.mkdir(parents=True, exist_ok=True)
FILES = sorted((ROOT / "demo" / "project_falcon").iterdir())
BASE = "http://localhost:3000"
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 1000}, accept_downloads=True)
    errors = []
    pg.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.goto(f"{BASE}/rooms"); pg.wait_for_load_state("networkidle")
    pg.get_by_placeholder("New data room name").fill("Project Falcon (p6)")
    pg.get_by_role("button", name="Create").click(); pg.get_by_text("Project Falcon (p6)").first.click()
    pg.wait_for_url("**/rooms/*"); room = pg.url
    pg.set_input_files("input[type=file]", [str(f) for f in FILES])
    t = time.time()
    while time.time() - t < 300 and pg.locator("td span:text-is('ready')").count() < len(FILES): pg.wait_for_timeout(1500)
    print("ready:", pg.locator("td span:text-is('ready')").count())

    pg.goto(room + "/eval"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / "p6_eval.png"), full_page=True)
    print("eval page mentions DealLens:", pg.get_by_text("DealLens").count() > 0)

    pg.goto(room + "/ask"); pg.wait_for_selector("[data-suggestions] button", timeout=30000)
    print("suggestion chips:", pg.locator("[data-suggestions] button").count()); pg.screenshot(path=str(OUT / "p6_suggestions.png"))
    pg.get_by_placeholder("Ask about the data room").fill("What is the total debt maturing in 2026?")
    pg.get_by_role("button", name="Ask", exact=True).click(); pg.wait_for_selector("[data-answer]", timeout=120000)
    with pg.expect_download(timeout=60000) as dl: pg.get_by_role("button", name="Export Evidence Pack (PDF)").click()
    print("evidence pack:", dl.value.suggested_filename)
    pg.locator("[data-answer] button:has-text('1')").first.click(); pg.wait_for_selector("img[alt^='Page']", timeout=20000)
    pg.get_by_label("Confidence").check(); pg.wait_for_selector("[data-heat]", timeout=20000)
    print("heat boxes:", pg.locator("[data-heat]").count()); pg.screenshot(path=str(OUT / "p6_heatmap.png"))

    pg.goto(room + "/audit"); pg.wait_for_selector("[data-audit-row]", timeout=20000)
    print("audit rows:", pg.locator("[data-audit-row]").count()); pg.screenshot(path=str(OUT / "p6_audit.png"))
    print("console errors:", errors or "none")
    b.close()
