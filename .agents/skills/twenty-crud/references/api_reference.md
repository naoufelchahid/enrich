# Twenty CRM REST API Reference

This reference documents the primary endpoints, schema definitions, and payload examples for Twenty CRM's REST API.

---

## 1. Core Endpoints Overview

| Object | REST Endpoint | Description |
| :--- | :--- | :--- |
| **Companies** | `/rest/companies` | B2B Accounts and Business Entities |
| **People** | `/rest/people` | Contacts, Individual Contacts |
| **Opportunities** | `/rest/opportunities` | Deals and Revenue Pipelines |
| **Tasks** | `/rest/tasks` | Activities, Reminders, and Action Items |
| **Notes** | `/rest/notes` | Interaction Records, Comments, Logs |
| **Workspace Members** | `/rest/workspaceMembers` | Internal CRM Users & Team Members |

---

## 2. Object Schemas & Examples

### 2.1 Companies (`/rest/companies`)

#### Schema Fields
- `id` (string, UUID): Unique identifier.
- `name` (string): Company name.
- `domainName` (object):
  - `primaryEmail` (string): Primary domain email/url.
- `employees` (number): Number of employees.
- `linkedinLink` (object): LinkedIn profile URL.
- `address` (object):
  - `addressStreet1` (string)
  - `addressStreet2` (string)
  - `addressCity` (string)
  - `addressState` (string)
  - `addressPostcode` (string)
  - `addressCountry` (string)
- `createdAt` (ISO Date string).
- `updatedAt` (ISO Date string).

#### Create Example
```json
{
  "name": "Acme Corporation",
  "domainName": {
    "primaryEmail": "acme.com"
  },
  "employees": 150
}
```

---

### 2.2 People (`/rest/people`)

#### Schema Fields
- `id` (string, UUID).
- `name` (object):
  - `firstName` (string)
  - `lastName` (string)
- `emails` (object):
  - `primaryEmail` (string)
  - `additionalEmails` (array)
- `phones` (object):
  - `primaryPhone` (string)
  - `primaryPhoneCountryCode` (string)
- `companyId` (string, UUID): Foreign key link to Company.
- `position` (string): Job title.

#### Create Example
```json
{
  "name": {
    "firstName": "Sarah",
    "lastName": "Connor"
  },
  "emails": {
    "primaryEmail": "sarah@cyberdyne.com"
  },
  "companyId": "123e4567-e89b-12d3-a456-426614174000",
  "position": "Head of Security"
}
```

---

### 2.3 Opportunities (`/rest/opportunities`)

#### Schema Fields
- `id` (string, UUID).
- `name` (string): Opportunity title.
- `amount` (object):
  - `amountMicros` (number): Amount in micros (e.g. 10,000 USD = 10000000000).
  - `currencyCode` (string): Currency code (e.g. "USD", "EUR").
- `stage` (string): Pipeline stage (e.g. `"NEW"`, `"SCREENING"`, `"MEETING"`, `"PROPOSAL"`, `"CUSTOMER"`, `"CLOSED_WON"`, `"CLOSED_LOST"`).
- `closeDate` (ISO Date string).
- `companyId` (string, UUID): Linked company.
- `pointOfContactId` (string, UUID): Linked person contact.

#### Create Example
```json
{
  "name": "Enterprise Renewal Q3",
  "amount": {
    "amountMicros": 25000000000,
    "currencyCode": "USD"
  },
  "stage": "PROPOSAL",
  "companyId": "123e4567-e89b-12d3-a456-426614174000"
}
```

---

### 2.4 Tasks (`/rest/tasks`)

#### Schema Fields
- `id` (string, UUID).
- `title` (string): Task description.
- `status` (string): Task status (e.g. `"TODO"`, `"IN_PROGRESS"`, `"DONE"`).
- `dueAt` (ISO Date string).
- `assigneeId` (string, UUID): Assigned workspace member.

#### Create Example
```json
{
  "title": "Follow up on enterprise proposal",
  "status": "TODO",
  "dueAt": "2026-08-10T17:00:00Z"
}
```

---

### 2.5 Notes (`/rest/notes`)

#### Schema Fields
- `id` (string, UUID).
- `title` (string).
- `body` (string): Note body text or markdown.

#### Create Example
```json
{
  "title": "Discovery Call Summary",
  "body": "Customer confirmed budget approval for 50 licenses."
}
```
