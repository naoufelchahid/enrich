import { defineObject } from 'twenty-sdk/define';

export const SKIPPED_LEAD_UNIVERSAL_IDENTIFIER = 'skipped-lead';

export default defineObject({
  name: 'skippedLead',
  label: 'Skipped Lead',
  singularLabel: 'Skipped Lead',
  pluralLabel: 'Skipped Leads',
  description: 'Rejected rows from the Excel file so they aren't silently lost.',
  fields: [
    {
      name: 'rowData',
      label: 'Row Data',
      type: 'text',
      isRequired: true,
      description: 'JSON stringified row data'
    },
    {
      name: 'reason',
      label: 'Reason',
      type: 'text',
      isRequired: true,
      description: 'Reason for skipping the lead'
    }
  ],
});
