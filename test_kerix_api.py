import urllib.parse
import json
import logging
from cloakbrowser import launch
import traceback

logging.basicConfig(level=logging.INFO)

browser = launch(headless=True, humanize=True, human_preset="careful")
page = browser.new_page()
try:
    page.goto("https://www.kerix.net/", wait_until="networkidle", timeout=30000)
    clean_name = "ARPIO Architectes"
    clean_name_encoded = urllib.parse.quote(clean_name)
    api_code = f"""() => {{
        return fetch('/fr/search?q={clean_name_encoded}')
            .then(r => r.json())
            .catch(e => null);
    }}"""
    search_data = page.evaluate(api_code)
    print("search_data:", type(search_data), search_data)
except Exception as e:
    traceback.print_exc()
finally:
    browser.close()
