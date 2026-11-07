import { ChangeEvent, useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, downloadJobExport, getActiveOrg } from '../api/client';
import type { DocumentInfo, Job, Member, Project, Resource, TMInfo, TBInfo } from '../api/types';
import { useUpdates } from '../ws/useUpdates';

function fmtDeadline(value: string | null): string {
  if (!value) return '—';
  return new Date(value.endsWith('Z') || value.includes('+') ? value : value + 'Z').toLocaleString();
}

export default function ProjectDashboardPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [resources, setResources] = useState<Resource[]>([]);
  const [tms, setTms] = useState<TMInfo[]>([]);
  const [tbs, setTbs] = useState<TBInfo[]>([]);
  const [error, setError] = useState('');
  const [newTm, setNewTm] = useState('');
  const [newTb, setNewTb] = useState('');
  const [newTmWrite, setNewTmWrite] = useState(true);
  // локальное состояние назначения: jobId -> {assigneeId, deadline}
  const [assignDraft, setAssignDraft] = useState<Record<number, { assigneeId: string; deadline: string }>>({});

  const load = useCallback(async () => {
    try {
      const [p, j, docs, res, m, tmsList, tbsList] = await Promise.all([
        api.project(projectId),
        api.jobs({ project_id: projectId }),
        api.documents(projectId),
        api.resources(projectId),
        api.members(getActiveOrg() ?? 0),
        api.tms(),
        api.tbs(),
      ]);
      setProject(p); setJobs(j); setDocuments(docs); setResources(res);
      setMembers(m); setTms(tmsList); setTbs(tbsList);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Load failed');
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  useUpdates((event) => {
    if (event.type.startsWith('job_') || event.type === 'segment_updated') void load();
  });

  // Опрос обработки документов (FR-10: асинхронная обработка)
  useEffect(() => {
    const pending = documents.some((d) => d.processing_status === 'processing');
    if (!pending) return;
    const timer = setInterval(() => void load(), 1500);
    return () => clearInterval(timer);
  }, [documents, load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError('');
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    }
  };

  const upload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await act(() => api.uploadDocument(projectId, file));
    if (e.target) e.target.value = '';
  };

  const translators = members.filter((m) => m.role === 'translator');

  if (!project) return <div className="muted center">{error || 'Loading…'}</div>;

  return (
    <>
      <section className="card">
        <div className="row wrap">
          <h3 className="panel-title">{project.name}</h3>
          <span className="muted">{project.source_lang} → {project.target_langs.join(', ')}</span>
          <span className="muted small-text">Deadline: {fmtDeadline(project.deadline)}</span>
          <span className={`status-chip ${project.mt_enabled ? 'st-in_progress' : 'st-new'}`}>
            MT {project.mt_enabled ? 'on' : 'off'}
          </span>
        </div>
        {project.instructions && <div className="muted small-text">Instructions: {project.instructions}</div>}
        <div className="row wrap" style={{ marginTop: 10 }}>
          <label className="btn small" style={{ cursor: 'pointer' }}>
            Upload document (TXT / DOCX / XLIFF)
            <input type="file" accept=".txt,.text,.docx,.xliff,.xlf" hidden onChange={upload} />
          </label>
          {documents.map((d) => (
            <span key={d.id} className={`status-chip st-${d.processing_status === 'ready' ? 'translated' : d.processing_status === 'failed' ? 'new' : 'drafted'}`}
                  title={d.processing_error ?? ''}>
              {d.name}: {d.processing_status}
            </span>
          ))}
        </div>
      </section>

      {error && <div className="alert">{error}</div>}

      <section className="card">
        <h3 className="panel-title">Jobs</h3>
        <table className="jobs-table">
          <thead>
            <tr><th>Document</th><th>Target</th><th>Status</th><th>Assignee</th><th>Deadline</th><th>Progress</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const draft = assignDraft[job.id] ?? {
                assigneeId: job.assignee ? String(job.assignee.id) : '',
                deadline: '',
              };
              const pct = job.total_segments ? Math.round((job.confirmed_segments / job.total_segments) * 100) : 0;
              return (
                <tr key={job.id}>
                  <td className="job-title">{job.document_name}</td>
                  <td className="muted">{job.target_lang}</td>
                  <td>
                    <span className={`status-chip st-${job.status}`}>{job.status.replace('_', ' ')}</span>
                  </td>
                  <td className="muted">{job.assignee?.username ?? '—'}</td>
                  <td className={job.is_overdue ? 'overdue' : 'muted'}>{fmtDeadline(job.deadline)}</td>
                  <td>
                    <div className="progress-row">
                      <div className="progress-track">
                        <div className="progress-fill" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="muted small-text">{job.confirmed_segments}/{job.total_segments}</span>
                    </div>
                  </td>
                  <td className="actions-cell">
                    {['unassigned', 'assigned', 'declined'].includes(job.status) && (
                      <span className="row" style={{ gap: 4 }}>
                        <select
                          value={draft.assigneeId}
                          onChange={(e) => setAssignDraft({ ...assignDraft, [job.id]: { ...draft, assigneeId: e.target.value } })}
                          style={{ width: 110 }}
                        >
                          <option value="">translator…</option>
                          {translators.map((m) => (
                            <option key={m.id} value={m.user.id}>{m.user.username}</option>
                          ))}
                        </select>
                        <input
                          type="datetime-local" style={{ width: 170 }}
                          value={draft.deadline}
                          onChange={(e) => setAssignDraft({ ...assignDraft, [job.id]: { ...draft, deadline: e.target.value } })}
                        />
                        <button
                          className="btn small primary"
                          disabled={!draft.assigneeId}
                          onClick={() => act(() => api.assignJob(
                            job.id, Number(draft.assigneeId),
                            draft.deadline ? new Date(draft.deadline).toISOString() : null,
                          ))}
                        >
                          Assign
                        </button>
                      </span>
                    )}
                    {job.status === 'submitted' && (
                      <>
                        <button className="btn small primary" onClick={() => act(() => api.completeJob(job.id))}>Complete</button>
                        <button
                          className="btn small"
                          onClick={() => {
                            const comment = window.prompt('Rework comment:');
                            if (comment) void act(() => api.returnJob(job.id, comment));
                          }}
                        >
                          Return to rework
                        </button>
                      </>
                    )}
                    {!['completed', 'canceled'].includes(job.status) && (
                      <button
                        className="btn small"
                        onClick={() => { if (window.confirm('Cancel this job?')) void act(() => api.cancelJob(job.id)); }}
                      >
                        Cancel
                      </button>
                    )}
                    {(job.status === 'submitted' || job.status === 'completed') && (
                      <button className="btn small" onClick={() => act(() => downloadJobExport(job.id))}>Download</button>
                    )}
                  </td>
                </tr>
              );
            })}
            {jobs.length === 0 && <tr><td colSpan={7} className="muted center">Upload a document to create jobs.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3 className="panel-title">Connected resources (TM / TB)</h3>
        {resources.length === 0 && <div className="muted small-text">Nothing connected yet.</div>}
        {resources.map((r) => (
          <div key={r.id} className="row notification">
            <span className={`status-chip ${r.tm ? 'st-translated' : 'st-in_progress'}`}>{r.tm ? 'TM' : 'TB'}</span>
            <strong>{r.tm?.name ?? r.tb?.name}</strong>
            <span className="muted small-text">
              {r.tm ? `${r.tm.source_lang}→${r.tm.target_lang}` : `${r.tb?.source_lang}→${r.tb?.target_lang}`}
            </span>
            <span className="muted small-text">search: {r.use_for_search ? 'yes' : 'no'}</span>
            {r.tm && <span className="muted small-text">write: {r.use_for_write ? 'yes' : 'no'}</span>}
            <span className="spacer" />
            <button className="btn small" onClick={() => act(() => api.disconnectResource(projectId, r.id))}>Disconnect</button>
          </div>
        ))}
        <div className="row wrap" style={{ marginTop: 10 }}>
          <select value={newTm} onChange={(e) => { setNewTm(e.target.value); setNewTb(''); }}>
            <option value="">connect TM…</option>
            {tms.filter((t) => t.source_lang === project.source_lang && project.target_langs.includes(t.target_lang))
                .map((t) => <option key={t.id} value={t.id}>TM: {t.name} ({t.source_lang}→{t.target_lang})</option>)}
          </select>
          <select value={newTb} onChange={(e) => { setNewTb(e.target.value); setNewTm(''); }}>
            <option value="">connect TB…</option>
            {tbs.filter((t) => t.source_lang === project.source_lang && project.target_langs.includes(t.target_lang))
                .map((t) => <option key={t.id} value={t.id}>TB: {t.name} ({t.source_lang}→{t.target_lang})</option>)}
          </select>
          {newTm && (
            <label className="muted small-text">
              <input type="checkbox" checked={newTmWrite} onChange={(e) => setNewTmWrite(e.target.checked)} /> use for write
            </label>
          )}
          <button
            className="btn small primary"
            disabled={!newTm && !newTb}
            onClick={() => act(() => api.connectResource(projectId, {
              tm_id: newTm ? Number(newTm) : undefined,
              tb_id: newTb ? Number(newTb) : undefined,
              use_for_search: true,
              use_for_write: Boolean(newTm) && newTmWrite,
            })).then(() => { setNewTm(''); setNewTb(''); })}
          >
            Connect
          </button>
        </div>
      </section>
    </>
  );
}
