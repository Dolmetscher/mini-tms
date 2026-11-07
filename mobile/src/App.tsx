import React, { useCallback, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { StatusBar as RNStatusBar, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';

import { api, wsUrl } from './api';
import type { Account, WsEvent } from './api';
import AccountsScreen from './screens/AccountsScreen';
import EditorScreen from './screens/EditorScreen';
import JobListScreen from './screens/JobListScreen';
import LoginScreen from './screens/LoginScreen';
import NotificationsScreen from './screens/NotificationsScreen';
import PMProjectsScreen from './screens/PMProjectsScreen';
import {
  getActiveKey, hydrateAccount, loadAccounts, setActiveKey, upsertAccount,
} from './store';
import { colors, common } from './theme';

export type Screen =
  | { name: 'jobs' }
  | { name: 'editor'; jobId: number }
  | { name: 'notifications' }
  | { name: 'pm' }
  | { name: 'accounts' }
  | { name: 'login' };

export default function App() {
  const [booting, setBooting] = useState(true);
  const [accounts, setAccounts] = useState<Awaited<ReturnType<typeof loadAccounts>>>([]);
  const [account, setAccount] = useState<Account | null>(null);
  const [screen, setScreen] = useState<Screen>({ name: 'jobs' });
  const [wsEvent, setWsEvent] = useState<WsEvent | null>(null);
  const [pendingCount, setPendingCount] = useState(0);

  // FR-01: восстановление активного аккаунта при запуске
  useEffect(() => {
    (async () => {
      try {
        const list = await loadAccounts();
        setAccounts(list);
        const activeKey = await getActiveKey();
        const meta = list.find((a) => a.key === activeKey) ?? list[0] ?? null;
        if (meta) {
          const hydrated = await hydrateAccount(meta);
          if (hydrated) {
            setAccount(hydrated);
            setScreen({ name: 'jobs' });
          } else {
            setScreen({ name: 'login' });
          }
        } else {
          setScreen({ name: 'login' });
        }
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  // Live-обновления: переподключение при обрыве (FR-03.9 «Нет связи»)
  useEffect(() => {
    if (!account) return;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    const connect = () => {
      socket = new WebSocket(wsUrl(account));
      socket.onmessage = (e) => {
        try {
          setWsEvent(JSON.parse(String(e.data)) as WsEvent);
        } catch {
          // игнорируем некорректное сообщение
        }
      };
      socket.onclose = () => {
        if (!disposed) retry = setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [account?.key]);

  const handleLoggedIn = useCallback(
    async (base: string, token: string, userId: number, username: string, orgId: number, orgName: string, role: Account['role']) => {
      const key = `${userId}:${orgId}`;
      await upsertAccount(
        { key, base, userId, username, orgId, orgName, role },
        token,
      );
      await setActiveKey(key);
      const acc: Account = { key, base, token, userId, username, orgId, orgName, role };
      setAccounts(await loadAccounts());
      setAccount(acc);
      setScreen({ name: 'jobs' });
    },
    [],
  );

  const switchAccount = useCallback(async (meta: (typeof accounts)[number] | null) => {
    if (meta === null) {
      setAccount(null);
      setScreen({ name: 'login' });
      return;
    }
    const hydrated = await hydrateAccount(meta);
    if (!hydrated) {
      setScreen({ name: 'login' });
      return;
    }
    // Проверка сессии: просроченный токен -> экран входа (FR-01)
    try {
      await api.me(hydrated);
      await setActiveKey(meta.key);
      setAccount(hydrated);
      setScreen({ name: 'jobs' });
    } catch {
      setScreen({ name: 'login' });
    }
  }, []);

  const onLogoutAccount = useCallback(async () => {
    await AsyncStorage.removeItem('tms.activeAccount');
    setAccount(null);
    setScreen({ name: 'login' });
  }, []);

  if (booting) {
    return (
      <View style={[styles.container, styles.center]}>
        <RNStatusBar barStyle="dark-content" />
      </View>
    );
  }

  if (!account || screen.name === 'login') {
    return (
      <View style={styles.container}>
        <StatusBar style="dark" />
        <LoginScreen
          onLoggedIn={handleLoggedIn}
          lastBase={account?.base}
        />
      </View>
    );
  }

  const isManager = account.role !== 'translator';

  return (
    <View style={styles.container}>
      <StatusBar style="dark" />
      <View style={styles.topbar}>
        <TouchableOpacity onPress={() => setScreen({ name: 'jobs' })}>
          <Text style={styles.brand}>Mini-TMS</Text>
        </TouchableOpacity>
        <View style={common.spacer} />
        {/* FR-01: активные аккаунт и организация видны всегда; тап — переключение */}
        <TouchableOpacity style={styles.accountChip} onPress={() => setScreen({ name: 'accounts' })}>
          <Text style={styles.accountText}>{account.username} · {account.orgName}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.tab, screen.name === 'notifications' && styles.tabActive]}
                          onPress={() => setScreen({ name: 'notifications' })}>
          <Text style={styles.tabText}>Bell</Text>
        </TouchableOpacity>
        {isManager && (
          <TouchableOpacity style={[styles.tab, screen.name === 'pm' && styles.tabActive]}
                            onPress={() => setScreen({ name: 'pm' })}>
            <Text style={styles.tabText}>PM</Text>
          </TouchableOpacity>
        )}
      </View>

      {screen.name === 'jobs' && (
        <JobListScreen
          account={account}
          wsEvent={wsEvent}
          onOpenJob={(jobId) => setScreen({ name: 'editor', jobId })}
          onLogout={onLogoutAccount}
        />
      )}
      {screen.name === 'editor' && (
        <EditorScreen
          account={account}
          jobId={screen.jobId}
          wsEvent={wsEvent}
          onBack={() => setScreen({ name: 'jobs' })}
          onPendingChange={setPendingCount}
        />
      )}
      {screen.name === 'notifications' && (
        <NotificationsScreen
          account={account}
          wsEvent={wsEvent}
          onOpenJob={(jobId) => setScreen({ name: 'editor', jobId })}
        />
      )}
      {screen.name === 'pm' && (
        <PMProjectsScreen account={account} wsEvent={wsEvent} />
      )}
      {screen.name === 'accounts' && (
        <AccountsScreen
          accounts={accounts}
          activeKey={account.key}
          onSwitch={(meta) => void switchAccount(meta)}
          onAdd={() => setScreen({ name: 'login' })}
          onBack={() => setScreen({ name: 'jobs' })}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    paddingTop: (RNStatusBar.currentHeight ?? 24) + 4,
  },
  center: { alignItems: 'center', justifyContent: 'center' },
  topbar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  brand: { fontSize: 18, fontWeight: 'bold', color: colors.accent },
  accountChip: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    backgroundColor: colors.blueBg,
  },
  accountText: { fontSize: 12, color: '#1D4ED8' },
  tab: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
  },
  tabActive: { borderColor: colors.accent },
  tabText: { fontSize: 13, color: colors.text },
});
