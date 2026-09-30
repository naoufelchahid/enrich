# Plan d'Implémentation : Campagne Mailing & Intégration Twenty CRM (Contexte Lab)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Développer et exécuter le pipeline d'ingestion ETL des 6 bases de données de contacts, la validation anti-bounce (DNS/MX et API), l'intégration dans Twenty CRM avec gestion des statuts "À enrichir", la configuration DNS de délivrabilité (`sales.contextelab.ma`), et l'orchestrateur de warm-up (100 emails/jour) avec 5 variantes d'emails axées sur le questionnement stratégique des dirigeants.

**Architecture:** Pipeline modulaire en Python composé d'adaptateurs ETL spécialisés (`etl_normalize.py`), d'un validateur d'hygiène et anti-bounce à deux passes (`antibounce_verifier.py`), d'un connecteur d'API Twenty CRM (`twenty_client.py`), d'un moteur de templates avec 5 variantes stratégiques (`templates.py`), d'un orchestrateur d'envoi respectant le warm-up (`campaign_dispatcher.py`), et d'un récepteur de webhooks Resend (`webhook_listener.py`).

**Tech Stack:** Python 3.10+, openpyxl, xlrd, dnspython, pydantic, requests, pytest, Docker / Docker Compose (Twenty CRM v2.26.0), Resend API.

## Global Constraints

- Domaine d'envoi : `sales.contextelab.ma` (sous-domaine isolé de réputation)
- Expéditeur (`From`) : `Naoufel Chahid <chahid.naoufel@sales.contextelab.ma>`
- Adresse de réponse (`Reply-To`) : `n.chahid@contextelab.ma`
- Warm-up Semaine 1 : Plafond strict de 100 emails/jour pendant 7 jours, étalés entre 9h00 et 17h30 (12 à 15 emails/h max)
- Règle Anti-Bounce : Zéro email invalide/rebond dans Twenty CRM. Tout email problématique est immédiatement purgé du contact et le statut passe à `A_ENRICHIR_EMAIL_INVALIDE`.
- Fichiers sources traités : `bdd/BDD1.xlsx`, `bdd/BDD2.xlsx`, `bdd/BDD3.xls`, `bdd/BDD4.xlsx`, `bdd/BDD5.xlsx`, `bdd/BDD6.xls` (`salaires_consolide.csv` est ignoré).

---

### Task 1: Environnement, Dépendances & Schéma Canonique Pydantic

**Files:**
- Create: `requirements.txt`
- Create: `scripts/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `NormalizedContact`, `CompanyRecord`, `EnrichmentStatusEnum`, `CampaignStatusEnum`, `EmailValidationStatusEnum` dans `scripts/models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
import pytest
from scripts.models import (
    NormalizedContact,
    CompanyRecord,
    EnrichmentStatusEnum,
    CampaignStatusEnum,
    EmailValidationStatusEnum,
)

def test_normalized_contact_valid():
    contact = NormalizedContact(
        company_name="Contexte Lab",
        activity_sector="Conseil IA",
        website="https://www.contextelab.ma",
        city="Casablanca",
        address="82 bd Abdelmoumen",
        phone_standard="0522000000",
        phone_mobile="+212661000000",
        civility="M.",
        first_name="Naoufel",
        last_name="Chahid",
        job_title="Directeur Général",
        is_dirigeant=True,
        raw_email="n.chahid@contextelab.ma",
        email="n.chahid@contextelab.ma",
        email_validation_status=EmailValidationStatusEnum.DELIVRABLE,
        source_file="BDD1",
        enrichment_status=EnrichmentStatusEnum.COMPLET,
        missing_fields=[],
    )
    assert contact.is_dirigeant is True
    assert contact.email == "n.chahid@contextelab.ma"

