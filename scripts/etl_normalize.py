import os
import re
from typing import List, Optional, Tuple, Union
import openpyxl
import xlrd

from scripts.models import (
    NormalizedContact,
    EnrichmentStatusEnum,
    EmailValidationStatusEnum,
)

# Regex matching executive / decision maker titles
DIRIGEANT_REGEX = re.compile(
    r"(?:\b|\A)("
    r"p\.?d\.?g\.?|"
    r"direct(eur|rice)\s+g[ée]n[ée]ral[e]?|"
    r"dg|"
    r"g[ée]rant[e]?|"
    r"pr[ée]sident[e]?|"
    r"fondat(eur|rice)|"
    r"co-?fondat(eur|rice)|"
    r"administrateur\s+d[ée]l[ée]gu[ée]|"
    r"administr\.?\s+d[ée]l[ée]gu[ée]|"
    r"administrateur\s+directeur\s+g[ée]n[ée]ral|"
    r"managing\s+director|"
    r"general\s+manager|"
    r"ceo|"
    r"direct(eur|rice)\s+associ[ée][e]?|"
    r"associ[ée]\s+g[ée]rant[e]?|"
    r"country\s+manager|"
    r"direct(eur|rice)\s+ex[ée]cutif|"
    r"chairman|"
    r"dirigeant[e]?|"
    r"propri[ée]taire"
    r")(?:\b|\Z)",
    re.IGNORECASE,
)

WEBMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.fr",
    "yahoo.com",
    "hotmail.fr",
    "hotmail.com",
    "outlook.fr",
    "outlook.com",
    "menara.ma",
    "wanadoo.net.ma",
    "wanadoo.fr",
    "live.fr",
    "live.com",
    "msn.com",
    "aol.com",
    "icloud.com",
}


def fix_mojibake(text: Optional[str]) -> Optional[str]:
    """Correct CP1252/UTF-8 double encoding artifacts (e.g., GÃƒÂ©rant -> Gérant)."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return ""
    for _ in range(3):
        if any(marker in s for marker in ("Ã", "Â", "\u0192")):
            try:
                s = s.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
        else:
            break
    return s


def is_dirigeant_title(title: Optional[str]) -> bool:
    """Return True if the given job title indicates a C-level or executive decision maker."""
    if not title:
        return False
    title_str = fix_mojibake(str(title)).strip()
    if not title_str:
        return False
    return bool(DIRIGEANT_REGEX.search(title_str))


def normalize_phone(raw: Optional[Union[str, float, int]]) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize Moroccan phone numbers into E.164 (+212...).
    Returns (phone_standard, phone_mobile).
    Fixe = +2125...
    Mobile = +2126... / +2127...
    """
    if raw is None:
        return None, None

    if isinstance(raw, float) and raw.is_integer():
        raw_str = str(int(raw))
    else:
        raw_str = str(raw).strip()

    if raw_str.endswith(".0"):
        raw_str = raw_str[:-2]

    # Handle multiple numbers separated by slashes or commas: evaluate first valid
    chunks = re.split(r"[/,;]", raw_str)
    for chunk in chunks:
        digits = re.sub(r"[^\d+]", "", chunk.strip())
        if not digits:
            continue

        if digits.startswith("00212"):
            digits = "+212" + digits[5:]
        elif digits.startswith("0"):
            digits = "+212" + digits[1:]
        elif digits.startswith("212") and not digits.startswith("+212"):
            digits = "+" + digits
        elif not digits.startswith("+") and len(digits) == 9 and digits[0] in ("5", "6", "7"):
            digits = "+212" + digits

        # Classification fixe (+2125...) vs mobile (+2126... / +2127...)
        if "+2125" in digits:
            return digits, None
        elif "+2126" in digits or "+2127" in digits:
            return None, digits
        elif digits.startswith("+212"):
            return digits, None

    return None, None


