import { defineLogicFunction } from 'twenty-sdk/define';
import { CoreApiClient } from 'twenty-client-sdk/core';
import { runAgent, type DatabaseEventPayload, type ObjectRecordCreateEvent } from 'twenty-sdk/logic-function';

type Person = {
  id: string;
  name?: { firstName?: string; lastName?: string };
  companyId?: string;
  enrichmentStatus?: string;
};

export default defineLogicFunction({
  universalIdentifier: '6b9572b6-a810-482f-8703-dc7f201b1a7d',
  name: 'enrichment-job',
  description: 'Enriches company and person data in the background, then triggers AI Persona generation',
  timeoutSeconds: 30,
  databaseEventTriggerSettings: {
    eventName: 'person.created'
  },
  handler: async (event: DatabaseEventPayload<ObjectRecordCreateEvent<Person>>) => {
    const person = event.properties.after;
    const client = new CoreApiClient({ runAs: 'application' });
    
    try {
      // 1. Update status to ENRICHING
      await client.mutation({
        updatePerson: {
          __args: { id: person.id, data: { enrichmentStatus: 'ENRICHING' } },
          id: true
        }
      });

      let companyData = null;
      let kerixEnriched = false;

      // 2. Enrich Company
      if (person.companyId) {
        const companyRes = await client.query({
          company: {
            __args: { id: person.companyId },
            id: true,
            name: true,
            domainName: { primaryLinkUrl: true }
          }
        });
        
        const company = companyRes.company;
        
        if (company && company.name) {
          // Kerix API Mock
          // Native integration per spec: https://www.kerix.net/fr/search?q=XYZ
          const kerixRes = await fetch(`https://www.kerix.net/fr/search?q=${encodeURIComponent(company.name)}`);
          if (kerixRes.ok) {
            kerixEnriched = true;
          }
        }
        companyData = company;
      }

      // 3. Enrich Person via LinkedIn API Mock
      const linkedInKey = process.env.linkedinApiKey;
      if (linkedInKey && person.name?.firstName) {
        // mock LinkedIn fetch
        await fetch(`https://api.linkedin-lowcost.com/v1/enrich?q=${encodeURIComponent(person.name.firstName)}`, {
          headers: { Authorization: `Bearer ${linkedInKey}` }
        });
      }

      // 4. Run AI Agent
      const aiInput = JSON.stringify({
        personName: person.name,
        companyName: companyData?.name,
        companyDomain: companyData?.domainName?.primaryLinkUrl,
        kerixEnriched
      });

      const agentResult = await runAgent({
        agentUniversalIdentifier: '6a87756e-826f-4d3b-85d8-3017a941ef01',
        prompt: `Here is the prospect data: ${aiInput}`
      });

      let personaReport = 'Failed to generate';
      let draftEmail = 'Failed to generate';

      try {
        const parsed = agentResult.result as any;
        if (parsed.personaReport) personaReport = parsed.personaReport;
        if (parsed.draftEmail) draftEmail = parsed.draftEmail;
      } catch (e) {
        personaReport = agentResult.error || 'Unknown error';
        draftEmail = agentResult.error || 'Unknown error';
      }

      // 5. Update Person with AI outputs
      await client.mutation({
        updatePerson: {
          __args: {
            id: person.id,
            data: {
              enrichmentStatus: 'SUCCESS',
              personaReport,
              draftEmail
            }
          },
          id: true
        }
      });

      // 6. Queue Email
      await client.mutation({
        createEmailQueueRecord: {
          __args: {
            data: {
              personId: person.id,
              emailContent: draftEmail,
              status: 'PENDING'
            }
          },
          id: true
        }
      });

      return { success: true };
    } catch (e: any) {
      // Mark as failed
      await client.mutation({
        updatePerson: {
          __args: { id: person.id, data: { enrichmentStatus: 'FAILED' } },
          id: true
        }
      });
      return { success: false, error: e.message };
    }
  }
});
