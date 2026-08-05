# Data Enrichment and Outreach Pipeline Design

## Overview
This design outlines a 4-step pipeline to import, enrich, and contact companies and individuals. The pipeline uses a Micro-Task architecture with independent scripts scheduled via Cron jobs to prevent slow tasks (like web scraping) from blocking others. Twenty CRM acts as the single source of truth for tracking state.

## Architecture & Data Flow

### Source of Truth
We will use **Twenty CRM** to track the state of every company and person. We'll add custom tracking fields to the CRM schema:
- **`Enrichment Status`** (Company & Person): `pending`, `company_enriched`, `person_enriched`
- **`Contact Status`** (Person only): `pending`, `contacted`, `bounced`

### 1. Data Import (`import_bdd.py`)
- **Execution:** Run manually when new data arrives.
- **Input:** Excel files (starting with `by_direction/bdd/BDD6.xls`).
- **Action:** 
  - Parses the Excel file.
  - Creates Companies and People in Twenty CRM.
  - Sets their `Enrichment Status` to `pending`.

### 2. Company Enrichment (`enrich_company.py`)
- **Execution:** Scheduled via Cron (e.g., every 15 minutes, processing a batch of 5-10 records).
- **Input:** Queries Twenty CRM for Companies where `Enrichment Status` = `pending`.
- **Action:**
  - Uses the existing `CloakBrowser` setup to perform a Google Search: `site:telecontact.ma OR site:kerix.net OR site:charika.ma intitle:"[Company Name]"`.
  - Determines the best source (priority: Kerix > Telecontact > Charika) from the search results.
  - Scrapes the target URL to extract address, RC, capital, ICE, effectif, etc.
  - Updates the Company record in Twenty CRM.
  - Updates the `Enrichment Status` to `company_enriched`.

### 3. Person Enrichment (`enrich_person.py`)
- **Execution:** Scheduled via Cron (e.g., every 15 minutes).
- **Input:** Queries Twenty CRM for People where the associated Company is `company_enriched` and the Person's `Enrichment Status` is `pending`.
- **Action:**
  - Uses `CloakBrowser` to search Google for the person's LinkedIn profile: `site:linkedin.com/in intitle:"[Person Name]" "[Company Name]"`.
  - Scrapes the LinkedIn profile to extract bio and work experience.
  - Updates the Person record in Twenty CRM.
  - Updates the `Enrichment Status` to `person_enriched` and sets `Contact Status` to `pending`.

### 4. Outreach Automation (`outreach.py`)
- **Execution:** Scheduled via Cron (Strict limit: 10 per hour).
- **Input:** Queries Twenty CRM for People where `Enrichment Status` = `person_enriched` and `Contact Status` = `pending`.
- **Action:**
  - Applies quality filters (e.g., must have a valid email or phone, company size must meet a certain threshold).
  - Uses Capconnect SMTP to send a highly customized cold email OR uses PyWhatKit to send a WhatsApp message.
  - Updates the `Contact Status` to `contacted` in Twenty CRM to ensure the person is never contacted twice.

## Requirements
- Twenty CRM API Key & Instance URL.
- CloakBrowser License Key.
- Capconnect SMTP Credentials.
- WhatsApp Web session authenticated for PyWhatKit.
- Standard Python libraries (Pandas, Requests, Schedule/Cron).
