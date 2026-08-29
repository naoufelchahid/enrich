import { defineObject, FieldType } from 'twenty-sdk/define';

export const EMAIL_QUEUE_RECORD_UNIVERSAL_IDENTIFIER = '1fa7de88-7550-4d51-8e9a-7c96dc8d85f8';

export default defineObject({
  universalIdentifier: EMAIL_QUEUE_RECORD_UNIVERSAL_IDENTIFIER,
  nameSingular: 'emailQueueRecord',
  namePlural: 'emailQueueRecords',
  labelSingular: 'Email Queue Record',
  labelPlural: 'Email Queue Records',
  description: 'Represents a single scheduled email to be sent.',
  labelIdentifierFieldMetadataUniversalIdentifier: 'c87db98b-0a06-4ec9-86bb-2a13ccfb04f2',
  fields: [
    {
      universalIdentifier: '8223d6a9-8255-46f3-a4e1-2292f447cf0f',
      name: 'personId',
      label: 'Person ID',
      type: FieldType.TEXT,
      description: 'ID of the Person record'
    },
    {
      universalIdentifier: 'c87db98b-0a06-4ec9-86bb-2a13ccfb04f2',
      name: 'emailContent',
      label: 'Email Content',
      type: FieldType.TEXT,
      description: 'The drafted content of the email'
    },
    {
      universalIdentifier: '21c322b7-a36a-493f-846f-236b328a2a7a',
      name: 'status',
      label: 'Status',
      type: FieldType.SELECT,
      options: [
        { label: 'Pending', value: 'PENDING', position: 0, color: 'blue' },
        { label: 'Sent', value: 'SENT', position: 1, color: 'green' },
        { label: 'Failed', value: 'FAILED', position: 2, color: 'red' }
      ]
    },
    {
      universalIdentifier: '31f0f423-ed21-4d37-8857-e6f98ef4fcfa',
      name: 'errorMessage',
      label: 'Error Message',
      type: FieldType.TEXT,
      description: 'Error message if sending failed'
    }
  ],
});
