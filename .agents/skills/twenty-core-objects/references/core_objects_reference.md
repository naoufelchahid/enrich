# Twenty CRM Core Objects & Metadata Reference

This reference details the REST API specifications for managing Twenty CRM Core Objects, Custom Data Models, Field Definitions, Webhooks, API Keys, Views, and Page Layouts.

---

## 1. Object Metadata (`/rest/metadata/objects`)

Object Metadata defines custom entities (e.g., Invoices, Contracts, Assets) and holds standard entity metadata.

### Fields:
- `id` (string, UUID): Unique object identifier.
- `nameSingular` (string): Internal singular name (e.g. `"invoice"`).
- `namePlural` (string): Internal plural name (e.g. `"invoices"`).
- `labelSingular` (string): UI singular label (e.g. `"Invoice"`).
- `labelPlural` (string): UI plural label (e.g. `"Invoices"`).
- `description` (string): Description of the entity.
- `icon` (string): Icon identifier (e.g. `"IconFileText"`).
- `isCustom` (boolean): `true` for user-created entities.
- `isSystem` (boolean): `true` for internal platform entities.

### Operations:
- `GET /rest/metadata/objects`: List all objects.
- `GET /rest/metadata/objects/{id}`: Retrieve single object definition.
- `POST /rest/metadata/objects`: Create new custom object.
- `PATCH /rest/metadata/objects/{id}`: Update object properties.
- `DELETE /rest/metadata/objects/{id}`: Remove custom object.

---

## 2. Field Metadata (`/rest/metadata/fields`)

Field Metadata defines properties on objects.

### Fields:
- `id` (string, UUID).
- `objectMetadataId` (string, UUID): Target object ID.
- `name` (string): camelCase field key.
- `label` (string): Display label.
- `type` (string): Field type enum (`TEXT`, `NUMERIC`, `BOOLEAN`, `DATE`, `DATE_TIME`, `SELECT`, `MULTI_SELECT`, `RELATION`, `EMAILS`, `PHONES`, `CURRENCY`, `RATING`).
- `description` (string).
- `options` (array of objects): Used for `SELECT` / `MULTI_SELECT`.

### Operations:
- `GET /rest/metadata/fields`: List field metadata.
- `GET /rest/metadata/fields/{id}`: Retrieve field metadata.
- `POST /rest/metadata/fields`: Create field definition.
- `PATCH /rest/metadata/fields/{id}`: Update field properties.
- `DELETE /rest/metadata/fields/{id}`: Delete field definition.

---

## 3. Webhooks (`/rest/metadata/webhooks`)

Webhooks trigger HTTP POST calls to external services upon CRM events.

### Fields:
- `id` (string, UUID).
- `targetUrl` (string): Endpoint URL.
- `description` (string).
- `operations` (array of strings): Events to subscribe to (e.g. `["company.created", "person.updated", "opportunity.deleted"]`).
- `secret` (string): Webhook signing secret.

---

## 4. API Keys (`/rest/metadata/apiKeys`)

Manage workspace API access credentials.

### Fields:
- `id` (string, UUID).
- `name` (string): Key label or description.
- `expiresAt` (ISO Date string).
- `revokedAt` (ISO Date string).
- `roleId` (string, UUID): Associated role.
