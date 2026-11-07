import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Project } from '../api/types';
import { useUpdates } from '../ws/useUpdates';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [source, setSource] = useState('en');
  const [targets, setTargets] = useState('ru');
  const [deadline, setDeadline] = useState('');
  const [instructions, setInstructions] = useState('');
  const [mtEnabled, setMtEnabled] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.projects().then(setProjects).catch((e) => setError(e instanceof Error ? e.message : 'Load failed'));
  }, []);

  useEffect(load, [load]);
  useUpdates((event) => {
    if (event.type.startsWith('job_')) load();
  });

  const create = async () => {
    setBusy(true);
    setError('');
    try {
      await api.createProject({
        name: name.trim(),
        source_lang: source.trim(),
        target_langs: targets.split(',').map((t) => t.trim()).filter(Boolean),
        deadline: deadline || null,
        instructions,
        mt_enabled: mtEnabled,
      });
      setName(''); setTargets('ru'); setDeadline(''); setInstructions('');
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="card">
        <h3 className="panel-title">New project</h3>
        <div className="row wrap">
          <input placeholder="Project name…" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 200 }} />
          <label className="muted small-text">source</label>
          <input value={source} onChange={(e) => setSource(e.target.value)} style={{ width: 64 }} />
          <label className="muted small-text">targets (comma-sep)</label>
          <input value={targets} onChange={(e) => setTargets(e.target.value)} style={{ width: 120 }} />
          <label className="muted small-text">deadline</label>
          <input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
          <label className="muted small-text"><input type="checkbox" checked={mtEnabled} onChange={(e) => setMtEnabled(e.target.checked)} /> MT</label>
        </div>
        <div className="row wrap" style={{ marginTop: 8 }}>
          <input
            placeholder="Instructions for translators…"
            value={instructions} onChange={(e) => setInstructions(e.target.value)}
            style={{ flex: 1, minWidth: 260 }}
          />
          <button className="btn primary" disabled={busy || !name.trim() || !targets.trim()} onClick={create}>
            Create project
          </button>
        </div>
      </section>

      {error && <div className="alert">{error}</div>}

      <section className="card">
        <table className="jobs-table">
          <thead>
            <tr><th>Project</th><th>Languages</th><th>Deadline</th><th>MT</th><th /></tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.id}>
                <td className="job-title">{p.name}</td>
                <td className="muted">{p.source_lang} → {p.target_langs.join(', ')}</td>
                <td className="muted">
                  {p.deadline ? new Date(p.deadline.endsWith('Z') || p.deadline.includes('+') ? p.deadline : p.deadline + 'Z').toLocaleString() : '—'}
                </td>
                <td>{p.mt_enabled ? 'on' : 'off'}</td>
                <td><Link className="btn small" to={`/projects/${p.id}`}>Open dashboard</Link></td>
              </tr>
            ))}
            {projects.length === 0 && (
              <tr><td colSpan={5} className="muted center">No projects yet.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}
