import { defineLogicFunction } from 'twenty-sdk/define';
import { CoreApiClient } from 'twenty-client-sdk/core';
import type { RoutePayload } from 'twenty-sdk/logic-function';

export default defineLogicFunction({
  universalIdentifier: '66a6a9be-a1c6-4d04-8b54-94efd7c2ff21',
  name: 'Data Curation Job',
  description: 'Ingests the mapped JSON from the Front Component and processes it into Company and Person records.',
  httpRouteTriggerSettings: {
    path: '/ingest-excel',
    httpMethod: 'POST',
    isAuthRequired: true
  },
  handler: async (payload: RoutePayload) => {
    const rows = (payload.body as any)?.rows || [];
    const client = new CoreApiClient({ runAs: 'application' });
    
    // Iterate over the mapped JSON rows and process them
    for (const row of rows) {
      if (!row.email) {
        // Missing critical field, save to SkippedLead
        await client.mutation({
          createSkippedLead: {
            __args: { data: { reason: "Missing email", rowData: JSON.stringify(row) } },
            id: true
          }
        });
        continue;
      }
      
      let companyId;
      if (row.companyName) {
        const companyRes = await client.mutation({
          createCompany: {
            __args: { data: { name: row.companyName, domainName: { primaryLinkUrl: row.companyWebsite || '' } } },
            id: true
          }
        });
        companyId = companyRes?.createCompany?.id;
      }
      
      await client.mutation({
        createPerson: {
          __args: { data: { 
            name: { firstName: row.firstName || '', lastName: row.lastName || '' }, 
            emails: { primaryEmail: row.email }, 
            companyId 
          } },
          id: true
        }
      });
    }

    return { success: true, ingested: rows.length };
  }
});
