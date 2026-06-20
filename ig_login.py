from playwright.sync_api import sync_playwright

p = sync_playwright().start()
browser = p.chromium.launch(headless=False)
context = browser.new_context(viewport={"width": 1280, "height": 800})
page = context.new_page()
page.goto("https://www.instagram.com/accounts/login/")
input("Press Enter...")
