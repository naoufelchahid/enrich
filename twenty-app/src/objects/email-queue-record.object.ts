import { defineObject } from 'twenty-sdk/define';

export const EMAIL_QUEUE_RECORD_UNIVERSAL_IDENTIFIER = 'email-queue-record';

export default defineObject({
  name: 'emailQueueRecord',
  label: 'Email Queue Record',
  singularLabel: 'Email Queue Record',
  pluralLabel: 'Email Queue Records',
  description: 'Represents a single scheduled email to be sent.',
  fields: [
    {
      name: 'personId',
      label: 'Person ID',
      type: 'text',
      isRequired: true,
      description: 'ID of the Person record'
    },
    {
      name: 'emailContent',
      label: 'Email Content',
      type: 'text',
      isRequired: true,
      description: 'The drafted content of the email'
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      isRequired: true,
      options: [
        { label: 'Pending', value: 'PENDING' },
        { label: 'Sent', value: 'SENT' },
        { label: 'Failed', value: 'FAILED' }
      ]
    },
    {
      name: 'errorMessage',
      label: 'Error Message',
      type: 'text',
      description: 'Error message if sending failed'
    }
  ],
});
