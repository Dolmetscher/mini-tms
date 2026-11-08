import React, { useCallback, useEffect, useState } from 'react';
import { Alert, FlatList, Modal, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import * as DocumentPicker from 'expo-document-picker';

import { api } from '../api';
import type { Account, DocumentInfo, Job, Member, Project, WsEvent } from '../api';
import { PROC_STATUS_LABELS, STATUS_LABELS } from '../labels';
import { colors, common, fmtDateTime, statusColors } from '../theme';

/** FR-10/FR-11/FR-14: проекты со смартфона — создание, загрузка файлов, назначение, приемка. */
export default function PMProjectsScreen({ account, wsEvent }: { account: Account; wsEvent: WsEvent | null }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<number | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState('');
  // создание проекта
  const [name, setName] = useState('');
  const [source, setSource] = useState('en');
  const [targets, setTargets] = useState('ru');
  const [instructions, setInstructions] = useState('');
  // назначение
  const [assignJob, setAssignJob] = useState<Job | null>(null);
  const [assignDeadline, setAssignDeadline] = useState('');
  // возврат
  const [returnJob, setReturnJob] = useState<Job | null>(null);
  const [returnComment, setReturnComment] = useState('');

  const load = useCallback(async () => {
    try {
      const list = await api.projects(account);
      setProjects(list);
      if (selectedProject !== null) {
        setJobs(await api.projectJobs(account, selectedProject));
        const meta = list.find((p) => p.id === selectedProject);
        setMembers(meta ? await api.members(account, account.orgId) : []);
      }
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить');
    }
  }, [account, selectedProject]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (wsEvent?.type.startsWith('job_')) void load();
  }, [wsEvent, load]);

  // Опрос асинхронной обработки документов (FR-10)
  useEffect(() => {
    if (!documents.some((d) => d.processing_status === 'processing')) return;
    const timer = setInterval(() => void load(), 1500);
    return () => clearInterval(timer);
  }, [documents, load]);

  const createProject = async () => {
    if (!name.trim() || !targets.trim()) return;
    try {
      await api.createProject(account, {
        name: name.trim(),
        source_lang: source.trim(),
        target_langs: targets.split(',').map((t) => t.trim()).filter(Boolean),
        instructions,
        mt_enabled: true,
        deadline: null,
      });
      setName('');
      setInstructions('');
      void load();
    } catch (err) {
      Alert.alert('Не удалось создать', err instanceof Error ? err.message : 'Неизвестная ошибка');
    }
  };

  const uploadDocument = async () => {
    if (selectedProject === null) {
      Alert.alert('Сначала выберите проект');
      return;
    }
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['text/plain', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/xml', 'text/xml'],
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.length) return;
      const asset = result.assets[0];
      const doc = await api.uploadDocument(account, selectedProject, {
        uri: asset.uri,
        name: asset.name ?? 'document',
        mime: asset.mimeType ?? 'application/octet-stream',
      });
      setDocuments((prev) => [doc, ...prev]);
      void load();
    } catch (err) {
      Alert.alert('Не удалось загрузить', err instanceof Error ? err.message : 'Неизвестная ошибка');
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      void load();
    } catch (err) {
      Alert.alert('Действие не удалось', err instanceof Error ? err.message : 'Неизвестная ошибка');
    }
  };

  const translators = members.filter((m) => m.role === 'translator');

  return (
    <View style={{ flex: 1 }}>
      {error ? <Text style={styles.error}>{error}</Text> : null}

      {/* Создание проекта (FR-10) */}
      <View style={common.card}>
        <Text style={{ fontWeight: 'bold', marginBottom: 6 }}>Новый проект</Text>
        <TextInput style={common.input} placeholder="Название проекта…" value={name} onChangeText={setName} />
        <View style={[common.row, { marginTop: 8 }]}>
          <Text style={common.small}>исходный</Text>
          <TextInput style={[common.input, { width: 60 }]} value={source} onChangeText={setSource} />
          <Text style={common.small}>целевые (,)</Text>
          <TextInput style={[common.input, { flex: 1 }]} value={targets} onChangeText={setTargets} />
        </View>
        <TextInput
          style={[common.input, { marginTop: 8 }]}
          placeholder="Инструкции…"
          value={instructions} onChangeText={setInstructions}
        />
        <TouchableOpacity
          style={[common.button, common.primaryButton, { marginTop: 8 }, (!name.trim() || !targets.trim()) && common.disabled]}
          disabled={!name.trim() || !targets.trim()}
          onPress={() => void createProject()}
        >
          <Text style={[common.buttonText, common.primaryButtonText]}>Создать проект</Text>
        </TouchableOpacity>
      </View>

      {/* Выбор проекта + загрузка документа */}
      <View style={common.card}>
        <Text style={{ fontWeight: 'bold', marginBottom: 6 }}>Проект</Text>
        <View style={[common.row, common.wrap]}>
          {projects.map((p) => (
            <TouchableOpacity
              key={p.id}
              style={[styles.projectChip, selectedProject === p.id && styles.projectChipActive]}
              onPress={() => { setSelectedProject(p.id); setDocuments([]); }}
            >
              <Text style={[styles.projectChipText, selectedProject === p.id && styles.projectChipTextActive]}>
                {p.name}
              </Text>
            </TouchableOpacity>
          ))}
          {projects.length === 0 && <Text style={common.small}>Пока нет проектов.</Text>}
        </View>
        {selectedProject !== null && (
          <TouchableOpacity style={[common.button, { marginTop: 8 }]} onPress={() => void uploadDocument()}>
            <Text style={common.buttonText}>Загрузить документ (TXT / DOCX / XLIFF)</Text>
          </TouchableOpacity>
        )}
        {documents.map((d) => {
          const badge = statusColors[d.processing_status] ?? statusColors.processing;
          return (
            <View key={d.id} style={[common.row, { marginTop: 6 }]}>
              <View style={[common.chip, { backgroundColor: badge.bg }]}>
                <Text style={[common.chipText, { color: badge.fg }]}>{PROC_STATUS_LABELS[d.processing_status] ?? d.processing_status}</Text>
              </View>
              <Text style={[common.small, { flex: 1 }]} numberOfLines={1}>
                {d.name}{d.processing_error ? ` — ${d.processing_error}` : ''}
              </Text>
            </View>
          );
        })}
      </View>

      {/* Задания проекта: назначение, приемка, отмена (FR-11/FR-14) */}
      {selectedProject !== null && (
        <FlatList
          data={jobs}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={{ paddingHorizontal: 12 }}
          renderItem={({ item }) => {
            const badge = statusColors[item.status] ?? statusColors.new;
            return (
              <View style={common.card}>
                <View style={common.row}>
                  <Text style={{ fontWeight: '600', flex: 1 }}>{item.document_name} → {item.target_lang}</Text>
                  <View style={[common.chip, { backgroundColor: badge.bg }]}>
                    <Text style={[common.chipText, { color: badge.fg }]}>{STATUS_LABELS[item.status] ?? item.status}</Text>
                  </View>
                </View>
                <Text style={common.small}>
                  {item.assignee?.username ?? 'не назначено'} · {item.confirmed_segments}/{item.total_segments} · срок: {fmtDateTime(item.deadline)}
                </Text>
                <View style={[common.row, common.wrap, { marginTop: 8 }]}>
                  {['unassigned', 'assigned', 'declined'].includes(item.status) && (
                    <TouchableOpacity style={[common.button, common.primaryButton]}
                                      onPress={() => setAssignJob(item)}>
                      <Text style={[common.buttonText, common.primaryButtonText]}>Назначить</Text>
                    </TouchableOpacity>
                  )}
                  {item.status === 'submitted' && (
                    <>
                      <TouchableOpacity style={[common.button, common.primaryButton]}
                                        onPress={() => void act(() => api.completeJob(account, item.id))}>
                        <Text style={[common.buttonText, common.primaryButtonText]}>Завершить</Text>
                      </TouchableOpacity>
                      <TouchableOpacity style={common.button} onPress={() => setReturnJob(item)}>
                        <Text style={common.buttonText}>Вернуть</Text>
                      </TouchableOpacity>
                    </>
                  )}
                  {!['completed', 'canceled'].includes(item.status) && (
                    <TouchableOpacity style={common.button}
                                      onPress={() => Alert.alert('Отмена задания', 'Отменить это задание?', [
                                        { text: 'Нет' },
                                        { text: 'Да', style: 'destructive', onPress: () => void act(() => api.cancelJob(account, item.id)) },
                                      ])}>
                      <Text style={common.buttonText}>Отменить</Text>
                    </TouchableOpacity>
                  )}
                </View>
              </View>
            );
          }}
          ListEmptyComponent={<Text style={styles.empty}>Загрузите документ, чтобы создать задания.</Text>}
        />
      )}

      {/* Модалка назначения: выбор переводчика + индивидуальный дедлайн */}
      <Modal visible={assignJob !== null} transparent animationType="fade" onRequestClose={() => setAssignJob(null)}>
        <View style={styles.modalBackdrop}>
          <View style={[common.card, styles.modalCard]}>
            <Text style={{ fontSize: 16, fontWeight: 'bold' }}>Назначить переводчика</Text>
            <Text style={common.small}>{assignJob?.document_name} → {assignJob?.target_lang}</Text>
            <Text style={styles.conflictTitle}>Индивидуальный дедлайн (опционально, UTC)</Text>
            <TextInput
              style={common.input}
              placeholder="2026-12-31T18:00:00"
              value={assignDeadline}
              onChangeText={setAssignDeadline}
              autoCapitalize="none"
            />
            <FlatList
              data={translators}
              keyExtractor={(item) => String(item.id)}
              style={{ maxHeight: 200, marginTop: 8 }}
              renderItem={({ item }) => (
                <TouchableOpacity
                  style={styles.memberRow}
                  onPress={() => {
                    if (!assignJob) return;
                    const deadline = assignDeadline.trim()
                      ? new Date(assignDeadline.trim()).toISOString()
                      : null;
                    void act(() => api.assignJob(account, assignJob.id, item.user.id, deadline));
                    setAssignJob(null);
                    setAssignDeadline('');
                  }}
                >
                  <Text>{item.user.username}</Text>
                </TouchableOpacity>
              )}
              ListEmptyComponent={<Text style={common.small}>В организации нет переводчиков.</Text>}
            />
            <TouchableOpacity style={[common.button, { marginTop: 8 }]} onPress={() => setAssignJob(null)}>
              <Text style={common.buttonText}>Закрыть</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Модалка возврата на доработку с комментарием (FR-14) */}
      <Modal visible={returnJob !== null} transparent animationType="fade" onRequestClose={() => setReturnJob(null)}>
        <View style={styles.modalBackdrop}>
          <View style={[common.card, styles.modalCard]}>
            <Text style={{ fontSize: 16, fontWeight: 'bold' }}>Вернуть на доработку</Text>
            <TextInput
              style={[common.input, { marginTop: 8 }]}
              placeholder="Комментарий для переводчика…"
              value={returnComment} onChangeText={setReturnComment} multiline
            />
            <View style={[common.row, { marginTop: 10 }]}>
              <TouchableOpacity style={common.button} onPress={() => { setReturnJob(null); setReturnComment(''); }}>
                <Text style={common.buttonText}>Отмена</Text>
              </TouchableOpacity>
              <View style={common.spacer} />
              <TouchableOpacity
                style={[common.button, common.primaryButton, !returnComment.trim() && common.disabled]}
                disabled={!returnComment.trim()}
                onPress={() => {
                  if (!returnJob) return;
                  void act(() => api.returnJob(account, returnJob.id, returnComment.trim()));
                  setReturnJob(null);
                  setReturnComment('');
                }}
              >
                <Text style={[common.buttonText, common.primaryButtonText]}>Вернуть</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  error: {
    color: colors.red, backgroundColor: colors.redBg, borderRadius: 8,
    padding: 10, marginHorizontal: 12, marginBottom: 6,
  },
  projectChip: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, marginRight: 6,
  },
  projectChipActive: { borderColor: colors.accent, backgroundColor: colors.blueBg },
  projectChipText: { fontSize: 13, color: colors.muted },
  projectChipTextActive: { color: colors.accent, fontWeight: '600' },
  memberRow: { paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: colors.border },
  modalBackdrop: {
    flex: 1, backgroundColor: 'rgba(15,23,42,0.45)', alignItems: 'center', justifyContent: 'center', padding: 16,
  },
  modalCard: { width: '100%', maxWidth: 480, maxHeight: '85%' },
  conflictTitle: { fontSize: 12, color: colors.muted, marginTop: 8 },
  empty: { textAlign: 'center', color: '#9CA3AF', marginTop: 20 },
});
