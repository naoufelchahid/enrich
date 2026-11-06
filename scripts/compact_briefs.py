"""Versioned acquisition briefs and transactional, campaign-wide cost ceilings.

Money uses Decimal strings; credits and quantities are independent dimensions.
A reservation is a conservative upper bound and remains charged while submitted
or uncertain. Only explicit reconciliation releases it. No provider is called
by this module, and neither message approvals nor campaign pauses are changed.
"""
import json
from decimal import Decimal, InvalidOperation, localcontext

from scripts.workflow_store import encode, fingerprint, now


def decimal(value, name='AMOUNT'):
    if isinstance(value, bool) or value is None:
        raise ValueError(name + '_REQUIRED')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(name + '_INVALID') from None
    if not result.is_finite() or result < 0:
        raise ValueError(name + '_INVALID')
    return result


def add(left, right):
    left, right = decimal(left), decimal(right)
    # Derive precision from the actual operands instead of silently rounding a
    # small reservation away when the campaign amount has many digits.
    with localcontext() as context:
        context.prec = max(len(left.as_tuple().digits), len(right.as_tuple().digits)) + abs(left.adjusted() - right.adjusted()) + 2
        return left + right


def multiply(left, right):
    left, right = decimal(left), decimal(right)
    with localcontext() as context:
        context.prec = len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2
        return left * right


def money(value):
    return format(decimal(value), 'f')


def default_acquisition_brief():
    """Proposal only: intentionally contains no invented budget or quantity cap."""
    return {'target': 'tous secteurs', 'zone': 'Maroc', 'admissible_goal': 20,
            'searches': [], 'volume_caps': {}, 'budgets': {},
            'stop_conditions': ['admissible_goal', 'volume_caps', 'budget_caps'],
            'purpose': 'new_acquisition'}


