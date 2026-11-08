import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { api, downloadJobExport } from '../api/client';
import { SEVERITY_LABELS, STATUS_LABELS, fmtDateTime } from '../api/labels';
import type { Job, JobDetail, LQAReport, WsEvent } from '../api/types';
import { useAuth } from '../state/AuthContext';
import { useUpdates } from '../ws/useUpdates';

const LQA_CATEGORIES: { value: string; label: string }[] = [
  { value: 'accuracy', label: 'Точность (смысл)' },
  { value: 'terminology', label: 'Терминология (термины)' },
  { value: 'fluency', label: 'Беглость (грамотность)' },
  { value: 'style', label: 'Стиль' },
  { value: 'locale', label: 'Локализация' },
  { value: 'consistency', label: 'Согласованность' },
];
const LQA_SEVERITIES: { value: string; label: string; penalty: number }[] = [
  { value: 'minor', label: 'Незначительная', penalty: 1 },
  { value: 'major', label: 'Существенная', penalty: 5 },
  { value: 'critical', label: 'Критичная', penalty: 10 },
];

export default function JobsPage() {
  const { user, token, activeOrg } = useAuth();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');
  const [mineOnly, setMineOnly] = useState(activeOrg?.role === 'translator');
  const [declining, setDeclining] = useState<number | null>(null);
  const [declineReason, setDeclineReason] = useState('');
  // LQA-ревью: задание, отчет, детали (сегменты для выбора) и форма разметки
  const [lqaJob, setLqaJob] = useState<Job | null>(null);
  const [lqaDetail, setLqaDetail] = useState<JobDetail | null>(null);
  const [lqaReport, setLqaReport] = useState<LQAReport | null>(null);
  const [lqaError, setLqaError] = useState('');
  const [lqaBusy, setLqaBusy] = useState(false);
  const [lqaSegment, setLqaSegment] = useState<number | null>(null);
  const [lqaCategory, setLqaCategory] = useState('accuracy');
  const [lqaSeverity, setLqaSeverity] = useState('minor');
  const [lqaComment, setLqaComment] = useState('');

  const load = useCallback(() => {
    api
      .jobs({ status: statusFilter || undefined, q: search || undefined, mine: mineOnly || undefined })
      .then(setJobs)
      .catch((err) => setError(err instanceof Error ? err.message : 'Не удалось загрузить задания'));
  }, [statusFilter, search, mineOnly]);

  useEffect(() => {
    if (token) load();
  }, [token, load]);

  useUpdates((event: WsEvent) => {
    if (event.type.startsWith('job_') || event.type === 'segment_updated') load();
    // Разметка LQA обновляется в открытом ревью без переоткрытия
    if ((event.type === 'lqa_error_added' || event.type === 'lqa_error_deleted')
      && lqaJob && event.job_id === lqaJob.id) {
      api.jobLqa(lqaJob.id).then(setLqaReport).catch(() => undefined);
    }
  });

  const openLqa = useCallback(async (job: Job) => {
    setLqaJob(job);
    setLqaError('');
    setLqaReport(null);
    setLqaDetail(null);
    setLqaSegment(null);
    setLqaComment('');
    try {
      const [report, detail] = await Promise.all([api.jobLqa(job.id), api.job(job.id)]);
      setLqaReport(report);
      setLqaDetail(detail);
      if (detail.segments.length) setLqaSegment(detail.segments[0].id);
    } catch (err) {
      setLqaError(err instanceof Error ? err.message : 'Не удалось загрузить LQA-отчет');
    }
  }, []);

  const addLqaError = async () => {
    if (!lqaJob || !lqaSegment || !lqaComment.trim()) return;
    setLqaBusy(true);
    try {
      await api.addLqaError(lqaJob.id, {
        segment_id: lqaSegment, category: lqaCategory, severity: lqaSeverity, comment: lqaComment.trim(),
      });
      setLqaComment('');
      setLqaReport(await api.jobLqa(lqaJob.id));
    } catch (err) {
      setLqaError(err instanceof Error ? err.message : 'Не удалось разметить ошибку');
    } finally {
      setLqaBusy(false);
    }
  };

  const removeLqaError = async (errorId: number) => {
    if (!lqaJob) return;
    try {
      await api.deleteLqaError(lqaJob.id, errorId);
      setLqaReport(await api.jobLqa(lqaJob.id));
    } catch (err) {
      setLqaError(err instanceof Error ? err.message : 'Не удалось снять ошибку');
    }
  };

  if (!token) return <Navigate to="/login" replace />;

  const isManager = activeOrg?.role === 'pm' || activeOrg?.role === 'admin';
  const myId = user?.id;

  const act = async (fn: () => Promise<unknown>) => {
    setError('');
    try {
      await fn();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Действие не удалось');
    }
  };

  return (
    <>
      <section className="card row wrap">
        <input
          placeholder="Поиск по проекту или документу…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">Все статусы</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <label className="muted small-text">
          <input type="checkbox" checked={mineOnly} onChange={(e) => setMineOnly(e.target.checked)} /> Только мои
        </label>
      </section>

      {error && <div className="alert">{error}</div>}

      {declining !== null && (
        <section className="card row">
          <span>Причина отклонения:</span>
          <input value={declineReason} onChange={(e) => setDeclineReason(e.target.value)} placeholder="Причина…" />
          <button className="btn" onClick={() => { setDeclining(null); setDeclineReason(''); }}>Отмена</button>
          <button
            className="btn primary"
            disabled={!declineReason.trim()}
            onClick={() => act(() => api.declineJob(declining, declineReason)).then(() => setDeclining(null))}
          >
            Отклонить задание
          </button>
        </section>
      )}

      <section className="card">
        <table className="jobs-table">
          <thead>
            <tr>
              <th>Задание</th><th>Языки</th><th>Дедлайн</th><th>Прогресс</th>
              <th>Статус</th><th>Исполнитель</th><th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const mine = job.assignee?.id === myId;
              const editable = mine && ['assigned', 'accepted', 'in_progress', 'rework'].includes(job.status);
              const pct = job.total_segments ? Math.round((job.confirmed_segments / job.total_segments) * 100) : 0;
              return (
                <tr key={job.id}>
                  <td>
                    <div className="job-title">{job.document_name}</div>
                    <div className="muted small-text">{job.project_name}</div>
                  </td>
                  <td className="muted">{job.source_lang} → {job.target_lang}</td>
                  <td className={job.is_overdue ? 'overdue' : ''}>
                    {fmtDateTime(job.deadline)}
                    {job.is_overdue && <div className="small-text overdue">просрочено</div>}
                  </td>
                  <td>
                    <div className="progress-row">
                      <div className="progress-track">
                        <div className="progress-fill" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="muted small-text">{job.confirmed_segments}/{job.total_segments} · {job.total_words} сл.</span>
                    </div>
                  </td>
                  <td><span className={`status-chip st-${job.status}`}>{STATUS_LABELS[job.status]}</span></td>
                  <td className="muted">{job.assignee?.username ?? '—'}</td>
                  <td className="actions-cell">
                    {mine && job.status === 'assigned' && (
                      <>
                        <button className="btn small" onClick={() => act(() => api.acceptJob(job.id))}>Принять</button>
                        <button className="btn small" onClick={() => setDeclining(job.id)}>Отклонить</button>
                      </>
                    )}
                    {editable && <Link className="btn small" to={`/editor/${job.id}`}>Открыть</Link>}
                    {mine && ['accepted', 'in_progress', 'rework'].includes(job.status) && (
                      <Link className="btn small" to={`/editor/${job.id}`}>Редактор</Link>
                    )}
                    {isManager && (job.status === 'submitted' || job.status === 'completed') && (
                      <button className="btn small" onClick={() => act(() => downloadJobExport(job.id))}>Скачать</button>
                    )}
                    {isManager && ['submitted', 'rework', 'completed'].includes(job.status) && (
                      <button className="btn small" onClick={() => void openLqa(job)}
                              title="Оценка качества перевода: разметка ошибок и LQA-скор">
                        LQA review
                      </button>
                    )}
                    {isManager && <Link className="btn small" to={`/projects/${job.project_id}`}>Панель</Link>}
                  </td>
                </tr>
              );
            })}
            {jobs.length === 0 && (
              <tr><td colSpan={7} className="muted center">Нет заданий по этим фильтрам.</td></tr>
            )}
          </tbody>
        </table>
      </section>
      {lqaJob && (
        <div className="modal-backdrop" onClick={() => setLqaJob(null)}>
          <div className="modal card lqa-modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="panel-title">LQA — оценка качества: {lqaJob.document_name}</h3>
            {lqaError && <div className="alert">{lqaError}</div>}
            {!lqaReport && <div className="muted">Загрузка…</div>}
            {lqaReport && (
              <>
                <div className="lqa-score-row">
                  <div className={`lqa-score ${lqaReport.passed ? 'passed' : 'failed'}`}>
                    <span className="lqa-score-value">{lqaReport.score.toFixed(1)}</span>
                    <span className="lqa-score-max">/ 100</span>
                  </div>
                  <div className="lqa-score-meta">
                    <span className={`status-chip ${lqaReport.passed ? 'st-translated' : 'st-new'}`}>
                      {lqaReport.passed ? 'ЗАЧЕТ' : 'НЕ ЗАЧЕТ'} (порог {lqaReport.pass_score})
                    </span>
                    <span className="muted small-text">
                      {lqaReport.error_count} ошибок · {lqaReport.penalty_points} штрафных баллов · {lqaReport.words} слов ·
                      score = 100 − penalty×1000/words
                    </span>
                  </div>
                </div>

                {lqaReport.by_category.length > 0 && (
                  <div className="lqa-cats">
                    {lqaReport.by_category.map((c) => (
                      <span key={c.category} className="lqa-cat-chip">
                        {c.label}: {c.count} ({c.penalty} б.)
                      </span>
                    ))}
                  </div>
                )}

                <div className="qa-list">
                  {lqaReport.errors.map((e) => (
                    <div key={e.id} className={`qa-item ${e.severity === 'critical' ? 'error' : e.severity === 'major' ? 'warning' : ''}`}>
                      <span className="muted">#{e.position + 1}</span>
                      <span className={`status-chip sev-${e.severity}`}>{SEVERITY_LABELS[e.severity] ?? e.severity} · {e.penalty} б.</span>
                      <span className={`status-chip st-drafted`}>{LQA_CATEGORIES.find((c) => c.value === e.category)?.label ?? e.category}</span>
                      <span className="lqa-comment">{e.comment}</span>
                      <span className="muted small-text">{e.author.username}</span>
                      <button className="btn small" title="Снять ошибку (разметка ошибочна)"
                              onClick={() => void removeLqaError(e.id)}>×</button>
                    </div>
                  ))}
                  {lqaReport.errors.length === 0 && (
                    <div className="muted">Ошибок не размечено. Отметьте замечания по сегментам ниже.</div>
                  )}
                </div>

                {lqaDetail && lqaDetail.segments.length > 0 && (
                  <div className="lqa-add-form">
                    <div className="muted small-text">Разметить ошибку:</div>
                    <div className="row wrap" style={{ gap: 6 }}>
                      <select value={lqaSegment ?? ''} onChange={(e) => setLqaSegment(Number(e.target.value) || null)}
                              style={{ maxWidth: 220 }}>
                        {lqaDetail.segments.map((s) => (
                          <option key={s.id} value={s.id}>
                            #{s.position + 1}: {s.source_text.slice(0, 40)}{s.source_text.length > 40 ? '…' : ''}
                          </option>
                        ))}
                      </select>
                      <select value={lqaCategory} onChange={(e) => setLqaCategory(e.target.value)}>
                        {LQA_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                      </select>
                      <select value={lqaSeverity} onChange={(e) => setLqaSeverity(e.target.value)}>
                        {LQA_SEVERITIES.map((s) => (
                          <option key={s.value} value={s.value}>{s.label} (+{s.penalty} б.)</option>
                        ))}
                      </select>
                    </div>
                    <div className="row" style={{ marginTop: 6 }}>
                      <input
                        placeholder="Что именно не так (комментарий ревьюера)…"
                        value={lqaComment}
                        onChange={(e) => setLqaComment(e.target.value)}
                        style={{ flex: 1 }}
                      />
                      <button className="btn primary" disabled={!lqaSegment || !lqaComment.trim() || lqaBusy}
                              onClick={() => void addLqaError()}>
                        {lqaBusy ? '…' : 'Разметить ошибку'}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn" onClick={() => setLqaJob(null)}>Закрыть</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
