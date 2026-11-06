"""CLI for the independent tender-company acquisition workflow."""

import argparse
import calendar
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .state import OperationStore


def months_ago(day, count):
    total = day.year * 12 + day.month - 1 - count
    year, month_index = divmod(total, 12)
    month = month_index + 1
    return day.replace(year=year, month=month,
                       day=min(day.day, calendar.monthrange(year, month)[1]))


def reconcile_provider(config, kind, subject, remote_id):
    from scripts.enrichment_clients import FullEnrichClient
    from scripts.no2bounce_client import No2BounceClient
    if kind not in ('FULLENRICH', 'NO2BOUNCE') or not subject or not remote_id:
        raise ValueError('Expected provider kind, subject and remote ID')
    subject_kind = 'person_linkedin' if kind == 'FULLENRICH' else 'email'
    store = OperationStore(config.db_path)
    with store.lock():
        op = store.get(kind, subject_kind, subject)
        if not op or op['state'] != 'UNKNOWN':
            raise ValueError('Expected an UNKNOWN operation')
        client = FullEnrichClient() if kind == 'FULLENRICH' else No2BounceClient()
        client.result(remote_id)  # A recognized in-progress response is also sufficient.
        store.finish(op['id'], 'SUCCEEDED', remote_id=remote_id, outcome='reconciled')
    return {'task': 'reconcile', 'kind': kind, 'status': 'SUCCEEDED'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=('preview', 'status', 'readiness', 'simulate',
                                           'backfill', 'daily', 'reconcile', 'schedule'), default='preview')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--kind', choices=('FULLENRICH', 'NO2BOUNCE'))
    parser.add_argument('--subject')
    parser.add_argument('--remote-id')
    args = parser.parse_args()
    config = Config.from_env()
    today = datetime.now(ZoneInfo('Africa/Casablanca')).date()
    if args.task == 'preview':
        result = {
            'task': 'preview', 'database': str(config.db_path),
            'max_companies': config.max_companies,
            'max_documents': config.max_documents,
            'max_run_minutes': config.max_run_minutes,
            'first_run_months': config.first_run_months,
            'daily_window_days': config.daily_window_days,
            'backfill_since': months_ago(today, config.first_run_months).isoformat(),
            'daily_since': (today - timedelta(days=config.daily_window_days)).isoformat(),
            'services_called': False,
        }
    elif args.task == 'status':
        result = {'task': 'status', **OperationStore(config.db_path).summary()}
    elif args.task == 'simulate':
        from .simulate import simulate
        result = simulate()
    elif args.task == 'readiness':
        from .readiness import readiness
        result = {'task': 'readiness', **readiness(probe_vision=True)}
    elif args.task == 'reconcile':
        result = reconcile_provider(config, args.kind, args.subject, args.remote_id)
    elif args.task == 'schedule':
        from .schedule import install
        result = {'task': 'schedule', **install(apply=args.apply)}
    else:
        from .readiness import readiness
        check = readiness(probe_vision=args.task == 'backfill')
        if not check['ready']:
            result = {'task': args.task, 'status': 'BLOCKED_READINESS', **check}
        else:
            from .runner import run
            since = (months_ago(today, config.first_run_months) if args.task == 'backfill'
                     else today - timedelta(days=config.daily_window_days))
            result = {'task': args.task, 'status': 'COMPLETED',
                      'since': since.isoformat(), **run(since=since, config=config)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
