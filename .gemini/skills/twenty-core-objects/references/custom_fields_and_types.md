# Custom Fields and Field Types Reference

This guide documents custom field creation, field types, select option structures, and relation configurations in Twenty CRM.

---

## 1. Supported Field Types

| Type Enum | Description | Example Value Structure |
| :--- | :--- | :--- |
| `TEXT` | Plain text | `"High Priority"` |
| `NUMERIC` | Number or decimal | `150.5` |
| `BOOLEAN` | True / False flag | `true` |
| `DATE` | Calendar date | `"2026-08-01"` |
| `DATE_TIME` | ISO Timestamp | `"2026-08-01T12:00:00.000Z"` |
| `SELECT` | Single select dropdown | `"ENTERPRISE"` |
| `MULTI_SELECT` | Multi select array | `["VIP", "RENEWAL"]` |
| `CURRENCY` | Money with currency | `{"amountMicros": 100000000, "currencyCode": "USD"}` |
| `EMAILS` | Structured emails | `{"primaryEmail": "test@example.com"}` |
| `PHONES` | Structured phone number | `{"primaryPhone": "+1234567890"}` |
| `ADDRESS` | Address compound | `{"addressStreet1": "123 Main St", "addressCity": "NY"}` |
| `RATING` | Star rating | `"RATING_5"` |

---

## 2. Select & Multi-Select Field Payload Structure

When creating a `SELECT` or `MULTI_SELECT` field, pass an `options` array:

```json
{
  "objectMetadataId": "123e4567-e89b-12d3-a456-426614174000",
  "name": "tier",
  "label": "Tier",
  "type": "SELECT",
  "options": [
    {
      "label": "Standard",
      "value": "STANDARD",
      "color": "blue",
      "position": 1
    },
    {
      "label": "Enterprise",
      "value": "ENTERPRISE",
      "color": "green",
      "position": 2
    }
  ]
}
```

---

## 3. Relation Field Payload Structure

Relation fields link two entities (e.g. One-to-Many or Many-to-One):

```json
{
  "objectMetadataId": "<SOURCE_OBJECT_UUID>",
  "name": "contract",
  "label": "Contract",
  "type": "RELATION",
  "relationTargetObjectMetadataId": "<TARGET_OBJECT_UUID>",
  "relationType": "MANY_TO_ONE"
}
```
