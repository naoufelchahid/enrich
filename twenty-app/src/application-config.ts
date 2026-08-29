import { defineApplication } from 'twenty-sdk/define';

import {
  APP_DESCRIPTION,
  APP_DISPLAY_NAME,
  APPLICATION_UNIVERSAL_IDENTIFIER,
} from 'src/constants/universal-identifiers';

export default defineApplication({
  universalIdentifier: APPLICATION_UNIVERSAL_IDENTIFIER,
  displayName: APP_DISPLAY_NAME,
  description: APP_DESCRIPTION,
  workspaceVariables: {
    smtpHost: { type: 'string', label: 'SMTP Host', isRequired: true },
    smtpPort: { type: 'number', label: 'SMTP Port', isRequired: true },
    smtpUser: { type: 'string', label: 'SMTP User', isRequired: true },
    smtpPassword: { type: 'string', label: 'SMTP Password', isRequired: true },
    linkedinApiKey: { type: 'string', label: 'LinkedIn API Key', isRequired: true }
  }
});
