import { defineLogicFunction } from 'twenty-sdk/define';

export default defineLogicFunction({
  name: 'Data Curation Job',
  description: 'Ingests the mapped JSON from the Front Component and processes it into Company and Person records.',
  httpRouteTriggerSettings: {
    path: '/ingest-excel',
    httpMethod: 'POST',
    isAuthRequired: true
  },
  handler: async (payload: { rows: any[] }, context) => {
    const { rows } = payload;
    
    // Iterate over the mapped JSON rows and process them
    // Creating Company and Person records via context.api
    
    // Using a mocked success for now, as testing seam is external network
    for (const row of rows) {
      if (!row.email) {
        // Missing critical field, save to SkippedLead
        await context.api.graphql.request(`
          mutation {
            createSkippedLead(data: { reason: "Missing email", originalData: $data }) { id }
          }
        `, { data: JSON.stringify(row) });
        continue;
      }
      
      // Upsert Company
      let companyId;
      if (row.companyName) {
        // Mocked graphql logic
        const companyRes = await context.api.graphql.request(`
          mutation {
            createCompany(data: { name: $name, domainName: $domain }) { id }
          }
        `, { name: row.companyName, domain: row.companyWebsite });
        companyId = companyRes?.createCompany?.id;
      }
      
      // Upsert Person
      await context.api.graphql.request(`
        mutation {
          createPerson(data: { 
            name: { firstName: $first, lastName: $last }, 
            emails: [$email], 
            companyId: $companyId 
          }) { id }
        }
      `, { 
        first: row.firstName || '', 
        last: row.lastName || '', 
        email: row.email, 
        companyId 
      });
    }

    return { success: true, ingested: rows.length };
  }
});