def test_contact_purged_email_for_enrichment():
    contact = NormalizedContact(
        company_name="Test SARL",
        activity_sector="BTP",
        city="Casablanca",
        first_name="Karim",
        last_name="Bennani",
        job_title="Gérant",
        is_dirigeant=True,
        raw_email="invalid-bounce@test.ma",
        email=None,
        email_validation_status=EmailValidationStatusEnum.BOUNCED,
        source_file="BDD2",
        enrichment_status=EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE,
        missing_fields=["email"],
    )
    assert contact.email is None
    assert contact.enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'scripts')

- [ ] **Step 3: Write dependencies & minimal implementation**

```txt
# requirements.txt
pydantic>=2.0.0
openpyxl>=3.1.0
xlrd>=2.0.1
dnspython>=2.4.0
requests>=2.31.0
pytest>=8.0.0
```

```python
# scripts/models.py
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class EnrichmentStatusEnum(str, Enum):
    COMPLET = "COMPLET"
    A_ENRICHIR_EMAIL_MANQUANT = "A_ENRICHIR_EMAIL_MANQUANT"
    A_ENRICHIR_EMAIL_INVALIDE = "A_ENRICHIR_EMAIL_INVALIDE"
    A_ENRICHIR_DONNEES_INCOMPLETES = "A_ENRICHIR_DONNEES_INCOMPLETES"

class CampaignStatusEnum(str, Enum):
    READY_TO_MAIL = "READY_TO_MAIL"
    WARMUP_BATCH_1 = "WARMUP_BATCH_1"
    EMAIL_SENT = "EMAIL_SENT"
    EMAIL_DELIVERED = "EMAIL_DELIVERED"
    REPLIED = "REPLIED"
    BOUNCED = "BOUNCED"
    UNSUBSCRIBED = "UNSUBSCRIBED"

class EmailValidationStatusEnum(str, Enum):
    DELIVRABLE = "DELIVRABLE"
    INVALID = "INVALID"
    BOUNCED = "BOUNCED"
    MISSING = "MISSING"

class NormalizedContact(BaseModel):
    company_name: str
    activity_sector: Optional[str] = None
    website: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    phone_standard: Optional[str] = None
    phone_mobile: Optional[str] = None
    civility: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    job_title: Optional[str] = None
    is_dirigeant: bool = False
    raw_email: Optional[str] = None
    email: Optional[str] = None
    email_validation_status: EmailValidationStatusEnum = EmailValidationStatusEnum.MISSING
    source_file: str
    enrichment_status: EnrichmentStatusEnum = EnrichmentStatusEnum.A_ENRICHIR_DONNEES_INCOMPLETES
    missing_fields: List[str] = Field(default_factory=list)

class CompanyRecord(BaseModel):
    name: str
    activity_sector: Optional[str] = None
    domain_name: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    phone_standard: Optional[str] = None
    source_file: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -r requirements.txt && pytest tests/test_models.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt scripts/models.py tests/test_models.py
git commit -m "feat(models): add canonical Pydantic schemas and enums"
```

---

### Task 2: Adaptateurs ETL et Détection des Dirigeants (`etl_normalize.py`)

**Files:**
- Create: `scripts/etl_normalize.py`
- Test: `tests/test_etl_normalize.py`

