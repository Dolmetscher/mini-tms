/** Хранилище аккаунтов (SecureStore для токенов, FR-01/§11) и офлайн-черновиков (FR-04). */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';

import type { Account } from './api';

const ACCOUNTS_KEY = 'tms.accounts'; // список без токенов
const TOKEN_PREFIX = 'tms.token.'; // токены — в SecureStore
const ACTIVE_KEY = 'tms.activeAccount';
const DRAFT_PREFIX = 'tms.draft.';
const PENDING_PREFIX = 'tms.pending.';

export interface AccountMeta {
  key: string;
  base: string;
  userId: number;
  username: string;
  orgId: number;
  orgName: string;
  role: Account['role'];
}

export function accountKey(userId: number, orgId: number): string {
  return `${userId}:${orgId}`;
}

export async function loadAccounts(): Promise<AccountMeta[]> {
  const raw = await AsyncStorage.getItem(ACCOUNTS_KEY);
  return raw ? (JSON.parse(raw) as AccountMeta[]) : [];
}

async function saveAccounts(list: AccountMeta[]): Promise<void> {
  await AsyncStorage.setItem(ACCOUNTS_KEY, JSON.stringify(list));
}

export async function saveToken(key: string, token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_PREFIX + key, token);
}

export async function readToken(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(TOKEN_PREFIX + key);
  } catch {
    return await AsyncStorage.getItem(TOKEN_PREFIX + key);
  }
}

export async function deleteToken(key: string): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_PREFIX + key).catch(() => undefined);
  await AsyncStorage.removeItem(TOKEN_PREFIX + key).catch(() => undefined);
}

export async function upsertAccount(meta: AccountMeta, token: string): Promise<void> {
  const list = await loadAccounts();
  const filtered = list.filter((a) => a.key !== meta.key);
  filtered.push(meta);
  await saveAccounts(filtered);
  await saveToken(meta.key, token);
}

export async function removeAccount(key: string): Promise<void> {
  const list = await loadAccounts();
  await saveAccounts(list.filter((a) => a.key !== key));
  await deleteToken(key);
  const active = await AsyncStorage.getItem(ACTIVE_KEY);
  if (active === key) await AsyncStorage.removeItem(ACTIVE_KEY);
}

export async function setActiveKey(key: string): Promise<void> {
  await AsyncStorage.setItem(ACTIVE_KEY, key);
}

export async function getActiveKey(): Promise<string | null> {
  return AsyncStorage.getItem(ACTIVE_KEY);
}

/** Полный аккаунт = метаданные + токен из SecureStore. null, если сессии нет. */
export async function hydrateAccount(meta: AccountMeta): Promise<Account | null> {
  const token = await readToken(meta.key);
  if (!token) return null;
  return {
    key: meta.key,
    base: meta.base,
    token,
    userId: meta.userId,
    username: meta.username,
    orgId: meta.orgId,
    orgName: meta.orgName,
    role: meta.role,
  };
}

// --- Офлайн-черновики и очередь несинхронизированных правок (FR-04) ---

export interface PendingChange {
  segmentId: number;
  target_text: string;
  confirmed?: boolean;
  base_updated_at?: string;
}

export async function saveDraft(accountKeyStr: string, segmentId: number, text: string): Promise<void> {
  await AsyncStorage.setItem(
    `${DRAFT_PREFIX}${accountKeyStr}.${segmentId}`,
    JSON.stringify({ text, ts: Date.now() }),
  );
}

export async function loadDraft(accountKeyStr: string, segmentId: number): Promise<string | null> {
  const raw = await AsyncStorage.getItem(`${DRAFT_PREFIX}${accountKeyStr}.${segmentId}`);
  if (!raw) return null;
  try {
    return (JSON.parse(raw) as { text: string }).text;
  } catch {
    return null;
  }
}

export async function clearDraft(accountKeyStr: string, segmentId: number): Promise<void> {
  await AsyncStorage.removeItem(`${DRAFT_PREFIX}${accountKeyStr}.${segmentId}`);
}

export async function enqueuePending(accountKeyStr: string, change: PendingChange): Promise<void> {
  const raw = await AsyncStorage.getItem(PENDING_PREFIX + accountKeyStr);
  const list: PendingChange[] = raw ? JSON.parse(raw) : [];
  const filtered = list.filter((c) => c.segmentId !== change.segmentId);
  filtered.push(change);
  await AsyncStorage.setItem(PENDING_PREFIX + accountKeyStr, JSON.stringify(filtered));
}

export async function dequeuePending(accountKeyStr: string, segmentId: number): Promise<void> {
  const raw = await AsyncStorage.getItem(PENDING_PREFIX + accountKeyStr);
  const list: PendingChange[] = raw ? JSON.parse(raw) : [];
  await AsyncStorage.setItem(
    PENDING_PREFIX + accountKeyStr,
    JSON.stringify(list.filter((c) => c.segmentId !== segmentId)),
  );
}

export async function listPending(accountKeyStr: string): Promise<PendingChange[]> {
  const raw = await AsyncStorage.getItem(PENDING_PREFIX + accountKeyStr);
  return raw ? JSON.parse(raw) : [];
}
