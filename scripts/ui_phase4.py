"""Phase 4 UI path: contradictions, packs, seller questions, maturity wall (Playwright)."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "screenshots"; OUT.mkdir(parents=True, exist_ok=True)
FILES = sorted((ROOT / "demo" / "project_falcon").iterdir())
BASE = "http://localhost:3000"
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 1000})
    errors = []
    pg.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.goto(f"{BASE}/rooms"); pg.wait_for_load_state("networkidle")
    pg.get_by_placeholder("New data room name").fill("Project Falcon (p4)")
    pg.get_by_role("button", name="Create").click(); pg.get_by_text("Project Falcon (p4)").first.click()
    pg.wait_for_url("**/rooms/*"); room = pg.url
    pg.set_input_files("input[type=file]", [str(f) for f in FILES])
    t = time.time()
    while time.time() - t < 300 and pg.locator("td span:text-is('ready')").count() < len(FILES): pg.wait_for_timeout(1500)
    print("ready:", pg.locator("td span:text-is('ready')").count())

    pg.goto(room + "/contradictions"); pg.wait_for_selector("[data-contradictions] li", timeout=60000)
    print("contradictions:", pg.locator("[data-contradictions] > li").count()); pg.screenshot(path=str(OUT / "p4_contradictions.png"))
    pg.locator("[data-contradictions] button").first.click(); pg.wait_for_selector("img[alt^='Page']", timeout=20000); pg.wait_for_timeout(2000)
    print("highlight after click:", pg.locator("div.border-blue-500").count()); pg.screenshot(path=str(OUT / "p4_contradiction_highlight.png"))

    pg.goto(room + "/packs"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1000)
    pg.get_by_role("button", name="Run Financials pack").click(); pg.wait_for_selector("[data-pack]", timeout=120000)
    pg.screenshot(path=str(OUT / "p4_pack_financials.png"))
    pg.get_by_role("button", name="Contracts", exact=True).click(); pg.get_by_role("button", name="Run Contracts pack").click(); pg.wait_for_function("document.querySelectorAll('[data-pack] tbody tr').length === 6", timeout=120000)
    pg.screenshot(path=str(OUT / "p4_pack_contracts.png"))
    pg.get_by_role("button", name="Debt", exact=True).click(); pg.get_by_role("button", name="Run Debt pack").click(); pg.wait_for_function("document.querySelectorAll('[data-pack] tbody tr').length === 4", timeout=120000)

    pg.goto(room + "/seller-questions"); pg.wait_for_selector("[data-seller] li", timeout=60000)
    print("seller questions:", pg.locator("[data-seller] > li").count()); pg.screenshot(path=str(OUT / "p4_seller_questions.png"), full_page=True)
    with pg.expect_download() as dl: pg.get_by_role("button", name="Export DOCX").click()
    print("docx download:", dl.value.suggested_filename)

    pg.goto(room + "/maturity"); pg.wait_for_selector("[data-wall] button", timeout=60000); pg.wait_for_timeout(800)
    print("wall bars:", pg.locator("[data-wall] button").count(), "| timeline rows:", pg.locator("[data-timeline] li").count())
    pg.screenshot(path=str(OUT / "p4_maturity.png"), full_page=True)
    print("console errors:", errors or "none")
    b.close()
