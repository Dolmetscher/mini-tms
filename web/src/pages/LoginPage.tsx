import { FormEvent, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { getApiBase, setApiBase } from '../api/client';
import { useAuth } from '../state/AuthContext';

export default function LoginPage() {
  const { token, ready, login } = useAuth();
  const navigate = useNavigate();
  const [server, setServer] = useState(getApiBase());
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  if (ready && token) return <Navigate to="/jobs" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    await doLogin(username, password);
  };

  const doLogin = async (u: string, p: string) => {
    setBusy(true);
    setError('');
    try {
      setApiBase(server.trim() || getApiBase());
      await login(u, p);
      navigate('/jobs');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <h1 className="brand">Mini&#8209;TMS CAT</h1>
        <p className="muted">Sign in to translate and manage jobs.</p>

        <label className="field">
          <span>Server address</span>
          <input value={server} onChange={(e) => setServer(e.target.value)} autoCapitalize="none" />
        </label>
        <label className="field">
          <span>Username</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>

        {error && <div className="alert">{error}</div>}

        <button className="btn primary" type="submit" disabled={busy || !username || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <div className="divider">demo accounts</div>
        <div className="row wrap">
          <button type="button" className="btn small" onClick={() => doLogin('pm', 'pm123')} disabled={busy}>
            Project Manager
          </button>
          <button type="button" className="btn small" onClick={() => doLogin('ann', 'ann123')} disabled={busy}>
            Linguist (ann)
          </button>
          <button type="button" className="btn small" onClick={() => doLogin('ivan', 'ivan123')} disabled={busy}>
            Linguist (ivan)
          </button>
          <button type="button" className="btn small" onClick={() => doLogin('admin', 'admin123')} disabled={busy}>
            Org Admin
          </button>
        </div>
      </form>
    </div>
  );
}
