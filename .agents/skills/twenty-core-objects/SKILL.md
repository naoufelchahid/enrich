---
name: twenty-core-objects
description: Handles CRUD operations for Twenty CRM Core Metadata & Platform Objects (Object Metadata, Field Metadata, Views, Webhooks, API Keys, and Page Layouts). Use this skill when defining custom objects, adding/modifying custom fields, configuring webhooks, managing workspace API keys, or customizing views and page layouts.
---

# Twenty CRM Core Objects & Metadata CRUD Skill

This skill provides complete instructions, schema specifications, and helper scripts for managing Twenty CRM **Core Objects & Metadata** via REST API endpoints.

---

## 1. Overview & Core Endpoints

Twenty CRM differentiates between **CRM Records** (e.g., companies, people) and **Core Objects / Metadata Entities** which define the CRM's structure and configuration.

| Core Metadata Object | REST Endpoint Slug | Description |
| :--- | :--- | :--- |
| **Object Metadata** | `/rest/metadata/objects` | Define custom & standard objects (e.g., Contracts, Assets) |
| **Field Metadata** | `/rest/metadata/fields` | Define custom fields (e.g., TEXT, SELECT, RELATION, DATE) |
| **Views Metadata** | `/rest/metadata/views` | Define list, kanban, and detail views |
| **Webhooks** | `/rest/metadata/webhooks` | Configure event webhooks (e.g. `company.created`) |
| **API Keys** | `/rest/metadata/apiKeys` | Manage workspace API keys & tokens |
| **Page Layouts** | `/rest/metadata/pageLayouts` | Detail page tab & widget layouts |

---

## 2. Core Metadata Objects CRUD Specifications

### 2.1 Object Metadata (`/rest/metadata/objects`)
Used to create and manage custom CRM entities.

#### Create Custom Object
- **Method**: `POST`
- **Endpoint**: `/rest/metadata/objects`
- **Payload Example**:
  ```json
  {
    "nameSingular": "contract",
    "namePlural": "contracts",
    "labelSingular": "Contract",
    "labelPlural": "Contracts",
    "description": "Customer service agreements and contracts",
    "icon": "IconFileText"
  }
  ```

#### List Objects
- **Method**: `GET`
- **Endpoint**: `/rest/metadata/objects`

---

### 2.2 Field Metadata (`/rest/metadata/fields`)
Used to create custom fields on custom or standard objects.

#### Supported Field Types
- `TEXT`, `NUMERIC`, `BOOLEAN`, `DATE`, `DATE_TIME`
- `SELECT`, `MULTI_SELECT` (requires `options` array)
- `RELATION` (requires relation configuration)
- `EMAILS`, `PHONES`, `ADDRESS`, `FULL_NAME`, `CURRENCY`, `RATING`

#### Create Custom Field Example
- **Method**: `POST`
- **Endpoint**: `/rest/metadata/fields`
- **Payload Example**:
  ```json
  {
    "objectMetadataId": "123e4567-e89b-12d3-a456-426614174000",
    "name": "contractValue",
    "label": "Contract Value",
    "type": "CURRENCY",
    "description": "Annual contract value in USD"
  }
  ```

---

### 2.3 Webhooks (`/rest/metadata/webhooks`)
Used to manage event notifications to external HTTP listeners.

#### Create Webhook Example
- **Method**: `POST`
- **Endpoint**: `/rest/metadata/webhooks`
- **Payload Example**:
  ```json
  {
    "targetUrl": "https://api.myapp.com/webhooks/twenty",
    "description": "Sync customer events",
    "operations": [
      "company.created",
      "person.updated"
    ]
  }
  ```

---

### 2.4 API Keys (`/rest/metadata/apiKeys`)
Used to manage workspace API credentials.

#### Create API Key Example
- **Method**: `POST`
- **Endpoint**: `/rest/metadata/apiKeys`
- **Payload Example**:
  ```json
  {
    "name": "Integration Service Key",
    "expiresAt": "2027-12-31T23:59:59.000Z"
  }
  ```

---

## 3. CLI Helper Utility (`twenty_core_client.py`)

A dedicated CLI script for managing core objects is located at `scripts/twenty_core_client.py`.

### Commands Syntax:

```bash
# List all custom & standard object definitions
python3 scripts/twenty_core_client.py list-objects

# Get object definition by ID
python3 scripts/twenty_core_client.py get-object <OBJECT_METADATA_UUID>

# Create a new custom object
python3 scripts/twenty_core_client.py create-object --data '{
  "nameSingular": "invoice",
  "namePlural": "invoices",
  "labelSingular": "Invoice",
  "labelPlural": "Invoices"
}'

# Create a new custom field
python3 scripts/twenty_core_client.py create-field --data '{
  "objectMetadataId": "<OBJECT_METADATA_UUID>",
  "name": "dueDate",
  "label": "Due Date",
  "type": "DATE"
}'

# List webhooks
python3 scripts/twenty_core_client.py list-webhooks

# Create a webhook
python3 scripts/twenty_core_client.py create-webhook --data '{
  "targetUrl": "https://hooks.example.com/crm",
  "operations": ["person.created"]
}'

# List workspace API Keys
python3 scripts/twenty_core_client.py list-apikeys
```

---

## 4. Best Practices & Guidelines

1. **Verify `objectMetadataId`**: When creating fields or views, always ensure you reference a valid `objectMetadataId`.
2. **Field Naming Conventions**: Use camelCase for field `name` (e.g. `contractValue`, `dueDate`) and Title Case for `label` (e.g. "Contract Value", "Due Date").
3. **Select Field Options**: For `SELECT` or `MULTI_SELECT` fields, provide array of options with `label`, `value`, `color`, and `position`.
4. **Header Auth**: All requests require `Authorization: Bearer <TWENTY_API_TOKEN>`.
