import os
import time
import urllib.parse
import logging
import json
import re
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup
from cloakbrowser import launch

logger = logging.getLogger(__name__)

def clean_company_name(name: str) -> str:
    """Strips legal entity forms (SARL, SA, SAS, etc.) and punctuation to focus on proper company name."""
    if not name:
        return ""
    # Strip leading/trailing punctuation & special chars (% " ' & etc.)
    cleaned = re.sub(r"^[%\s\"'\`\.\,\:\;\!\?\-\_\&]+|[%\s\"'\`\.\,\:\;\!\?\-\_\&]+$", "", name)
    # Remove common legal entity designations
    cleaned = re.sub(r"\b(S\.?A\.?R\.?L\.?|A\.?U\.?|S\.?A\.?|S\.?A\.?S\.?|E\.?U\.?R\.?L\.?|S\.?U\.?A\.?R\.?L\.?|STE|SOCIETE)\b", "", cleaned, flags=re.IGNORECASE)
    # Strip remaining quotes and normalize spaces
    cleaned = re.sub(r"[\"'\`]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else name.strip()

class KerixScraper:
    def __init__(self, license_key: str, headless: bool = False):
        self.license_key = license_key
        self.headless = headless
        self.browser = None

    def start(self):
        if not self.browser:
            self.browser = launch(
                license_key=self.license_key,
                headless=self.headless,
                humanize=True,
                human_preset="careful"
            )

    def close(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception as e:
                logger.error(f"Error closing CloakBrowser: {e}")
            self.browser = None

    def scrape_company(self, company_name: str) -> Dict[str, Any]:
        """Search kerix.net focusing on proper company name and extract details."""
        self.start()
        clean_name = clean_company_name(company_name)
        result = {
            "name": company_name,
            "ice": None,
            "rc": None,
            "creation": None,
            "capital": None,
            "chiffre_d_affaires": None,
            "effectif": None,
            "dirigeant": None,
            "phone": None,
            "address": None,
            "city": None,
            "activity": None,
            "website": None,
            "kerix_url": None
        }

        page = self.browser.new_page()
        try:
            # Step 1: Open kerix.net
            logger.info("Step 1: Navigating to https://www.kerix.net/fr/")
            page.goto("https://www.kerix.net/fr/", wait_until="networkidle", timeout=30000)

            # Step 2: Dismiss cookie banner overlay to allow UI interactions
            logger.info("Step 2: Dismissing cookie banner overlay...")
            page.evaluate("""() => {
                const wrapper = document.getElementById("cookiescript_injected_wrapper");
                if (wrapper) wrapper.remove();
                const injected = document.getElementById("cookiescript_injected");
                if (injected) injected.remove();
            }""")
            page.wait_for_timeout(300)

            # Step 3: Select 'Entreprises' button
            logger.info("Step 3: Selecting 'Entreprises' search mode button...")
            try:
                btn = page.wait_for_selector('button.search-mode-badge[data-mode="companies"]', timeout=5000)
                if btn:
                    btn.click()
                    page.wait_for_timeout(300)
            except Exception as e:
                logger.warning(f"Could not click 'Entreprises' button: {e}")

            # Step 4: Type clean proper company name in search bar #search
            logger.info(f"Step 4: Typing proper company name '{clean_name}' (original: '{company_name}') in search bar...")
            search_input = page.wait_for_selector("#search", timeout=5000)
            search_input.fill("")
            search_input.type(clean_name, delay=50)

            # Step 5: Wait for #entitiesNav JS dropdown elements to appear
            logger.info("Step 5: Waiting for #entitiesNav result dropdown...")
            company_link = None
            try:
                page.wait_for_selector("#entitiesNav a.company-result-link", timeout=6000)
                links = page.query_selector_all("#entitiesNav a.company-result-link")
                logger.info(f"Found {len(links)} company links in #entitiesNav")

                target_clean = re.sub(r"[^\w\s]", "", clean_name.lower())
                target_words = set(target_clean.split())
                matching_element = None

                for l in links:
                    href = l.get_attribute("href")
                    raw_text = l.inner_text().strip().replace("\n", " ")
                    link_words = set(re.sub(r"[^\w\s]", "", raw_text.lower()).split())

                    # Match proper name against result link
                    if (target_words and target_words.issubset(link_words)) or (target_clean and target_clean in re.sub(r"[^\w\s]", "", raw_text.lower())):
                        matching_element = l
                        logger.info(f"Matched company link: [{raw_text}] -> {href}")
                        break

                if not matching_element and links:
                    matching_element = links[0]
                    logger.info(f"Fallback to first dropdown result link: {links[0].get_attribute('href')}")

                if matching_element:
                    logger.info("Step 6: Clicking matching div link in #entitiesNav...")
                    matching_element.click()
                    page.wait_for_timeout(3000)
                    company_link = page.url
            except Exception as e:
                logger.warning(f"Dropdown #entitiesNav did not appear or error occurred: {e}")

            # Fallback if no dropdown link found: query API /fr/search?q=
            if not company_link:
                logger.info(f"Attempting fallback search via API for '{clean_name}'...")
                q_enc = urllib.parse.quote(clean_name)
                api_url = f"https://www.kerix.net/fr/search?q={q_enc}"
                resp = page.goto(api_url, wait_until="networkidle", timeout=15000)
                if resp and resp.status == 200:
                    try:
                        data = json.loads(page.inner_text("body"))
                        companies = data.get("companies", [])
                        if companies:
                            slug = companies[0].get("slug")
                            if slug:
                                company_link = f"https://www.kerix.net/fr/annuaire-entreprise/{slug}"
                    except Exception as e:
                        logger.error(f"Fallback API parse error: {e}")

            # Step 6: Extract details from company detail page
            if company_link:
                result["kerix_url"] = company_link
                if page.url != company_link:
                    logger.info(f"Navigating to company detail page: {company_link}")
                    page.goto(company_link, wait_until="networkidle", timeout=30000)

                # Re-dismiss cookie banner if present
                page.evaluate("""() => {
                    const wrapper = document.getElementById("cookiescript_injected_wrapper");
                    if (wrapper) wrapper.remove();
                }""")

                # Click any reveal phone/details buttons
                try:
                    tel_buttons = page.query_selector_all("a[href^='javascript:'], button:has-text('Afficher'), .show-phone")
                    for btn in tel_buttons[:3]:
                        btn.click()
                        page.wait_for_timeout(300)
                except Exception:
                    pass

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text(separator=" ")

                # 1. Effectif
                eff_el = soup.find(lambda t: t.name == "strong" and "effectif" in t.text.lower())
                if eff_el and eff_el.parent:
                    result["effectif"] = re.sub(r"\s+", " ", eff_el.parent.get_text().replace("Effectif:", "")).strip()

                # 2. Chiffre D'affaires
                ca_el = soup.find(lambda t: t.name == "strong" and "chiffre" in t.text.lower())
                if ca_el and ca_el.parent:
                    result["chiffre_d_affaires"] = re.sub(r"\s+", " ", ca_el.parent.get_text().replace("Chiffre D'affaires:", "")).strip()

                # 3. Capital
                cap_el = soup.find(lambda t: t.name == "strong" and "capital" in t.text.lower())
                if cap_el and cap_el.parent:
                    result["capital"] = re.sub(r"\s+", " ", cap_el.parent.get_text().replace("Capital:", "")).strip()

                # 4. RC
                rc_el = soup.find(lambda t: t.name == "strong" and "rc" in t.text.lower())
                if rc_el and rc_el.parent:
                    result["rc"] = re.sub(r"\s+", " ", rc_el.parent.get_text().replace("Rc:", "")).strip()

                # 5. Création
                creation_el = soup.find(lambda t: t.name == "strong" and "création" in t.text.lower())
                if creation_el and creation_el.parent:
                    result["creation"] = re.sub(r"\s+", " ", creation_el.parent.get_text().replace("Création:", "")).strip()

                # 6. ICE
                ice_el = soup.find(lambda t: t.name == "strong" and "ice" in t.text.lower())
                if ice_el and ice_el.parent:
                    result["ice"] = re.sub(r"\s+", " ", ice_el.parent.get_text().replace("Ice:", "")).strip()
                if not result["ice"]:
                    ice_match = re.search(r"ICE\s*[:\.-]?\s*(\d{15})", text, re.IGNORECASE)
                    if ice_match:
                        result["ice"] = ice_match.group(1)

                # 7. Dirigeants
                dir_header = soup.find(lambda t: t.name in ["h5", "h4"] and "dirigeant" in t.text.lower())
                if dir_header and dir_header.parent:
                    par_lists = dir_header.parent.find_all("p", class_="par-list")
                    if par_lists:
                        dirs = [re.sub(r"\s+", " ", p.get_text()).strip() for p in par_lists]
                        result["dirigeant"] = ", ".join([d for d in dirs if d])
                if not result["dirigeant"]:
                    dir_match = re.search(r"(?:Dirigeant|Gérant|Responsable|CEO|Manager)\s*[:\.-]?\s*([A-Za-zÀ-ÿ\s]{3,30})", text, re.IGNORECASE)
                    if dir_match:
                        d_val = re.sub(r'[\r\n\t×]', ' ', dir_match.group(1)).strip()
                        d_val = re.sub(r'\s+', ' ', d_val)
                        if len(d_val) > 2:
                            result["dirigeant"] = d_val

                # 8. Activités
                act_header = soup.find(lambda t: t.name in ["h5", "h4"] and "activite" in t.text.lower())
                if act_header and act_header.parent:
                    p_act = act_header.parent.find("p")
                    if p_act:
                        result["activity"] = re.sub(r"\s+", " ", p_act.get_text()).strip()

                # Phone extraction
                phone_match = re.search(r"(?:Tél|Tel|Téléphone)\s*[:\.-]?\s*([\+0]\d[\d\s\.\-/]{8,15})", text, re.IGNORECASE)
                if phone_match:
                    result["phone"] = phone_match.group(1).strip()

                # Address / City
                address_p = soup.find("p", class_="card-text")
                if address_p:
                    addr_text = re.sub(r"\s+", " ", address_p.get_text()).strip()
                    if addr_text:
                        result["address"] = addr_text
                        
                        # Attempt to extract city if possible
                        city_match = re.search(r"\d{5}\s+([A-Za-zÀ-ÿ\-\s]+?)\s+\-", addr_text)
                        if city_match:
                            result["city"] = city_match.group(1).strip()


        except Exception as e:
            logger.error(f"Scraping error for {company_name}: {e}")
        finally:
            page.close()

        json_output = json.dumps(result, indent=2, ensure_ascii=False)
        logger.info(f"Scraped JSON result for '{company_name}':\n{json_output}")

        return result
