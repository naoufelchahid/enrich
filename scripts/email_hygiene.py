"""Conservative address hygiene. PASS is never a deliverability verdict."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import dns.exception
import dns.resolver

DISPOSABLE_DOMAINS = frozenset({
    'mailinator.com', 'yopmail.com', 'yopmail.fr', 'yopmail.net',
    'guerrillamail.com', 'sharklasers.com', '10minutemail.com',
    'temp-mail.org', 'tempmail.com', 'throwaway.email', 'maildrop.cc',
})

GENERIC_MAILBOXES = frozenset({'contact', 'info', 'commercial', 'sales', 'support',
    'admin', 'administration', 'office', 'hello', 'bonjour', 'reception', 'accueil',
    'rh', 'recrutement', 'hr', 'compta', 'comptabilite', 'direction', 'marketing',
    'service', 'clients', 'secretariat', 'noreply', 'no-reply', 'webmaster'})

# Consumer mailbox services are forbidden even when used by a business or
# reported deliverable by a provider. Custom business domains hosted by these
# providers (for example Google Workspace) are not consumer mailbox domains.
PERSONAL_EMAIL_DOMAINS = frozenset({
    'gmail.com', 'googlemail.com', 'hotmail.com', 'outlook.com', 'live.com', 'msn.com',
    'yahoo.com', 'ymail.com', 'rocketmail.com', 'aol.com', 'aim.com',
    'icloud.com', 'me.com', 'mac.com', 'proton.me', 'protonmail.com', 'pm.me',
    'tuta.com', 'tutanota.com', 'tutanota.de', 'tutamail.com', 'tuta.io', 'keemail.me',
    'gmx.com', 'gmx.net', 'gmx.de', 'mail.com', 'email.com', 'inbox.com',
    'fastmail.com', 'fastmail.fm', 'hey.com', 'hushmail.com', 'zoho.com',
    'orange.fr', 'wanadoo.fr', 'laposte.net', 'free.fr', 'sfr.fr', 'neuf.fr',
    'bbox.fr', 'numericable.fr', 'club-internet.fr', 'aliceadsl.fr',
    'btinternet.com', 'btopenworld.com', 'virginmedia.com', 'sky.com',
    'comcast.net', 'verizon.net', 'att.net', 'sbcglobal.net', 'bellsouth.net',
    'qq.com', 'foxmail.com', '163.com', '126.com', 'yeah.net', 'naver.com',
    'daum.net', 'hanmail.net', 'nate.com', 'mail.ru', 'inbox.ru', 'bk.ru',
    'list.ru', 'yandex.ru', 'yandex.com', 'yandex.kz', 'web.de', 'rambler.ru',
    'protonmail.ch', 'privaterelay.appleid.com', 'duck.com', 'mozmail.com',
    'simplelogin.com', 'simplelogin.co', 'slmail.me',
})
# Official public signup aliases, including misleading professional-looking
# names: https://www.mail.com/mail/domains/ (checked 2026-10-09).
MAIL_COM_DOMAINS = frozenset('''
mail.com email.com post.com accountant.com africamail.com artlover.com asia.com
cheerful.com consultant.com contractor.net dr.com engineer.com europe.com execs.com
hackermail.com iname.com musician.org myself.com techie.com usa.com activist.com
adexec.com alumni.com archaeologist.com bartender.net chef.net chemist.com collector.org
columnist.com comic.com hairdresser.net counsellor.com diplomats.com financier.com
fireman.net gardener.com geologist.com graphic-designer.com graduate.org legislator.com
lobbyist.com minister.com optician.com orthodontist.net pediatrician.com photographer.net
physicist.net politician.com priest.com programmer.net registerednurses.com repairman.com
secretary.net socialworker.net sociologist.com songwriter.net teachers.org technologist.com
therapist.net publicist.com realtyagent.com bikerider.com birdlover.com kittymail.com
lovecat.com marchmail.com boardermail.com brew-master.com catlover.com clubmember.org
nonpartisan.com petlover.com doglover.com greenmail.net theplate.com bsdmail.com
cyberdude.com cybergal.com cyberservices.com cyber-wizard.com linuxmail.org null.net
webname.com acdcfan.com angelic.com discofan.com elvisfan.com hiphopfan.com kissfans.com
madonnafan.com metalfan.com ninfan.com ravemail.com reggaefan.com californiamail.com
dallasmail.com nycmail.com sanfranmail.com australiamail.com berlin.com brazilmail.com
chinamail.com dublin.com dutchmail.com englandmail.com arcticmail.com europemail.com
germanymail.com irelandmail.com israelmail.com italymail.com koreamail.com mexicomail.com
moscowmail.com munich.com polandmail.com safrica.com scotlandmail.com spainmail.com
swedenmail.com swissmail.com torontomail.com atheist.com disciples.com muslim.com
protestant.com reborn.com reincarnate.com religious.com saintly.com cutey.com dbzmail.com
doramail.com galaxyhit.com hilarious.com 2trom.com innocent.com keromail.com toothfairy.com
toke.com tvstar.com uymail.com
'''.split())
FORBIDDEN_EMAIL_DOMAINS = PERSONAL_EMAIL_DOMAINS | DISPOSABLE_DOMAINS | MAIL_COM_DOMAINS


def is_personal_email(email):
    if not isinstance(email, str) or '@' not in email:
        return False
    domain = email.rsplit('@', 1)[1].strip().lower().rstrip('.')
    labels=domain.split('.')
    if any('.'.join(labels[i:]) in FORBIDDEN_EMAIL_DOMAINS for i in range(len(labels))):
        return True
    # Regional aliases such as hotmail.fr, yahoo.co.uk, outlook.es, live.be.
    return bool(re.fullmatch(r'(?:[a-z0-9-]+\.)*(?:hotmail|outlook|live|msn|yahoo|aol|gmx)\.(?:(?:co|com)\.)?[a-z]{2,3}', domain))


def personal_email_replacement(lead):
    """Treat the forbidden source as missing, preserving it only for audit."""
    email = lead.get('raw_email') or lead.get('clean_email') or lead.get('validated_email')
    updates = {'original_email':lead.get('original_email') or email,
               'raw_email':None, 'clean_email':None, 'validated_email':None,
               'email_validation_source':None, 'email_validated_at':None,
               'email_check_status':'INVALID', 'email_checked_input':email,
               'email_check_reason':'PERSONAL_DOMAIN', 'neverbounce_status':'UNKNOWN',
               'enrichment_status':'PENDING', 'workflow_wait_reason':'REPLACEMENT_EMAIL_REQUIRED'}
    if lead.get('contact_discovery_status') not in ('SUBMITTED','UNKNOWN','FAILED','NEEDS_REVIEW','RETRY_SCHEDULED','NO_MATCH'):
        updates['contact_discovery_status']='PENDING'
    return updates


def name_tokens(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.findall('[a-z0-9]+', text)


def email_identity(email, first_name, last_name):
    """Explain the 40% identity filter; this is never identity confirmation."""
    if not valid_email(email):
        return {'score': 0, 'accepted': False, 'reason': 'SYNTAX', 'matches': []}
    if is_personal_email(email):
        return {'score': 0, 'accepted': False, 'reason': 'PERSONAL_DOMAIN', 'matches': []}
    local = email.split('@')[0].lower().split('+')[0]
    parts = name_tokens(local)
    if any(p in GENERIC_MAILBOXES for p in parts):
        return {'score': 0, 'accepted': False, 'reason': 'GENERIC', 'matches': []}
    first, last = name_tokens(first_name), name_tokens(last_name)
    compact = ''.join(parts)
    matches = []
    score = 0
    # Exact tokens avoid matching "ali" inside unrelated "qualite" mailboxes.
    if any(p in parts for p in last) or (last and compact == ''.join(last)):
        score += 60
        matches.append('last_name')
    if any(p in parts for p in first) or (first and compact == ''.join(first)):
        score += 40
        matches.append('first_name')
    if first and last:
        forms = {''.join(first) + ''.join(last), ''.join(last) + ''.join(first)}
        initial_forms = {first[0][0] + ''.join(last), ''.join(last) + first[0][0],
                         ''.join(first) + last[0][0], last[0][0] + ''.join(first)}
        if compact in forms:
            score, matches = 100, ['first_name', 'last_name']
        elif compact in initial_forms:
            score, matches = max(score, 80), ['name_and_initial']
        elif len(parts) == 2 and parts == [first[0][0], last[0][0]]:
            score, matches = max(score, 40), ['two_initials']
    return {'score': min(score, 100), 'accepted': score >= 40,
            'reason': 'MATCH' if score >= 40 else 'BELOW_40', 'matches': matches}


def valid_email(value):
    if not isinstance(value, str) or len(value) > 254 or value.count('@') != 1:
        return False
    local, domain = value.split('@')
    if not local or len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        return False
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        return False
    labels = domain.split('.')
    return (len(labels) >= 2 and len(domain) <= 253 and not labels[-1].isdigit()
            and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', x) for x in labels))


def fresh(timestamp, *, at=None):
    try:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:  # SQLite CURRENT_TIMESTAMP is UTC.
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (at or datetime.now(timezone.utc)) - parsed
        return timedelta(0) <= age <= timedelta(days=90)
    except (TypeError, ValueError, OverflowError):
        return False


def resolve_mail_domain(domain):
    try:
        records = list(dns.resolver.resolve(domain, 'MX', lifetime=3, search=False))
        if len(records) == 1 and str(records[0].exchange) == '.':
            return 'INVALID'  # Explicit null MX: domain refuses email.
        return 'PASS' if records and all(str(r.exchange) != '.' for r in records) else 'WAITING'
    except dns.resolver.NXDOMAIN:
        return 'INVALID'
    except (dns.exception.DNSException, OSError):
        # No MX may use implicit address delivery; timeout is also not invalidity.
        return 'WAITING'


def inspect_email(email, *, domain_cache=None):
    if isinstance(email, str) and (not email.isascii() or email.startswith(chr(34))):
        return 'WAITING'  # Unsupported syntax is not proof of an invalid mailbox.
    if not valid_email(email):
        return 'INVALID'
    if is_personal_email(email):
        return 'INVALID'
    domain = email.rsplit('@', 1)[1].lower()
    if any(domain == d or domain.endswith('.' + d) for d in DISPOSABLE_DOMAINS):
        return 'WAITING'  # Exclude disposable addresses without inventing a mailbox verdict.
    # Only reuse DNS answers within this invocation, not across later retries.
    cache = domain_cache if domain_cache is not None else {}
    if domain not in cache:
        cache[domain] = resolve_mail_domain(domain)
    return cache[domain]
