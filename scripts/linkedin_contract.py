"""Versioned LinkedIn extraction contract; generates the JSON Schema locally."""
from datetime import datetime, timezone, timedelta
from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from scripts.enrichment_clients import linkedin_url

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Target(StrictModel):
    lead_id: int = Field(gt=0)
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    source_file: str
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

class Identity(StrictModel):
    status: Literal['CONFIRMED', 'NEEDS_REVIEW', 'NOT_FOUND', 'BLOCKED', 'REFERENCE']
    basis: list[str]
    alternative_urls: list[str]
    @field_validator('basis')
    @classmethod
    def evidence_nonempty(cls, v):
        if any(not x.strip() for x in v):
            raise ValueError('Identity evidence cannot be blank')
        return v

class Evidence(StrictModel):
    source_url: str
    @field_validator('source_url')
    @classmethod
    def source(cls, v):
        p = urlparse(v)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port:
            raise ValueError('An observed HTTPS source URL is required')
        return v

class Entry(Evidence):
    """Professional item; unused fields remain null rather than guessed."""
    title: str | None = None
    organization: str | None = None
    organization_url: str | None = None
    location: str | None = None
    description: str | None = None
    dates_displayed: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    url: str | None = None

class Section(StrictModel):
    status: Literal['COMPLETE', 'PARTIAL', 'NOT_OBSERVED', 'NOT_VISIBLE', 'BLOCKED']
    source_url: str | None
    text: str | None = None
    items: list[Entry] = Field(default_factory=list)
    limitation: str | None = None
    @model_validator(mode='after')
    def observed_source(self):
        if (self.text or self.items or self.status == 'COMPLETE') and not self.source_url:
            raise ValueError('Observed section requires a source URL')
        if self.status in ('NOT_OBSERVED', 'NOT_VISIBLE', 'BLOCKED') and (self.text or self.items):
            raise ValueError('Use PARTIAL for sections with some observed content')
        return self

class Profile(Evidence):
    full_name: str
    headline: str | None
    location: str | None
    company_displayed: str | None = None
    school_displayed: str | None = None
    followers_displayed: str | None = None
    top_skills: Section | None = None
    interests: Section | None = None
    about: Section
    experience: Section
    education: Section
    skills: Section
    certifications: Section
    languages: Section
    services: Section
    featured: Section
    projects: Section
    publications: Section
    volunteering: Section
    honors: Section
    recommendations: Section

class OriginalPost(Evidence):
    author: str | None
    text: str | None

class Post(Evidence):
    kind: Literal['POST', 'REPOST', 'REPOST_WITH_COMMENTARY', 'ARTICLE']
    author: str
    permalink: str | None
    date_displayed: str | None
    published_at: str | None
    date_precision: Literal['DAY', 'MONTH', 'YEAR', 'RELATIVE', 'UNKNOWN']
    text: str
    text_status: Literal['EXPANDED', 'TRUNCATED', 'NOT_VISIBLE']
    original: OriginalPost | None
    edited: bool | None = None
    reactions: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    @model_validator(mode='after')
    def original_distinct(self):
        if self.kind.startswith('REPOST') and self.original is None:
            raise ValueError('Reposts must distinguish the original author/content')
        return self

class Comment(Evidence):
    author: str
    text: str
    published_at: str | None
    date_precision: Literal['DAY','MONTH','YEAR','RELATIVE','UNKNOWN']
    date_displayed: str | None
    text_status: Literal['EXPANDED','TRUNCATED','NOT_VISIBLE']


class Activity(StrictModel):
    status: Literal['COMPLETE', 'PARTIAL', 'NO_VISIBLE_POSTS', 'NOT_OBSERVED', 'BLOCKED']
    source_url: str | None
    sorted_by: Literal['RECENT', 'UNKNOWN']
    exhaustive: Literal[False]
    posts: list[Post] = Field(max_length=3)
    comments: list[Comment] = Field(default_factory=list,max_length=3)
    limitation: str | None
    @model_validator(mode='after')
    def consistent(self):
        if self.status == 'NO_VISIBLE_POSTS' and self.posts:
            raise ValueError('Activity status contradicts posts')
        if self.status in ('NOT_OBSERVED', 'BLOCKED') and (self.posts or self.comments):
            raise ValueError('Activity status contradicts posts')
        if self.status == 'COMPLETE' and not (self.posts or self.comments):
            raise ValueError('Use NO_VISIBLE_POSTS for an inspected empty activity')
        return self

class Extraction(StrictModel):
    schema_version: Literal['1.0']
    extraction_id: str = Field(min_length=8, max_length=128)
    acquisition_method: Literal['linkedin_mcp', 'codex_chrome']
    purpose: Literal['CONTACT', 'REFERENCE']
    target: Target | None
    observed_at: str
    identity: Identity
    linkedin_url: str | None
    profile: Profile | None
    activity: Activity
    limitations: list[str]

    @field_validator('observed_at')
    @classmethod
    def timestamp(cls, v):
        d = datetime.fromisoformat(v.replace('Z', '+00:00'))
        if not d.tzinfo or d > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError('Observation must be timezone-aware and not in the future')
        return v

    @field_validator('linkedin_url')
    @classmethod
    def personal_url(cls, v):
        if v is not None and linkedin_url(v) != v:
            raise ValueError('Canonical personal LinkedIn URL required')
        return v

    @model_validator(mode='after')
    def coherent(self):
        if (self.purpose == 'CONTACT') != (self.target is not None):
            raise ValueError('CONTACT needs a target; REFERENCE must not target a lead')
        if self.purpose == 'REFERENCE' and self.identity.status != 'REFERENCE':
            raise ValueError('Reference is not an identity confirmation')
        if self.identity.status == 'CONFIRMED':
            if not self.linkedin_url or not self.profile or len(self.identity.basis) < 2 or self.identity.alternative_urls:
                raise ValueError('Confirmed identity requires profile, URL, evidence and no unresolved alternatives')
        if self.profile and linkedin_url(self.profile.source_url) != self.linkedin_url:
            raise ValueError('Profile source must match the personal URL')
        return self
