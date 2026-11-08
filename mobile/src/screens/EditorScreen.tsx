import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, FlatList, KeyboardAvoidingView, Modal, Platform, StyleSheet,
  Text, TextInput, TouchableOpacity, View,
} from 'react-native';

import { ApiError, api, tagTokens } from '../api';
import type { Account, JobDetail, MTSuggestion, QAReport, Segment, TMSuggestion, TBHit, WsEvent } from '../api';
import { MATCH_TYPE_LABELS, SEVERITY_LABELS, STATUS_LABELS, TR_STATUS_LABELS } from '../labels';
import {
  clearDraft, dequeuePending, enqueuePending, loadDraft, listPending, saveDraft,
} from '../store';
import { colors, common, statusColors } from '../theme';

interface Props {
  account: Account;
  jobId: number;
  wsEvent: WsEvent | null;
  onBack: () => void;
  onPendingChange?: (count: number) => void;
}

type SaveState = 'saved' | 'dirty' | 'saving' | 'offline' | 'error';
type Filter = 'all' | 'untranslated' | 'unconfirmed';
const EDITABLE = ['accepted', 'in_progress', 'rework'];

/** FR-03..FR-09: мобильный CAT-редактор. */
export default function EditorScreen({ account, jobId, wsEvent, onBack, onPendingChange }: Props) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState('');
  const [activeId, setActiveId] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [suggestions, setSuggestions] = useState<TMSuggestion[]>([]);
  const [tbHits, setTbHits] = useState<TBHit[]>([]);
  const [mt, setMt] = useState<MTSuggestion | null>(null);
  const [mtBusy, setMtBusy] = useState(false);
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [qaReport, setQaReport] = useState<QAReport | null>(null);
  const [conflict, setConflict] = useState<Segment | null>(null);
  const undoRef = useRef<string[]>([]);
  const redoRef = useRef<string[]>([]);
  const [, forceRender] = useState(0);

  const activeRef = useRef<Segment | null>(null);
  const draftRef = useRef('');
  draftRef.current = draft;
  const jobRef = useRef<JobDetail | null>(null);
  jobRef.current = job;

  const active = useMemo(() => job?.segments.find((s) => s.id === activeId) ?? null, [job, activeId]);
  activeRef.current = active;

  const syncPendingCount = useCallback(async () => {
    if (!onPendingChange) return;
    onPendingChange((await listPending(account.key)).length);
  }, [account.key, onPendingChange]);

  const load = useCallback(async () => {
    try {
      setJob(await api.job(account, jobId));
      setError('');
      await syncPendingCount();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить задание');
    }
  }, [account, jobId, syncPendingCount]);

  useEffect(() => {
    void load();
  }, [load]);

  /** FR-04: отправка изменений с локальной очередью и повтором при потере связи. */
  const persist = useCallback(async (segment: Segment, text: string, extra?: { confirmed?: boolean }): Promise<boolean> => {
    setSaveState('saving');
    try {
      const saved = await api.saveSegment(account, segment.id, {
        target_text: text,
        base_updated_at: segment.updated_at,
        ...extra,
      });
      setJob((prev) => (prev ? {
        ...prev,
        segments: prev.segments.map((s) => (s.id === saved.id ? saved : s)),
      } : prev));
      await clearDraft(account.key, segment.id);
      await dequeuePending(account.key, segment.id);
      await syncPendingCount();
      setSaveState('saved');
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.body) {
        // Конфликт версий не решается скрытой перезаписью — спрашиваем пользователя
        setConflict(err.body as Segment);
        return false;
      }
      if (err instanceof ApiError && (err.status === 0 || err.status >= 500)) {
        setSaveState('offline');
        await saveDraft(account.key, segment.id, text);
        await enqueuePending(account.key, {
          segmentId: segment.id, target_text: text,
          confirmed: extra?.confirmed, base_updated_at: segment.updated_at,
        });
        await syncPendingCount();
        return false;
      }
      setSaveState('error');
      return false;
    }
  }, [account, syncPendingCount]);

  // Debounced автосохранение (локально ≤1 с, на сервер ≤5 с — §11)
  useEffect(() => {
    if (!active || saveState !== 'dirty') return;
    const timer = setTimeout(() => {
      void persist(active, draft);
    }, 800);
    return () => clearTimeout(timer);
  }, [draft, active, saveState, persist]);

  // FR-04: восстановление связи -> повторная отправка очереди
  useEffect(() => {
    if (!wsEvent) return;
    void (async () => {
      const pending = await listPending(account.key);
      for (const change of pending) {
        const seg = jobRef.current?.segments.find((s) => s.id === change.segmentId);
        if (seg) {
          const serverVersionChanged = change.base_updated_at && change.base_updated_at !== seg.updated_at;
          if (!serverVersionChanged) {
            await persist(seg, change.target_text, change.confirmed ? { confirmed: true } : undefined);
          }
        }
      }
    })();
  }, [wsEvent, account.key, persist]);

  const selectSegment = useCallback(async (seg: Segment) => {
    setActiveId(seg.id);
    setSaveState('saved');
    setMt(null);
    undoRef.current = [];
    redoRef.current = [];
    forceRender((n) => n + 1);
    const local = await loadDraft(account.key, seg.id);
    if (local !== null && local !== seg.target_text) {
      setDraft(local); // несинхронизированный текст восстановлен (FR-04)
      setSaveState('dirty');
    } else {
      setDraft(seg.target_text);
    }
    try {
      setSuggestions(await api.tmSearch(account, jobRef.current!.project_id, seg.source_text, jobRef.current!.target_lang));
      setTbHits(await api.tbLookup(account, jobRef.current!.project_id, seg.source_text, jobRef.current!.target_lang));
    } catch {
      // подсказки необязательны; редактор продолжает работать (FR-06/07)
    }
  }, [account]);

  // WS: чужие правки подмешиваются (кроме активного сегмента)
  useEffect(() => {
    if (!wsEvent || wsEvent.type !== 'segment_updated' || wsEvent.job_id !== jobId) return;
    setJob((prev) => {
      if (!prev) return prev;
      const segments = prev.segments.map((s) => {
        if (s.id !== wsEvent.segment_id) return s;
        if (activeRef.current?.id === s.id) return s;
        return {
          ...s,
          translation_status: (wsEvent.translation_status as Segment['translation_status']) ?? s.translation_status,
          confirmed: (wsEvent.confirmed as boolean) ?? s.confirmed,
          target_text: (wsEvent.target_text as string) ?? s.target_text,
        };
      });
      return { ...prev, segments };
    });
  }, [wsEvent, jobId]);

  const visible = useMemo(() => {
    if (!job) return [];
    let list = job.segments;
    if (filter === 'untranslated') list = list.filter((s) => s.translation_status === 'untranslated');
    if (filter === 'unconfirmed') list = list.filter((s) => !s.confirmed);
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      list = list.filter((s) => s.source_text.toLowerCase().includes(needle) || s.target_text.toLowerCase().includes(needle));
    }
    return list;
  }, [job, filter, search]);

  if (!job) {
    return (
      <View style={{ flex: 1 }}>
        <TouchableOpacity style={[common.button, { margin: 12 }]} onPress={onBack}>
          <Text style={common.buttonText}>← Задания</Text>
        </TouchableOpacity>
        <Text style={[common.muted, { textAlign: 'center' }]}>{error || 'Загрузка…'}</Text>
      </View>
    );
  }

  const canEdit = EDITABLE.includes(job.status);

  const onChangeDraft = (text: string) => {
    if (!active) return;
    undoRef.current = [...undoRef.current.slice(-50), draft];
    redoRef.current = [];
    forceRender((n) => n + 1);
    setDraft(text);
    setSaveState('dirty');
    void saveDraft(account.key, active.id, text); // локально немедленно (FR-04)
  };

  const undo = () => {
    if (!undoRef.current.length || !active) return;
    const previous = undoRef.current[undoRef.current.length - 1];
    undoRef.current = undoRef.current.slice(0, -1);
    redoRef.current = [...redoRef.current, draft];
    setDraft(previous);
    setSaveState('dirty');
    forceRender((n) => n + 1);
  };

  const redo = () => {
    if (!redoRef.current.length || !active) return;
    const next = redoRef.current[redoRef.current.length - 1];
    redoRef.current = redoRef.current.slice(0, -1);
    undoRef.current = [...undoRef.current, draft];
    setDraft(next);
    setSaveState('dirty');
    forceRender((n) => n + 1);
  };

  const insertAtCursor = (snippet: string) => {
    onChangeDraft(draft + snippet);
  };

  const confirmAndNext = async () => {
    if (!active) return;
    const ok = await persist(active, draft, { confirmed: true });
    if (!ok) return;
    const next = job.segments.find((s) => !s.confirmed && s.id !== active.id);
    if (next) await selectSegment(next);
    else setActiveId(null);
  };

  const goto = (offset: number) => {
    const list = visible.length ? visible : job.segments;
    const idx = list.findIndex((s) => s.id === activeId);
    const next = list[idx + offset];
    if (next) void selectSegment(next);
  };

  const runMt = async () => {
    if (!active) return;
    setMtBusy(true);
    try {
      setMt(await api.mtSuggest(account, job.id, active.id));
    } catch (err) {
      // Отказ MT не блокирует редактор (FR-08)
      setMt({ text: err instanceof Error ? err.message : 'MT недоступен', provider: 'unavailable' });
    } finally {
      setMtBusy(false);
    }
  };

  const openQa = async () => {
    try {
      setQaReport(await api.jobQa(account, job.id));
    } catch (err) {
      Alert.alert('Не удалось выполнить QA', err instanceof Error ? err.message : 'Неизвестная ошибка');
    }
  };

  const doSubmit = async () => {
    try {
      await api.submitJob(account, job.id);
      setQaReport(null);
      onBack();
    } catch (err) {
      Alert.alert('Не удалось сдать задание', err instanceof Error ? err.message : 'Неизвестная ошибка');
      setQaReport(null);
    }
  };

  const keepServer = () => {
    if (!conflict) return;
    onChangeDraft(conflict.target_text);
    setJob((prev) => (prev ? {
      ...prev,
      segments: prev.segments.map((s) => (s.id === conflict.id ? conflict : s)),
    } : prev));
    setSaveState('saved');
    void clearDraft(account.key, conflict.id);
    void dequeuePending(account.key, conflict.id);
    setConflict(null);
  };

  const overwriteMine = () => {
    const server = conflict;
    setConflict(null);
    if (server) void persist({ ...server }, draft);
  };

  const sc = active ? statusColors[active.confirmed ? 'confirmed' : active.translation_status] : statusColors.new;
  const saveLabels: Record<SaveState, string> = {
    saved: '✓ Сохранено', dirty: 'Правка…', saving: 'Сохранение…',
    offline: '⚠ Нет связи — черновик сохранен локально', error: '⚠ Ошибка сохранения',
  };

  return (
    <View style={{ flex: 1 }}>
      <View style={[common.row, { paddingHorizontal: 12, paddingVertical: 6 }]}>
        <TouchableOpacity style={common.button} onPress={onBack}>
          <Text style={common.buttonText}>← Jobs</Text>
        </TouchableOpacity>
        <View style={{ flex: 1, marginHorizontal: 8 }}>
          <Text style={{ fontWeight: 'bold', color: colors.accent }} numberOfLines={1}>{job.document_name}</Text>
          <Text style={common.small}>{job.project_name} · {job.source_lang} → {job.target_lang}</Text>
        </View>
        <View style={[common.chip, { backgroundColor: (statusColors[job.status] ?? statusColors.new).bg }]}>
          <Text style={[common.chipText, { color: (statusColors[job.status] ?? statusColors.new).fg }]}>
            {STATUS_LABELS[job.status] ?? job.status}
          </Text>
        </View>
      </View>

      {job.rework_comment ? <Text style={styles.rework}>Возвращено на доработку: {job.rework_comment}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={[common.row, common.wrap, { paddingHorizontal: 12, marginBottom: 6 }]}>
        <TextInput style={[common.input, { flex: 1, minWidth: 140 }]} placeholder="Поиск по сегментам…" value={search} onChangeText={setSearch} />
        {(['all', 'untranslated', 'unconfirmed'] as Filter[]).map((f) => (
          <TouchableOpacity key={f} style={[styles.filterChip, filter === f && styles.filterChipActive]}
                            onPress={() => setFilter(f)}>
            <Text style={[styles.filterText, filter === f && styles.filterTextActive]}>
              {f === 'all' ? 'Все' : f === 'untranslated' ? 'Не перев.' : 'Не подтв.'}
            </Text>
          </TouchableOpacity>
        ))}
        <TouchableOpacity style={common.button} onPress={() => void openQa()}>
          <Text style={common.buttonText}>QA</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        data={visible}
        keyExtractor={(item) => String(item.id)}
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingHorizontal: 12 }}
        renderItem={({ item }) => {
          const badge = statusColors[item.confirmed ? 'confirmed' : item.translation_status] ?? statusColors.new;
          return (
            <TouchableOpacity style={[common.card, activeId === item.id && styles.activeCard]}
                              onPress={() => void selectSegment(item)}>
              <View style={[common.row, { marginBottom: 4 }]}>
                <Text style={styles.segNum}>{item.position + 1}</Text>
                <View style={common.spacer} />
                {item.is_mandatory && <View style={[common.chip, { backgroundColor: colors.grayBg }]}><Text style={common.chipText}>обяз.</Text></View>}
                <View style={[common.chip, { backgroundColor: badge.bg }]}>
                  <Text style={[common.chipText, { color: badge.fg }]}>
                    {item.confirmed ? 'подтверждено' : (TR_STATUS_LABELS[item.translation_status] ?? item.translation_status)}
                  </Text>
                </View>
              </View>
              <Text style={styles.source}>{item.source_text}</Text>
              <Text style={[styles.target, !item.target_text && styles.targetEmpty]}>
                {item.target_text || 'Нажмите, чтобы перевести…'}
              </Text>
            </TouchableOpacity>
          );
        }}
        ListEmptyComponent={<Text style={styles.empty}>Нет сегментов по фильтру.</Text>}
      />

      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.editorArea}>
          <View style={[common.row, { marginBottom: 6 }]}>
            <Text style={[styles.saveState, saveState === 'offline' || saveState === 'error' ? { color: colors.red } : null]}>
              {saveLabels[saveState]}
            </Text>
            <View style={common.spacer} />
            {active && canEdit && (
              <>
                <TouchableOpacity style={[common.button, !undoRef.current.length && common.disabled]} onPress={undo}>
                  <Text style={common.buttonText}>Отменить</Text>
                </TouchableOpacity>
                <TouchableOpacity style={[common.button, !redoRef.current.length && common.disabled]} onPress={redo}>
                  <Text style={common.buttonText}>Вернуть</Text>
                </TouchableOpacity>
                <TouchableOpacity style={common.button} onPress={() => goto(-1)}>
                  <Text style={common.buttonText}>←</Text>
                </TouchableOpacity>
                <TouchableOpacity style={common.button} onPress={() => goto(1)}>
                  <Text style={common.buttonText}>→</Text>
                </TouchableOpacity>
              </>
            )}
          </View>

          {active ? (
            <>
              <Text style={common.small}>Сегмент {active.position + 1} / {job.segments.length} · {active.source_words} слов</Text>
              <Text style={[common.small, { color: colors.text }]} numberOfLines={3}>{active.source_text}</Text>

              {suggestions.length > 0 && (
                <View style={{ marginTop: 6 }}>
                  {suggestions.slice(0, 2).map((s, i) => (
                    <TouchableOpacity key={i} style={styles.tmItem} disabled={!canEdit}
                                      onPress={() => onChangeDraft(s.target_suggestion)}>
                      <Text style={styles.tmScore}>{s.match_percentage}%</Text>
                      <Text style={{ flex: 1 }} numberOfLines={2}>
                        {s.target_suggestion}{' '}
                        <Text style={common.small}>· {s.tm_name} · {MATCH_TYPE_LABELS[s.match_type] ?? s.match_type}</Text>
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              )}

              {tbHits.length > 0 && (
                <View style={{ marginTop: 6 }}>
                  {tbHits.slice(0, 3).map((h, i) => (
                    <TouchableOpacity key={i} style={[styles.tbItem, h.status === 'forbidden' && styles.tbForbidden]}
                                      disabled={!canEdit} onPress={() => insertAtCursor(h.translation)}>
                      <Text style={{ fontSize: 13 }}>
                        <Text style={{ fontWeight: 'bold' }}>{h.term}</Text> → {h.translation}
                        {h.note ? ` · ${h.note}` : ''} <Text style={common.small}>· {h.tb_name}</Text>
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              )}

              {tagTokens(active.source_text).length > 0 && (
                <View style={[common.row, common.wrap, { marginTop: 6 }]}>
                  {tagTokens(active.source_text).map((tag) => (
                    <TouchableOpacity key={tag} style={styles.tagChip} disabled={!canEdit}
                                      onPress={() => insertAtCursor(tag)}>
                      <Text style={styles.tagText}>{tag}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              )}

              {canEdit ? (
                <>
                  <TextInput
                    style={[common.input, styles.textarea]}
                    multiline
                    value={draft}
                    onChangeText={onChangeDraft}
                    placeholder="Введите перевод…"
                    textAlignVertical="top"
                  />
                  <View style={[common.row, { marginTop: 8 }]}>
                    <TouchableOpacity style={[common.button, common.primaryButton]} onPress={() => void confirmAndNext()}>
                      <Text style={[common.buttonText, common.primaryButtonText]}>Подтвердить и далее</Text>
                    </TouchableOpacity>
                    <View style={common.spacer} />
                    <TouchableOpacity style={common.button} onPress={() => void runMt()} disabled={mtBusy}>
                      <Text style={common.buttonText}>{mtBusy ? 'MT…' : 'Запросить MT'}</Text>
                    </TouchableOpacity>
                  </View>
                  {mt && (
                    <TouchableOpacity style={styles.mtBox} onPress={() => onChangeDraft(mt.text)}>
                      <Text style={common.small}>Машинный перевод ({mt.provider}) — нажмите, чтобы вставить, затем вычитайте:</Text>
                      <Text>{mt.text}</Text>
                    </TouchableOpacity>
                  )}
                </>
              ) : (
                <Text style={[common.muted, { marginTop: 8 }]}>Только для чтения в статусе «{STATUS_LABELS[job.status] ?? job.status}».</Text>
              )}
            </>
          ) : (
            <Text style={[common.muted, styles.placeholder]}>
              {job.total_segments > 0 && job.confirmed_segments === job.total_segments
                ? '✓ Все обязательные сегменты подтверждены. Прогоните QA и сдавайте.'
                : 'Выберите сегмент выше, чтобы перевести.'}
            </Text>
          )}
        </View>
      </KeyboardAvoidingView>

      {/* FR-09: QA перед сдачей */}
      <Modal visible={qaReport !== null} transparent animationType="fade"
             onRequestClose={() => setQaReport(null)}>
        <View style={styles.modalBackdrop}>
          <View style={[common.card, styles.modalCard]}>
            <Text style={{ fontSize: 16, fontWeight: 'bold' }}>Проверка качества</Text>
            {qaReport && (
              <>
                <Text style={[styles.qaSummary, { color: qaReport.can_submit ? colors.green : colors.red }]}>
                  {qaReport.can_submit
                    ? 'Блокирующих ошибок нет. Можно сдавать.'
                    : `Блокирующих: ${qaReport.blocking_count}, предупреждений: ${qaReport.warning_count}.`}
                </Text>
                <FlatList
                  data={qaReport.items}
                  keyExtractor={(_, i) => String(i)}
                  style={{ maxHeight: 260 }}
                  renderItem={({ item }) => {
                    const badge = statusColors[item.severity] ?? statusColors.warning;
                    return (
                      <TouchableOpacity style={styles.qaItem}
                                        onPress={() => {
                                          const seg = job.segments.find((s) => s.id === item.segment_id);
                                          if (seg) void selectSegment(seg);
                                          setQaReport(null);
                                        }}>
                        <View style={[common.chip, { backgroundColor: badge.bg }]}>
                          <Text style={[common.chipText, { color: badge.fg }]}>{SEVERITY_LABELS[item.severity] ?? item.severity}</Text>
                        </View>
                        <Text style={{ flex: 1, fontSize: 13 }}>#{item.position + 1} {item.message}</Text>
                      </TouchableOpacity>
                    );
                  }}
                  ListEmptyComponent={<Text style={common.muted}>Замечаний нет.</Text>}
                />
                <View style={[common.row, { marginTop: 10 }]}>
                  <TouchableOpacity style={common.button} onPress={() => setQaReport(null)}>
                    <Text style={common.buttonText}>Закрыть</Text>
                  </TouchableOpacity>
                  <View style={common.spacer} />
                  <TouchableOpacity
                    style={[common.button, common.primaryButton, !qaReport.can_submit && common.disabled]}
                    disabled={!qaReport.can_submit} onPress={() => void doSubmit()}
                  >
                    <Text style={[common.buttonText, common.primaryButtonText]}>Сдать задание</Text>
                  </TouchableOpacity>
                </View>
              </>
            )}
          </View>
        </View>
      </Modal>

      {/* FR-04: конфликт версий — выбор версии пользователем */}
      <Modal visible={conflict !== null} transparent animationType="fade" onRequestClose={() => setConflict(null)}>
        <View style={styles.modalBackdrop}>
          <View style={[common.card, styles.modalCard]}>
            <Text style={{ fontSize: 16, fontWeight: 'bold' }}>Конфликт версий</Text>
            <Text style={[common.small, { marginVertical: 6 }]}>
              Сегмент изменен на сервере. Выберите, какую версию оставить.
            </Text>
            <Text style={styles.conflictTitle}>Версия сервера</Text>
            <Text style={styles.conflictText}>{conflict?.target_text || '—'}</Text>
            <Text style={styles.conflictTitle}>Ваш черновик</Text>
            <Text style={styles.conflictText}>{draft || '—'}</Text>
            <View style={[common.row, { marginTop: 10 }]}>
              <TouchableOpacity style={common.button} onPress={keepServer}>
                <Text style={common.buttonText}>Оставить серверную</Text>
              </TouchableOpacity>
              <View style={common.spacer} />
              <TouchableOpacity style={[common.button, common.primaryButton]} onPress={overwriteMine}>
                <Text style={[common.buttonText, common.primaryButtonText]}>Перезаписать моей</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  rework: {
    color: colors.red, backgroundColor: colors.redBg, borderRadius: 8,
    padding: 10, marginHorizontal: 12, marginBottom: 6,
  },
  error: {
    color: colors.red, backgroundColor: colors.redBg, borderRadius: 8,
    padding: 10, marginHorizontal: 12, marginBottom: 6,
  },
  segNum: { color: '#9CA3AF', fontSize: 12 },
  source: { fontSize: 15, color: colors.text },
  target: { fontSize: 14, color: colors.accent, marginTop: 2 },
  targetEmpty: { color: '#9CA3AF', fontStyle: 'italic' },
  activeCard: { borderWidth: 2, borderColor: colors.accent, padding: 12 },
  filterChip: {
    paddingHorizontal: 8, paddingVertical: 5, borderRadius: 12,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card,
  },
  filterChipActive: { borderColor: colors.accent, backgroundColor: colors.blueBg },
  filterText: { fontSize: 11, color: colors.muted },
  filterTextActive: { color: colors.accent, fontWeight: '600' },
  editorArea: {
    padding: 12,
    backgroundColor: colors.card,
    borderTopWidth: 1,
    borderColor: colors.border,
  },
  saveState: { fontSize: 12, fontWeight: '600', color: colors.green },
  tmItem: {
    flexDirection: 'row', gap: 8, alignItems: 'center',
    backgroundColor: '#F0FDF4', borderColor: '#BBF7D0', borderWidth: 1,
    borderRadius: 8, padding: 8, marginBottom: 6,
  },
  tmScore: { fontWeight: 'bold', color: colors.green },
  tbItem: {
    backgroundColor: '#EFF6FF', borderColor: '#BFDBFE', borderWidth: 1,
    borderRadius: 8, padding: 8, marginBottom: 6,
  },
  tbForbidden: { backgroundColor: colors.redBg, borderColor: '#FECACA' },
  tagChip: {
    borderColor: '#FCD34D', borderWidth: 1, backgroundColor: '#FFFBEB',
    borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3,
  },
  tagText: { fontFamily: 'monospace', fontSize: 11 },
  textarea: { minHeight: 80, marginTop: 8 },
  mtBox: {
    backgroundColor: '#FAF5FF', borderColor: '#E9D5FF', borderWidth: 1,
    borderRadius: 8, padding: 8, marginTop: 8, gap: 4,
  },
  placeholder: { textAlign: 'center', padding: 16 },
  modalBackdrop: {
    flex: 1, backgroundColor: 'rgba(15,23,42,0.45)', alignItems: 'center', justifyContent: 'center', padding: 16,
  },
  modalCard: { width: '100%', maxWidth: 480, maxHeight: '85%' },
  qaSummary: { fontWeight: '600', marginVertical: 6 },
  qaItem: { flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 6 },
  conflictTitle: { fontSize: 12, color: colors.muted, marginTop: 6 },
  conflictText: {
    borderWidth: 1, borderColor: colors.border, borderRadius: 8, padding: 8, minHeight: 40,
  },
  empty: { textAlign: 'center', color: '#9CA3AF', marginTop: 20 },
});
