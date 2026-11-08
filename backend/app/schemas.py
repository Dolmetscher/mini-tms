from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["admin", "pm", "translator"]
JobStatus = Literal[
    "unassigned", "assigned", "declined", "accepted", "in_progress",
    "submitted", "rework", "completed", "canceled",
]
TranslationStatus = Literal["untranslated", "drafted", "translated"]
TBStatus = Literal["preferred", "admitted", "forbidden"]


class AuthoredModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth / orgs ---


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(AuthoredModel):
    id: int
    username: str


class MembershipOut(AuthoredModel):
    org_id: int
    org_name: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    memberships: list[MembershipOut]


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgOut(AuthoredModel):
    id: int
    name: str
    mt_allowed: bool


class OrgUpdate(BaseModel):
    mt_allowed: bool


class MemberCreate(BaseModel):
    username: str
    password: str = Field(min_length=4)
    role: Role = "translator"


class MemberRoleUpdate(BaseModel):
    role: Role


class MemberOut(AuthoredModel):
    id: int  # membership id
    user: UserOut
    role: str


# --- projects / documents / resources ---


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_lang: str = Field(min_length=2, max_length=8)
    target_langs: list[str] = Field(min_length=1)
    deadline: Optional[datetime] = None
    instructions: str = ""
    mt_enabled: bool = True


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    deadline: Optional[datetime] = None
    instructions: Optional[str] = None
    mt_enabled: Optional[bool] = None
    status: Optional[Literal["active", "archived", "canceled"]] = None


class UserRef(AuthoredModel):
    id: int
    username: str


class ProjectOut(AuthoredModel):
    id: int
    name: str
    source_lang: str
    target_langs: list[str]
    deadline: Optional[datetime]
    instructions: str
    mt_enabled: bool
    status: str
    manager: UserRef
    created_at: datetime


class DocumentOut(AuthoredModel):
    id: int
    project_id: int
    name: str
    format: str
    processing_status: str
    processing_error: Optional[str]
    processing_notes: list[str]
    created_at: datetime


class ResourceCreate(BaseModel):
    tm_id: Optional[int] = None
    tb_id: Optional[int] = None
    use_for_search: bool = True
    use_for_write: bool = False


class TMRef(AuthoredModel):
    id: int
    name: str
    source_lang: str
    target_lang: str


class TBRef(AuthoredModel):
    id: int
    name: str
    source_lang: str
    target_lang: str


class ResourceOut(AuthoredModel):
    id: int
    tm: Optional[TMRef] = None
    tb: Optional[TBRef] = None
    use_for_search: bool
    use_for_write: bool


# --- jobs / segments ---


class JobOut(AuthoredModel):
    id: int
    project_id: int
    project_name: str
    document_id: int
    document_name: str
    document_format: str
    source_lang: str
    target_lang: str
    status: str
    deadline: Optional[datetime]
    project_deadline: Optional[datetime]
    instructions: str
    is_overdue: bool
    assignee: Optional[UserRef]
    total_segments: int
    confirmed_segments: int
    total_words: int
    updated_at: datetime


class SegmentOut(AuthoredModel):
    id: int
    job_id: int
    position: int
    source_text: str
    target_text: str
    translation_status: str
    confirmed: bool
    is_mandatory: bool
    source_words: int
    updated_at: datetime


class JobDetail(JobOut):
    rework_comment: str
    decline_reason: str
    segments: list[SegmentOut]


class AssignRequest(BaseModel):
    assignee_id: int
    deadline: Optional[datetime] = None


class DeclineRequest(BaseModel):
    reason: str = Field(min_length=1)


class ReturnRequest(BaseModel):
    comment: str = Field(min_length=1)


class SegmentUpdate(BaseModel):
    target_text: Optional[str] = None
    confirmed: Optional[bool] = None
    # Оптимистичная блокировка (FR-04): клиент шлет updated_at, на котором основывался.
    base_updated_at: Optional[datetime] = None


