import { defineLogicFunction } from 'twenty-sdk/define';
import { CoreApiClient } from 'twenty-client-sdk/core';
// @ts-ignore
import nodemailer from 'nodemailer';

export default defineLogicFunction({
  universalIdentifier: 'f7215b2e-a5f1-4db8-b570-8abf7c462be1',
  name: 'drip-cron-job',
  description: 'Cron job to process one EmailQueueRecord every 6 minutes using SMTP',
  timeoutSeconds: 30,
  cronTriggerSettings: {
    pattern: '*/6 * * * *'
  },
  handler: async () => {
    const client = new CoreApiClient({ runAs: 'application' });
    
    // Fetch 1 pending email record
    const { emailQueueRecords } = await client.query({
      emailQueueRecords: {
        __args: {
          filter: { status: { eq: 'PENDING' } },
          first: 1
        },
        edges: {
          node: {
            id: true,
            emailContent: true,
            personId: true
          }
        }
      }
    });

    const pendingEdges = emailQueueRecords?.edges || [];
    if (pendingEdges.length === 0) {
      return { success: true, message: 'No pending emails' };
    }

    const record = pendingEdges[0].node;

    // Fetch the person's email
    const { person } = await client.query({
      person: {
        __args: { id: record.personId },
        emails: { primaryEmail: true },
        name: { firstName: true }
      }
    });

    const primaryEmail = person?.emails?.primaryEmail;

    if (!primaryEmail) {
      // Fail record
      await client.mutation({
        updateEmailQueueRecord: {
          __args: {
            id: record.id,
            data: { status: 'FAILED', errorMessage: 'No primary email found for person' }
          },
          id: true
        }
      });
      return { success: false, message: 'No email found' };
    }

    const smtpHost = process.env.smtpHost;
    const smtpPort = process.env.smtpPort;
    const smtpUser = process.env.smtpUser;
    const smtpPassword = process.env.smtpPassword;

    if (!smtpHost || !smtpPort || !smtpUser || !smtpPassword) {
      const errorMsg = 'SMTP credentials not configured in workspace variables';
      await client.mutation({
        updateEmailQueueRecord: {
          __args: {
            id: record.id,
            data: { status: 'FAILED', errorMessage: errorMsg }
          },
          id: true
        }
      });
      return { success: false, message: errorMsg };
    }

    const transporter = nodemailer.createTransport({
      host: smtpHost,
      port: Number(smtpPort),
      secure: Number(smtpPort) === 465,
      auth: {
        user: smtpUser,
        pass: smtpPassword,
      }
    });

    try {
      await transporter.sendMail({
        from: smtpUser,
        to: primaryEmail,
        subject: `Hello ${person?.name?.firstName || ''}, let's connect!`,
        text: record.emailContent
      });

      // Mark as sent
      await client.mutation({
        updateEmailQueueRecord: {
          __args: {
            id: record.id,
            data: { status: 'SENT' }
          },
          id: true
        }
      });

      return { success: true, message: 'Email sent successfully' };
    } catch (e: any) {
      // Mark as failed
      await client.mutation({
        updateEmailQueueRecord: {
          __args: {
            id: record.id,
            data: { status: 'FAILED', errorMessage: e.message }
          },
          id: true
        }
      });
      return { success: false, message: `Failed to send email: ${e.message}` };
    }
  }
});
