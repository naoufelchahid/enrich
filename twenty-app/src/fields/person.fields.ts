import { defineField } from 'twenty-sdk/define';

export const personaReportField = defineField({
  name: 'personaReport',
  objectIdentifier: 'person',
  label: 'Persona Report',
  type: 'text',
  description: 'AI generated Persona Report'
});

export const draftEmailField = defineField({
  name: 'draftEmail',
  objectIdentifier: 'person',
  label: 'Draft Email',
  type: 'text',
  description: 'AI drafted email content'
});

export const enrichmentStatusField = defineField({
  name: 'enrichmentStatus',
  objectIdentifier: 'person',
  label: 'Enrichment Status',
  type: 'select',
  options: [
    { label: 'Pending', value: 'PENDING' },
    { label: 'Enriching', value: 'ENRICHING' },
    { label: 'Success', value: 'SUCCESS' },
    { label: 'Failed', value: 'FAILED' }
  ],
  description: 'Status of the enrichment process'
});