class QAItem(BaseModel):
    segment_id: int
    position: int
    code: str
    severity: Literal["error", "warning"]
    message: str


class QAReport(BaseModel):
    can_submit: bool
    blocking_count: int
    warning_count: int
    items: list[QAItem]


class MTSuggestion(BaseModel):
    text: str
    provider: str


# --- леверидж TM: анализ и пред-перевод ---


class AnalysisCategory(BaseModel):
    code: str
    label: str
    segments: int
    words: int
    weighted: int


class AnalysisReport(BaseModel):
    job_id: int
    source_lang: str
    target_lang: str
    tm_names: list[str]
    segments: int
    words: int
    weighted_words: int
    categories: list[AnalysisCategory]


class PreTranslateRequest(BaseModel):
    """min_score=100 — только точные совпадения (по умолчанию);
    ниже — включать fuzzy вплоть до TMS_FUZZY_MIN_SCORE."""
    min_score: int = Field(default=100, ge=60, le=100)


class PreTranslateResult(BaseModel):
    filled_exact: int
    filled_fuzzy: int
    propagated: int
    skipped: int


# --- LQA (оценка качества) и комментарии к сегментам ---

LQACategory = Literal["accuracy", "terminology", "fluency", "style", "locale", "consistency"]
LQASeverity = Literal["minor", "major", "critical"]


class LQAErrorCreate(BaseModel):
    segment_id: int
    category: LQACategory
    severity: LQASeverity
    comment: str = Field(min_length=1, max_length=2000)


class LQAErrorOut(BaseModel):
    id: int
    job_id: int
    segment_id: int
    position: int
    category: str
    severity: str
    penalty: float
    comment: str
    author: UserRef
    created_at: datetime


class LQACategoryStat(BaseModel):
    category: str
    label: str
    count: int
    penalty: float


class LQAReport(BaseModel):
    job_id: int
    words: int
    errors: list[LQAErrorOut]
    error_count: int
    penalty_points: float
    score: float
    pass_score: float
    passed: bool
    by_category: list[LQACategoryStat]


class SegmentNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class SegmentNoteOut(BaseModel):
    id: int
    segment_id: int
    position: int
    author: UserRef
    body: str
    created_at: datetime


# --- TM / TB resources ---


class TMCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_lang: str = Field(min_length=2, max_length=8)
    target_lang: str = Field(min_length=2, max_length=8)


class TMOut(AuthoredModel):
    id: int
    name: str
    source_lang: str
    target_lang: str
    entry_count: int
    created_at: datetime


class TMEntryOut(AuthoredModel):
    id: int
    source_text: str
    target_text: str
    updated_at: datetime


class TMEntryUpsert(BaseModel):
    source_text: str = Field(min_length=1)
    target_text: str = Field(min_length=1)


class TMSuggestionOut(BaseModel):
    source_match: str
    target_suggestion: str
    match_percentage: int
    tm_name: str
    match_type: Literal["exact", "fuzzy", "concordance"]


class TBCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_lang: str = Field(min_length=2, max_length=8)
    target_lang: str = Field(min_length=2, max_length=8)


class TBOut(AuthoredModel):
    id: int
    name: str
    source_lang: str
    target_lang: str
    entry_count: int
    created_at: datetime


class TBEntryOut(AuthoredModel):
    id: int
    term: str
    translation: str
    note: str
    status: str


class TBEntryUpsert(BaseModel):
    term: str = Field(min_length=1)
    translation: str = Field(min_length=1)
    note: str = ""
    status: TBStatus = "preferred"


class TBHitOut(BaseModel):
    term: str
    translation: str
    note: str
    status: str
    tb_name: str


# --- notifications ---


class NotificationOut(AuthoredModel):
    id: int
    type: str
    payload: dict
    created_at: datetime
    read_at: Optional[datetime]
