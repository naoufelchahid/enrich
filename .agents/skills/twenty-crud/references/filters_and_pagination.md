# Twenty CRM Filtering, Pagination, and Sorting Reference

This document provides detailed examples and syntax rules for constructing REST queries in Twenty CRM.

---

## 1. Filter Query Syntax

Filters are supplied via the `filter` query parameter.

Format: `field[COMPARATOR]:value`

### 1.1 Comparators

| Comparator | Description | Example |
| :--- | :--- | :--- |
| `eq` | Exact match | `filter=stage[eq]:"CLOSED_WON"` |
| `neq` | Not equal to | `filter=stage[neq]:"CLOSED_LOST"` |
| `gt` | Greater than | `filter=amount.amountMicros[gt]:5000000000` |
| `gte` | Greater than or equal to | `filter=createdAt[gte]:"2026-01-01T00:00:00Z"` |
| `lt` | Less than | `filter=employees[lt]:50` |
| `lte` | Less than or equal to | `filter=employees[lte]:500` |
| `in` | Value in JSON array | `filter=stage[in]:["PROPOSAL","CUSTOMER"]` |
| `is` | Null / Boolean check | `filter=deletedAt[is]:NULL` |
| `startsWith` | String prefix match | `filter=name[startsWith]:"Acme"` |
| `like` | SQL LIKE match (case-sensitive) | `filter=name[like]:"%Corp%"` |
| `ilike` | SQL ILIKE match (case-insensitive) | `filter=name[ilike]:"%acme%"` |

---

## 2. Logical Combinations

### 2.1 AND Conjunction (Default)
Multiple field filter parameters joined with commas are implicitly `AND`ed together:
```text
filter=stage[eq]:"PROPOSAL",amount.amountMicros[gte]:10000000000
```

### 2.2 Logical OR
Use `or(...)` to wrap alternative conditions:
```text
filter=or(stage[eq]:"NEW",stage[eq]:"SCREENING")
```

### 2.3 Logical NOT
Use `not(...)` to negate a single condition:
```text
filter=not(status[eq]:"ARCHIVED")
```

---

## 3. Pagination Management

Twenty CRM uses cursor-based pagination for predictable list scanning.

### Parameters:
- `limit`: Number of items per page (default `60`, max `60`).
- `starting_after`: Opaque string cursor indicating the start of the next set of items.
- `ending_before`: Opaque string cursor indicating the previous set of items.

### Response Structure:
```json
{
  "pageInfo": {
    "hasNextPage": true,
    "hasPreviousPage": false,
    "startCursor": "eyJpZCI6IjEyMyJ9",
    "endCursor": "eyJpZCI6Ijc4OSJ9"
  }
}
```

### Fetching Next Page Example:
```bash
curl -H "Authorization: Bearer $TWENTY_API_TOKEN" \
  "http://localhost:3005/rest/companies?limit=60&starting_after=eyJpZCI6Ijc4OSJ9"
```

---

## 4. Ordering & Sorting (`order_by`)

Order query parameter format:
`order_by=field1[Direction1],field2[Direction2]`

### Directions:
- `AscNullsFirst` (default for Ascending)
- `AscNullsLast`
- `DescNullsFirst`
- `DescNullsLast`

### Examples:
```text
# Sort by creation date descending
order_by=createdAt[DescNullsLast]

# Sort by stage ascending, then created date descending
order_by=stage[AscNullsFirst],createdAt[DescNullsLast]
```
