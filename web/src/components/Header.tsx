import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api } from '../api/client';
import type { AppNotification } from '../api/types';
import { useAuth } from '../state/AuthContext';
import { useUpdates } from '../ws/useUpdates';

export default function Header() {
  const { user, activeOrg, memberships, setActiveOrgId, logout } = useAuth();
  const [unread, setUnread] = useState(0);
  const location = useLocation();

  const refreshUnread = () => {
    api.notifications().then((list: AppNotification[]) => {
      setUnread(list.filter((n) => !n.read_at).length);
    }).catch(() => setUnread(0));
  };

  useEffect(refreshUnread, [location.pathname]);

  useUpdates((event) => {
    if (event.type === 'notification') refreshUnread();
  });

  const changePassword = async () => {
    const current = window.prompt('Current password:');
    if (!current) return;
    const next = window.prompt('New password (min 4 chars, must differ):');
    if (!next) return;
    try {
      await api.changePassword(current, next);
      window.alert('Password updated.');
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Password change failed');
    }
  };

  return (
    <header className="topbar">
      <Link to="/jobs" className="brand">Mini&#8209;TMS</Link>
      <nav className="nav">
        <Link to="/jobs" className={location.pathname.startsWith('/jobs') ? 'active' : ''}>Jobs</Link>
        {activeOrg && activeOrg.role !== 'translator' && (
          <>
            <Link to="/projects" className={location.pathname.startsWith('/projects') ? 'active' : ''}>Projects</Link>
            <Link to="/resources" className={location.pathname.startsWith('/resources') ? 'active' : ''}>TM / TB</Link>
            <Link to="/rates" className={location.pathname.startsWith('/rates') ? 'active' : ''}>Rates</Link>
            <Link to="/reports" className={location.pathname.startsWith('/reports') ? 'active' : ''}>Reports</Link>
          </>
        )}
        <Link to="/notifications" className={location.pathname.startsWith('/notifications') ? 'active' : ''}>
          Bell{unread > 0 ? ` (${unread})` : ''}
        </Link>
      </nav>
      <span className="spacer" />
      {memberships.length > 1 && activeOrg && (
        <select
          className="org-select"
          value={activeOrg.org_id}
          onChange={(e) => setActiveOrgId(Number(e.target.value))}
          title="Active organization"
        >
          {memberships.map((m) => (
            <option key={m.org_id} value={m.org_id}>{m.org_name}</option>
          ))}
        </select>
      )}
      <span className={`role-badge role-${activeOrg?.role}`}>{activeOrg?.role}</span>
      <span className="muted">{user?.username}</span>
      <button className="btn small" title="Change your password" onClick={() => void changePassword()}>Password</button>
      <button className="btn small" onClick={logout}>Log out</button>
    </header>
  );
}
