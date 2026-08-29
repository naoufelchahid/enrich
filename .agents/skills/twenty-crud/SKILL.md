---
name: twenty-crud
description: Handles CRUD (Create, Read, Update, Delete) operations for Twenty CRM via its REST API. Use this skill whenever interacting with Twenty CRM data, querying records, filtering, creating, updating, or deleting companies, people, opportunities, tasks, notes, or custom objects.
---

# Twenty CRM CRUD Operations Skill

This skill provides comprehensive instructions, API patterns, filter matrices, and script tools for interacting with Twenty CRM's REST API to perform robust CRUD (Create, Read, Update, Delete) operations.

---

## 1. Overview & Architecture

Twenty CRM provides a REST API exposed at:
- **Base URL**: `http://localhost:3005/rest` (or server environment URL)
- **OpenAPI Schema**: `http://localhost:3005/open-api/core` (or `/rest/open-api/core`)
- **MCP Server**: `http://localhost:3005/mcp`

### Authentication
All REST requests require Bearer Token authorization passed via HTTP headers:

```http
Authorization: Bearer <YOUR_TWENTY_API_TOKEN>
Content-Type: application/json
```

> **Security Note**: Never pass tokens in URL query parameters. Always send them in the `Authorization` header.

---

## 2. Core CRM Entities

Twenty CRM supports the following core objects (plus any custom objects created in the workspace):

| Object Name | Endpoint Slug | Primary Fields |
| :--- | :--- | :--- |
| **Companies** | `/rest/companies` | `name`, `domainName`, `employees`, `linkedinLink`, `address` |
| **People** | `/rest/people` | `name`, `emails`, `phones`, `companyId`, `position`, `avatarUrl` |
| **Opportunities** | `/rest/opportunities` | `name`, `amount`, `stage`, `closeDate`, `companyId`, `pointOfContactId` |
| **Tasks** | `/rest/tasks` | `title`, `status`, `dueAt`, `assigneeId`, `targetObject` |
| **Notes** | `/rest/notes` | `title`, `body`, `position`, `targetObject` |
| **Workspace Members** | `/rest/workspaceMembers` | `name`, `userEmail`, `avatarUrl` |

---

## 3. CRUD API Reference & Payload Formats

### 3.1. Read / List Records (Find Many)
- **Method**: `GET`
- **Endpoint**: `/rest/{objectName}`
- **Query Parameters**:
  - `limit`: Number of records to return (default `60`, max `60`).
  - `starting_after`: Cursor for fetching the next page.
  - `ending_before`: Cursor for fetching the previous page.
  - `filter`: Filter criteria (see Section 4).
  - `order_by`: Sorting criteria (e.g. `createdAt[DescNullsLast],name[AscNullsFirst]`).

#### Example Request
```bash
curl -X GET "http://localhost:3005/rest/companies?limit=20&order_by=createdAt[DescNullsLast]" \
  -H "Authorization: Bearer $TWENTY_API_TOKEN" \
  -H "Content-Type: application/json"
```

#### Response Structure
```json
{
  "data": {
    "companies": [
      {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "name": "Acme Corp",
        "domainName": {
          "primaryEmail": "info@acme.com",
          "secondaryEmails": []
        },
        "createdAt": "2026-08-01T10:00:00.000Z",
        "updatedAt": "2026-08-01T10:00:00.000Z"
      }
    ]
  },
  "pageInfo": {
    "hasNextPage": false,
    "hasPreviousPage": false,
    "startCursor": "cursor1",
    "endCursor": "cursor2"
  }
}
```

---

### 3.2. Read One Record (Find One)
- **Method**: `GET`
- **Endpoint**: `/rest/{objectName}/{id}`

#### Example Request
```bash
curl -X GET "http://localhost:3005/rest/companies/123e4567-e89b-12d3-a456-426614174000" \
  -H "Authorization: Bearer $TWENTY_API_TOKEN"
```

---

### 3.3. Create Record
- **Method**: `POST`
- **Endpoint**: `/rest/{objectName}`
- **Body**: JSON object containing field values.

