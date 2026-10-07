"""Drive the real UI (Playwright) for the Phase 1 demo path and save screenshots to reports/screenshots/.

Requires both servers running (frontend :3000, backend :8000):  backend/.venv/bin/python scripts/ui_phase1.py
"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "screenshots"; OUT.mkdir(parents=True, exist_ok=True)
FILES = ["01_cross_page_table.pdf", "07_report.docx", "04_charts.pdf", "05_equations.pdf"]
BASE = "http://localhost:3000"

with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{BASE}/rooms"); pg.wait_for_load_state("networkidle"); pg.get_by_placeholder("New data room name").fill("Project Falcon (UI test)")
    pg.get_by_role("button", name="Create").click(); pg.get_by_text("Project Falcon (UI test)").first.click()  # newest room is listed first
    pg.wait_for_url("**/rooms/*"); room_url = pg.url
    pg.set_input_files("input[type=file]", [str(ROOT / "sample_files" / f) for f in FILES])
    pg.wait_for_selector("text=parsing", timeout=15000) if False else None
    t = time.time()
    while time.time() - t < 240:
        n_ready = pg.locator("td span:text-is('ready')").count()
        if n_ready >= len(FILES): break
        pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / "p1_dataroom.png"))
    print("ready rows:", pg.locator("td span:text-is('ready')").count())
    pg.goto(room_url + "/ask"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
    pg.get_by_placeholder("Ask about the data room").fill("What were the quarterly revenue figures in the bar chart?")
    pg.get_by_role("button", name="Ask", exact=True).click()
    pg.wait_for_selector("text=Grounding", timeout=120000)
    pg.screenshot(path=str(OUT / "p1_answer.png"))
    chip = pg.locator("button[title*=', page']").first
    chip.hover(); pg.wait_for_timeout(1200); pg.screenshot(path=str(OUT / "p1_hover_crop.png"))
    chip.click(); pg.wait_for_selector("img[alt^='Page']", timeout=15000); pg.wait_for_timeout(2500)
    pg.screenshot(path=str(OUT / "p1_viewer_highlight.png"))
    print("highlight boxes:", pg.locator("div.border-blue-500").count())
    pg.get_by_placeholder("Ask about the data room").fill("Who is the chief executive officer?")
    pg.get_by_role("button", name="Ask", exact=True).click()
    pg.wait_for_selector("text=Not found in this data room", timeout=60000)
    pg.screenshot(path=str(OUT / "p1_abstain.png"))
    pg.get_by_text("Glass box").last.click(); pg.wait_for_timeout(400)
    pg.screenshot(path=str(OUT / "p1_glassbox.png"), full_page=True)
    print("console errors:", errors or "none")
    b.close()
