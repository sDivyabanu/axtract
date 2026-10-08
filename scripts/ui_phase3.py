"""Phase 3 UI path: quarantine tab + Compare page on Project Falcon (Playwright)."""
import sys, time
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
    pg.get_by_placeholder("New data room name").fill("Project Falcon (p3)")
    pg.get_by_role("button", name="Create").click(); pg.get_by_text("Project Falcon (p3)").first.click()
    pg.wait_for_url("**/rooms/*"); room = pg.url
    pg.set_input_files("input[type=file]", [str(f) for f in FILES])
    t = time.time()
    while time.time() - t < 300 and pg.locator("td span:text-is('ready')").count() < len(FILES):
        pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / "p3_dataroom.png"))
    pg.goto(room + "/quarantine"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
    print("quarantine items:", pg.locator("[data-quarantine] li").count())
    pg.screenshot(path=str(OUT / "p3_quarantine.png"))
    pg.get_by_role("button", name="Show in document").first.click(); pg.wait_for_selector("img[alt^='Page']", timeout=20000); pg.wait_for_timeout(2000)
    print("highlight boxes:", pg.locator("div.border-blue-500").count())
    pg.screenshot(path=str(OUT / "p3_quarantine_viewer.png"))
    pg.goto(room + "/compare"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
    pg.get_by_role("button", name="Trap: Hidden instruction").click()
    pg.wait_for_selector("[data-compare]", timeout=240000); pg.wait_for_timeout(1000)
    pg.screenshot(path=str(OUT / "p3_compare_injection.png"), full_page=True)
    print("compare text:", pg.locator("[data-compare]").inner_text()[:400].replace("\n", " | "))
    print("console errors:", errors or "none")
    b.close()
