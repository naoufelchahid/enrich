import { defineField, FieldType, STANDARD_OBJECT_UNIVERSAL_IDENTIFIERS } from 'twenty-sdk/define';

export const personaReportField = defineField({
  universalIdentifier: '66a5b7b9-8e42-436d-92d6-44c107297920',
  name: 'personaReport',
  objectUniversalIdentifier: STANDARD_OBJECT_UNIVERSAL_IDENTIFIERS.person.universalIdentifier,
  label: 'Persona Report',
  type: FieldType.TEXT,
  description: 'AI generated Persona Report'
});

export const draftEmailField = defineField({
  universalIdentifier: '6a42211c-223d-4c3f-b8ff-f1f456edae54',
  name: 'draftEmail',
  objectUniversalIdentifier: STANDARD_OBJECT_UNIVERSAL_IDENTIFIERS.person.universalIdentifier,
  label: 'Draft Email',
  type: FieldType.TEXT,
  description: 'AI drafted email content'
});

export const enrichmentStatusField = defineField({
  universalIdentifier: '820464c8-38cd-4927-bdf2-f8c7b822d1af',
  name: 'enrichmentStatus',
  objectUniversalIdentifier: STANDARD_OBJECT_UNIVERSAL_IDENTIFIERS.person.universalIdentifier,
  label: 'Enrichment Status',
  type: FieldType.SELECT,
  options: [
    { label: 'Pending', value: 'PENDING', position: 0, color: 'blue' },
    { label: 'Enriching', value: 'ENRICHING', position: 1, color: 'yellow' },
    { label: 'Success', value: 'SUCCESS', position: 2, color: 'green' },
    { label: 'Failed', value: 'FAILED', position: 3, color: 'red' }
  ],
  description: 'Status of the enrichment process'
});
