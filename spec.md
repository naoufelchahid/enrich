## Problem Statement
The user currently uses disconnected Python scripts to import and enrich leads. This lacks a unified UI for data curation, requires running manual scripts for enrichment (Kerix, LinkedIn), and lacks an automated autonomous AI agent to draft personalized emails and a drip campaign manager that respects SMTP limits natively within their CRM.

## Solution
A complete refactor of the logic into a native Twenty App. This app will provide:
1. A Front Component UI in the sidebar for uploading and mapping columns of the BDD6.xlsx file.
2. Background jobs to enrich `Company` and `Person` records via Kerix's JSON API and a low-cost LinkedIn API.
3. An autonomous native Twenty AI Agent that generates Persona Reports and drafts emails (using inbound marketing best practices) as soon as enrichment completes.
4. A cron-based Drip Automation that strictly picks one email from a custom `EmailQueueRecord` queue every 6 minutes to send via SMTP, ensuring rate limits are respected without manual intervention.

## User Stories
1. As a sales representative, I want a dedicated full-page UI in Twenty to upload my BDD6.xlsx file, so that I can easily bring new leads into the CRM.
2. As a sales representative, I want a visual column mapping UI after uploading the file, so that I can correctly map inconsistent Excel columns to Twenty's standard fields (Name, Email, Phone, etc.).
3. As a data manager, I want rejected rows (e.g., missing email) to be saved to a `SkippedLead` object, so that they aren't silently lost and can be audited later.
4. As a sales representative, I want the system to automatically enrich each company in the background via the Kerix JSON API and a LinkedIn API, so that I get up-to-date prospect information without manual scraping.
5. As an administrator, I want enrichment tasks to run as isolated background jobs, so that rate limits from data providers do not cause the entire batch process to fail or crash.
6. As a sales representative, I want an autonomous AI agent to automatically write a Persona Report and draft an outbound email immediately upon successful enrichment, so that I don't have to trigger the AI manually for every prospect.
7. As a marketing manager, I want the AI Agent's system prompt to enforce strict inbound marketing best practices (e.g., PAS framework, under 100 words), so that outbound emails convert at a higher rate.
8. As a sales representative, I want the drafted emails to be placed in an `EmailQueueRecord` queue, so that the system can manage the sending schedule.
9. As a systems administrator, I want a cron job to automatically send exactly one pending email from the queue every 6 minutes via my configured SMTP provider, so that I never exceed my 10 emails/hour limit and avoid IP blacklisting.
10. As a systems administrator, I want SMTP sending failures to permanently mark the queue record as 'Failed' with the error message attached, so that I can investigate and requeue manually without triggering an automated retry loop that might lock my email account.
11. As an administrator, I want to store all external API keys and SMTP credentials as Twenty Workspace Variables, so that I can manage them securely through the Twenty UI without editing code.

## Implementation Decisions
- **Modules Built/Modified:**
  - `Twenty App`: A new custom application bundle built within Twenty's extension framework.
  - `Front Component`: A React component registered in the Navigation Menu for uploading and mapping Excel files.
  - `Data Curation Job`: A Logic Function that ingests the JSON from the Front Component.
  - `Enrichment Job`: A Background Job that hits the Kerix API (`/fr/search`) and Serpent API (or Scrapingdog).
  - `AI Agent`: A native Twenty AI Agent configured to use the workspace's LLM connection and an injected system prompt for inbound marketing.
  - `Drip Cron Job`: A Logic Function triggered every 6 minutes to process the `EmailQueueRecord`.
- **Architectural Decisions:**
  - Parsing the `.xlsx` file happens entirely on the client side (browser) to avoid payload limits. The Front Component sends structured JSON to the backend.
  - Kerix enrichment utilizes their native JSON search endpoint (`https://www.kerix.net/fr/search?q=XYZ`) to completely bypass the need for a headless browser (Puppeteer/Playwright).
  - Proxycurl is deprecated due to shutdown; the app is designed generically to support low-cost HTTP API alternatives for LinkedIn enrichment.
  - The AI generation step is fully autonomous and triggers programmatically after enrichment completes.
- **Schema Changes:**
  - Native `Company` and `Person` standard objects will be used.
  - Custom fields injected on `Person`: `Persona Report`, `Draft Email`, `Enrichment Status`.
  - New Custom Object: `SkippedLead` (for audit of rejected excel rows).
  - New Custom Object: `EmailQueueRecord` (Fields: Person ID, Email Content, Status, Error Message).
- **Configuration:**
  - Secrets (SMTP, API keys) managed natively via `defineApplication` Workspace Variables.

## Testing Decisions
- A good test in this module will only test external behavior and end-to-end flows, avoiding testing internal parsing or mapping mechanics inside the Logic Functions.
- **External Network Seam:** We will mock the external HTTP calls (Kerix, LinkedIn, OpenAI, SMTP) using an HTTP mocking tool (like `nock` or `msw`). This will allow us to test the entire job's side effects without network requests.
- **Twenty App Context Seam:** Logic functions will be invoked with a mocked Twenty Context (simulating `ctx.api`) to assert that the jobs successfully create/update the correct `Company`, `Person`, `SkippedLead`, and `EmailQueueRecord` entities.
- **Front Component Seam:** We will use React Testing Library to test the Excel parsing and column mapping logic by simulating user interactions (uploading a mock file, mapping columns) and asserting the exact JSON payload dispatched to the backend, treating the internal DOM rendering as a black box.

## Out of Scope
- Open/click tracking for the drip campaign (not implemented in V1 to protect deliverability and reduce complexity).
- Headless browser automation (all external calls are direct HTTP requests).
- Synchronous UI for monitoring the status of background jobs (users can check the `Company`/`EmailQueueRecord` object views natively in Twenty).

## Further Notes
- Ensure that the SMTP client (`nodemailer`) is robust against timeouts and accurately captures error messages for the `Failed` state.
- The AI Agent prompts will be provided explicitly by the user, so the implementation should allow for injecting these templates cleanly.
