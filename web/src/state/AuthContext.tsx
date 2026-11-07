import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { api, setActiveOrg, storeToken } from '../api/client';
import type { Membership, User } from '../api/types';

interface AuthContextValue {
  user: User | null;
  token: string | null;
  ready: boolean;
  memberships: Membership[];
  activeOrg: Membership | null;
  setActiveOrgId: (orgId: number) => void;
  login: (username: string, password: string) => Promise<LoginResult>;
  logout: () => void;
}

export interface LoginResult {
  needsOrgChoice: boolean;
  memberships: Membership[];
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readStoredOrg(memberships: Membership[], storedOrgId: number | null): Membership | null {
  if (!storedOrgId) return null;
  return memberships.find((m) => m.org_id === storedOrgId) ?? null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('tms_token'));
  const [ready, setReady] = useState(!localStorage.getItem('tms_token'));

  // Восстановление сессии: /me возвращает memberships и подтверждает токен
  useEffect(() => {
    if (!token) return;
    api
      .me()
      .then((res) => {
        setUser(res.user);
        setMemberships(res.memberships);
        if (res.memberships.length > 0) {
          const stored = Number(localStorage.getItem('tms_org')) || res.memberships[0].org_id;
          const active = readStoredOrg(res.memberships, stored) ?? res.memberships[0];
          setActiveOrg(active.org_id);
        }
      })
      .catch(() => {
        storeToken(null);
        setToken(null);
      })
      .finally(() => setReady(true));
  }, [token]);

  const login = useCallback(async (username: string, password: string): Promise<LoginResult> => {
    const res = await api.login(username, password);
    storeToken(res.access_token);
    setToken(res.access_token);
    setUser(res.user);
    setMemberships(res.memberships);
    if (res.memberships.length > 0) {
      const stored = Number(localStorage.getItem('tms_org')) || res.memberships[0].org_id;
      const active = readStoredOrg(res.memberships, stored) ?? res.memberships[0];
      setActiveOrg(active.org_id);
    }
    return { needsOrgChoice: res.memberships.length > 1, memberships: res.memberships };
  }, []);

  const logout = useCallback(() => {
    storeToken(null);
    setActiveOrg(null);
    setToken(null);
    setUser(null);
    setMemberships([]);
  }, []);

  const setActiveOrgId = useCallback((orgId: number) => {
    setActiveOrg(orgId);
    // Перезагрузка, чтобы все экраны перечитали данные в контексте новой организации.
    window.location.assign('/jobs');
  }, []);

  const activeOrg = useMemo(
    () => memberships.find((m) => m.org_id === Number(localStorage.getItem('tms_org') || 0)) ?? memberships[0] ?? null,
    [memberships],
  );

  return (
    <AuthContext.Provider value={{ user, token, ready, memberships, activeOrg, setActiveOrgId, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
