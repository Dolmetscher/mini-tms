from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Все метки времени — наивный UTC (SQLite не хранит tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    # Версия пароля: попадает в JWT; смена пароля инкрементирует — все
    # ранее выданные токены отзываются (stateless-ревокация).
    pwd_ver: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Политика безопасности: разрешена ли передача текста внешнему MT-провайдеру (FR-08).
    mt_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="org")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_membership"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # admin | pm | translator

    org: Mapped["Organization"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_lang: Mapped[str] = mapped_column(String(8))
    target_langs: Mapped[list] = mapped_column(JSON, default=list)  # на каждый — отдельное задание
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    instructions: Mapped[str] = mapped_column(Text, default="")
    mt_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    manager_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived | canceled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    manager: Mapped["User"] = relationship()
    jobs: Mapped[list["Job"]] = relationship(back_populates="project")


class TM(Base):
    __tablename__ = "tms"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_lang: Mapped[str] = mapped_column(String(8))
    target_lang: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entries: Mapped[list["TMEntry"]] = relationship(
        back_populates="tm", cascade="all, delete-orphan"
    )


class TMEntry(Base):
    __tablename__ = "tm_entries"
    __table_args__ = (UniqueConstraint("tm_id", "source_text", name="uq_tm_entry_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tm_id: Mapped[int] = mapped_column(ForeignKey("tms.id"), index=True)
    source_text: Mapped[str] = mapped_column(Text)
    target_text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    tm: Mapped["TM"] = relationship(back_populates="entries")


class TB(Base):
    __tablename__ = "tbs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_lang: Mapped[str] = mapped_column(String(8))
    target_lang: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entries: Mapped[list["TBEntry"]] = relationship(
        back_populates="tb", cascade="all, delete-orphan"
    )


class TBEntry(Base):
    __tablename__ = "tb_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    tb_id: Mapped[int] = mapped_column(ForeignKey("tbs.id"), index=True)
    term: Mapped[str] = mapped_column(Text)  # термин на исходном языке
    translation: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="preferred")  # preferred | admitted | forbidden

    tb: Mapped["TB"] = relationship(back_populates="entries")


class ProjectResource(Base):
    __tablename__ = "project_resources"
    __table_args__ = (UniqueConstraint("project_id", "tm_id", "tb_id", name="uq_project_resource"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    tm_id: Mapped[int | None] = mapped_column(ForeignKey("tms.id"), nullable=True, index=True)
    tb_id: Mapped[int | None] = mapped_column(ForeignKey("tbs.id"), nullable=True, index=True)
    use_for_search: Mapped[bool] = mapped_column(Boolean, default=True)
    # Только для TM: запись подтвержденных переводов (FR-12).
    use_for_write: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(8))  # txt | docx | xliff
    storage_path: Mapped[str] = mapped_column(Text)
    # Обработка асинхронная (FR-10): клиент опрашивает статус.
    processing_status: Mapped[str] = mapped_column(String(16), default="processing")  # processing | ready | failed
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Предупреждения о неподдерживаемых элементах, выявленных при импорте (§9).
    processing_notes: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    target_lang: Mapped[str] = mapped_column(String(8))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # Индивидуальный дедлайн задания (FR-11); пусто — берется дедлайн проекта.
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Жизненный цикл §7: unassigned | assigned | declined | accepted | in_progress
    # | submitted | rework | completed | canceled. Просрочка — признак, не статус.
    status: Mapped[str] = mapped_column(String(16), default="unassigned", index=True)
    decline_reason: Mapped[str] = mapped_column(Text, default="")
    rework_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped["Project"] = relationship(back_populates="jobs")
    document: Mapped["Document"] = relationship()
    assignee: Mapped["User | None"] = relationship()
    segments: Mapped[list["Segment"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Segment.position"
    )


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    source_text: Mapped[str] = mapped_column(Text)
    target_text: Mapped[str] = mapped_column(Text, default="")
    translation_status: Mapped[str] = mapped_column(String(16), default="untranslated")  # untranslated | drafted | translated
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    source_words: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    job: Mapped["Job"] = relationship(back_populates="segments")


class SegmentNote(Base):
    """Комментарий к сегменту: диалог переводчик ↔ менеджер/ревьюер.

    Практика CAT-инструментов (Trados/memoQ comments): обсуждение конкретного
    сегмента ведется рядом с текстом, а не в почте — контекст не теряется.
    """
    __tablename__ = "segment_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped["User"] = relationship()


class LQAError(Base):
    """Ошибка качества, размеченная ревьюером (LQA/MQM-подобная методология).

    Категории и серьёзность — упрощенный MQM; штрафные баллы настраиваются
    в config. Оценка = 100 − штраф×1000/слов (практика LQA-скоринга на
    1000 слов, аналогично memoQ LQA / TAUS DQF).
    """
    __tablename__ = "lqa_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # accuracy | terminology | fluency | style | locale | consistency
    category: Mapped[str] = mapped_column(String(16))
    # minor | major | critical
    severity: Mapped[str] = mapped_column(String(8))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped["User"] = relationship()


class JobReminder(Base):
    __tablename__ = "job_reminders"
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_job_reminder"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # 24h | 2h
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateCard(Base):
    """Прайс-лист организации: ставка за слово по категориям анализа объема.

    Биллинг — недостающее звено «анализ → деньги»: котировка задания считается
    как сумма слов категории × ставка категории (практика Trados/memoQ:
    скидка за совпадения зашита в ставку). Валюта — на карте; минимальная
    сумма заказа (min_charge) — стандарт индустрии для мелких заказов.
    """
    __tablename__ = "rate_cards"
    __table_args__ = (
        UniqueConstraint("org_id", "source_lang", "target_lang", "currency", name="uq_rate_card"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_lang: Mapped[str] = mapped_column(String(8))
    target_lang: Mapped[str] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    # {category_code: ставка за слово}; категории = config.RATE_CATEGORIES
    rates: Mapped[dict] = mapped_column(JSON, default=dict)
    min_charge: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SegmentRevision(Base):
    """История правок сегмента (track changes CAT-инструментов).

    Каждое изменение target_text фиксируется снапшотом: кто, когда, какой текст.
    Позволяет ревьюеру и менеджеру увидеть эволюцию перевода сегмента —
    аналог History/Track Changes в Trados/memoQ.
    """
    __tablename__ = "segment_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped["User"] = relationship()


class SegmentLock(Base):
    """Лок сегмента «кто редактирует — тот блокирует».

    Оптимистичная блокировка (409 по updated_at) защищает от потери правок,
    но о конфликте редакторы узнают постфактум. Мягкий лок с TTL показывает
    занятость заранее: пока владелец держит сегмент открытым (клиент продлевает
    лок heartbeat-ом), чужие сохранения отвергаются с 423. Лок старше TTL
    считается просроченным (вкладка закрылась без явного снятия) и свободен.
    """
    __tablename__ = "segment_locks"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(
        ForeignKey("segments.id"), unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship()


class QaRegexRule(Base):
    """QA-правило на регулярном выражении (RX-проверки, практика Trados/memoQ).

    Семантика: паттерн «должен встречаться» / «не должен встречаться» в
    исходнике или переводе обязательного сегмента. Покрывает корпоративные
    требования, которые не выражаются встроенными проверками: типографские
    кавычки, запрет латиницы в кириллице, неразрывные пробелы, заглушки TODO.
    severity=error блокирует сдачу наравне со встроенными ошибками QA.
    """
    __tablename__ = "qa_regex_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    pattern: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(String(8), default="target")  # source | target
    must_match: Mapped[bool] = mapped_column(Boolean, default=False)
    # warning | error (error — блокирующая, не дает сдать задание)
    severity: Mapped[str] = mapped_column(String(8), default="warning")
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Feedback(Base):
    """Сообщение обратной связи из интерфейса (кнопка «Feedback»).

    Бета-канал связи с пользователями: баги, идеи и вопросы уходят прямо из
    UI вместе со страницей, где нажали кнопку, — не теряются в почте и чатах.
    Ленту читает менеджмент организации (GET /api/feedback, последние 100).
    """
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="other")  # bug | idea | question | other
    message: Mapped[str] = mapped_column(Text)
    page: Mapped[str] = mapped_column(String(200), default="")  # где нажали кнопку, напр. /editor/12
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped["User"] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column()
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
