import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, FlatList, RefreshControl, StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';

import { api } from '../api';
import type { Account, Job, WsEvent } from '../api';
import { STATUS_LABELS } from '../labels';
import { colors, common, fmtDateTime, statusColors } from '../theme';

interface Props {
  account: Account;
  wsEvent: WsEvent | null;
  onOpenJob: (jobId: number) => void;
  onLogout: () => void;
}

const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: '', label: 'Все' },
  { key: 'assigned', label: 'Назначено' },
  { key: 'accepted', label: 'Принято' },
  { key: 'in_progress', label: 'В работе' },
  { key: 'submitted', label: 'Сдано' },
  { key: 'rework', label: 'Доработка' },
  { key: 'completed', label: 'Завершено' },
];

/** FR-02: список заданий с поиском, фильтрами и принятием/отклонением. */
export default function JobListScreen({ account, wsEvent, onOpenJob, onLogout }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [decliningJob, setDecliningJob] = useState<Job | null>(null);
  const [declineReason, setDeclineReason] = useState('');

  const load = useCallback(async () => {
    try {
      setJobs(await api.jobs(account, { status: statusFilter || undefined, q: search || undefined, mine: true }));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить задания');
    }
  }, [account, statusFilter, search]);

  useEffect(() => {
    void load();
  }, [load]);

  // Live-обновление: события по заданиям перечитывают список (§11: ≤10 c)
  useEffect(() => {
    if (wsEvent && (wsEvent.type.startsWith('job_') || wsEvent.type === 'segment_updated')) void load();
  }, [wsEvent, load]);

  const accept = async (job: Job) => {
    try {
      await api.acceptJob(account, job.id);
      void load();
    } catch (err) {
      Alert.alert('Не удалось принять', err instanceof Error ? err.message : 'Неизвестная ошибка');
    }
  };

  const decline = async () => {
    if (!decliningJob || !declineReason.trim()) return;
    try {
      await api.declineJob(account, decliningJob.id, declineReason.trim());
      setDecliningJob(null);
      setDeclineReason('');
      void load();
    } catch (err) {
      Alert.alert('Не удалось отклонить', err instanceof Error ? err.message : 'Неизвестная ошибка');
    }
  };

  return (
    <View style={{ flex: 1 }}>
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={[common.card, { paddingVertical: 10 }]}>
        <TextInput
          style={common.input}
          placeholder="Поиск по проекту или документу…"
          value={search}
          onChangeText={setSearch}
        />
        <View style={[common.row, common.wrap, { marginTop: 8 }]}>
          {STATUS_FILTERS.map((f) => (
            <TouchableOpacity
              key={f.key}
              style={[styles.filterChip, statusFilter === f.key && styles.filterChipActive]}
              onPress={() => setStatusFilter(f.key)}
            >
              <Text style={[styles.filterText, statusFilter === f.key && styles.filterTextActive]}>{f.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {decliningJob && (
        <View style={common.card}>
          <Text style={{ fontWeight: '600' }}>Отклонить «{decliningJob.document_name}» — причина:</Text>
          <TextInput style={[common.input, { marginTop: 8 }]} value={declineReason} onChangeText={setDeclineReason} placeholder="Причина…" />
          <View style={[common.row, { marginTop: 8 }]}>
            <TouchableOpacity style={common.button} onPress={() => setDecliningJob(null)}>
              <Text style={common.buttonText}>Отмена</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[common.button, common.primaryButton, !declineReason.trim() && common.disabled]}
                              onPress={() => void decline()} disabled={!declineReason.trim()}>
              <Text style={[common.buttonText, common.primaryButtonText]}>Отклонить</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      <FlatList
        data={jobs}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={{ padding: 12 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load().finally(() => setRefreshing(false)); }} />}
        renderItem={({ item }) => {
          const sc = statusColors[item.status] ?? statusColors.new;
          const pct = item.total_segments ? Math.round((item.confirmed_segments / item.total_segments) * 100) : 0;
          const mine = item.assignee?.id === account.userId;
          const editable = mine && ['assigned', 'accepted', 'in_progress', 'rework'].includes(item.status);
          return (
            <TouchableOpacity style={common.card} onPress={() => editable && onOpenJob(item.id)}>
              <Text style={styles.jobTitle}>{item.document_name}</Text>
              <Text style={common.small}>{item.project_name} · {item.source_lang} → {item.target_lang}</Text>
              <View style={[common.row, { marginTop: 6 }]}>
                <View style={styles.track}>
                  <View style={[styles.fill, { flex: pct }]} />
                  <View style={[styles.rest, { flex: 100 - pct }]} />
                </View>
                <Text style={common.small}>{item.confirmed_segments}/{item.total_segments} · {item.total_words} сл.</Text>
              </View>
              <View style={[common.row, { marginTop: 8 }]}>
                <View style={[common.chip, { backgroundColor: sc.bg }]}>
                  <Text style={[common.chipText, { color: sc.fg }]}>{STATUS_LABELS[item.status] ?? item.status}</Text>
                </View>
                {item.is_overdue && (
                  <View style={[common.chip, { backgroundColor: colors.redBg }]}>
                    <Text style={[common.chipText, { color: colors.red }]}>просрочено</Text>
                  </View>
                )}
                <View style={common.spacer} />
                <Text style={common.small}>срок: {fmtDateTime(item.deadline)}</Text>
              </View>
              {mine && item.status === 'assigned' && (
                <View style={[common.row, { marginTop: 10 }]}>
                  <TouchableOpacity style={[common.button, common.primaryButton]} onPress={() => void accept(item)}>
                    <Text style={[common.buttonText, common.primaryButtonText]}>Принять</Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={common.button} onPress={() => setDecliningJob(item)}>
                    <Text style={common.buttonText}>Отклонить</Text>
                  </TouchableOpacity>
                </View>
              )}
            </TouchableOpacity>
          );
        }}
        ListEmptyComponent={!jobs.length ? <Text style={styles.empty}>Нет заданий по этим фильтрам.</Text> : null}
      />

      <View style={[common.row, { padding: 10 }]}>
        <TouchableOpacity onPress={onLogout}>
          <Text style={{ color: colors.muted, fontSize: 13 }}>Выйти (аккаунты — чип выше)</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  error: {
    color: colors.red, backgroundColor: colors.redBg, borderRadius: 8,
    padding: 10, marginHorizontal: 12, marginTop: 8,
  },
  jobTitle: { fontSize: 16, fontWeight: '600', color: colors.text },
  track: {
    flex: 1, height: 8, borderRadius: 4, overflow: 'hidden',
    flexDirection: 'row', backgroundColor: colors.grayBg,
  },
  fill: { backgroundColor: colors.green },
  rest: { backgroundColor: colors.grayBg },
  filterChip: {
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 12,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card,
  },
  filterChipActive: { borderColor: colors.accent, backgroundColor: colors.blueBg },
  filterText: { fontSize: 12, color: colors.muted },
  filterTextActive: { color: colors.accent, fontWeight: '600' },
  empty: { textAlign: 'center', color: '#9CA3AF', marginTop: 40 },
});
