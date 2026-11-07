import React from 'react';
import { FlatList, Text, TouchableOpacity, View } from 'react-native';

import type { Account } from '../api';
import type { AccountMeta } from '../store';
import { removeAccount } from '../store';
import { colors, common } from '../theme';

interface Props {
  accounts: AccountMeta[];
  activeKey: string;
  onSwitch: (meta: AccountMeta) => void;
  onAdd: () => void;
  onBack: () => void;
}

/** FR-01: переключение аккаунтов без повторного ввода пароля, удаление локальных данных. */
export default function AccountsScreen({ accounts, activeKey, onSwitch, onAdd, onBack }: Props) {
  return (
    <View style={{ flex: 1, paddingHorizontal: 12 }}>
      <View style={[common.row, { marginBottom: 10 }]}>
        <TouchableOpacity style={common.button} onPress={onBack}>
          <Text style={common.buttonText}>← Back</Text>
        </TouchableOpacity>
        <Text style={{ fontSize: 16, fontWeight: 'bold' }}>Accounts</Text>
        <View style={common.spacer} />
        <TouchableOpacity style={[common.button, common.primaryButton]} onPress={onAdd}>
          <Text style={[common.buttonText, common.primaryButtonText]}>Add account</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        data={accounts}
        keyExtractor={(item) => item.key}
        renderItem={({ item }) => (
          <View style={common.card}>
            <TouchableOpacity style={common.row} onPress={() => onSwitch(item)} disabled={item.key === activeKey}>
              <View style={{ flex: 1 }}>
                <Text style={{ fontWeight: '600' }}>
                  {item.username} {item.key === activeKey ? '(active)' : ''}
                </Text>
                <Text style={common.small}>{item.orgName} · {item.role}</Text>
              </View>
              {item.key === activeKey ? (
                <Text style={common.small}>current</Text>
              ) : (
                <Text style={{ color: colors.accent }}>Switch</Text>
              )}
            </TouchableOpacity>
            <TouchableOpacity
              style={{ marginTop: 8 }}
              onPress={() => {
                void removeAccount(item.key).then(onBack);
              }}
            >
              <Text style={{ color: colors.red, fontSize: 13 }}>Remove account and local data</Text>
            </TouchableOpacity>
          </View>
        )}
        ListEmptyComponent={<Text style={[common.muted, { textAlign: 'center', marginTop: 30 }]}>No accounts yet.</Text>}
      />
    </View>
  );
}
