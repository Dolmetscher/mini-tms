from datetime import timedelta

from sqlalchemy.orm import Session

from .auth import hash_password
from .models import (
    Document, Job, Membership, Organization, Project, ProjectResource, Segment,
    TB, TBEntry, TM, TMEntry, User,
)
from .services.words import count_words

DEMO_TM = [
    ("Welcome to our new platform.", "Добро пожаловать на нашу новую платформу."),
    ("Contact support", "Обратитесь в поддержку"),
]

DEMO_SEGMENTS = [
    "Welcome to our new platform.",
    "Save time and translate faster.",
    "Contact support for help.",
]

DEMO_TB = [
    ("platform", "платформа", "Preferred product term", "preferred"),
    ("support", "поддержка", "", "preferred"),
    ("fast", "быстренько", "Colloquial variant is forbidden", "forbidden"),
]


def seed_if_empty(db: Session) -> None:
    if db.query(User).count() > 0:
        return

    admin = User(username="admin", password_hash=hash_password("admin123"))
    pm = User(username="pm", password_hash=hash_password("pm123"))
    ann = User(username="ann", password_hash=hash_password("ann123"))
    ivan = User(username="ivan", password_hash=hash_password("ivan123"))
    db.add_all([admin, pm, ann, ivan])
    db.flush()

    org = Organization(name="Demo Org", mt_allowed=True)
    db.add(org)
    db.flush()
    db.add_all([
        Membership(org_id=org.id, user_id=admin.id, role="admin"),
        Membership(org_id=org.id, user_id=pm.id, role="pm"),
        Membership(org_id=org.id, user_id=ann.id, role="translator"),
        Membership(org_id=org.id, user_id=ivan.id, role="translator"),
    ])

    tm = TM(org_id=org.id, name="Demo TM", source_lang="en", target_lang="ru")
    db.add(tm)
    db.flush()
    for source, target in DEMO_TM:
        db.add(TMEntry(tm_id=tm.id, source_text=source, target_text=target))

    tb = TB(org_id=org.id, name="Demo TB", source_lang="en", target_lang="ru")
    db.add(tb)
    db.flush()
    for term, translation, note, status in DEMO_TB:
        db.add(TBEntry(tb_id=tb.id, term=term, translation=translation, note=note, status=status))

    project = Project(
        org_id=org.id,
        name="Marketing Brochure",
        source_lang="en",
        target_langs=["ru"],
        deadline=None,
        instructions="Use the demo TM and TB. Keep tone marketing-friendly.",
        mt_enabled=True,
        manager_id=pm.id,
    )
    db.add(project)
    db.flush()
    db.add(ProjectResource(project_id=project.id, tm_id=tm.id, use_for_search=True, use_for_write=True))
    db.add(ProjectResource(project_id=project.id, tb_id=tb.id, use_for_search=True))

    document = Document(
        org_id=org.id,
        project_id=project.id,
        name="Marketing Brochure.txt",
        format="txt",
        storage_path="",  # сид-документ не хранит файл: экспорт собирается из сегментов
        processing_status="ready",
        uploaded_by=pm.id,
    )
    db.add(document)
    db.flush()
    job = Job(
        org_id=org.id,
        project_id=project.id,
        document_id=document.id,
        target_lang="ru",
        assignee_id=ann.id,
        status="assigned",
        deadline=None,
    )
    db.add(job)
    db.flush()
    for position, source in enumerate(DEMO_SEGMENTS):
        db.add(Segment(job_id=job.id, position=position, source_text=source,
                       source_words=count_words(source)))

    db.commit()
