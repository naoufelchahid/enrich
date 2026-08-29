import { defineObject, FieldType } from 'twenty-sdk/define';

export const SKIPPED_LEAD_UNIVERSAL_IDENTIFIER = '4f8dbcd1-0d32-424a-9eb6-809dfb0c53d5';

export default defineObject({
  universalIdentifier: SKIPPED_LEAD_UNIVERSAL_IDENTIFIER,
  nameSingular: 'skippedLead',
  namePlural: 'skippedLeads',
  labelSingular: 'Skipped Lead',
  labelPlural: 'Skipped Leads',
  description: 'Rejected rows from the Excel file so they are not silently lost.',
  labelIdentifierFieldMetadataUniversalIdentifier: '59c1c4f0-b997-41eb-81c0-038202d08a0d',
  fields: [
    {
      universalIdentifier: '66a1ef85-2e11-477c-a496-e2db7e5bba87',
      name: 'rowData',
      label: 'Row Data',
      type: FieldType.TEXT,
      description: 'JSON stringified row data'
    },
    {
      universalIdentifier: '59c1c4f0-b997-41eb-81c0-038202d08a0d',
      name: 'reason',
      label: 'Reason',
      type: FieldType.TEXT,
      description: 'Reason for skipping the lead'
    }
  ],
});
