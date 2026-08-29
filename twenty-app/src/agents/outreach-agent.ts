import { defineAgent } from 'twenty-sdk/define';

export default defineAgent({
  universalIdentifier: '6a87756e-826f-4d3b-85d8-3017a941ef01',
  name: 'outreach-agent',
  label: 'Outreach Agent',
  description: 'Autonomous AI agent that writes Persona Reports and drafts outbound emails based on inbound marketing best practices.',
  prompt: `You are an expert inbound marketer. Your task is to write a short Persona Report and an outbound email based on the enriched company and person profile provided.
The email must strictly follow the PAS (Problem-Agitate-Solution) framework and be under 100 words.
Do not include any pleasantries like "I hope this email finds you well".
Output valid JSON containing exactly two keys: "personaReport" (string) and "draftEmail" (string).`
});
