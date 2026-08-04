# Company Import and Enrichment Pipeline

## Objective
To import a dataset of companies and related people (`BDD6.xls`) into Twenty CRM, ensuring that each company is only created once, and all associated people are linked to the correct company. Following the import, an enrichment pipeline will fetch additional data (ICE, RC, Address, etc.) via a Google AI digest search, falling back to specific Moroccan directories (Kerix, Charika) if critical data is missing.

## Architecture Overview
The process is split into a **Two-Step Pipeline** to ensure robustness. If the enrichment process fails (e.g., due to network issues or rate limits), the foundational CRM data remains intact, and the enrichment can be safely restarted.

1. **Step 1:** Data Deduplication & Import (`import_clean.py`)
2. **Step 2:** Enrichment Pipeline (`enrich_pipeline.py`)

---

## 1. Data Deduplication & Import (`import_clean.py`)

### Input
- File: `by_direction/bdd/BDD6.xls`
- Format: Contains columns such as `Société`, `G`, `Prénom et Nom`, `Fonction`, `TEL`, `E-mail`.

### Process
1. **Load and Clean:** 
   - Load the Excel file.
   - For deduplication purposes, normalize the `Société` column (strip leading/trailing whitespace, convert to lowercase). The original casing will be preserved for the actual CRM record.
2. **Group Data:**
   - Group the rows (people) by the normalized company name.
3. **CRM Insertion:**
   - Iterate through the unique companies.
   - **Create Company:** Insert the company into Twenty CRM and retrieve the resulting `companyId`.
   - **Create People:** For the current company, iterate through its associated people. 
     - Filter out roles like 'etudiant' / 'élève ingénieur'.
     - Sanitize phone numbers (ensure `+212` prefix for local numbers).
     - Create the person in Twenty CRM, linking them to the `companyId`.

---

## 2. Enrichment Pipeline (`enrich_pipeline.py`)

### Input
- Companies existing in Twenty CRM that require enrichment.

### Process
1. **Fetch Targets:** 
   - Query Twenty CRM for companies (or process sequentially from the recently imported batch).
2. **Pass 1: Google AI Digest (Primary)**
   - Use `CloakBrowser` to run a Google search for the company (e.g., `"Company Name" Morocco ICE RC contact`).
   - Scrape the "AI Overview" (digest) text from the Google results page.
   - Send the raw digest text to an LLM (e.g., Gemini) with a strict prompt: *Extract ICE, RC, Address, Phone, Capital, and Activity into a JSON object.*
   - Stage this JSON data.
3. **Pass 2: Directory Scraper Fallback (Secondary)**
   - Evaluate the staged data. If critical fields (like `ICE`, `RC`, or `Phone`) are missing, trigger the fallback scrapers.
   - Run the **Kerix** scraper and the **Charika** scraper.
   - Merge any newly found data into the staged JSON, prioritizing existing LLM data if conflicts arise.
4. **CRM Update:**
   - Send a `PATCH` request to Twenty CRM to update the company with the enriched fields.
   - Add a CRM "Note" to the company containing the raw Google AI digest text and any scraper logs, ensuring full traceability of where the data came from.

---

## Technical Constraints & Considerations
- **Environment:** Requires Python virtual environment (`venv`).
- **Dependencies:** `pandas`, LLM SDK (e.g., `google-generativeai`), CRM Client, `CloakBrowser`.
- **Extensibility:** The directory fallback framework must be designed so that scrapers for *Annuaire* and *Pages Jaunes* can be easily plugged in later without altering the core pipeline logic.
