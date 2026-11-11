import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api, storeToken } from '../api/client';
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
    const current = window.prompt('Текущий пароль:');
    if (!current) return;
    const next = window.prompt('Новый пароль (мин. 4 символа, должен отличаться):');
    if (!next) return;
    try {
      const { access_token } = await api.changePassword(current, next);
      // Смена пароля отзывает все токены: сохраняем свежий, чтобы сессия
      // продолжилась без повторного входа.
      storeToken(access_token);
      window.alert('Пароль обновлен.');
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Не удалось сменить пароль');
    }
  };

  return (
    <header className="topbar">
      <Link to="/jobs" className="brand">Mini&#8209;TMS</Link>
      <nav className="nav">
        <Link to="/jobs" className={location.pathname.startsWith('/jobs') ? 'active' : ''}>Задания</Link>
        {activeOrg && activeOrg.role !== 'translator' && (
          <>
            <Link to="/projects" className={location.pathname.startsWith('/projects') ? 'active' : ''}>Проекты</Link>
            <Link to="/resources" className={location.pathname.startsWith('/resources') ? 'active' : ''}>TM / TB</Link>
            <Link to="/rates" className={location.pathname.startsWith('/rates') ? 'active' : ''}>Ставки</Link>
            <Link to="/reports" className={location.pathname.startsWith('/reports') ? 'active' : ''}>Отчеты</Link>
            <Link to="/feedback" className={location.pathname.startsWith('/feedback') ? 'active' : ''}>Отзывы</Link>
          </>
        )}
        <Link to="/notifications" className={location.pathname.startsWith('/notifications') ? 'active' : ''}>
          Уведомления{unread > 0 ? ` (${unread})` : ''}
        </Link>
      </nav>
      <span className="spacer" />
      {memberships.length > 1 && activeOrg && (
        <select
          className="org-select"
          value={activeOrg.org_id}
          onChange={(e) => setActiveOrgId(Number(e.target.value))}
          title="Активная организация"
        >
          {memberships.map((m) => (
            <option key={m.org_id} value={m.org_id}>{m.org_name}</option>
          ))}
        </select>
      )}
      <span className={`role-badge role-${activeOrg?.role}`}>{activeOrg?.role}</span>
      <span className="muted">{user?.username}</span>
      <a className="btn small" href="/manual.html" target="_blank" rel="noreferrer" title="Справочный портал (руководство пользователя)">Справка</a>
      <button className="btn small" title="Сменить свой пароль" onClick={() => void changePassword()}>Пароль</button>
      <button className="btn small" onClick={logout}>Выйти</button>
    </header>
  );
}
