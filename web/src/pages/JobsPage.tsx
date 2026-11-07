import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { api, downloadJobExport } from '../api/client';
import type { Job, WsEvent } from '../api/types';
import { useAuth } from '../state/AuthContext';
import { useUpdates } from '../ws/useUpdates';

const STATUS_LABELS: Record<string, string> = {
  unassigned: 'unassigned', assigned: 'assigned', declined: 'declined',
  accepted: 'accepted', in_progress: 'in progress', submitted: 'submitted',
  rework: 'rework', completed: 'completed', canceled: 'canceled',
};

function fmtDeadline(value: string | null): string {
  if (!value) return '—';
  const d = new Date(value.endsWith('Z') || value.includes('+') ? value : value + 'Z');
  return d.toLocaleString();
}

export default function JobsPage() {
  const { user, token, activeOrg } = useAuth();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');
  const [mineOnly, setMineOnly] = useState(activeOrg?.role === 'translator');
  const [declining, setDeclining] = useState<number | null>(null);
  const [declineReason, setDeclineReason] = useState('');

  const load = useCallback(() => {
    api
      .jobs({ status: statusFilter || undefined, q: search || undefined, mine: mineOnly || undefined })
      .then(setJobs)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load jobs'));
  }, [statusFilter, search, mineOnly]);

  useEffect(() => {
    if (token) load();
  }, [token, load]);

  useUpdates((event: WsEvent) => {
    if (event.type.startsWith('job_') || event.type === 'segment_updated') load();
  });

  if (!token) return <Navigate to="/login" replace />;

  const isManager = activeOrg?.role === 'pm' || activeOrg?.role === 'admin';
  const myId = user?.id;

  const act = async (fn: () => Promise<unknown>) => {
    setError('');
    try {
      await fn();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    }
  };

  return (
    <>
      <section className="card row wrap">
        <input
          placeholder="Search project or document…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <label className="muted small-text">
          <input type="checkbox" checked={mineOnly} onChange={(e) => setMineOnly(e.target.checked)} /> My jobs
        </label>
      </section>

      {error && <div className="alert">{error}</div>}

      {declining !== null && (
        <section className="card row">
          <span>Decline reason:</span>
          <input value={declineReason} onChange={(e) => setDeclineReason(e.target.value)} placeholder="Reason…" />
          <button className="btn" onClick={() => { setDeclining(null); setDeclineReason(''); }}>Cancel</button>
          <button
            className="btn primary"
            disabled={!declineReason.trim()}
            onClick={() => act(() => api.declineJob(declining, declineReason)).then(() => setDeclining(null))}
          >
            Decline job
          </button>
        </section>
      )}

      <section className="card">
        <table className="jobs-table">
          <thead>
            <tr>
              <th>Job</th><th>Languages</th><th>Deadline</th><th>Progress</th>
              <th>Status</th><th>Assignee</th><th>Actions</th>
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
                    {fmtDeadline(job.deadline)}
                    {job.is_overdue && <div className="small-text overdue">overdue</div>}
                  </td>
                  <td>
                    <div className="progress-row">
                      <div className="progress-track">
                        <div className="progress-fill" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="muted small-text">{job.confirmed_segments}/{job.total_segments} · {job.total_words}w</span>
                    </div>
                  </td>
                  <td><span className={`status-chip st-${job.status}`}>{STATUS_LABELS[job.status]}</span></td>
                  <td className="muted">{job.assignee?.username ?? '—'}</td>
                  <td className="actions-cell">
                    {mine && job.status === 'assigned' && (
                      <>
                        <button className="btn small" onClick={() => act(() => api.acceptJob(job.id))}>Accept</button>
                        <button className="btn small" onClick={() => setDeclining(job.id)}>Decline</button>
                      </>
                    )}
                    {editable && <Link className="btn small" to={`/editor/${job.id}`}>Open</Link>}
                    {mine && ['accepted', 'in_progress', 'rework'].includes(job.status) && (
                      <Link className="btn small" to={`/editor/${job.id}`}>Editor</Link>
                    )}
                    {isManager && (job.status === 'submitted' || job.status === 'completed') && (
                      <button className="btn small" onClick={() => act(() => downloadJobExport(job.id))}>Download</button>
                    )}
                    {isManager && <Link className="btn small" to={`/projects/${job.project_id}`}>Dashboard</Link>}
                  </td>
                </tr>
              );
            })}
            {jobs.length === 0 && (
              <tr><td colSpan={7} className="muted center">No jobs match the filters.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}
