import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scripts.build_thunderbird_exact_reply import adapt
from scripts.thunderbird_client import ThunderbirdClient
from scripts.workflow_sender import send_approved
from tests.test_workflow_refactor import state, sample_draft, approve, next_morning


def test_installed_reply_api_is_probed_without_sending():
    mail=ThunderbirdClient(dry_run=True)
    mail.dry_run=False
    with patch.object(mail,'_call_mcp_tool',return_value={'tools':[{'name':'replyToMessage','inputSchema':{'properties':{'body':{},'messageId':{}}}}]}) as call:
        assert not mail.supports_exact_reply()
        assert call.call_args.kwargs['rpc_method']=='tools/list'


def test_exact_reply_is_sent_with_explicit_body_and_subject_override():
    mail=ThunderbirdClient(dry_run=True)
    mail.dry_run=False
    reference=json.dumps({'id':'original-message','folder_path':'mailbox://inbox'})
    with patch.object(mail,'supports_exact_reply',return_value=True),patch.object(mail,'_call_mcp_tool',return_value={'success':True,'message':'Reply sent'}) as call:
        assert mail.send_approved_email('alice@example.org','Re: objet exact','Corps approuvé exact',reference)['success']
        tool,args=call.call_args.args
        assert tool=='replyToMessage'
        assert args['includeQuoted'] is False and args['isHtml'] is False
        assert args['body']=='Corps approuvé exact' and args['subject']=='Re: objet exact'
        assert args['messageId']=='original-message'


def test_unsupported_native_reply_never_reaches_a_send_tool(state):
    store,lead,telegram,breaker=state
    draft=sample_draft(store,lead,kind='REPLY',reply_message_id=json.dumps({'id':'original','folder_path':'mailbox://inbox'}))
    approve(store,telegram,draft)
    mail=ThunderbirdClient(dry_run=True)
    with patch.object(mail,'supports_exact_reply',return_value=False),patch.object(mail,'send_email') as call:
        assert send_approved(store,mail,breaker,'101',at=next_morning())['status']=='THUNDERBIRD_EXACT_REPLY_CAPABILITY_REQUIRED'
        call.assert_not_called()
        with store.connection() as c:
            assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]==0


def test_opened_compose_window_is_not_a_confirmed_send():
    mail=ThunderbirdClient(dry_run=True)
    mail.dry_run=False
    with patch.object(mail,'send_email',return_value={'success':True,'result':{'success':True,'message':'Compose window opened'}}):
        with pytest.raises(RuntimeError,match='NOT_CONFIRMED'):
            mail.send_approved_email('alice@example.org','objet','corps')


def test_raw_dsn_and_folder_paths_are_required_for_message_reads():
    mail=ThunderbirdClient(dry_run=True)
    with patch.object(mail,'_call_mcp_tool',return_value={'rawSource':'Final-Recipient: rfc822; alice@example.org'}) as call:
        assert 'Final-Recipient' in mail.workflow_raw_message('message-1','mailbox://inbox')
        assert call.call_args.args[1]['folderPath']=='mailbox://inbox'


def test_thunderbird_patch_refuses_unknown_extension_source():
    with pytest.raises(ValueError,match='CONTRACT_CHANGED'):
        adapt('unrelated source code')


@pytest.mark.parametrize('method', ['send_email','send_approved_email'])
def test_personal_recipient_is_blocked_at_transport_boundary(method):
    mail = ThunderbirdClient(dry_run=True)
    with patch.object(mail,'_call_mcp_tool') as transport:
        with pytest.raises(ValueError, match='PERSONAL_EMAIL_FORBIDDEN'):
            getattr(mail,method)('Alice <alice@gmail.com>','objet','texte')
        transport.assert_not_called()
