import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { AppNotification } from '../api/types';
import { useUpdates } from '../ws/useUpdates';
import { useAuth } from '../state/AuthContext';

const TYPE_LABELS: Record<string, string> = {
  job_assigned: 'New job assigned',
  job_accepted: 'Job accepted',
  job_declined: 'Job declined',
  job_submitted: 'Job submitted',
  job_returned: 'Job returned to rework',
  job_reassigned: 'Job reassigned',
  job_canceled: 'Job canceled',
  deadline_reminder: 'Deadline reminder',
};

export default function NotificationsPage() {
  const [items, setItems] = useState<AppNotification[]>([]);
  // Контакты для внешних уведомлений (email / Telegram) — только при включенных каналах
  const [email, setEmail] = useState('');
  const [tgChat, setTgChat] = useState('');
  const [contactSaved, setContactSaved] = useState(false);
  const [contactError, setContactError] = useState('');
  const { token } = useAuth();

  const load = useCallback(() => {
    api.notifications().then(setItems).catch(() => setItems([]));
  }, []);

  useEffect(load, [load]);

  useEffect(() => {
    if (!token) return;
    api.me()
      .then((r) => { setEmail(r.user.email ?? ''); setTgChat(r.user.telegram_chat_id ?? ''); })
      .catch(() => undefined);
  }, [token]);

  const saveContacts = async () => {
    setContactError('');
    setContactSaved(false);
    try {
      await api.updateMyContact({ email: email.trim(), telegram_chat_id: tgChat.trim() });
      setContactSaved(true);
      window.setTimeout(() => setContactSaved(false), 2500);
    } catch (err) {
      setContactError(err instanceof Error ? err.message : 'Save failed');
    }
  };

  useUpdates((event) => {
    if (event.type === 'notification') load();
  });

  const openJob = (n: AppNotification): string | null => {
    const jobId = n.payload['job_id'];
    return typeof jobId === 'number' ? `/editor/${jobId}` : null;
  };

  return (
    <>
      <section className="card row">
        <h3 className="panel-title">Notification center</h3>
        <span className="spacer" />
        <button className="btn small" onClick={() => api.markAllNotificationsRead().then(load)}>
          Mark all read
        </button>
      </section>

      <section className="card">
        {items.length === 0 && <div className="muted center">No notifications.</div>}
        {items.map((n) => {
          const link = openJob(n);
          return (
            <div key={n.id} className={`notification ${n.read_at ? 'read' : 'unread'}`}>
              <div>
                <strong>{TYPE_LABELS[n.type] ?? n.type}</strong>
                <div className="muted small-text">
                  {typeof n.payload['document'] === 'string' && n.payload['document']}
                  {typeof n.payload['reason'] === 'string' && ` — ${n.payload['reason']}`}
                  {typeof n.payload['comment'] === 'string' && ` — ${n.payload['comment']}`}
                  {n.type === 'deadline_reminder' && typeof n.payload['offset'] === 'string'
                    && ` — deadline in ${n.payload['offset']}`}
                  {' · '}
                  {new Date(n.created_at.endsWith('Z') || n.created_at.includes('+') ? n.created_at : n.created_at + 'Z').toLocaleString()}
                </div>
              </div>
              <span className="spacer" />
              {!n.read_at && (
                <button className="btn small" onClick={() => api.markNotificationRead(n.id).then(load)}>
                  Mark read
                </button>
              )}
              {link && <Link className="btn small" to={link}>Open job</Link>}
            </div>
          );
        })}
      </section>

      <section className="card column" style={{ gap: 8 }}>
        <h3 className="panel-title">External delivery channels (beta)</h3>
        <p className="muted small-text" style={{ margin: 0 }}>
          Job events (assignment, submission, rework, deadline reminders) can also reach you by email
          or Telegram if the administrator has enabled those channels. Telegram: message the bot first,
          then paste your numeric chat id (from @userinfobot) or @username.
        </p>
        <div className="row wrap">
          <input placeholder="you@example.com" value={email} style={{ minWidth: 240 }}
                 onChange={(e) => setEmail(e.target.value)} />
          <input placeholder="Telegram chat id / @username" value={tgChat} style={{ minWidth: 200 }}
                 onChange={(e) => setTgChat(e.target.value)} />
          <button className="btn small primary" onClick={() => void saveContacts()}>Save</button>
          {contactSaved && <span className="done-note">✓ Saved</span>}
        </div>
        {contactError && <div className="alert">{contactError}</div>}
      </section>
    </>
  );
}
