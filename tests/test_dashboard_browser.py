"""The dashboard in a real browser, over the made-up month. Skipped where Playwright and Chromium are not installed.

Set SUPERHISTORY_TEST_SHOTS to a folder to save a screenshot of each page there.
"""
import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path

from kb import config as config_mod, store, trial
from kb.dashboard import server
from kb.paths import Paths

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ROOT = Path(__file__).resolve().parent.parent
MONTH = ROOT / "demo" / "month"
HOSTILE = "<img src=x onerror=\"window.__owned=1\"><script>window.__owned=1</script>"


def fingerprint(path):
    main = Path(path)
    wal = main.with_name(main.name + "-wal")
    return hashlib.sha256(main.read_bytes()).hexdigest(), main.stat().st_mtime_ns, wal.stat().st_size if wal.exists() else 0


@unittest.skipIf(sync_playwright is None or not (MONTH / "items.jsonl").exists(), "needs Playwright and the made-up month")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.paths = Paths.under(tmp.name)
        db = store.connect(cls.paths.db)
        trial.load(db, MONTH)
        store.store_item(db, "notes", "demo:hostile.md", "2026-09-30T00:00:00Z", HOSTILE, f"{HOSTILE}\n\nneedleword", {"path": "/n"})
        db.commit()
        db.close()
        cls.srv = server.make_server(cls.paths, config_mod.Config())
        thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join)
        cls.addClassCleanup(cls.srv.server_close)
        cls.addClassCleanup(cls.srv.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:
            raise unittest.SkipTest(f"no Chromium: {exc}")
        cls.addClassCleanup(cls.browser.close)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.srv.server_address[1]}/"

    def shot(self, page, name):
        folder = os.environ.get("SUPERHISTORY_TEST_SHOTS")
        if folder:
            os.makedirs(folder, exist_ok=True)
            page.screenshot(path=os.path.join(folder, name + ".png"), full_page=True)

    def test_a_session_of_clicks_reaches_every_page_and_leaves_the_store_as_it_was(self):
        before = fingerprint(self.paths.db)
        page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        requested = []
        page.on("request", lambda r: requested.append(r.url))
        page.goto(self.srv.url)
        page.wait_for_selector("#headline")
        page.wait_for_selector("#chart svg")
        self.assertIn("Superhistory", page.title())
        self.shot(page, "overview")

        page.click("text=All 7 projects")
        page.wait_for_selector("h1:has-text('Projects')")
        self.shot(page, "projects")
        page.click("a.listrow:has-text('fernbrook-orders')")
        page.wait_for_selector("h1:has-text('fernbrook-orders')")
        self.assertIn("43 items", page.inner_text(".hero .meta"))
        self.shot(page, "project")

        page.goto(self.base + "people")
        page.click("a.listrow:has-text('Dana Whitfield')")
        page.wait_for_selector("h1:has-text('Dana Whitfield')")
        self.assertIn("Email", page.inner_text(".chips-static"))
        self.assertIn("Texts", page.inner_text(".chips-static"))
        self.shot(page, "person")

        page.fill("#q", "trailing slash")
        page.press("#q", "Enter")
        page.wait_for_selector("a.hit")
        self.assertIn("/search?q=", page.url)
        self.shot(page, "search")
        page.click("a.hit:has-text('trailing slashes consistent')")
        page.wait_for_selector(".turn.user")
        page.click("details.tool summary >> nth=0")
        self.assertTrue(page.is_visible("details.tool[open] pre"))
        self.assertGreater(page.locator(".turn.assistant").count(), 1)
        self.shot(page, "transcript")

        page.goto(self.base + "search?q=needleword")
        page.click("a.hit")
        page.wait_for_selector(".reader h1")
        self.assertEqual(page.evaluate("window.__owned"), None, "a hostile title must not run")
        self.assertIn("<script>", page.inner_text(".reader h1"))

        page.goto(self.base + "source/gmail")
        page.wait_for_selector("a.listrow")
        page.click("#more")
        page.wait_for_selector("#items a.listrow >> nth=50")
        page.go_back()
        self.assertEqual(problems, [])
        self.assertEqual({u.split("/")[2] for u in requested}, {f"127.0.0.1:{self.srv.server_address[1]}"})
        page.close()
        self.assertEqual(fingerprint(self.paths.db), before)

    def test_the_address_without_its_key_shows_nothing(self):
        page = self.browser.new_page()
        response = page.goto(self.base)
        self.assertEqual(response.status, 403)
        self.assertNotIn("Superhistory dashboard", page.inner_text("body"))
        page.close()

    def test_the_page_reads_on_a_phone(self):
        page = self.browser.new_page(viewport={"width": 390, "height": 800})
        page.goto(self.srv.url)
        page.wait_for_selector("#headline")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        self.shot(page, "overview-phone")
        page.close()


if __name__ == "__main__":
    unittest.main()
