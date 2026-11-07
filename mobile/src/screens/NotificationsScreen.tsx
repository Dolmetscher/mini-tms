import React, { useCallback, useEffect, useState } from 'react';
import { FlatList, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { api } from '../api';
import type { Account, AppNotification, WsEvent } from '../api';
import { colors, common, fmtDateTime } from '../theme';

const TYPE_LABELS: Record<string, string> = {
  job_assigned: 'New job assigned',
  job_accepted: 'Job accepted',
  job_declined: 'Job declined',
  job_submitted: 'Job submitted',
  job_returned: 'Job returned to rework',
  job_reassigned: 'Job reassigned',
  job_canceled: 'Job canceled',
  deadline_reminder: 'Deadline reminder',
};

/** §8: внутренний центр уведомлений; тап открывает задание в активном аккаунте (FR-01). */
export default function NotificationsScreen(
  { account, wsEvent, onOpenJob }: {
    account: Account; wsEvent: WsEvent | null; onOpenJob: (jobId: number) => void;
  },
) {
  const [items, setItems] = useState<AppNotification[]>([]);

  const load = useCallback(async () => {
    try {
      setItems(await api.notifications(account));
    } catch {
      setItems([]);
    }
  }, [account]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (wsEvent?.type === 'notification') void load();
  }, [wsEvent, load]);

  const detail = (n: AppNotification): string => {
    const parts: string[] = [];
    if (typeof n.payload['document'] === 'string') parts.push(n.payload['document']);
    if (typeof n.payload['reason'] === 'string') parts.push(n.payload['reason']);
    if (typeof n.payload['comment'] === 'string') parts.push(n.payload['comment']);
    if (n.type === 'deadline_reminder' && typeof n.payload['offset'] === 'string') {
      parts.push(`deadline in ${n.payload['offset']}`);
    }
    return parts.join(' — ');
  };

  return (
    <View style={{ flex: 1 }}>
      <View style={[common.row, { paddingHorizontal: 12, marginBottom: 8 }]}>
        <Text style={{ fontSize: 16, fontWeight: 'bold' }}>Notifications</Text>
        <View style={common.spacer} />
        <TouchableOpacity style={common.button}
                          onPress={() => void api.markAllNotificationsRead(account).then(load)}>
          <Text style={common.buttonText}>Mark all read</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        data={items}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={{ padding: 12 }}
        renderItem={({ item }) => {
          const jobId = typeof item.payload['job_id'] === 'number' ? item.payload['job_id'] : null;
          return (
            <View style={[common.card, item.read_at && styles.read]}>
              <View style={common.row}>
                <Text style={{ fontWeight: '600', flex: 1 }}>{TYPE_LABELS[item.type] ?? item.type}</Text>
                {!item.read_at && (
                  <TouchableOpacity style={common.button}
                                    onPress={() => void api.markNotificationRead(account, item.id).then(load)}>
                    <Text style={common.buttonText}>Read</Text>
                  </TouchableOpacity>
                )}
              </View>
              {detail(item) ? <Text style={common.small}>{detail(item)}</Text> : null}
              <View style={[common.row, { marginTop: 4 }]}>
                <Text style={common.small}>{fmtDateTime(item.created_at)}</Text>
                <View style={common.spacer} />
                {jobId && (
                  <TouchableOpacity onPress={() => onOpenJob(jobId)}>
                    <Text style={{ color: colors.accent, fontWeight: '600' }}>Open job</Text>
                  </TouchableOpacity>
                )}
              </View>
            </View>
          );
        }}
        ListEmptyComponent={<Text style={styles.empty}>No notifications.</Text>}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  read: { opacity: 0.65 },
  empty: { textAlign: 'center', color: '#9CA3AF', marginTop: 40 },
});
