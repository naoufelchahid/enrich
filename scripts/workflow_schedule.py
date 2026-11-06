"""Non-destructive cron installation, readiness gate and calendar retention."""
import argparse
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from scripts.workflow import readiness, ROOT

BEGIN='# BEGIN ENRICH WORKFLOW V2'
END='# END ENRICH WORKFLOW V2'


def cron_block(root=ROOT, *, mailing_only=False):
    wrapper=shlex.quote(str(Path(root)/'scripts/cron_agent_dispatcher.sh'))
    # Application clocks use Africa/Casablanca explicitly; do not alter the
    # global cron timezone, which would affect unrelated existing jobs.
    return '\n'.join([BEGIN,
        f'* * * * * {wrapper} --task send_next_candidate',
        f'*/30 * * * * {wrapper} --task review_inbox_and_sync',
        f'37 2 * * * {wrapper} --task retention',END])


def merge_crontab(existing,block):
    lines=existing.splitlines()
    start=[i for i,line in enumerate(lines) if line==BEGIN]
    finish=[i for i,line in enumerate(lines) if line==END]
    if bool(start)!=bool(finish) or len(start)>1 or len(finish)>1 or (start and finish[0]<start[0]):
        raise ValueError('MALFORMED_EXISTING_WORKFLOW_CRON')
    if start:
        lines=lines[:start[0]]+lines[finish[0]+1:]
    return '\n'.join(lines+[block])+'\n'


def install_cron(*,apply=False,root=ROOT,ready=None,mailing_only=False):
    current=subprocess.run(['crontab','-l'],capture_output=True,text=True)
    if current.returncode not in (0,1):
        raise RuntimeError('CRONTAB_READ_FAILED')
    old=current.stdout if current.returncode==0 else ''
    rendered=merge_crontab(old,cron_block(root,mailing_only=mailing_only))
    configuration=ready if ready is not None else readiness(probe=apply)
    if not apply:
        return {'status':'PREVIEW','ready':configuration['ready'],'cron':rendered}
    if not configuration['ready']:
        return {'status':'ACTIVATION_BLOCKED','missing':configuration['missing']}
    # Preserve exactly the unrelated entries; abort if they changed meanwhile.
    check=subprocess.run(['crontab','-l'],capture_output=True,text=True)
    if (check.stdout if check.returncode==0 else '')!=old:
        raise RuntimeError('CRONTAB_CHANGED_RETRY_REQUIRED')
    backup=Path(root)/'bdd/workflow/cron-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f.txt')
    backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup.write_text(old)
    backup.chmod(0o600)
    result=subprocess.run(['crontab','-'],input=rendered,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError('CRONTAB_INSTALL_FAILED')
    return {'status':'INSTALLED','backup':str(backup)}


def purge_archives(directory,*,at=None,dry_run=False):
    at=at or datetime.now(timezone.utc)
    # Twelve calendar months, including leap-day clamping.
    try:
        cutoff=at.replace(year=at.year-1)
    except ValueError:
        cutoff=at.replace(year=at.year-1,day=28)
    removed,unreadable=[],[]
    for path in Path(directory).glob('*.json'):
        try:
            data=json.loads(path.read_text())
            stamp=datetime.fromisoformat(data['archived_at'].replace('Z','+00:00'))
            if not stamp.tzinfo:
                raise ValueError()
        except (OSError,ValueError,KeyError):
            unreadable.append(path.name)
            continue
        if stamp<cutoff:
            if not dry_run:
                path.unlink()
            removed.append(path.name)
    return {'dry_run':dry_run,'expired_archives':len(removed),'unreadable':unreadable}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--mailing-only',action='store_true',
                        help='Schedule approved CRM outreach and monitoring without new lead preparation')
    args=parser.parse_args()
    print(json.dumps(install_cron(apply=args.apply,mailing_only=args.mailing_only),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
