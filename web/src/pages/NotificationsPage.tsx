import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { fmtDateTime } from '../api/labels';
import type { AppNotification } from '../api/types';
import { useUpdates } from '../ws/useUpdates';
import { useAuth } from '../state/AuthContext';

const TYPE_LABELS: Record<string, string> = {
  job_assigned: 'Назначено новое задание',
  job_accepted: 'Задание принято',
  job_declined: 'Задание отклонено',
  job_submitted: 'Задание сдано',
  job_returned: 'Задание возвращено на доработку',
  job_reassigned: 'Задание переназначено',
  job_canceled: 'Задание отменено',
  deadline_reminder: 'Напоминание о дедлайне',
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
      setContactError(err instanceof Error ? err.message : 'Не удалось сохранить');
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
        <h3 className="panel-title">Центр уведомлений</h3>
        <span className="spacer" />
        <button className="btn small" onClick={() => api.markAllNotificationsRead().then(load)}>
          Прочитать все
        </button>
      </section>

      <section className="card">
        {items.length === 0 && <div className="muted center">Уведомлений нет.</div>}
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
                    ? ` — дедлайн через ${n.payload['offset']}`
                    : null}
                  {' · '}
                  {fmtDateTime(n.created_at)}
                </div>
              </div>
              <span className="spacer" />
              {!n.read_at && (
                <button className="btn small" onClick={() => api.markNotificationRead(n.id).then(load)}>
                  Прочитано
                </button>
              )}
              {link && <Link className="btn small" to={link}>Открыть задание</Link>}
            </div>
          );
        })}
      </section>

      <section className="card column" style={{ gap: 8 }}>
        <h3 className="panel-title">Внешние каналы доставки (бета)</h3>
        <p className="muted small-text" style={{ margin: 0 }}>
          События по заданиям (назначение, сдача, доработка, напоминания о дедлайне) могут приходить
          на email или в Telegram, если администратор включил эти каналы. Telegram: сначала напишите
          боту, затем вставьте свой числовой chat id (из @userinfobot) или @username.
        </p>
        <div className="row wrap">
          <input placeholder="you@example.com" value={email} style={{ minWidth: 240 }}
                 onChange={(e) => setEmail(e.target.value)} />
          <input placeholder="Telegram chat id / @username" value={tgChat} style={{ minWidth: 200 }}
                 onChange={(e) => setTgChat(e.target.value)} />
          <button className="btn small primary" onClick={() => void saveContacts()}>Сохранить</button>
          {contactSaved && <span className="done-note">✓ Сохранено</span>}
        </div>
        {contactError && <div className="alert">{contactError}</div>}
      </section>
    </>
  );
}
