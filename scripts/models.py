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
    company_domain: Optional[str] = None
    company_domain_source: Optional[str] = None
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
    linkedin_url: Optional[str] = None
    recent_posts: Optional[str] = None
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