#### Example Request (Create Person)
```bash
curl -X POST "http://localhost:3005/rest/people" \
  -H "Authorization: Bearer $TWENTY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": {
      "firstName": "John",
      "lastName": "Doe"
    },
    "emails": {
      "primaryEmail": "john.doe@example.com"
    },
    "companyId": "123e4567-e89b-12d3-a456-426614174000"
  }'
```

---

### 3.4. Update Record (Partial Update)
- **Method**: `PATCH`
- **Endpoint**: `/rest/{objectName}/{id}`
- **Body**: JSON object with updated fields.

#### Example Request (Update Opportunity Stage)
```bash
curl -X PATCH "http://localhost:3005/rest/opportunities/98765432-e89b-12d3-a456-426614174000" \
  -H "Authorization: Bearer $TWENTY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "stage": "CLOSED_WON",
    "amount": {
      "amountMicros": 50000000000,
      "currencyCode": "USD"
    }
  }'
```

---

### 3.5. Delete Record
- **Method**: `DELETE`
- **Endpoint**: `/rest/{objectName}/{id}`

#### Example Request
```bash
curl -X DELETE "http://localhost:3005/rest/tasks/11223344-e89b-12d3-a456-426614174000" \
  -H "Authorization: Bearer $TWENTY_API_TOKEN"
```

---

### 3.6. Batch Operations
- **Method**: `POST`
- **Endpoint**: `/rest/{objectName}/batch` (or multiple items request)

---

## 4. Query Filtering & Sorting Syntax

Twenty CRM uses a structured `filter` parameter:

### Comparators
| Comparator | Meaning | Example |
| :--- | :--- | :--- |
| `eq` | Equals | `filter=status[eq]:"OPEN"` |
| `neq` | Not Equals | `filter=status[neq]:"CLOSED"` |
| `gt` / `gte` | Greater Than / Or Equal | `filter=createdAt[gte]:"2026-01-01"` |
| `lt` / `lte` | Less Than / Or Equal | `filter=amount.amountMicros[lt]:1000000` |
| `in` | Value in array | `filter=id[in]:["id1","id2"]` |
| `is` | Null check | `filter=deletedAt[is]:NULL` |
| `containsAny` | Array overlap | `filter=tags[containsAny]:["vip"]` |
| `startsWith` | String starts with | `filter=name[startsWith]:"Acme"` |
| `like` / `ilike` | Pattern match (case insensitive) | `filter=name[ilike]:"%tech%"` |

### Logical Combinations
- **Multiple conditions (AND)**: Separate by comma.
  `filter=status[eq]:"OPEN",createdAt[gte]:"2026-01-01"`
- **Logical OR**: Wrap in `or(...)`.
  `filter=or(status[eq]:"OPEN",assigneeId[is]:NULL)`
- **Logical NOT**: Wrap in `not(...)`.
  `filter=not(status[eq]:"CLOSED")`

---

## 5. Python CLI Helper Utility (`twenty_client.py`)

A standalone helper script is provided at `scripts/twenty_client.py` for automated interaction.

### Commands Syntax:
```bash
# List companies with filter
python3 scripts/twenty_client.py list companies --filter 'name[ilike]:"%acme%"' --limit 10

# Get single record by ID
python3 scripts/twenty_client.py get companies 123e4567-e89b-12d3-a456-426614174000

# Create record
python3 scripts/twenty_client.py create people --data '{"name": {"firstName": "Jane", "lastName": "Smith"}}'

# Update record
python3 scripts/twenty_client.py update companies 123e4567-e89b-12d3-a456-426614174000 --data '{"name": "New Name"}'

# Delete record
python3 scripts/twenty_client.py delete tasks 11223344-e89b-12d3-a456-426614174000
```

---

## 6. Best Practices & Guidelines
1. **Always Verify Token**: Ensure `TWENTY_API_TOKEN` environment variable or `--token` option is set.
2. **Use Composite Fields Correctly**: Note structured fields like `name` (`firstName`, `lastName`), `emails` (`primaryEmail`), `domainName` (`primaryEmail`).
3. **Cursor Pagination**: For scanning large datasets, iterate using `starting_after=<endCursor>`.
4. **Error Handling**: Twenty REST API returns structured error objects with HTTP codes (`400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`). Inspect error messages for details.