def split_name(full: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract civility, first name, and last name from a full name string.
    Returns (civility, first_name, last_name).
    """
    if not full:
        return None, None, None

    cleaned = fix_mojibake(str(full))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None, None, None

    civ = None
    for prefix in ["Mme.", "Mme", "M.", "Mr.", "Mr", "Mlle.", "Mlle", "Dr.", "Dr", "M "]:
        if cleaned.startswith(prefix):
            civ = prefix.strip()
            cleaned = cleaned[len(prefix):].strip()
            break

    parts = cleaned.split(" ")
    if not parts or not parts[0]:
        return civ, None, None
    if len(parts) == 1:
        return civ, parts[0], ""
    return civ, parts[0], " ".join(parts[1:])


def build_normalized_contact(
    company_name: str,
    source_file: str,
    activity_sector: Optional[str] = None,
    website: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    phone_standard: Optional[str] = None,
    phone_mobile: Optional[str] = None,
    civility: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    job_title: Optional[str] = None,
    raw_email: Optional[str] = None,
) -> NormalizedContact:
    """Helper to instantiate a NormalizedContact with computed status and missing fields."""
    comp = (fix_mojibake(company_name) or "").strip()
    act = fix_mojibake(activity_sector).strip() if activity_sector else None
    web = str(website).strip() if website else None
    c_city = fix_mojibake(city).strip() if city else None
    addr = fix_mojibake(address).strip() if address else None
    civ = str(civility).strip() if civility else None
    fn = fix_mojibake(first_name).strip() if first_name else None
    ln = fix_mojibake(last_name).strip() if last_name else None
    jt = fix_mojibake(job_title).strip() if job_title else None
    em = str(raw_email).strip().lower() if raw_email else None
    if em and "@" not in em:
        em = None

    is_dirigeant = is_dirigeant_title(jt)

    missing = []
    if not em:
        missing.append("email")
    if not (phone_standard or phone_mobile):
        missing.append("phone")
    if not fn:
        missing.append("first_name")
    if not ln:
        missing.append("last_name")
    if not jt:
        missing.append("job_title")
    if not comp:
        missing.append("company_name")
    if not c_city:
        missing.append("city")
    if not act:
        missing.append("activity_sector")

    if not em:
        enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT
        val_status = EmailValidationStatusEnum.MISSING
        email = None
    elif not comp or (not fn and not ln):
        enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_DONNEES_INCOMPLETES
        val_status = EmailValidationStatusEnum.MISSING
        email = em
    else:
        enrichment_status = EnrichmentStatusEnum.COMPLET
        val_status = EmailValidationStatusEnum.MISSING
        email = em

    return NormalizedContact(
        company_name=comp,
        activity_sector=act,
        website=web,
        city=c_city,
        address=addr,
        phone_standard=phone_standard,
        phone_mobile=phone_mobile,
        civility=civ,
        first_name=fn,
        last_name=ln,
        job_title=jt,
        is_dirigeant=is_dirigeant,
        raw_email=em,
        email=email,
        email_validation_status=val_status,
        source_file=source_file,
        enrichment_status=enrichment_status,
        missing_fields=missing,
    )


def parse_bdd1(file_path: str) -> List[NormalizedContact]:
    """Parse BDD1.xlsx: up to 5 contacts per company."""
    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    ws = wb["Feuil1"]
    contacts: List[NormalizedContact] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not any(row):
            continue

        act = row[0]
        comp = row[1] or ""
        addr = row[2]
        city = row[4]
        tel1 = row[5]
        tel2 = row[6]
        gsm = row[7]
        web = row[24] if len(row) > 24 else None

        f1, m1 = normalize_phone(tel1)
        f2, m2 = normalize_phone(tel2)
        fg, mg = normalize_phone(gsm)
        phone_std = f1 or f2 or fg
        phone_mob = mg or m1 or m2

        # 5 contacts pairs: (contact, fonction, email)
        contact_slots = [
            (9, 10, 11),
            (12, 13, 14),
            (15, 16, 17),
            (18, 19, 20),
            (21, 22, 23),
        ]

        found_contact = False
        for c_idx, t_idx, e_idx in contact_slots:
            c_name = row[c_idx] if len(row) > c_idx else None
            c_title = row[t_idx] if len(row) > t_idx else None
            c_email = row[e_idx] if len(row) > e_idx else None

            if c_name or c_title or c_email:
                found_contact = True
                civ, fn, ln = split_name(c_name)
                contacts.append(
                    build_normalized_contact(
                        company_name=str(comp),
                        source_file="BDD1",
                        activity_sector=str(act) if act else None,
                        website=str(web) if web else None,
                        city=str(city) if city else None,
                        address=str(addr) if addr else None,
                        phone_standard=phone_std,
                        phone_mobile=phone_mob,
                        civility=civ,
                        first_name=fn,
                        last_name=ln,
                        job_title=str(c_title) if c_title else None,
                        raw_email=str(c_email) if c_email else None,
                    )
                )

        if not found_contact and comp:
            contacts.append(
                build_normalized_contact(
                    company_name=str(comp),
                    source_file="BDD1",
                    activity_sector=str(act) if act else None,
                    website=str(web) if web else None,
                    city=str(city) if city else None,
                    address=str(addr) if addr else None,
                    phone_standard=phone_std,
                    phone_mobile=phone_mob,
                )
            )

    wb.close()
    return contacts


def parse_bdd2(file_path: str) -> List[NormalizedContact]:
    """Parse BDD2.xlsx: up to 5 executives per company with single company email."""
    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    ws = wb["BDD"]
    contacts: List[NormalizedContact] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not any(row):
            continue

        comp = row[0] or ""
        addr = row[1]
        city = row[3]
        tel1 = row[14] if len(row) > 14 else None
        tel2 = row[15] if len(row) > 15 else None
        c_email = row[17] if len(row) > 17 else None
        web = row[18] if len(row) > 18 else None
        act = row[19] if len(row) > 19 else None

        f1, m1 = normalize_phone(tel1)
        f2, m2 = normalize_phone(tel2)
        phone_std = f1 or f2
        phone_mob = m1 or m2

        exec_slots = [
            (4, 5),
            (6, 7),
            (8, 9),
            (10, 11),
            (12, 13),
        ]

        found_exec = False
        for c_idx, t_idx in exec_slots:
            name = row[c_idx] if len(row) > c_idx else None
            title = row[t_idx] if len(row) > t_idx else None

            if name or title:
                found_exec = True
                civ, fn, ln = split_name(name)
                contacts.append(
                    build_normalized_contact(
                        company_name=str(comp),
                        source_file="BDD2",
                        activity_sector=str(act) if act else None,
                        website=str(web) if web else None,
                        city=str(city) if city else None,
                        address=str(addr) if addr else None,
                        phone_standard=phone_std,
                        phone_mobile=phone_mob,
                        civility=civ,
                        first_name=fn,
                        last_name=ln,
                        job_title=str(title) if title else None,
                        raw_email=str(c_email) if c_email else None,
                    )
                )

        if not found_exec and comp:
            contacts.append(
                build_normalized_contact(
                    company_name=str(comp),
                    source_file="BDD2",
                    activity_sector=str(act) if act else None,
                    website=str(web) if web else None,
                    city=str(city) if city else None,
                    address=str(addr) if addr else None,
                    phone_standard=phone_std,
                    phone_mobile=phone_mob,
                    raw_email=str(c_email) if c_email else None,
                )
            )

    wb.close()
    return contacts


def parse_bdd3_global(file_path: str) -> List[NormalizedContact]:
    """Parse BDD3.xls sheet 'BDD GLOBAL'."""
    wb = xlrd.open_workbook(file_path)
    ws = wb.sheet_by_name("BDD GLOBAL")
    contacts: List[NormalizedContact] = []

    # Row 0 is empty, Row 1 is header
    for r in range(2, ws.nrows):
        row = ws.row_values(r)
        if not any(row):
            continue

        act = row[0]
        comp = row[1] or ""
        civility = row[2]
        contact_name = row[3]
        title = row[4]
        email = row[5]
        gsm = row[6]
        tel = row[7]
        addr = row[9] if len(row) > 9 else None
        city = row[10] if len(row) > 10 else None

        fg, mg = normalize_phone(gsm)
        ft, mt = normalize_phone(tel)
        phone_std = ft or fg
        phone_mob = mg or mt

        civ, fn, ln = split_name(contact_name)
        final_civ = str(civility).strip() if civility else civ

        contacts.append(
            build_normalized_contact(
                company_name=str(comp),
                source_file="BDD3_Global",
                activity_sector=str(act) if act else None,
                city=str(city) if city else None,
                address=str(addr) if addr else None,
                phone_standard=phone_std,
                phone_mobile=phone_mob,
                civility=final_civ,
                first_name=fn,
                last_name=ln,
                job_title=str(title) if title else None,
                raw_email=str(email) if email else None,
            )
        )

    return contacts


def parse_bdd3_2023(file_path: str) -> List[NormalizedContact]:
    """Parse BDD3.xls sheet '2023'."""
    wb = xlrd.open_workbook(file_path)
    ws = wb.sheet_by_name("2023")
    contacts: List[NormalizedContact] = []

    for r in range(1, ws.nrows):
        row = ws.row_values(r)
        if not any(row):
            continue

        c0 = str(row[0]).strip() if row[0] is not None else ""
        c2 = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
        c3 = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
        c4 = row[4] if len(row) > 4 else None
        c5 = str(row[5]).strip() if len(row) > 5 and row[5] is not None else ""

        if not c0 and not c2 and not c5:
            continue

        # Check for column inversion between c0 (company) and c3 (job title)
        lower_c0 = c0.lower()
        lower_c3 = c3.lower()
        title_indicators = ("chargé", "directeur", "responsable", "chef", "ingénieur", "consultant", "dg", "gerant")
        if any(ind in lower_c0 for ind in title_indicators) and not any(ind in lower_c3 for ind in title_indicators):
            company_name = c3
            job_title = c0
        else:
            company_name = c0
            job_title = c3

        civ, fn, ln = split_name(c2)
        f, m = normalize_phone(c4)

        contacts.append(
            build_normalized_contact(
                company_name=company_name,
                source_file="BDD3_2023",
                phone_standard=f,
                phone_mobile=m,
                civility=civ,
                first_name=fn,
                last_name=ln,
                job_title=job_title if job_title else None,
                raw_email=c5 if c5 else None,
            )
        )

    return contacts


def parse_bdd4(file_path: str) -> List[NormalizedContact]:
    """Parse BDD4.xlsx: Spanish headers."""
    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    ws = wb["Feuil1"]
    contacts: List[NormalizedContact] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not any(row):
            continue

        comp = row[0] or ""
        fn = row[1]
        ln = row[2]
        fijo = row[3]
        movil = row[4]
        email = row[5]
        web = row[6]
        act = row[7]
        addr = row[8]
        city = row[9]

        f1, m1 = normalize_phone(fijo)
        f2, m2 = normalize_phone(movil)
        phone_std = f1 or f2
        phone_mob = m2 or m1

        contacts.append(
            build_normalized_contact(
                company_name=str(comp),
                source_file="BDD4",
                activity_sector=str(act) if act else None,
                website=str(web) if web else None,
                city=str(city) if city else None,
                address=str(addr) if addr else None,
                phone_standard=phone_std,
                phone_mobile=phone_mob,
                first_name=str(fn) if fn else None,
                last_name=str(ln) if ln else None,
                raw_email=str(email) if email else None,
            )
        )

    wb.close()
    return contacts


def parse_bdd5(file_path: str) -> List[NormalizedContact]:
    """Parse BDD5.xlsx: TITLE, LASTNAME, FIRSTNAME, JOB_TITLE, EMAIL."""
    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    ws = wb["Agnitas Admin_2017_10_26"]
    contacts: List[NormalizedContact] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not any(row):
            continue

        civ = row[0]
        ln = row[1]
        fn = row[2]
        title = row[3]
        email = row[4]

        comp = ""
        web = None
        if email and "@" in str(email):
            domain = str(email).split("@")[-1].strip().lower()
            if domain not in WEBMAIL_DOMAINS and "." in domain:
                comp = domain
                web = f"https://{domain}"

        contacts.append(
            build_normalized_contact(
                company_name=comp,
                source_file="BDD5",
                website=web,
                civility=str(civ) if civ else None,
                first_name=str(fn) if fn else None,
                last_name=str(ln) if ln else None,
                job_title=str(title) if title else None,
                raw_email=str(email) if email else None,
            )
        )

    wb.close()
    return contacts


def parse_bdd6(file_path: str) -> List[NormalizedContact]:
    """Parse BDD6.xls: Société, G, Prénom et Nom, Fonction, TEL, E-mail."""
    wb = xlrd.open_workbook(file_path)
    ws = wb.sheet_by_name("Sheet1")
    contacts: List[NormalizedContact] = []

    for r in range(1, ws.nrows):
        row = ws.row_values(r)
        if not any(row):
            continue

        c0 = row[0]
        c1 = row[1]
        c2 = row[2]
        c3 = row[3]
        c4 = row[4]
        c5 = row[5] if len(row) > 5 else None

        # Check if col 0 was shifted/phone
        is_col0_phone = False
        if isinstance(c0, (float, int)):
            is_col0_phone = True
        elif isinstance(c0, str) and c0.replace(".", "").isdigit() and len(c0.strip()) >= 8:
            is_col0_phone = True

        if is_col0_phone:
            company_name = str(c3).strip() if c3 else ""
            job_title = None
            phone_raw = c0
        else:
            company_name = str(c0).strip() if c0 else ""
            job_title = str(c3).strip() if c3 else None
            phone_raw = c4

        f, m = normalize_phone(phone_raw)

        # Name and Civility extraction
        g_val = str(c1).strip() if c1 is not None else ""
        name_val = str(c2).strip() if c2 is not None else ""

        if g_val in ["M.", "Mme.", "Mme", "M", "Mlle", "Dr."]:
            civility = g_val
            civ, fn, ln = split_name(name_val)
        elif g_val and not name_val:
            civility, fn, ln = split_name(g_val)
        elif g_val and name_val:
            civility = None
            fn = name_val
            ln = g_val
        else:
            civility, fn, ln = split_name(name_val)

        contacts.append(
            build_normalized_contact(
                company_name=company_name,
                source_file="BDD6",
                phone_standard=f,
                phone_mobile=m,
                civility=civility,
                first_name=fn,
                last_name=ln,
                job_title=job_title,
                raw_email=str(c5).strip() if c5 else None,
            )
        )

    return contacts


def parse_all_sources(data_dir: str) -> List[NormalizedContact]:
    """Parse all 6 BDD files in data_dir into a single list of NormalizedContact records."""
    all_contacts: List[NormalizedContact] = []

    bdd1_path = os.path.join(data_dir, "BDD1.xlsx")
    if os.path.exists(bdd1_path):
        all_contacts.extend(parse_bdd1(bdd1_path))

    bdd2_path = os.path.join(data_dir, "BDD2.xlsx")
    if os.path.exists(bdd2_path):
        all_contacts.extend(parse_bdd2(bdd2_path))

    bdd3_path = os.path.join(data_dir, "BDD3.xls")
    if os.path.exists(bdd3_path):
        all_contacts.extend(parse_bdd3_global(bdd3_path))
        all_contacts.extend(parse_bdd3_2023(bdd3_path))

    bdd4_path = os.path.join(data_dir, "BDD4.xlsx")
    if os.path.exists(bdd4_path):
        all_contacts.extend(parse_bdd4(bdd4_path))

    bdd5_path = os.path.join(data_dir, "BDD5.xlsx")
    if os.path.exists(bdd5_path):
        all_contacts.extend(parse_bdd5(bdd5_path))

    bdd6_path = os.path.join(data_dir, "BDD6.xls")
    if os.path.exists(bdd6_path):
        all_contacts.extend(parse_bdd6(bdd6_path))

    return all_contacts
