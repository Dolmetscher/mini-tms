"""Доставка переводов: сбор целевых файлов проекта в один ZIP-пакет.

Практика TMS (XTRF/Plunet; «return package» в Trados): по завершении проекта
менеджер забирает все целевые файлы одним архивом, а не выкачивает задания
по одному. В архив вкладывается манифест — что внутри, статусы заданий и
прогресс подтверждения.
"""
import io
import os
import zipfile
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import models
from ..formats import segmentation

# Сдавать/принимать можно только из этих статусов (§7) — они же «готово к выдаче».
DELIVERABLE_STATUSES = ("submitted", "completed")


def export_target_bytes(job: models.Job) -> tuple[bytes, str]:
    """Целевой файл задания: оригинал с подставленными переводами, (байты, формат).

    Документ без сохраненного оригинала (текст, введенный при создании)
    выгружается склейкой переводов — как и экспорт одиночного задания.
    """
    targets = [segment.target_text for segment in job.segments]
    if not job.document.storage_path:
        return ("\n".join(targets) + "\n").encode("utf-8"), job.document.format
    with open(job.document.storage_path, "rb") as fh:
        original = fh.read()
    return segmentation.export_file(job.document.format, original, targets), job.document.format


def deliverable_jobs(db: Session, project: models.Project) -> list[models.Job]:
    """Задания проекта, готовые к доставке (submitted/completed), с готовым документом."""
    jobs = (
        db.query(models.Job)
        .filter(
            models.Job.project_id == project.id,
            models.Job.status.in_(DELIVERABLE_STATUSES),
        )
        .order_by(models.Job.target_lang, models.Job.id)
        .all()
    )
    return [job for job in jobs if job.document.processing_status == "ready"]


def build_delivery_zip(jobs: list[models.Job]) -> bytes:
    """ZIP с целевыми файлами + манифест доставки.

    Имена файлов: {документ}_{язык}.{формат}; при совпадении имен к имени
    добавляется номер задания (два документа с одинаковым именем в проекте).
    """
    buffer = io.BytesIO()
    used_names: set[str] = set()
    manifest = [
        f"Mini-TMS delivery package — generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "",
    ]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for job in jobs:
            payload, fmt = export_target_bytes(job)
            base, _ = os.path.splitext(job.document.name)
            name = f"{base}_{job.target_lang}.{fmt}"
            if name in used_names:
                name = f"{base}_{job.target_lang}_{job.id}.{fmt}"
            used_names.add(name)
            archive.writestr(name, payload)
            confirmed = sum(1 for segment in job.segments if segment.confirmed)
            manifest.append(
                f"{name}\t{job.status}\t"
                f"{job.project.source_lang}->{job.target_lang}\t"
                f"{confirmed}/{len(job.segments)} segments confirmed"
            )
        archive.writestr("manifest.txt", "\n".join(manifest) + "\n")
    return buffer.getvalue()
