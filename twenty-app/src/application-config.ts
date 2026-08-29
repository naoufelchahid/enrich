import { defineApplication, FieldType } from 'twenty-sdk/define';

import {
  APP_DESCRIPTION,
  APP_DISPLAY_NAME,
  APPLICATION_UNIVERSAL_IDENTIFIER,
} from 'src/constants/universal-identifiers';

export default defineApplication({
  universalIdentifier: APPLICATION_UNIVERSAL_IDENTIFIER,
  displayName: APP_DISPLAY_NAME,
  description: APP_DESCRIPTION,
  serverVariables: {
    smtpHost: { type: FieldType.TEXT, description: 'SMTP Host', isRequired: true },
    smtpPort: { type: FieldType.NUMBER, description: 'SMTP Port', isRequired: true },
    smtpUser: { type: FieldType.TEXT, description: 'SMTP User', isRequired: true },
    smtpPassword: { type: FieldType.TEXT, description: 'SMTP Password', isRequired: true, isSecret: true },
    linkedinApiKey: { type: FieldType.TEXT, description: 'LinkedIn API Key', isRequired: true, isSecret: true }
  }
});
