import json
from unittest.mock import patch

import pytest

from scripts.thunderbird_client import ThunderbirdClient
from scripts.telegram_approval import submit_draft
from scripts.workflow import Workflow
from scripts.workflow_sender import send_approved
from tests.test_workflow_refactor import state, sample_draft, approve, next_morning
from tests.test_compact_campaigns import campaign


def test_stop_at_draft_blocks_even_an_approved_email_without_reservation(state, monkeypatch):
    store, lead, telegram, breaker = state
    draft = sample_draft(store, lead)
    approve(store, telegram, draft)
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT', '1')
    mail = ThunderbirdClient(dry_run=True)
    assert send_approved(store, mail, breaker, '101', at=next_morning())['status'] == 'STOPPED_AT_DRAFT'
    assert not mail._sent_emails
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0] == 0
        assert c.execute('SELECT status FROM workflow_drafts WHERE id=?', (draft['id'],)).fetchone()[0] == 'APPROVED'


@pytest.mark.parametrize('method', ['send_email', 'send_approved_email'])
def test_stop_at_draft_blocks_direct_transport_calls(monkeypatch, method):
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT', '1')
    mail = ThunderbirdClient(dry_run=True)
    with patch.object(mail, '_call_mcp_tool') as transport:
        with pytest.raises(RuntimeError, match='WORKFLOW_STOP_AT_DRAFT'):
            getattr(mail, method)('alice@example.org', 'Objet exact', 'Corps exact')
        transport.assert_not_called()


def test_draft_only_prepares_reviewed_v2_without_telegram(campaign,monkeypatch):
    from scripts.compact_copy import CompactCopy
    from tests.test_compact_copy import accept
    store,lead,telegram,breaker,workflow=campaign
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT','1')
    result=CompactCopy(store,critic_runner=accept).prepare(lead['journal_contact_id'],'V2')
    assert result['status']=='DRAFT_CREATED' and result['sends']==0
    assert telegram.sent==[]
    with store.connection() as c:
        row=c.execute('SELECT status,telegram_message_id FROM workflow_drafts').fetchone()
        assert tuple(row)==('PENDING',None)
        assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]==0


def test_general_telegram_pause_still_blocks_submission(state):
    store,lead,telegram,breaker=state
    with store.connection() as c:
        c.execute("INSERT INTO workflow_meta VALUES('telegram_submission_paused','operator_pause')")
    draft=sample_draft(store,lead)
    assert submit_draft(store,telegram,draft['id'])['status']=='TELEGRAM_SUBMISSION_PAUSED'
    assert telegram.sent==[]
