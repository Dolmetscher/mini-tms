"""RX-проверки QA: правила на регулярных выражениях (практика Trados/memoQ).

Корпоративные требования, которые не выражаются встроенными проверками QA
(типографские кавычки, запрет латиницы в кириллическом переводе, заглушки
TODO, неразрывные пробелы), описываются правилом вида «паттерн должен / не
должен встречаться в исходнике или переводе». Правила организации применяются
в run_qa к обязательным сегментам; severity=error блокирует сдачу задания.

Паттерн компилируется при создании/правке — невалидный отклоняется с 422;
эндпоинт /test прогоняет паттерн по образцу текста для предпросмотра в UI.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .. import config, models, schemas
from ..deps import Context, get_context
from ..services import audit

router = APIRouter(prefix="/api/qa-rules", tags=["qa-rules"])


def _compile_or_422(pattern: str, case_sensitive: bool) -> re.Pattern:
    if len(pattern) > config.QA_REGEX_MAX_PATTERN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Паттерн длиннее {config.QA_REGEX_MAX_PATTERN} символов",
        )
    try:
        return re.compile(pattern, flags=0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Некорректное регулярное выражение: {exc}",
        ) from exc


def _rule_or_404(ctx: Context, rule_id: int) -> models.QaRegexRule:
    rule = ctx.db.get(models.QaRegexRule, rule_id)
    if rule is None or rule.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="QA-правило не найдено")
    return rule


@router.get("", response_model=list[schemas.QaRegexRuleOut])
def list_rules(ctx: Context = Depends(get_context)):
    """Правила организации видят все участники: переводчики должны знать требования."""
    return (
        ctx.db.query(models.QaRegexRule)
        .filter(models.QaRegexRule.org_id == ctx.org_id)
        .order_by(models.QaRegexRule.id)
        .all()
    )


@router.post("", response_model=schemas.QaRegexRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: schemas.QaRegexRuleCreate, ctx: Context = Depends(get_context)):
    """Новое правило (pm/admin): паттерн проверяется компиляцией."""
    ctx.require_role("admin", "pm")
    _compile_or_422(body.pattern, body.case_sensitive)
    rule = models.QaRegexRule(org_id=ctx.org_id, **body.model_dump())
    ctx.db.add(rule)
    ctx.db.flush()
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "qa_rule.create", "qa_rule", rule.id, {
        "name": rule.name, "severity": rule.severity, "side": rule.side,
    })
    ctx.db.commit()
    return rule


@router.patch("/{rule_id}", response_model=schemas.QaRegexRuleOut)
def update_rule(rule_id: int, body: schemas.QaRegexRuleUpdate, ctx: Context = Depends(get_context)):
    """Правка правила, вкл. переключатель enabled (временно выключить без удаления)."""
    ctx.require_role("admin", "pm")
    rule = _rule_or_404(ctx, rule_id)
    changes = body.model_dump(exclude_unset=True)
    if "pattern" in changes or "case_sensitive" in changes:
        pattern = changes.get("pattern", rule.pattern)
        case_sensitive = changes.get("case_sensitive", rule.case_sensitive)
        _compile_or_422(pattern, case_sensitive)
    for field, value in changes.items():
        setattr(rule, field, value)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "qa_rule.update", "qa_rule", rule.id, changes)
    ctx.db.commit()
    ctx.db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    rule = _rule_or_404(ctx, rule_id)
    ctx.db.delete(rule)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "qa_rule.delete", "qa_rule", rule_id, {"name": rule.name})
    ctx.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=schemas.QaRegexTestResult)
def test_rule(body: schemas.QaRegexTestRequest, ctx: Context = Depends(get_context)):
    """Прогон паттерна по образцу: UI показывает, что именно найдет правило."""
    try:
        pattern = _compile_or_422(body.pattern, body.case_sensitive)
    except HTTPException as exc:
        return schemas.QaRegexTestResult(matches=[], error=str(exc.detail))
    matches = [m.group(0) for m in pattern.finditer(body.text)]
    return schemas.QaRegexTestResult(matches=matches[:20])
