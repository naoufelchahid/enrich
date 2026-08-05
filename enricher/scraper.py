import urllib.parse
import logging
import json
import re
from typing import Dict, Any
import requests
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
            logger.info("Step 1: Searching Google for company domains...")
            query = f'site:telecontact.ma OR site:kerix.net OR site:charika.ma intitle:"{clean_name}"'
            q_enc = urllib.parse.quote(query)
            
            page.goto(f"https://www.google.com/search?q={q_enc}&hl=en", wait_until="networkidle", timeout=30000)

            try:
                page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (let b of btns) {
                        if (b.innerText.includes('Accept all') || b.innerText.includes('Tout accepter') || b.innerText.includes('I agree')) {
                            b.click();
                            break;
                        }
                    }
                }""")
                page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Failed to dismiss Google cookie banner: {e}")

            target_url = None
            try:
                page.wait_for_selector("a", timeout=5000)
                links = page.query_selector_all("a")
                for link in links:
                    href = link.get_attribute("href")
                    if href and any(d in href for d in ["kerix.net", "telecontact.ma", "charika.ma"]):
                        if "google.com" not in href and "webcache" not in href and "translate.goog" not in href:
                            target_url = href
                            break
            except Exception as e:
                logger.warning(f"Could not find search results on Google: {e}")

            if not target_url:
                logger.info("No matching URLs found on Google.")
                return result

            logger.info(f"Matched URL from Google: {target_url}")

            if "telecontact.ma" in target_url:
                result["telecontact_url"] = target_url
                page.goto(target_url, wait_until="networkidle", timeout=30000)
                html = page.content()
                phone_match = re.search(r"(?:Tél|Tel|Téléphone)\s*[:\.-]?\s*([\+0]\d[\d\s\.\-/]{8,15})", html, re.IGNORECASE)
                if phone_match:
                    result["phone"] = phone_match.group(1).strip()
                return result

            elif "charika.ma" in target_url:
                result["charika_url"] = target_url
                page.goto(target_url, wait_until="networkidle", timeout=30000)
                html = page.content()
                ice_match = re.search(r"ICE\s*[:\.-]?\s*(\d{15})", html, re.IGNORECASE)
                if ice_match:
                    result["ice"] = ice_match.group(1)
                return result

            elif "kerix.net" in target_url:
                result["kerix_url"] = target_url
                page.goto(target_url, wait_until="networkidle", timeout=30000)

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


class CharikaScraper:
    DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    def __init__(self, headless=True):
        pass # Not using CloakBrowser for Charika by default unless needed, using simple requests for test

    def scrape_company(self, name: str) -> dict:
        """Fallback simple scraper for charika.ma"""
        clean_name = clean_company_name(name)
        if not clean_name:
            clean_name = name.strip()
        sanitized_name = re.sub(r'[^\w\s\-]', '', clean_name)
        slug = re.sub(r'\s+', '-', sanitized_name.strip())
        encoded_slug = urllib.parse.quote(slug)
        url = f"https://www.charika.ma/societe-{encoded_slug}"
        data = {}
        headers = {
            "User-Agent": self.DEFAULT_USER_AGENT
        }
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                text = soup.get_text()
                ice_match = re.search(r'ICE[^\d]*(\d{9,15})', text, re.IGNORECASE)
                if ice_match:
                    data['ice'] = ice_match.group(1)
        except Exception as e:
            logger.error(f"Charika scrape failed: {e}")
        return data

class LinkedInScraper:
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

    def scrape_person(self, person_name: str, company_name: str) -> Dict[str, Any]:
        """Search Google for person's LinkedIn profile and extract basic info."""
        self.start()
        
        result = {
            "name": person_name,
            "company": company_name,
            "linkedin_url": None,
            "headline": None
        }

        page = self.browser.new_page()
        try:
            logger.info(f"Searching Google for LinkedIn profile of {person_name} at {company_name}...")
            # Query format: site:linkedin.com/in intitle:"person name" "company name"
            query = f'site:linkedin.com/in intitle:"{person_name}" "{company_name}"'
            q_enc = urllib.parse.quote(query)
            
            page.goto(f"https://www.google.com/search?q={q_enc}&hl=en", wait_until="networkidle", timeout=30000)

            try:
                page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (let b of btns) {
                        if (b.innerText.includes('Accept all') || b.innerText.includes('Tout accepter') || b.innerText.includes('I agree')) {
                            b.click();
                            break;
                        }
                    }
                }""")
                page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Failed to dismiss Google cookie banner: {e}")

            target_url = None
            headline = None
            try:
                page.wait_for_selector("a", timeout=5000)
                # Find the first valid linkedin.com/in url from Google results
                elements = page.query_selector_all("div.g") # Google search result block
                for el in elements:
                    link = el.query_selector("a")
                    if link:
                        href = link.get_attribute("href")
                        if href and "linkedin.com/in" in href and "google.com" not in href:
                            target_url = href
                            # try to extract snippet/headline if possible from the Google result
                            h3 = el.query_selector("h3")
                            if h3:
                                headline = h3.inner_text()
                            break
            except Exception as e:
                logger.warning(f"Could not find search results on Google: {e}")

            if target_url:
                logger.info(f"Matched LinkedIn URL from Google: {target_url}")
                result["linkedin_url"] = target_url
                if headline:
                    result["headline"] = headline.replace(" - LinkedIn", "").strip()
            else:
                logger.info("No matching LinkedIn URLs found on Google.")

        except Exception as e:
            logger.error(f"Scraping error for {person_name} ({company_name}): {e}")
        finally:
            page.close()

        json_output = json.dumps(result, indent=2, ensure_ascii=False)
        logger.info(f"Scraped JSON result for '{person_name}':\n{json_output}")

        return result

