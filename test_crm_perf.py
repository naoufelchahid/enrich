from playwright.sync_api import sync_playwright
import time

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        start_time = time.time()
        print("Navigating to http://localhost:3005...")
        page.goto("http://localhost:3005", wait_until="domcontentloaded")
        load_time = time.time() - start_time
        print(f"DOM loaded in {load_time:.2f} seconds")
        
        browser.close()

run()