**Interfaces:**
- Consumes: `NormalizedContact`, `EnrichmentStatusEnum` from `scripts/models.py`
- Produces: `is_dirigeant_title(title: str) -> bool`, `normalize_phone(raw: str) -> tuple[Optional[str], Optional[str]]`, `parse_all_sources(data_dir: str) -> List[NormalizedContact]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_etl_normalize.py
from scripts.etl_normalize import is_dirigeant_title, normalize_phone, split_name

def test_is_dirigeant_detection():
    assert is_dirigeant_title("Directeur Général") is True
    assert is_dirigeant_title("P.D.G.") is True
    assert is_dirigeant_title("Gérant") is True
    assert is_dirigeant_title("Administrateur Délégué") is True
    assert is_dirigeant_title("Managing Director") is True
    assert is_dirigeant_title("Stagiaire") is False
    assert is_dirigeant_title("Chef de projet informatique") is False
    assert is_dirigeant_title(None) is False

def test_normalize_phone():
    # 05 = standard fixe, 06/07 = mobile
    fixe, mobile = normalize_phone("05 22 24 53 29")
    assert fixe == "+212522245329"
    assert mobile is None

    fixe, mobile = normalize_phone("06 61 48 47 22")
    assert fixe is None
    assert mobile == "+212661484722"

def test_split_name():
    civ, first, last = split_name("M.  Karim  Belaous Abid")
    assert civ == "M."
    assert first == "Karim"
    assert last == "Belaous Abid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_etl_normalize.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'scripts.etl_normalize')

- [ ] **Step 3: Write minimal implementation in `scripts/etl_normalize.py`**

Implement `is_dirigeant_title`, `normalize_phone`, `split_name`, and adapters for BDD1, BDD2, BDD3, BDD4, BDD5, BDD6 mapping raw rows to `NormalizedContact`.

```python
# scripts/etl_normalize.py
import re
from typing import Optional, Tuple, List
import openpyxl, xlrd
from scripts.models import NormalizedContact, EnrichmentStatusEnum, EmailValidationStatusEnum

DIRIGEANT_REGEX = re.compile(
    r"\b(p\.?d\.?g\.?|directeur\s+général|directrice\s+générale|dg|gérant|gérante|président|présidente|fondateur|fondatrice|administrateur\s+délégué|administrateur\s+directeur\s+général|managing\s+director|ceo|directeur\s+associé|country\s+manager|directeur\s+exécutif|chairman)\b",
    re.IGNORECASE,
)

def is_dirigeant_title(title: Optional[str]) -> bool:
    if not title:
        return False
    return bool(DIRIGEANT_REGEX.search(str(title).strip()))

