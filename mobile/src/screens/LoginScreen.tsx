import React, { useState } from 'react';
import {
  KeyboardAvoidingView, Platform, StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';

import { login } from '../api';
import type { Account, Membership } from '../api';
import { DEFAULT_API_BASE } from '../config';
import { colors, common } from '../theme';

interface Props {
  onLoggedIn: (
    base: string, token: string, userId: number, username: string,
    orgId: number, orgName: string, role: Account['role'],
  ) => void;
  lastBase?: string;
}

/** FR-01: вход; при нескольких организациях — явный выбор активной. */
export default function LoginScreen({ onLoggedIn, lastBase }: Props) {
  const [base, setBase] = useState(lastBase ?? DEFAULT_API_BASE);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [memberships, setMemberships] = useState<Membership[] | null>(null);
  const [session, setSession] = useState<{ token: string; user: { id: number; username: string } } | null>(null);

  const doLogin = async () => {
    if (busy || !username || !password) return;
    setBusy(true);
    setError('');
    try {
      const res = await login(base.trim(), username, password);
      if (res.memberships.length === 1) {
        const m = res.memberships[0];
        onLoggedIn(base.trim(), res.access_token, res.user.id, res.user.username, m.org_id, m.org_name, m.role);
      } else {
        setSession({ token: res.access_token, user: res.user });
        setMemberships(res.memberships);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setBusy(false);
    }
  };

  const pickOrg = (m: Membership) => {
    if (!session) return;
    onLoggedIn(base.trim(), session.token, session.user.id, session.user.username, m.org_id, m.org_name, m.role);
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.card}>
        <Text style={styles.brand}>Mini-TMS CAT</Text>
        <Text style={common.muted}>Sign in to translate and manage jobs.</Text>

        {memberships ? (
          <>
            <Text style={[styles.label, { marginTop: 20 }]}>Choose organization:</Text>
            {memberships.map((m) => (
              <TouchableOpacity key={m.org_id} style={[common.button, styles.orgButton]} onPress={() => pickOrg(m)}>
                <Text style={common.buttonText}>{m.org_name} — {m.role}</Text>
              </TouchableOpacity>
            ))}
          </>
        ) : (
          <>
            <Text style={styles.label}>Server address</Text>
            <TextInput
              style={common.input} value={base} onChangeText={setBase}
              autoCapitalize="none" keyboardType="url" placeholder="http://192.168.1.42:8000"
            />
            <Text style={styles.label}>Username</Text>
            <TextInput style={common.input} value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} />
            <Text style={styles.label}>Password</Text>
            <TextInput style={common.input} value={password} onChangeText={setPassword} secureTextEntry />

            {error ? <Text style={styles.error}>{error}</Text> : null}

            <TouchableOpacity
              style={[common.button, common.primaryButton, (!username || !password || busy) && common.disabled]}
              onPress={() => void doLogin()} disabled={!username || !password || busy}
            >
              <Text style={[common.buttonText, common.primaryButtonText]}>
                {busy ? 'Signing in…' : 'Sign in'}
              </Text>
            </TouchableOpacity>

            <Text style={styles.hint}>
              Emulator uses http://10.0.2.2:8000 — for a real phone enter your computer's Wi-Fi IP.
            </Text>
          </>
        )}
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  card: { flex: 1, justifyContent: 'center', paddingHorizontal: 28 },
  brand: { fontSize: 30, fontWeight: 'bold', color: colors.accent, textAlign: 'center' },
  label: { fontSize: 13, color: colors.muted, marginBottom: 4, marginTop: 12 },
  error: {
    color: colors.red, backgroundColor: colors.redBg, borderColor: '#FECACA',
    borderWidth: 1, borderRadius: 8, padding: 10, marginTop: 12, fontSize: 13,
  },
  hint: { marginTop: 18, fontSize: 12, color: '#9CA3AF', textAlign: 'center', lineHeight: 17 },
  orgButton: { marginTop: 8 },
});
