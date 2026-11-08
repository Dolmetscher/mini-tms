import { useCallback, useEffect, useState } from 'react';
import { api, downloadOrgBackup, getActiveOrg } from '../api/client';
import type { ProductivityRow } from '../api/types';

type Period = 'all' | '30' | '90';

/** Отчеты для менеджмента: продуктивность переводчиков + бэкап организации. */
export default function ReportsPage() {
  const [rows, setRows] = useState<ProductivityRow[]>([]);
  const [period, setPeriod] = useState<Period>('all');
  const [error, setError] = useState('');
  const orgId = getActiveOrg() ?? 0;

  const load = useCallback(() => {
    const updatedAfter = period === 'all' ? undefined : new Date(Date.now() - Number(period) * 86400_000).toISOString();
    api.productivity(updatedAfter)
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'));
  }, [period]);

  useEffect(() => { void load(); }, [load]);

  const backup = async () => {
    setError('');
    try {
      await downloadOrgBackup(orgId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Backup failed');
    }
  };

  return (
    <>
      <section className="card row wrap">
        <h3 className="panel-title">Translator productivity</h3>
        <span className="spacer" />
        {(['all', '30', '90'] as Period[]).map((p) => (
          <button key={p} className={`btn small ${period === p ? 'primary' : ''}`} onClick={() => setPeriod(p)}>
            {p === 'all' ? 'All time' : `Last ${p} days`}
          </button>
        ))}
        <button className="btn small" onClick={() => void backup()} title="Full JSON backup of this organization">
          Backup JSON
        </button>
      </section>

      {error && <div className="alert">{error}</div>}

      <section className="card">
        <p className="muted small-text">
          Confirmed words and job flow per translator; average LQA score counts jobs with marked
          errors (trivial 100s don’t inflate the average). Overdue = deadline passed, job not closed.
        </p>
        <table className="jobs-table">
          <thead>
            <tr>
              <th>Translator</th><th>Assigned</th><th>Completed</th><th>Overdue</th>
              <th>Confirmed words</th><th>LQA jobs</th><th>Avg LQA</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.user.id}>
                <td className="job-title">{r.user.username}</td>
                <td>{r.jobs_assigned}</td>
                <td>{r.jobs_completed}</td>
                <td className={r.jobs_overdue ? 'overdue' : ''}>{r.jobs_overdue}</td>
                <td>{r.words_confirmed}</td>
                <td>{r.lqa_jobs}</td>
                <td>
                  {r.avg_lqa_score === null
                    ? <span className="muted">—</span>
                    : <span className={`status-chip ${r.avg_lqa_score >= 95 ? 'st-translated' : 'st-new'}`}>
                        {r.avg_lqa_score}
                      </span>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="muted center">No translators with jobs in this period.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}
