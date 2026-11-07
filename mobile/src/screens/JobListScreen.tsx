import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, FlatList, RefreshControl, StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';

import { api } from '../api';
import type { Account, Job, WsEvent } from '../api';
import { colors, common, fmtDateTime, statusColors } from '../theme';

interface Props {
  account: Account;
  wsEvent: WsEvent | null;
  onOpenJob: (jobId: number) => void;
  onLogout: () => void;
}

const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: '', label: 'All' },
  { key: 'assigned', label: 'Assigned' },
  { key: 'accepted', label: 'Accepted' },
  { key: 'in_progress', label: 'In progress' },
  { key: 'submitted', label: 'Submitted' },
  { key: 'rework', label: 'Rework' },
  { key: 'completed', label: 'Completed' },
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
      setError(err instanceof Error ? err.message : 'Failed to load jobs');
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
      Alert.alert('Accept failed', err instanceof Error ? err.message : 'Unknown error');
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
      Alert.alert('Decline failed', err instanceof Error ? err.message : 'Unknown error');
    }
  };

  return (
    <View style={{ flex: 1 }}>
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={[common.card, { paddingVertical: 10 }]}>
        <TextInput
          style={common.input}
          placeholder="Search project or document…"
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
          <Text style={{ fontWeight: '600' }}>Decline “{decliningJob.document_name}” — reason:</Text>
          <TextInput style={[common.input, { marginTop: 8 }]} value={declineReason} onChangeText={setDeclineReason} placeholder="Reason…" />
          <View style={[common.row, { marginTop: 8 }]}>
            <TouchableOpacity style={common.button} onPress={() => setDecliningJob(null)}>
              <Text style={common.buttonText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[common.button, common.primaryButton, !declineReason.trim() && common.disabled]}
                              onPress={() => void decline()} disabled={!declineReason.trim()}>
              <Text style={[common.buttonText, common.primaryButtonText]}>Decline</Text>
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
                <Text style={common.small}>{item.confirmed_segments}/{item.total_segments} · {item.total_words}w</Text>
              </View>
              <View style={[common.row, { marginTop: 8 }]}>
                <View style={[common.chip, { backgroundColor: sc.bg }]}>
                  <Text style={[common.chipText, { color: sc.fg }]}>{item.status.replace('_', ' ')}</Text>
                </View>
                {item.is_overdue && (
                  <View style={[common.chip, { backgroundColor: colors.redBg }]}>
                    <Text style={[common.chipText, { color: colors.red }]}>overdue</Text>
                  </View>
                )}
                <View style={common.spacer} />
                <Text style={common.small}>due {fmtDateTime(item.deadline)}</Text>
              </View>
              {mine && item.status === 'assigned' && (
                <View style={[common.row, { marginTop: 10 }]}>
                  <TouchableOpacity style={[common.button, common.primaryButton]} onPress={() => void accept(item)}>
                    <Text style={[common.buttonText, common.primaryButtonText]}>Accept</Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={common.button} onPress={() => setDecliningJob(item)}>
                    <Text style={common.buttonText}>Decline</Text>
                  </TouchableOpacity>
                </View>
              )}
            </TouchableOpacity>
          );
        }}
        ListEmptyComponent={!jobs.length ? <Text style={styles.empty}>No jobs match the filters.</Text> : null}
      />

      <View style={[common.row, { padding: 10 }]}>
        <TouchableOpacity onPress={onLogout}>
          <Text style={{ color: colors.muted, fontSize: 13 }}>Log out (switch accounts via chip above)</Text>
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
