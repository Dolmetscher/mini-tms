import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { api } from '../api/client';
import type { FeedbackKind } from '../api/types';

const KINDS: { value: FeedbackKind; label: string }[] = [
  { value: 'bug', label: 'Bug report' },
  { value: 'idea', label: 'Feature idea' },
  { value: 'question', label: 'Question' },
  { value: 'other', label: 'Something else' },
];

/** Плавающая кнопка обратной связи (бета): видна на всех страницах, кроме логина.
 * Сообщение уходит вместе с адресом страницы, где нажали кнопку, — баг проще
 * воспроизвести. Ленту читает менеджмент организации (GET /api/feedback). */
export default function FeedbackButton() {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<FeedbackKind>('bug');
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent'>('idle');
  const [error, setError] = useState('');
  const location = useLocation();

  const close = () => { setOpen(false); setError(''); };

  const submit = async () => {
    setStatus('sending');
    setError('');
    try {
      await api.sendFeedback({ kind, message, page: location.pathname });
      setStatus('sent');
      setMessage('');
      // Короткая «спасибо»-пауза, чтобы пользователь увидел подтверждение.
      window.setTimeout(() => { setOpen(false); setStatus('idle'); }, 1400);
    } catch (err) {
      setStatus('idle');
      setError(err instanceof Error ? err.message : 'Send failed');
    }
  };

  return (
    <>
      <button className="feedback-fab" title="Send feedback to the team" onClick={() => setOpen(true)}>
        💬 Feedback
      </button>
      {open && (
        <div className="modal-backdrop" onClick={close}>
          <div className="modal card feedback-modal" onClick={(e) => e.stopPropagation()}>
            {status === 'sent' ? (
              <p className="done-note">✓ Thanks! Your feedback was sent.</p>
            ) : (
              <>
                <h3 className="panel-title">Feedback</h3>
                <p className="muted">
                  Found a bug or have an idea? Tell us — the current page is attached automatically.
                </p>
                <div className="row wrap" style={{ gap: 6, margin: '8px 0' }}>
                  {KINDS.map((k) => (
                    <button
                      key={k.value}
                      className={`btn small ${kind === k.value ? 'primary' : ''}`}
                      onClick={() => setKind(k.value)}
                    >
                      {k.label}
                    </button>
                  ))}
                </div>
                <textarea
                  className="feedback-text"
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Describe the problem or idea…"
                  rows={5}
                  maxLength={4000}
                  autoFocus
                />
                <div className="muted">Page: {location.pathname}</div>
                {error && <p className="alert">{error}</p>}
                <div className="row" style={{ marginTop: 10 }}>
                  <button className="btn" onClick={close}>Cancel</button>
                  <span className="spacer" />
                  <button
                    className="btn primary"
                    disabled={status === 'sending' || message.trim().length < 3}
                    onClick={() => void submit()}
                  >
                    {status === 'sending' ? 'Sending…' : 'Send'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
