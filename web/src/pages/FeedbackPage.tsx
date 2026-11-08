import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { FeedbackItem, FeedbackKind } from '../api/types';

type KindFilter = FeedbackKind | 'all';

const KINDS: { value: KindFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'bug', label: 'Bugs' },
  { value: 'idea', label: 'Ideas' },
  { value: 'question', label: 'Questions' },
  { value: 'other', label: 'Other' },
];

const KIND_CHIP: Record<FeedbackKind, string> = {
  bug: 'st-new',
  idea: 'st-in_progress',
  question: 'st-drafted',
  other: 'st-translated',
};

function fmtDate(value: string): string {
  return new Date(value.endsWith('Z') || value.includes('+') ? value : value + 'Z').toLocaleString();
}

/** Лента обратной связи (бета): сообщения из кнопки «💬 Feedback» в UI.
 * Читает менеджмент; тип + автор + страница, где нажали кнопку. */
export default function FeedbackPage() {
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [kind, setKind] = useState<KindFilter>('all');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api.feedback()
      .then(setItems)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { void load(); }, [load]);

  const visible = kind === 'all' ? items : items.filter((i) => i.kind === kind);

  return (
    <>
      <section className="card row wrap">
        <h3 className="panel-title">Feedback</h3>
        <span className="spacer" />
        {KINDS.map((k) => (
          <button key={k.value} className={`btn small ${kind === k.value ? 'primary' : ''}`} onClick={() => setKind(k.value)}>
            {k.label}
          </button>
        ))}
        <button className="btn small" onClick={() => void load()}>Refresh</button>
      </section>

      {error && <div className="alert">{error}</div>}

      <section className="card">
        <p className="muted small-text">
          Messages sent from the floating «💬 Feedback» button; the page where the user pressed it
          is attached automatically — follow the link to reproduce a bug faster.
        </p>

        {loading && <div className="muted">Loading…</div>}

        <div className="feedback-list">
          {visible.map((fb) => (
            <div key={fb.id} className="feedback-row">
              <div className="row wrap" style={{ gap: 6, alignItems: 'center' }}>
                <span className={`status-chip ${KIND_CHIP[fb.kind]}`}>{fb.kind}</span>
                <b>{fb.author.username}</b>
                <span className="muted">{fmtDate(fb.created_at)}</span>
                {fb.page && (
                  <Link className="feedback-page-link" to={fb.page} title="Open the page where feedback was sent">
                    {fb.page}
                  </Link>
                )}
              </div>
              <div className="feedback-message">{fb.message}</div>
            </div>
          ))}
          {!loading && visible.length === 0 && (
            <div className="muted center">No feedback yet — the beta testers are quiet so far.</div>
          )}
        </div>
      </section>
    </>
  );
}