def normalize_phone(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not raw:
        return None, None
    digits = re.sub(r"[^\d+]", "", str(raw).strip())
    if not digits:
        return None, None
    if digits.startswith("0"):
        digits = "+212" + digits[1:]
    elif digits.startswith("212") and not digits.startswith("+212"):
        digits = "+" + digits
    
    # Classification fixe (+2125...) vs mobile (+2126... / +2127...)
    if "+2125" in digits:
        return digits, None
    elif "+2126" in digits or "+2127" in digits:
        return None, digits
    return digits, None

def split_name(full: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if not full:
        return None, None, None
    cleaned = re.sub(r"\s+", " ", str(full).strip())
    civ = None
    for prefix in ["Mme.", "Mme", "M.", "M "]:
        if cleaned.startswith(prefix):
            civ = prefix.strip()
            cleaned = cleaned[len(prefix):].strip()
            break
    parts = cleaned.split(" ")
    if len(parts) == 1:
        return civ, parts[0], ""
    return civ, parts[0], " ".join(parts[1:])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_etl_normalize.py -v`  
Expected: PASS

- [ ] **Step 5: Implement comprehensive parsing of all 6 sources and write integration test**

Add function `parse_all_sources(data_dir: str) -> List[NormalizedContact]` and verify that running it over `bdd/` extracts thousands of records with properly identified `is_dirigeant` flags and missing field listings.

- [ ] **Step 6: Commit**

```bash
git add scripts/etl_normalize.py tests/test_etl_normalize.py
git commit -m "feat(etl): implement normalization adapters for 6 BDD files and executive detection"
```

---

### Task 3: Système Anti-Bounce en Deux Passes (`antibounce_verifier.py`)

**Files:**
- Create: `scripts/antibounce_verifier.py`
- Test: `tests/test_antibounce.py`

**Interfaces:**
- Consumes: `NormalizedContact` from `scripts/models.py`
- Produces: `verify_contact_list(contacts: List[NormalizedContact], api_key: Optional[str] = None) -> Tuple[List[NormalizedContact], List[NormalizedContact]]` returning `(valid_for_mailing, purged_to_enrich)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_antibounce.py
from scripts.antibounce_verifier import (
    validate_email_syntax,
    check_dns_mx,
    process_antibounce_pass1,
)
from scripts.models import NormalizedContact, EnrichmentStatusEnum, EmailValidationStatusEnum

def test_validate_email_syntax():
    assert validate_email_syntax("chahid.naoufel@contextelab.ma") is True
    assert validate_email_syntax("invalid..syntax@domain.com") is False
    assert validate_email_syntax("noatsign.com") is False
    assert validate_email_syntax("") is False
    assert validate_email_syntax(None) is False

def test_check_dns_mx():
    # contextelab.ma or google.com should have MX
    assert check_dns_mx("google.com") is True
    # Non-existent domain should return False
    assert check_dns_mx("non-existent-domain-xyz-12345678.ma") is False

def test_process_antibounce_purges_invalid():
    bad_contact = NormalizedContact(
        company_name="Fictive",
        first_name="Ali",
        last_name="Test",
        raw_email="ali@non-existent-domain-xyz-12345678.ma",
        email="ali@non-existent-domain-xyz-12345678.ma",
        source_file="BDD1",
        is_dirigeant=True,
    )
    processed = process_antibounce_pass1([bad_contact])[0]
    # Invalid MX must purge email and set A_ENRICHIR_EMAIL_INVALIDE
    assert processed.email is None
    assert processed.enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
    assert processed.email_validation_status == EmailValidationStatusEnum.INVALID
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_antibounce.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'scripts.antibounce_verifier')

- [ ] **Step 3: Write minimal implementation in `scripts/antibounce_verifier.py`**

Implement syntax validation, DNS MX resolution with caching, and contact list processing.

```python
# scripts/antibounce_verifier.py
import re
from typing import List, Optional
import dns.resolver
from scripts.models import NormalizedContact, EnrichmentStatusEnum, EmailValidationStatusEnum

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
MX_CACHE = {}

def validate_email_syntax(email: Optional[str]) -> bool:
    if not email:
        return False
    clean = email.strip()
    if ".." in clean:
        return False
    return bool(EMAIL_REGEX.match(clean))

def check_dns_mx(domain: str) -> bool:
    domain = domain.lower().strip()
    if domain in MX_CACHE:
        return MX_CACHE[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=3.0)
        has_mx = len(answers) > 0
        MX_CACHE[domain] = has_mx
        return has_mx
    except Exception:
        MX_CACHE[domain] = False
        return False

def process_antibounce_pass1(contacts: List[NormalizedContact]) -> List[NormalizedContact]:
    seen_emails = set()
    output = []
    for c in contacts:
        if not c.raw_email:
            c.email = None
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT
            c.email_validation_status = EmailValidationStatusEnum.MISSING
            output.append(c)
            continue
            
        clean_email = c.raw_email.strip().lower()
        if not validate_email_syntax(clean_email):
            c.email = None
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
            c.email_validation_status = EmailValidationStatusEnum.INVALID
            output.append(c)
            continue
            
        if clean_email in seen_emails:
            # Doublon rejeté
            continue
        seen_emails.add(clean_email)
        
        domain = clean_email.split("@")[-1]
        if not check_dns_mx(domain):
            c.email = None
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
            c.email_validation_status = EmailValidationStatusEnum.INVALID
            output.append(c)
            continue
            
        c.email = clean_email
        c.email_validation_status = EmailValidationStatusEnum.DELIVRABLE
        c.enrichment_status = EnrichmentStatusEnum.COMPLET
        output.append(c)
    return output
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_antibounce.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/antibounce_verifier.py tests/test_antibounce.py
git commit -m "feat(antibounce): add two-pass verification with MX resolution and email purging"
```

---

### Task 4: Connecteur Twenty CRM (`twenty_client.py`)

**Files:**
- Create: `scripts/twenty_client.py`
- Test: `tests/test_twenty_client.py`

**Interfaces:**
- Consumes: `NormalizedContact` from `scripts/models.py`
- Produces: `TwentyClient(base_url, api_key)` with methods:
  - `upsert_company(company: CompanyRecord) -> str` (returns companyId)
  - `upsert_person(contact: NormalizedContact, company_id: Optional[str]) -> str` (returns personId)
  - `purge_bounced_email(person_id: str)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_twenty_client.py
import responses
from scripts.twenty_client import TwentyClient
from scripts.models import NormalizedContact, EnrichmentStatusEnum, EmailValidationStatusEnum

@responses.activate
def test_upsert_person_with_clean_email():
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"data": {"createPerson": {"id": "person-123"}}},
        status=201,
    )
    client = TwentyClient(base_url="http://localhost:3005", api_key="test-key")
    contact = NormalizedContact(
        company_name="Atlas SARL",
        first_name="Hassan",
        last_name="Tahiri",
        job_title="DG",
        is_dirigeant=True,
        email="hassan@atlas.ma",
        source_file="BDD3",
        enrichment_status=EnrichmentStatusEnum.COMPLET,
    )
    person_id = client.upsert_person(contact, company_id="comp-1")
    assert person_id == "person-123"

@responses.activate
def test_upsert_person_purged_for_enrichment():
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"data": {"createPerson": {"id": "person-456"}}},
        status=201,
    )
    client = TwentyClient(base_url="http://localhost:3005", api_key="test-key")
    contact = NormalizedContact(
        company_name="Atlas SARL",
        first_name="Hassan",
        last_name="Tahiri",
        job_title="DG",
        is_dirigeant=True,
        email=None,  # Purged
        source_file="BDD1",
        enrichment_status=EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE,
    )
    person_id = client.upsert_person(contact, company_id="comp-1")
    assert person_id == "person-456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_twenty_client.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'responses')

- [ ] **Step 3: Add `responses` to requirements and implement `TwentyClient`**

```python
# scripts/twenty_client.py
import requests
from typing import Optional, Dict, Any
from scripts.models import NormalizedContact, CompanyRecord, EnrichmentStatusEnum

class TwentyClient:
    def __init__(self, base_url: str = "http://localhost:3005", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        }

    def upsert_company(self, company: CompanyRecord) -> str:
        payload = {
            "name": company.name,
            "domainName": company.domain_name or "",
            "address": company.address or "",
            "city": company.city or "",
        }
        res = requests.post(f"{self.base_url}/rest/companies", json=payload, headers=self.headers)
        res.raise_for_status()
        data = res.json()
        return data.get("data", {}).get("createCompany", {}).get("id", "dummy-comp-id")

    def upsert_person(self, contact: NormalizedContact, company_id: Optional[str] = None) -> str:
        payload: Dict[str, Any] = {
            "name": {
                "firstName": contact.first_name or "",
                "lastName": contact.last_name or "",
            },
            "jobTitle": contact.job_title or "",
            "companyId": company_id,
        }
        # N'injecter l'email QUE s'il est validé
        if contact.email:
            payload["emails"] = {"primaryEmail": contact.email}
        else:
            payload["emails"] = None

        if contact.phone_mobile:
            payload["phones"] = {"primaryPhone": contact.phone_mobile}

        res = requests.post(f"{self.base_url}/rest/people", json=payload, headers=self.headers)
        res.raise_for_status()
        data = res.json()
        return data.get("data", {}).get("createPerson", {}).get("id", "dummy-person-id")

    def purge_bounced_email(self, person_id: str):
        payload = {
            "emails": None,
        }
        res = requests.patch(f"{self.base_url}/rest/people/{person_id}", json=payload, headers=self.headers)
        res.raise_for_status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install responses && pytest tests/test_twenty_client.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/twenty_client.py tests/test_twenty_client.py
git commit -m "feat(crm): implement Twenty CRM REST API connector with email purging"
```

---

### Task 5: Moteur de Templates & Copywriting Stratégique (`templates.py`)

**Files:**
- Create: `scripts/templates.py`
- Test: `tests/test_templates.py`

**Interfaces:**
- Produces: `render_email_template(variant: str, contact: NormalizedContact) -> tuple[str, str]` (returns `(subject, html_body)`) for variants `'A'`, `'B'`, `'C'`, `'D'`, `'E'`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_templates.py
from scripts.templates import render_email_template, AVAILABLE_VARIANTS
from scripts.models import NormalizedContact

def test_all_variants_exist():
    assert set(AVAILABLE_VARIANTS) == {"A", "B", "C", "D", "E"}

def test_template_existential_doubt_hook():
    contact = NormalizedContact(
        company_name="Maroc Industrie",
        first_name="Adil",
        last_name="Bennani",
        job_title="Directeur Général",
        email="a.bennani@marocindustrie.ma",
        source_file="BDD3",
        is_dirigeant=True,
    )
    for v in AVAILABLE_VARIANTS:
        subject, body = render_email_template(v, contact)
        assert len(subject) > 10
        assert "Adil" in body or "Bennani" in body
        assert "Contexte Lab" in body
        assert "Formation" in body or "formation" in body
        # Vérification du lien de désinscription
        assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in body or "désinscri" in body.lower()
        # Poids inférieur à 100 Ko
        assert len(body.encode("utf-8")) < 100 * 1024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_templates.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'scripts.templates')

- [ ] **Step 3: Write minimal implementation in `scripts/templates.py`**

Implement the 5 variants in HTML minimaliste (< 50 Ko), with the core psychological hook: *Questionnement sur l'accélération de l'IA, le risque d'inaction sur la compétitivité, et la formation comme point de départ pour décider avec clarté*.

```python
# scripts/templates.py
from typing import Tuple
from scripts.models import NormalizedContact

AVAILABLE_VARIANTS = ["A", "B", "C", "D", "E"]

def render_email_template(variant: str, contact: NormalizedContact) -> Tuple[str, str]:
    nom_dirigeant = contact.last_name or "Madame, Monsieur"
    if contact.first_name:
        salutation = f"Bonjour M. {contact.last_name}" if contact.civility == "M." else f"Bonjour {contact.first_name}"
    else:
        salutation = "Bonjour"

    entreprise = contact.company_name or "votre entreprise"
    secteur = contact.activity_sector or "votre secteur"

    if variant == "A":
        # Variante A : Le Décalage de Compétitivité
        subject = f"{entreprise} face à l'accélération de l'IA au Maroc"
        content = f"""
        <p>{salutation},</p>
        <p>L’intelligence artificielle n’est plus un sujet de prospective pour 2030 : elle commence déjà à modifier les marges, la productivité et la vitesse d’exécution dans {secteur}.</p>
        <p>Face à ce rythme qui s'accélère, une question stratégique se pose pour tout dirigeant : <strong>est-ce que votre entreprise prépare activement cette transition, ou risquez-vous de voir des concurrents creuser l'écart de compétitivité ?</strong></p>
        <p>Le piège actuel est de vouloir acheter des outils au hasard. Chez <strong>Contexte Lab</strong>, nous pensons que le seul point de départ sain est <strong>la formation de la direction</strong> : acquérir les repères concrets pour distinguer le bruit médiatique des vraies opportunités de rentabilité.</p>
        <p>Une fois ces repères posés, vous pouvez décider avec clarté de vos prochaines étapes.</p>
        <p>Seriez-vous ouvert à échanger 10 minutes cette semaine ? Répondez simplement à cet email pour me dire si le sujet fait écho à vos priorités actuelles.</p>
        """
    elif variant == "B":
        # Variante B : Subir le Rythme ou Fixer la Trajectoire
        subject = "IA : subir le rythme ou fixer la trajectoire ?"
        content = f"""
        <p>{salutation},</p>
        <p>L'accélération des outils IA au Maroc est désormais une réalité de terrain. Dans la plupart des entreprises, les équipes commencent à tester des solutions dans leur coin, souvent sans méthode ni contrôle.</p>
        <p>Pour un dirigeant, le risque n'est pas seulement technologique : <strong>c'est la perte de maîtrise et de compétitivité face à ceux qui structurent leur démarche.</strong></p>
        <p>Notre parcours <em>Formation IA pour Dirigeants</em> a été conçu pour cela : vous donner, sans jargon, les clés pour arbitrer, cadrer les risques et décider où investir utilement dans vos processus.</p>
        <p>Quel est votre sentiment actuel sur la maturité de {entreprise} face à cette transition ?</p>
        """
    elif variant == "C":
        # Variante C : L'Illusion du Temps
        subject = "Avons-nous encore le temps d'attendre sur l'IA ?"
        content = f"""
        <p>{salutation},</p>
        <p>Dans chaque tournant technologique, l'erreur classique est de croire qu'on a le temps de voir venir. Mais la courbe de l'IA est exponentielle, et l'inaction est aujourd'hui le choix le plus coûteux.</p>
        <p>Pensez-vous que {entreprise} dispose aujourd'hui d'une vision claire pour tirer parti de cette vague, ou le sujet reste-t-il encore flou au sein de votre comité de direction ?</p>
        <p>La formation est le premier pas pragmatique : elle vous permet de faire l'inventaire de vos données et processus avant de vous lancer, pour décider avec discernement.</p>
        <p>Si vous souhaitez en discuter librement, vous pouvez me répondre directement ici.</p>
        """
    elif variant == "D":
        # Variante D : De Dirigeant à Dirigeant
        subject = f"Échange entre dirigeants : intégrer l'IA dans {secteur}"
        content = f"""
        <p>{salutation},</p>
        <p>En tant que dirigeant d'agence IA à Casablanca, je constate quotidiennement le même fossé : beaucoup d'effets d'annonce, mais très peu de méthodes adaptées à la réalité économique de nos entreprises marocaines.</p>
        <p>Comment intégrer l'IA avant qu'elle ne devienne un handicap de compétitivité pour {entreprise} ?</p>
        <p>En formant d'abord ceux qui décident. Nous aidons les dirigeants à identifier 2 ou 3 cas d'usage à fort retour sur investissement dans leur métier.</p>
        <p>Seriez-vous disponible pour un court appel de courtoisie de 10 minutes ?</p>
        """
    else: # E
        # Variante E : Le Cadrage Décisionnel 30 min
        subject = f"Quel sujet IA mérite vos 30 prochaines minutes, pour {entreprise} ?"
        content = f"""
        <p>{salutation},</p>
        <p>Si vos équipes ou votre conseil d'administration vous demandaient demain d'arbitrer sur vos investissements IA, sauriez-vous exactement où se trouvent les gains réels et où sont les risques ?</p>
        <p>Avant d'engager des budgets ou des prestataires, <strong>la formation vous apporte la lucidité stratégique</strong> : poser les limites, comprendre ce qui est faisable et piloter vos prochaines décisions avec assurance.</p>
        <p>Je vous propose un échange de cadrage gratuit de 30 minutes pour faire le point sur votre secteur : <a href="https://calendly.com/chahid-naoufel/echange-autour-de-vos-enjeux-commerciaux-et-ia">Réserver un créneau</a> ou répondez directement à cet email.</p>
        """

    footer = f"""
    <p>Bien cordialement,</p>
    <p><strong>Naoufel Chahid</strong><br>
    Fondateur — Contexte Lab SARL AU<br>
    Casablanca, Maroc<br>
    <a href="https://www.contextelab.ma">www.contextelab.ma</a></p>
    <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 24px 0;" />
    <p style="font-size: 11px; color: #888;">
      Vous recevez cet email car vous êtes répertorié comme dirigeant d'entreprise au Maroc. 
      Pour ne plus recevoir nos communications, <a href="{{{{{{RESEND_UNSUBSCRIBE_URL}}}}}}">cliquez ici pour vous désinscrire</a>.
    </p>
    """
    html_body = f"<!DOCTYPE html><html><body style='font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #222;'>{content}{footer}</body></html>"
    return subject, html_body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_templates.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/templates.py tests/test_templates.py
git commit -m "feat(templates): implement 5 strategic email variants targeting executive decision-making"
```

---

### Task 6: Orchestrateur de Warm-up & Dispatcher (`campaign_dispatcher.py`)

**Files:**
- Create: `scripts/campaign_dispatcher.py`
- Test: `tests/test_campaign_dispatcher.py`

**Interfaces:**
- Consumes: `TwentyClient`, `render_email_template`, `NormalizedContact`
- Produces: CLI script with `--dry-run`, `--send`, `--batch-size 100`, pacing 1 email / 4 min, circuit breaker.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_campaign_dispatcher.py
from scripts.campaign_dispatcher import CampaignDispatcher
from scripts.models import NormalizedContact, CampaignStatusEnum

def test_dispatcher_dry_run_allocation():
    dispatcher = CampaignDispatcher(resend_api_key="mock-key", dry_run=True)
    mock_contacts = [
        NormalizedContact(
            company_name=f"Societe {i}",
            first_name=f"Prenom{i}",
            last_name=f"Nom{i}",
            email=f"contact{i}@societe{i}.ma",
            source_file="BDD3",
            is_dirigeant=True,
        ) for i in range(10)
    ]
    results = dispatcher.prepare_batch(mock_contacts, batch_size=5)
    assert len(results) == 5
    # Rotation des 5 variantes A, B, C, D, E
    variants_used = [r["variant"] for r in results]
    assert variants_used == ["A", "B", "C", "D", "E"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_campaign_dispatcher.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'scripts.campaign_dispatcher')

- [ ] **Step 3: Implement `CampaignDispatcher` in `scripts/campaign_dispatcher.py`**

Include Resend API calls (`POST https://api.resend.com/emails`), rate-limiting sleep, logging, dry-run simulation, and circuit-breaker.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_campaign_dispatcher.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/campaign_dispatcher.py tests/test_campaign_dispatcher.py
git commit -m "feat(dispatcher): implement warm-up campaign dispatcher with 100/day batching and dry-run"
```

---

### Task 7: Récepteur Webhook & Gestion des Rebonds (`webhook_listener.py`)

**Files:**
- Create: `scripts/webhook_listener.py`
- Test: `tests/test_webhook_listener.py`

**Interfaces:**
- Produces: Webhook server handling `email.delivered`, `email.bounced`, `email.complained` and calling `TwentyClient.purge_bounced_email(person_id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webhook_listener.py
from scripts.webhook_listener import handle_resend_event

def test_handle_bounced_event():
    event = {
        "type": "email.bounced",
        "data": {
            "to": ["bounced-user@example.com"],
            "bounce": {"message": "mailbox unavailable"},
            "tags": [{"name": "person_id", "value": "person-999"}],
        }
    }
    action = handle_resend_event(event)
    assert action["status"] == "PURGED"
    assert action["person_id"] == "person-999"
    assert "bounced-user@example.com" in action["email"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webhook_listener.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'scripts.webhook_listener')

- [ ] **Step 3: Implement `handle_resend_event` and server in `scripts/webhook_listener.py`**

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webhook_listener.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/webhook_listener.py tests/test_webhook_listener.py
git commit -m "feat(webhook): add Resend webhook receiver and automatic bounce email purging"
```

---

### Task 8: Fiche de Configuration DNS & Guide Déploiement

**Files:**
- Create: `docs/SETUP_RESEND_DNS.md`
- Test: Verification checklist script `scripts/verify_dns_setup.py`

- [ ] **Step 1: Document the exact DNS records for `sales.contextelab.ma`**
- [ ] **Step 2: Provide Python DNS verification script using `dnspython` to check SPF, DKIM and DMARC**
- [ ] **Step 3: Commit**

```bash
git add docs/SETUP_RESEND_DNS.md scripts/verify_dns_setup.py
git commit -m "docs(dns): add DNS setup guide and automated verification tool for sales.contextelab.ma"
```
