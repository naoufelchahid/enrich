"""Authenticated Telegram control of versioned briefs and local acquisition queues.

handle returns text for the existing authorized presentation mechanism. It never
notifies Telegram, calls a provider, approves a draft or changes the mail pause.
Only deterministic local enqueue operations are resumed after an interrupted
command; provider submission and reconciliation remain with their own workers.
"""
import json

from scripts.compact_briefs import default_acquisition_brief, decimal
from scripts.workflow_store import encode, fingerprint, now


class CompactTelegramController:
    def __init__(self, store, budgets, client, *, apify=None, discovery=None):
        self.store, self.budgets, self.client = store, budgets, client
        self.apify, self.discovery = apify, discovery
        with store.connection() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS workflow_control_updates (
                    update_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS workflow_control_commands (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
                    result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_brief_presentations (
                    brief_id TEXT NOT NULL, version INTEGER NOT NULL, operator_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL, content_hash TEXT NOT NULL,
                    PRIMARY KEY(brief_id,version,operator_id,chat_id));
            ''')

    @staticmethod
    def recognizes(update):
        if update.get('callback_query'):
            return False
        text = (update.get('message') or {}).get('text') or ''
        return isinstance(text, str) and text.split(maxsplit=1)[0:1] in (['/brief'], ['/workflows'])

    def _authorized(self, update):
        message = update.get('message') or {}
        actor, chat = str((message.get('from') or {}).get('id')), str((message.get('chat') or {}).get('id'))
        return (bool(self.client.operator_id) and bool(self.client.chat_id)
                and actor == self.client.operator_id and chat == self.client.chat_id)

    @staticmethod
    def _parse(text):
        words = text.split(maxsplit=4)
        if words[0] == '/workflows':
            if len(words) > 2:
                raise ValueError('USAGE: /workflows [lane]')
            return {'action':'status', 'lane':words[1] if len(words)==2 else None}
        if len(words) < 3:
            raise ValueError('USAGE: /brief create|show|launch ID ...')
        action, identity = words[1], words[2]
        if action == 'create':
            # maxsplit here preserves JSON whitespace without shell-like parsing.
            raw = text.split(maxsplit=3)
            definition = json.loads(raw[3]) if len(raw)==4 else default_acquisition_brief()
            return {'action':'create','brief_id':identity,'definition':definition}
        if action == 'show':
            if len(words) > 4:
                raise ValueError('USAGE: /brief show ID [VERSION]')
            return {'action':'show','brief_id':identity,'version':int(words[3]) if len(words)==4 else None}
        if action == 'launch':
            parts = text.split(maxsplit=5)
            if len(parts) != 6:
                raise ValueError('USAGE: /brief launch ID VERSION apify|linkedin JSON')
            return {'action':'launch','brief_id':identity,'version':int(parts[3]),
                    'provider':parts[4], 'options':json.loads(parts[5])}
        raise ValueError('UNKNOWN_BRIEF_ACTION')

    def handle(self, update):
        if not self.recognizes(update):
            return None
        if not self._authorized(update):
            return {'status':'UNAUTHORIZED'}
        if type(update.get('update_id')) is not int:
            return {'status':'INVALID_UPDATE'}
        update_id = str(update['update_id'])
        content_hash = fingerprint(update)
        with self.store.lock('telegram-control'):
            with self.store.connection() as c:
                old = c.execute('SELECT * FROM workflow_control_updates WHERE update_id=?', (update_id,)).fetchone()
                if old and old['content_hash'] != content_hash:
                    return {'status':'UPDATE_REPLAY_CONTENT_CHANGED'}
                if old and old['result'] is not None:
                    return {**json.loads(old['result']), 'replay':True}
                c.execute('INSERT OR IGNORE INTO workflow_control_updates VALUES(?,?,NULL)', (update_id,content_hash))
            try:
                request = self._parse(update['message']['text'])
            except (ValueError, TypeError, IndexError):
                return self._finish_update(update_id, {'status':'INVALID_COMMAND','text':'Commande invalide. Utilisez /brief create|show|launch ou /workflows.'})
            # Identical mutation commands share one intent even if resent as a
            # new message/update. Read commands remain fresh on a new update.
            command_id = 'telegram-control:' + fingerprint({'operator':self.client.operator_id,
                'chat':self.client.chat_id, 'request':request,
                'read_update':update_id if request['action'] in ('status','show') else None})
            with self.store.connection() as c:
                old = c.execute('SELECT * FROM workflow_control_commands WHERE id=?', (command_id,)).fetchone()
                if old and old['state']=='APPLIED':
                    return self._finish_update(update_id, {**json.loads(old['result']), 'replay':True})
                c.execute('INSERT OR IGNORE INTO workflow_control_commands VALUES(?,?,\'PENDING\',NULL,?,?)',
                    (command_id,encode(request),now(),now()))
            try:
                result = self._execute(request, command_id)
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                # No provider is called here. A failed local intent can be
                # retried after an access/budget/configuration correction.
                result = {'status':'WAITING','reason':str(exc),'text':'En attente : '+str(exc)}
            with self.store.connection() as c:
                c.execute("UPDATE workflow_control_commands SET state=?,result=?,updated_at=? WHERE id=?",
                    ('APPLIED' if result['status'] not in ('WAITING',) else 'WAITING',encode(result),now(),command_id))
                self.store.event('telegram_control',None,{'command_id':command_id,'request':request,'result':result},
                    key='telegram-control-update:'+update_id,connection=c)
            return self._finish_update(update_id,result)

    def _finish_update(self, update_id, result):
        with self.store.connection() as c:
            c.execute('UPDATE workflow_control_updates SET result=? WHERE update_id=?', (encode(result),update_id))
        return result

    def _present(self, brief):
        if brief is None:
            raise ValueError('BRIEF_NOT_FOUND')
        d = brief['definition']
        text = (f"Brief {brief['brief_id']} — version {brief['version']} — {brief['state']}\n"
                f"Cible : {d.get('target')} ; zone : {d.get('zone')}\n"
                f"Objectif admissible : {d.get('admissible_goal')}\n"
                'Recherches : '+encode(d.get('searches',[]))+'\n'
                'Quantités : '+encode(d.get('volume_caps',{}))+'\n'
                'Budgets : '+encode(d.get('budgets',{}))+'\n'
                'Arrêts : '+encode(d.get('stop_conditions',[]))+'\n'
                'Blocages : '+encode(self.budgets.blockers(brief['brief_id'],brief['version'])))
        return {'status':'PREVIEW_READY','brief_id':brief['brief_id'],'version':brief['version'],
                'definition_hash':fingerprint(d),'text':text}

    def deliver(self, update, result):
        if result is None:
            return None
        try:
            with self.store.lock('telegram-control-delivery'):
                return self._deliver(update,result)
        except RuntimeError as exc:
            if str(exc)=='WORKFLOW_BUSY':
                return {**result,'delivery':'TELEGRAM_PRESENTATION_BUSY'}
            raise

    def _deliver(self, update, result):
        """Present a controller result exactly once; unknown transport waits.

A brief becomes presented only after confirmed receipts for every text chunk.
A crash after send intent never triggers another sendMessage automatically.
"""
        if result is None or result.get('status') == 'UNAUTHORIZED' or not result.get('text'):
            return result
        if not self._authorized(update):
            return {'status':'UNAUTHORIZED'}
        text = result['text']
        chunks, current, units = [], [], 0
        for char in text:
            width = len(char.encode('utf-16-le')) // 2
            if current and units + width > 4000:
                chunks.append(''.join(current));current,units = [],0
            current.append(char);units += width
        if current:
            chunks.append(''.join(current))
        receipt_ids = []
        for index, chunk in enumerate(chunks):
            key = 'telegram-control-delivery:' + str(update['update_id']) + ':' + str(index)
            payload = {'chat_id':self.client.chat_id,'text':chunk,'update_id':update['update_id']}
            operation = self.store.operation(None,'TELEGRAM_CONTROL_PRESENT',payload,key=key)
            if json.loads(operation['payload']) != payload:
                return {**result,'delivery':'CONTENT_CHANGED_REVIEW_REQUIRED'}
            if operation['status'] == 'SUCCEEDED':
                receipt_ids.append(json.loads(operation['result'])['message_id'])
                continue
            if operation['status'] in ('SENDING','UNKNOWN'):
                return {**result,'delivery':'TELEGRAM_PRESENTATION_RECONCILIATION_REQUIRED'}
            self.store.finish_operation(operation['id'],'SENDING')
            try:
                response = self.client.call('sendMessage',{'chat_id':self.client.chat_id,'text':chunk})
                if not isinstance(response,dict) or type(response.get('message_id')) is not int:
                    raise ValueError('MISSING_TELEGRAM_RECEIPT')
            except Exception:
                self.store.finish_operation(operation['id'],'UNKNOWN')
                return {**result,'delivery':'TELEGRAM_PRESENTATION_RECONCILIATION_REQUIRED'}
            self.store.finish_operation(operation['id'],'SUCCEEDED',{'message_id':response['message_id']})
            receipt_ids.append(response['message_id'])
        if result.get('status') == 'PREVIEW_READY':
            with self.store.connection() as c:
                c.execute('INSERT OR REPLACE INTO workflow_brief_presentations VALUES(?,?,?,?,?)',
                    (result['brief_id'],result['version'],self.client.operator_id,self.client.chat_id,result['definition_hash']))
        return {**result,'status':'PRESENTED' if result.get('status')=='PREVIEW_READY' else result['status'],
                'delivery':'CONFIRMED','telegram_message_ids':receipt_ids}

    def _execute(self, request, command_id):
        action = request['action']
        if action == 'create':
            brief = self.budgets.create_brief(request['brief_id'], request['definition'],command_id=command_id)
            return self._present(brief)
        if action == 'show':
            return self._present(self.budgets.brief(request['brief_id'],request['version']))
        if action == 'status':
            return self._status(request['lane'])
        identity, version, provider, options = request['brief_id'],request['version'],request['provider'],request['options']
        latest = self.budgets.brief(identity)
        if latest is None or latest['version'] != version:
            raise RuntimeError('STALE_BRIEF_VERSION')
        with self.store.connection() as c:
            shown = c.execute('SELECT content_hash FROM workflow_brief_presentations WHERE brief_id=? AND version=? AND operator_id=? AND chat_id=?',
                (identity,version,self.client.operator_id,self.client.chat_id)).fetchone()
        if shown is None or shown[0] != fingerprint(latest['definition']):
            raise RuntimeError('PRESENT_EXACT_BRIEF_VERSION_FIRST')
        blockers = self.budgets.blockers(identity,version)
        if blockers:
            raise RuntimeError(';'.join(blockers))
        if not isinstance(options,dict):
            raise ValueError('ACQUISITION_OPTIONS_REQUIRED')
        if provider == 'apify':
            if self.apify is None or getattr(self.apify,'client',None) is None:
                raise RuntimeError('APIFY_TRANSPORT_NOT_CONFIGURED')
            if set(options) != {'limits_by_search','run_limit','maximum_charge'}:
                raise ValueError('APIFY_LIMITS_AND_COST_REQUIRED')
            if options['maximum_charge'] is None:
                raise ValueError('COST_UNEVALUABLE:apify')
            decimal(options['maximum_charge'])
            # Validate the full set before the first enqueue, then the service's
            # deterministic task keys make interruption/resume safe.
            if not isinstance(options['limits_by_search'],dict) or not options['limits_by_search']:
                raise ValueError('FINITE_SEARCH_LIMITS_REQUIRED')
            if type(options['run_limit']) is not int or options['run_limit'] <= 0:
                raise ValueError('RUN_LIMIT_REQUIRED')
            for term,limit in options['limits_by_search'].items():
                if term not in latest['definition']['searches'] or type(limit) is not int or limit <= 0:
                    raise ValueError('INVALID_REVIEWED_SEARCH_LIMIT')
            self.budgets.launch(identity,version,command_id=command_id+':launch')
            tasks = self.apify.enqueue(identity,version,**options)
        elif provider == 'linkedin':
            if self.discovery is None or getattr(self.discovery,'cost_guard',None) is None:
                raise RuntimeError('LINKEDIN_DISCOVERY_NOT_CONFIGURED')
            if set(options) != {'company_ids'} or not isinstance(options['company_ids'],list) or not options['company_ids']:
                raise ValueError('COMPANY_IDS_REQUIRED')
            for company_id in options['company_ids']:
                company = self.store.candidate(company_id) if isinstance(company_id,str) else None
                if company is None or not company['data'].get('company_name'):
                    raise ValueError('COMPANY_DOSSIER_REQUIRED')
            preflight = getattr(self.discovery.cost_guard,'preflight',None)
            if preflight is None:
                raise RuntimeError('COST_PREFLIGHT_REQUIRED:linkedin_mcp')
            preflight(provider='linkedin_mcp',quantity=len(options['company_ids'])*len(latest['definition']['searches']))
            self.budgets.launch(identity,version,command_id=command_id+':launch')
            tasks = [self.discovery.enqueue_company(company_id,identity,version) for company_id in options['company_ids']]
        else:
            raise ValueError('UNKNOWN_ACQUISITION_PROVIDER')
        return {'status':'QUEUED','brief_id':identity,'version':version,'task_ids':[task['id'] for task in tasks],
                'text':f"Brief {identity} version {version} : {len(tasks)} tâche(s) {provider} en file. Aucun envoi email déclenché."}

    def _status(self, lane):
        with self.store.connection() as c:
            clauses,args = (' WHERE lane=?',[lane]) if lane else ('',[])
            rows = [dict(row) for row in c.execute('SELECT lane,status,wait_reason,count(*) AS count FROM workflow_tasks'+clauses+' GROUP BY lane,status,wait_reason ORDER BY lane,status',args)]
            pause = c.execute("SELECT value FROM workflow_meta WHERE key='telegram_submission_paused'").fetchone()
        services = {'apify':'ADAPTER_PRESENT_UNPROBED' if self.apify is not None and getattr(self.apify,'client',None) is not None else 'APIFY_TRANSPORT_NOT_CONFIGURED',
                    'discovery':'ADAPTER_PRESENT_UNPROBED' if self.discovery is not None and getattr(self.discovery,'cost_guard',None) is not None else 'LINKEDIN_DISCOVERY_NOT_CONFIGURED'}
        return {'status':'STATUS','lanes':rows,'services':services,
                'telegram_submission_paused':bool(pause and pause[0]),
                'text':'État des parcours : '+encode(rows)+'\nServices : '+encode(services)}
