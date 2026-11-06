"""Install one independent daily cron line while preserving all existing lines."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .readiness import readiness


ROOT = Path(__file__).resolve().parents[2]
BEGIN = '# BEGIN ENRICH TENDER ACQUISITION'
END = '# END ENRICH TENDER ACQUISITION'


def block(root=ROOT):
    wrapper = shlex.quote(str(Path(root) / 'scripts/tender_acquisition/run.sh'))
    return '\n'.join((BEGIN, f'27 3 * * * {wrapper} --task daily', END))


def merge(existing: str, new_block: str) -> str:
    lines = existing.splitlines()
    starts = [i for i, line in enumerate(lines) if line == BEGIN]
    ends = [i for i, line in enumerate(lines) if line == END]
    if len(starts) != len(ends) or len(starts) > 1 or (starts and ends[0] < starts[0]):
        raise ValueError('MALFORMED_TENDER_CRON_BLOCK')
    if starts:
        lines = lines[:starts[0]] + lines[ends[0] + 1:]
    return '\n'.join(lines + [new_block]) + '\n'


def install(*, apply: bool = False, root=ROOT, ready=None):
    current = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if current.returncode not in (0, 1):
        raise RuntimeError('CRONTAB_READ_FAILED')
    old = current.stdout if current.returncode == 0 else ''
    merged = merge(old, block(root))
    if not apply:
        return {'status': 'PREVIEW', 'cron': merged}
    condition = ready if ready is not None else readiness(probe_vision=True)
    if not condition['ready']:
        return {'status': 'BLOCKED_READINESS', 'checks': condition['checks']}
    check = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if (check.stdout if check.returncode == 0 else '') != old:
        raise RuntimeError('CRONTAB_CHANGED')
    backup = Path(root) / 'bdd/workflow/cron-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f-tender.txt')
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(old, encoding='utf-8')
    backup.chmod(0o600)
    result = subprocess.run(['crontab', '-'], input=merged, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('CRONTAB_INSTALL_FAILED')
    return {'status': 'INSTALLED', 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(install(apply=args.apply), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