class BriefBudget:
    def __init__(self, store):
        self.store = store
        with store.connection() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS workflow_briefs (
                    brief_id TEXT NOT NULL, version INTEGER NOT NULL,
                    definition TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(brief_id,version));
                CREATE TABLE IF NOT EXISTS workflow_brief_commands (
                    command_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_budget_reservations (
                    id TEXT PRIMARY KEY, brief_id TEXT NOT NULL, version INTEGER NOT NULL,
                    provider TEXT NOT NULL, request_hash TEXT NOT NULL, amount TEXT NOT NULL,
                    currency TEXT NOT NULL, credits TEXT NOT NULL, quantity INTEGER NOT NULL,
                    quantity_unit TEXT NOT NULL, state TEXT NOT NULL, actual_amount TEXT,
                    actual_credits TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS workflow_budget_campaign ON workflow_budget_reservations(brief_id);
                CREATE TABLE IF NOT EXISTS workflow_brief_admissions (
                    brief_id TEXT NOT NULL, candidate_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(brief_id,candidate_id));
            ''')

    @staticmethod
    def _brief(row):
        return {**dict(row), 'definition': json.loads(row['definition'])} if row else None

    def brief(self, brief_id, version=None, *, connection=None):
        if connection is None:
            with self.store.connection() as c:
                return self.brief(brief_id, version, connection=c)
        query, args = 'SELECT * FROM workflow_briefs WHERE brief_id=?', [brief_id]
        if version is not None:
            query += ' AND version=?'
            args.append(version)
        return self._brief(connection.execute(query + ' ORDER BY version DESC LIMIT 1', args).fetchone())

    @staticmethod
    def _replay(c, command_id, request):
        if not command_id:
            raise ValueError('COMMAND_ID_REQUIRED')
        row = c.execute('SELECT * FROM workflow_brief_commands WHERE command_id=?', (command_id,)).fetchone()
        if row:
            if row['request_hash'] != fingerprint(request):
                raise ValueError('COMMAND_REPLAY_CONTENT_CHANGED')
            return json.loads(row['result'])
        return None

    @staticmethod
    def _remember(c, command_id, request, result):
        c.execute('INSERT INTO workflow_brief_commands VALUES(?,?,?)', (command_id, fingerprint(request), encode(result)))

    @staticmethod
    def _validate_definition(definition):
        if not isinstance(definition, dict):
            raise ValueError('BRIEF_DEFINITION_REQUIRED')
        for name in ('target', 'zone', 'purpose'):
            if name in definition and not isinstance(definition[name], str):
                raise ValueError('INVALID_' + name.upper())
        if 'admissible_goal' in definition and (type(definition['admissible_goal']) is not int or definition['admissible_goal'] <= 0):
            raise ValueError('INVALID_ADMISSIBLE_GOAL')
        if not isinstance(definition.get('searches', []), list) or any(not isinstance(x, str) or not x.strip() for x in definition.get('searches', [])):
            raise ValueError('INVALID_SEARCHES')
        caps = definition.get('volume_caps', {})
        if not isinstance(caps, dict) or any(not k or type(v) is not int or v <= 0 for k, v in caps.items()):
            raise ValueError('INVALID_VOLUME_CAPS')
        budgets = definition.get('budgets', {})
        if not isinstance(budgets, dict):
            raise ValueError('INVALID_BUDGETS')
        if budgets.get('campaign_cap') is not None:
            decimal(budgets['campaign_cap'], 'CAMPAIGN_CAP')
        if not isinstance(budgets.get('providers', {}), dict):
            raise ValueError('INVALID_PROVIDER_CAPS')
        for provider, cap in budgets.get('providers', {}).items():
            if not provider or not isinstance(cap, dict):
                raise ValueError('INVALID_PROVIDER_CAPS')
            if cap.get('money_cap') is not None:
                decimal(cap['money_cap'], 'PROVIDER_CAP')
            if not isinstance(cap.get('credit_caps', {}), dict):
                raise ValueError('INVALID_CREDIT_CAPS')
            for unit, value in cap.get('credit_caps', {}).items():
                if not unit:
                    raise ValueError('CREDIT_UNIT_REQUIRED')
                decimal(value, 'CREDIT_CAP')
        conditions = definition.get('stop_conditions', [])
        if not isinstance(conditions, list) or any(not isinstance(x, str) or not x for x in conditions):
            raise ValueError('INVALID_STOP_CONDITIONS')

    def create_brief(self, brief_id, definition, *, command_id):
        if not brief_id:
            raise ValueError('BRIEF_ID_REQUIRED')
        self._validate_definition(definition)
        request = {'kind': 'create', 'brief_id': brief_id, 'definition': definition}
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            replay = self._replay(c, command_id, request)
            if replay:
                return replay
            previous = self.brief(brief_id, connection=c)
            result = {'brief_id': brief_id, 'version': previous['version'] + 1 if previous else 1,
                      'definition': definition, 'state': 'DRAFT', 'created_at': now()}
            c.execute('INSERT INTO workflow_briefs VALUES(?,?,?,?,?)',
                (brief_id, result['version'], encode(definition), 'DRAFT', result['created_at']))
            self._remember(c, command_id, request, result)
            return result

    def _blockers(self, c, brief):
        if brief is None:
            return ['BRIEF_NOT_FOUND']
        d = brief['definition']
        missing = []
        for field in ('target', 'zone', 'admissible_goal', 'searches', 'volume_caps', 'stop_conditions'):
            if not d.get(field):
                missing.append(field.upper() + '_REQUIRED')
        budget = d.get('budgets', {})
        if not isinstance(budget.get('currency'), str) or len(budget.get('currency', '')) != 3 or not budget['currency'].isalpha() or budget['currency'] != budget['currency'].upper():
            missing.append('CURRENCY_REQUIRED')
        if budget.get('campaign_cap') is None:
            missing.append('CAMPAIGN_CAP_REQUIRED')
        providers = budget.get('providers', {})
        if not providers:
            missing.append('PROVIDER_CAPS_REQUIRED')
        for name, cap in providers.items():
            if cap.get('money_cap') is None:
                missing.append('PROVIDER_CAP_REQUIRED:' + name)
            if not isinstance(cap.get('cost_basis'), str) or not cap['cost_basis'].strip():
                missing.append('COST_BASIS_REQUIRED:' + name)
        totals = self._totals(c, brief['brief_id'])
        if totals['overrun']:
            missing.append('BUDGET_OVERRUN_REQUIRES_REVIEW')
        if totals['currencies'] - {budget.get('currency')}:
            missing.append('CURRENCY_CHANGED_WITH_EXISTING_RESERVATIONS')
        if budget.get('campaign_cap') is not None and totals['money'] > decimal(budget['campaign_cap']):
            missing.append('CAMPAIGN_CAP_BELOW_COMMITTED')
        for name, usage in totals['providers'].items():
            cap = providers.get(name)
            if cap is None:
                missing.append('EXISTING_PROVIDER_CAP_REQUIRED:' + name)
                continue
            if cap.get('money_cap') is not None and usage['money'] > decimal(cap['money_cap']):
                missing.append('PROVIDER_CAP_BELOW_COMMITTED:' + name)
            for unit, amount in usage['credits'].items():
                if unit not in cap.get('credit_caps', {}) or amount > decimal(cap['credit_caps'][unit]):
                    missing.append('CREDIT_CAP_BELOW_COMMITTED:' + name + ':' + unit)
        for unit, count in totals['quantities'].items():
            if unit not in d.get('volume_caps', {}) or count > d['volume_caps'][unit]:
                missing.append('VOLUME_CAP_BELOW_COMMITTED:' + unit)
        if d.get('admissible_goal') and totals['admitted'] >= d['admissible_goal']:
            missing.append('ADMISSIBLE_GOAL_REACHED')
        return missing

    def blockers(self, brief_id, version=None):
        with self.store.connection() as c:
            return self._blockers(c, self.brief(brief_id, version, connection=c))

    def launch(self, brief_id, version, *, command_id):
        request = {'kind': 'launch', 'brief_id': brief_id, 'version': version}
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            replay = self._replay(c, command_id, request)
            if replay:
                return replay
            latest = self.brief(brief_id, connection=c)
            if latest is None or latest['version'] != version:
                raise RuntimeError('STALE_BRIEF_VERSION')
            reasons = self._blockers(c, latest)
            if reasons:
                raise RuntimeError(';'.join(reasons))
            c.execute("UPDATE workflow_briefs SET state='LAUNCHED' WHERE brief_id=? AND version=?", (brief_id, version))
            result = {**latest, 'state': 'LAUNCHED'}
            self._remember(c, command_id, request, result)
            return result

    @staticmethod
    def _reservation(row):
        if not row:
            return None
        value = dict(row)
        for name in ('credits', 'actual_credits'):
            value[name] = json.loads(value[name]) if value[name] is not None else None
        return value

    def reservation(self, key):
        with self.store.connection() as c:
            return self._reservation(c.execute('SELECT * FROM workflow_budget_reservations WHERE id=?', (key,)).fetchone())

    def _totals(self, c, brief_id):
        result = {'money': Decimal(0), 'providers': {}, 'quantities': {}, 'currencies': set(), 'overrun': False,
                  'admitted': c.execute('SELECT count(*) FROM workflow_brief_admissions WHERE brief_id=?', (brief_id,)).fetchone()[0]}
        for row in c.execute('SELECT * FROM workflow_budget_reservations WHERE brief_id=?', (brief_id,)):
            if row['state'] == 'FAILED_CERTAIN':
                continue
            result['currencies'].add(row['currency'])
            result['overrun'] |= row['state'] == 'OVERRUN'
            amount = decimal(row['actual_amount'] if row['actual_amount'] is not None else row['amount'])
            credits = json.loads(row['actual_credits'] if row['actual_credits'] is not None else row['credits'])
            result['money'] = add(result['money'], amount)
            provider = result['providers'].setdefault(row['provider'], {'money': Decimal(0), 'credits': {}})
            provider['money'] = add(provider['money'], amount)
            for unit, count in credits.items():
                provider['credits'][unit] = add(provider['credits'].get(unit, Decimal(0)), decimal(count))
            unit = row['quantity_unit']
            result['quantities'][unit] = result['quantities'].get(unit, 0) + row['quantity']
        return result

    def reserve(self, brief_id, version, provider, *, key, amount, quantity=1,
                quantity_unit='items', credits=None, payload=None):
        if amount is None:
            raise RuntimeError('COST_UNEVALUABLE:' + provider)
        amount = money(amount)
        if not key or not provider or not quantity_unit or type(quantity) is not int or quantity <= 0:
            raise ValueError('INVALID_RESERVATION')
        if credits is not None and not isinstance(credits, dict):
            raise ValueError('INVALID_CREDITS')
        credits = {k: money(v) for k, v in (credits or {}).items()}
        if any(not unit for unit in credits):
            raise ValueError('CREDIT_UNIT_REQUIRED')
        request = {'brief_id': brief_id, 'version': version, 'provider': provider, 'amount': amount,
                   'quantity': quantity, 'quantity_unit': quantity_unit, 'credits': credits, 'payload': payload}
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM workflow_budget_reservations WHERE id=?', (key,)).fetchone()
            if old:
                if old['request_hash'] != fingerprint(request):
                    raise ValueError('RESERVATION_REPLAY_CONTENT_CHANGED')
                return self._reservation(old)
            brief = self.brief(brief_id, connection=c)
            if brief is None or brief['version'] != version:
                raise RuntimeError('STALE_BRIEF_VERSION')
            if brief['state'] != 'LAUNCHED':
                raise RuntimeError('BRIEF_NOT_LAUNCHED')
            reasons = self._blockers(c, brief)
            if reasons:
                raise RuntimeError(';'.join(reasons))
            d, totals = brief['definition'], self._totals(c, brief_id)
            budgets = d['budgets']
            cap = budgets['providers'].get(provider)
            if cap is None:
                raise RuntimeError('PROVIDER_CAP_REQUIRED:' + provider)
            used = totals['providers'].get(provider, {'money': Decimal(0), 'credits': {}})
            if add(totals['money'], decimal(amount)) > decimal(budgets['campaign_cap']):
                raise RuntimeError('CAMPAIGN_BUDGET_REACHED')
            if add(used['money'], decimal(amount)) > decimal(cap['money_cap']):
                raise RuntimeError('PROVIDER_BUDGET_REACHED:' + provider)
            if quantity_unit not in d['volume_caps']:
                raise RuntimeError('VOLUME_CAP_REQUIRED:' + quantity_unit)
            if totals['quantities'].get(quantity_unit, 0) + quantity > d['volume_caps'][quantity_unit]:
                raise RuntimeError('VOLUME_LIMIT_REACHED:' + quantity_unit)
            for unit, count in credits.items():
                if unit not in cap.get('credit_caps', {}):
                    raise RuntimeError('CREDIT_CAP_REQUIRED:' + provider + ':' + unit)
                if add(used['credits'].get(unit, Decimal(0)), decimal(count)) > decimal(cap['credit_caps'][unit]):
                    raise RuntimeError('CREDIT_LIMIT_REACHED:' + provider + ':' + unit)
            c.execute('''INSERT INTO workflow_budget_reservations VALUES
                (?,?,?,?,?,?,?,?,?,?,'RESERVED',NULL,NULL,?,?)''',
                (key, brief_id, version, provider, fingerprint(request), amount, budgets['currency'],
                 encode(credits), quantity, quantity_unit, now(), now()))
            return self._reservation(c.execute('SELECT * FROM workflow_budget_reservations WHERE id=?', (key,)).fetchone())

    def mark_submitted(self, key):
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM workflow_budget_reservations WHERE id=?', (key,)).fetchone()
            if row is None:
                raise ValueError('RESERVATION_NOT_FOUND')
            if row['state'] in ('RESERVED', 'UNKNOWN'):
                c.execute("UPDATE workflow_budget_reservations SET state='SUBMITTED',updated_at=? WHERE id=?", (now(), key))
            return self._reservation(c.execute('SELECT * FROM workflow_budget_reservations WHERE id=?', (key,)).fetchone())

    def reconcile(self, key, state, *, actual_amount=None, actual_credits=None):
        if state not in ('CONFIRMED', 'FAILED_CERTAIN', 'UNKNOWN'):
            raise ValueError('INVALID_RECONCILIATION_STATE')
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM workflow_budget_reservations WHERE id=?', (key,)).fetchone()
            if row is None:
                raise ValueError('RESERVATION_NOT_FOUND')
            amount, credits = None, None
            if state == 'CONFIRMED':
                amount = money(actual_amount)
                reserved = json.loads(row['credits'])
                if actual_credits is not None and not isinstance(actual_credits, dict):
                    raise ValueError('INVALID_ACTUAL_CREDITS')
                if not set(reserved).issubset(actual_credits or {}):
                    raise ValueError('ACTUAL_CREDITS_REQUIRED')
                credits = {unit: money(value) for unit, value in (actual_credits or {}).items()}
                if any(not unit for unit in credits):
                    raise ValueError('CREDIT_UNIT_REQUIRED')
                if decimal(amount) > decimal(row['amount']) or any(unit not in reserved or decimal(value) > decimal(reserved[unit]) for unit, value in credits.items()):
                    state = 'OVERRUN'
            elif actual_amount is not None or actual_credits is not None:
                raise ValueError('UNEXPECTED_RECONCILIATION_COST')
            if row['state'] in ('CONFIRMED', 'FAILED_CERTAIN', 'OVERRUN'):
                if (row['state'], row['actual_amount'], row['actual_credits']) != (state, amount, encode(credits) if credits is not None else None):
                    raise RuntimeError('RESERVATION_ALREADY_RECONCILED')
                return self._reservation(row)
            c.execute('UPDATE workflow_budget_reservations SET state=?,actual_amount=?,actual_credits=?,updated_at=? WHERE id=?',
                      (state, amount, encode(credits) if credits is not None else None, now(), key))
            return self._reservation(c.execute('SELECT * FROM workflow_budget_reservations WHERE id=?', (key,)).fetchone())

    def record_admitted(self, brief_id, candidate_id):
        if not candidate_id:
            raise ValueError('CANDIDATE_ID_REQUIRED')
        with self.store.connection() as c:
            if not self.brief(brief_id, connection=c):
                raise ValueError('BRIEF_NOT_FOUND')
            c.execute('INSERT OR IGNORE INTO workflow_brief_admissions VALUES(?,?,?)', (brief_id, candidate_id, now()))

    def summary(self, brief_id):
        with self.store.connection() as c:
            totals = self._totals(c, brief_id)
            latest = self.brief(brief_id, connection=c)
            return {'brief': latest, 'money_committed': str(totals['money']),
                    'currencies': sorted(totals['currencies']), 'admitted': totals['admitted'],
                    'quantities': totals['quantities'], 'providers': {name: {'money': str(v['money']),
                        'credits': {u: str(n) for u, n in v['credits'].items()}} for name, v in totals['providers'].items()},
                    'blockers': self._blockers(c, latest)}

    def guard(self, brief_id, version, estimates):
        """Return the pre-call guard used by qualification/acquisition adapters.

Each estimate supplies an upper bound per requested unit, already denominated
in the brief's currency. No conversion or pricing discovery occurs here.
"""
        def reserve_cost(*, provider, operation_id, quantity=1, payload=None):
            estimate = estimates.get(provider)
            if not estimate or estimate.get('amount') is None:
                raise RuntimeError('COST_UNEVALUABLE:' + provider)
            return self.reserve(brief_id, version, provider, key=operation_id,
                amount=multiply(estimate['amount'], quantity), quantity=quantity,
                quantity_unit=estimate.get('quantity_unit', 'items'),
                credits={unit: multiply(value, quantity) for unit, value in estimate.get('credits', {}).items()},
                payload=payload)
        def submitted(*, operation_id, remote_id=None):
            return self.mark_submitted(operation_id)
        def failed(*, operation_id, certain=False):
            return self.reconcile(operation_id, 'FAILED_CERTAIN' if certain else 'UNKNOWN')
        def unknown(*, operation_id):
            return self.reconcile(operation_id, 'UNKNOWN')
        def reconcile(*, operation_id, actual_amount, actual_credits=None):
            return self.reconcile(operation_id, 'CONFIRMED', actual_amount=actual_amount, actual_credits=actual_credits)
        def preflight(*, provider, quantity=1, payload=None):
            if type(quantity) is not int or quantity <= 0:
                raise ValueError('INVALID_RESERVATION_QUANTITY')
            estimate = estimates.get(provider)
            if not estimate or estimate.get('amount') is None:
                raise RuntimeError('COST_UNEVALUABLE:' + provider)
            amount = multiply(estimate['amount'],quantity)
            credits = {unit:multiply(value,quantity) for unit,value in estimate.get('credits',{}).items()}
            unit = estimate.get('quantity_unit','items')
            with self.store.connection() as c:
                brief = self.brief(brief_id,connection=c)
                if brief is None or brief['version'] != version:
                    raise RuntimeError('STALE_BRIEF_VERSION')
                reasons = self._blockers(c,brief)
                if reasons:
                    raise RuntimeError(';'.join(reasons))
                d,totals = brief['definition'],self._totals(c,brief_id)
                cap = d['budgets']['providers'].get(provider)
                if cap is None:
                    raise RuntimeError('PROVIDER_CAP_REQUIRED:' + provider)
                usage = totals['providers'].get(provider,{'money':Decimal(0),'credits':{}})
                if add(totals['money'],amount) > decimal(d['budgets']['campaign_cap']):
                    raise RuntimeError('CAMPAIGN_BUDGET_REACHED')
                if add(usage['money'],amount) > decimal(cap['money_cap']):
                    raise RuntimeError('PROVIDER_BUDGET_REACHED:' + provider)
                if unit not in d['volume_caps'] or totals['quantities'].get(unit,0)+quantity > d['volume_caps'][unit]:
                    raise RuntimeError('VOLUME_LIMIT_REACHED:' + unit)
                for credit,count in credits.items():
                    if credit not in cap.get('credit_caps',{}):
                        raise RuntimeError('CREDIT_CAP_REQUIRED:' + provider + ':' + credit)
                    if add(usage['credits'].get(credit,Decimal(0)),count) > decimal(cap['credit_caps'][credit]):
                        raise RuntimeError('CREDIT_LIMIT_REACHED:' + provider + ':' + credit)
            return {'amount':money(amount),'credits':{u:money(v) for u,v in credits.items()},'quantity':quantity,'quantity_unit':unit}
        reserve_cost.preflight = preflight
        reserve_cost.submitted = submitted
        reserve_cost.failed = failed
        reserve_cost.unknown = unknown
        reserve_cost.reconcile = reconcile
        return reserve_cost

    def propose_strategy(self, brief_id, version, specialist):
        """Give the specialist only the search dossier and validate its proposal.

The returned object is data for parent review; it neither versions nor launches
any brief and cannot change a budget through this interface.
"""
        brief = self.brief(brief_id, version)
        if brief is None:
            raise ValueError('BRIEF_NOT_FOUND')
        d = brief['definition']
        proposal = specialist({key: d.get(key) for key in ('target', 'zone', 'admissible_goal', 'searches')})
        if not isinstance(proposal, dict) or set(proposal) != {'searches', 'rationale'} or not isinstance(proposal['rationale'], str):
            raise ValueError('INVALID_STRATEGY_SCHEMA')
        if not isinstance(proposal['searches'], list) or not proposal['searches'] or any(not isinstance(x, str) or not x.strip() for x in proposal['searches']):
            raise ValueError('INVALID_STRATEGY_SCHEMA')
        return json.loads(encode(proposal))
