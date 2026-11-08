import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, downloadBilingualExport, downloadJobXliffHandoff } from '../api/client';
import { MATCH_TYPE_LABELS, STATUS_LABELS, TR_STATUS_LABELS, fmtDateTime } from '../api/labels';
import type {
  AnalysisReport, JobDetail, LockConflictBody, MTSuggestion, QAReport, Segment, SegmentLockInfo,
  SegmentNote, SegmentRevision, TBHit, TMSuggestion, WsEvent,
} from '../api/types';
import { useAuth } from '../state/AuthContext';
import { useUpdates } from '../ws/useUpdates';

type SaveState = 'saved' | 'dirty' | 'saving' | 'offline' | 'error';
type Filter = 'all' | 'untranslated' | 'unconfirmed' | 'qa';

const TAG_RE = /<x id="[^"]+"\s*\/>|<g id="[^"]+">|<\/g>/g;
const EDITABLE = ['accepted', 'in_progress', 'rework'];

function draftKey(segmentId: number): string {
  return `tms.draft.${segmentId}`;
}

function uniqueTags(text: string): string[] {
  return [...new Set(text.match(TAG_RE) ?? [])];
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Подсветка терминов TB в исходнике: точечное подчеркивание + перевод в tooltip.
 * Термины сортируются по длине (длинные первыми), граница слова — чтобы
 * «cat» не подсвечивался внутри «category». Практика CAT: переводчик видит
 * в исходнике, какие слова имеют утвержденный перевод. */
function renderSourceWithTerms(text: string, hits: TBHit[]): ReactNode[] {
  const terms = [...new Set(hits.map((h) => h.term))]
    .filter((t) => t.trim().length > 1)
    .sort((a, b) => b.length - a.length);
  if (!terms.length) return [text];
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}])(?:${terms.map(escapeRegExp).join('|')})(?![\\p{L}\\p{N}])`,
    'giu',
  );
  const byTerm = new Map(hits.map((h) => [h.term.toLowerCase(), h]));
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(pattern)) {
    const term = match[0];
    const hit = byTerm.get(term.toLowerCase());
    if (!hit || match.index === undefined) continue;
    if (match.index > last) out.push(text.slice(last, match.index));
    out.push(
      <span key={key++} className={`term-hit ${hit.status === 'forbidden' ? 'term-forbidden' : ''}`}
            title={`${hit.status}: ${hit.translation}${hit.note ? ` — ${hit.note}` : ''}`}>
        {term}
      </span>,
    );
    last = match.index + term.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length ? out : [text];
}

function fmtNoteTime(value: string): string {
  return fmtDateTime(value);
}

export default function EditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { token, user } = useAuth();
  const jobId = Number(id);

  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState('');
  const [activeId, setActiveId] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [suggestions, setSuggestions] = useState<TMSuggestion[]>([]);
  const [concordance, setConcordance] = useState('');
  const [concordanceResults, setConcordanceResults] = useState<TMSuggestion[]>([]);
  const [concSide, setConcSide] = useState<'source' | 'target'>('source');
  const [tbHits, setTbHits] = useState<TBHit[]>([]);
  const [mt, setMt] = useState<MTSuggestion | null>(null);
  const [mtBusy, setMtBusy] = useState(false);
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [qaReport, setQaReport] = useState<QAReport | null>(null);
  const [conflict, setConflict] = useState<{ server: Segment } | null>(null);
  const [undoStack, setUndoStack] = useState<string[]>([]);
  const [redoStack, setRedoStack] = useState<string[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisReport | null>(null);
  const [preTrOpen, setPreTrOpen] = useState(false);
  const [preTrBusy, setPreTrBusy] = useState(false);
  // Псевдо-перевод (проверка пайплайна) и XLIFF-хендофф
  const [pseudoOpen, setPseudoOpen] = useState(false);
  const [pseudoBusy, setPseudoBusy] = useState(false);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const handoffInputRef = useRef<HTMLInputElement | null>(null);
  const [notice, setNotice] = useState('');
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [findText, setFindText] = useState('');
  const [replaceText, setReplaceText] = useState('');
  const [replaceBusy, setReplaceBusy] = useState(false);
  const [notes, setNotes] = useState<SegmentNote[]>([]);
  const [noteText, setNoteText] = useState('');
  const [notedIds, setNotedIds] = useState<Set<number>>(new Set());
  // история правок сегмента (track changes)
  const [history, setHistory] = useState<SegmentRevision[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  // Локи «кто редактирует — тот блокирует»: кто сейчас держит какой сегмент
  const [locks, setLocks] = useState<SegmentLockInfo[]>([]);
  const lockedIdRef = useRef<number | null>(null); // сегмент, лок которого держим мы

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const active = useMemo(
    () => job?.segments.find((s) => s.id === activeId) ?? null,
    [job, activeId],
  );
  const activeRef = useRef<Segment | null>(null);
  activeRef.current = active;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  // ref вместо состояния: persist (useCallback без deps) видит актуальное значение
  const historyOpenRef = useRef(false);
  historyOpenRef.current = historyOpen;

  const load = useCallback(async () => {
    if (!Number.isFinite(jobId)) return;
    try {
      const data = await api.job(jobId);
      setJob(data);
      setLocks(data.locks ?? []); // серверная истина: свежие локи задания
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить задание');
    }
  }, [jobId]);

  useEffect(() => {
    if (token) void load();
  }, [token, load]);

  const persist = useCallback(async (segment: Segment, text: string, extra?: { confirmed?: boolean }) => {
    setSaveState('saving');
    try {
      const saved = await api.saveSegment(segment.id, {
        target_text: text,
        base_updated_at: segment.updated_at,
        ...extra,
      });
      setJob((prev) => prev ? {
        ...prev,
        segments: prev.segments.map((s) => (s.id === saved.id ? saved : s)),
        confirmed_segments: prev.segments.reduce(
          (acc, s) => acc + (s.id === saved.id ? (saved.confirmed ? 1 : 0) : (s.confirmed && s.is_mandatory ? 1 : 0)), 0,
        ),
      } : prev);
      localStorage.removeItem(draftKey(segment.id));
      setSaveState('saved');
      if (historyOpenRef.current) {
        api.segmentHistory(segment.id).then(setHistory).catch(() => setHistory([]));
      }
      return true;
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409 && err.body) {
          setConflict({ server: err.body as Segment });
          return false;
        }
        if (err.status === 423) {
          // Сегмент занял другой редактор (кто редактирует — тот блокирует):
          // черновик сохраняется локально, занятость показываем сразу.
          const detail = (err.body as LockConflictBody | undefined)?.detail;
          if (detail) {
            setLocks((prev) => [
              ...prev.filter((l) => l.segment_id !== segment.id),
              { segment_id: segment.id, user: detail.locked_by, locked_at: detail.locked_at },
            ]);
            setNotice(`Сегмент редактирует ${detail.locked_by.username} — до завершения он только для чтения.`);
          }
          setSaveState('error');
          return false;
        }
        setSaveState(err.status === 0 ? 'offline' : 'error');
        if (err.status === 0) {
          localStorage.setItem(draftKey(segment.id), JSON.stringify({ text, ts: Date.now() }));
          if (retryTimer.current) clearTimeout(retryTimer.current);
          retryTimer.current = setTimeout(() => {
            if (draftRef.current.trim()) void persist(segment, draftRef.current);
          }, 5000);
        }
        return false;
      }
      setSaveState('error');
      return false;
    }
  }, []);

  // Debounced автосохранение (≤5 c по ТЗ, держим 800 мс после последней правки)
  useEffect(() => {
    if (!active || saveState !== 'dirty') return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      void persist(active, draft);
    }, 800);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [draft, active, saveState, persist]);

  // Лок сегмента: захват/продление и снятие. Чужой свежий лок приходит в теле 423.
  const acquireLock = useCallback(async (segmentId: number) => {
    try {
      const res = await api.lockSegment(segmentId);
      lockedIdRef.current = segmentId;
      setLocks((prev) => [
        ...prev.filter((l) => l.segment_id !== segmentId),
        { segment_id: segmentId, user: res.locked_by, locked_at: res.locked_at },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 423) {
        const detail = (err.body as LockConflictBody | undefined)?.detail;
        if (detail) {
          setLocks((prev) => [
            ...prev.filter((l) => l.segment_id !== segmentId),
            { segment_id: segmentId, user: detail.locked_by, locked_at: detail.locked_at },
          ]);
        }
      }
    }
  }, []);

  const releaseLock = useCallback(async (segmentId: number | null | undefined) => {
    if (!segmentId || lockedIdRef.current !== segmentId) return; // отпускаем только свой лок
    lockedIdRef.current = null;
    try {
      await api.unlockSegment(segmentId);
    } catch {
      // связь упала — сервер снимет лок по TTL
    }
    setLocks((prev) => prev.filter((l) => l.segment_id !== segmentId));
  }, []);

  const selectSegment = useCallback(async (seg: Segment) => {
    // Локи: уходим с предыдущего сегмента — снимаем; новый занимаем,
    // если задание редактируемо (кто редактирует — тот блокирует)
    const previousId = activeRef.current?.id ?? null;
    if (previousId !== seg.id) void releaseLock(previousId);
    if (job && EDITABLE.includes(job.status)) void acquireLock(seg.id);
    setActiveId(seg.id);
    setSaveState('saved');
    setUndoStack([]);
    setRedoStack([]);
    setMt(null);
    setHistory([]);
    setHistoryOpen(false);
    const local = localStorage.getItem(draftKey(seg.id));
    if (local) {
      try {
        const parsed = JSON.parse(local) as { text: string };
        if (parsed.text !== seg.target_text) {
          // Несинхронизированный текст после перезапуска (FR-04)
          setDraft(parsed.text);
          setSaveState('dirty');
        } else {
          setDraft(seg.target_text);
        }
      } catch {
        setDraft(seg.target_text);
      }
    } else {
      setDraft(seg.target_text);
    }
    if (job) {
      api.tmSearch(job.project_id, seg.source_text, job.target_lang)
        .then(setSuggestions).catch(() => setSuggestions([]));
      api.tbLookup(job.project_id, seg.source_text, job.target_lang)
        .then(setTbHits).catch(() => setTbHits([]));
    }
    api.segmentNotes(seg.id)
      .then((list) => {
        setNotes(list);
        if (list.length) setNotedIds((prev) => new Set(prev).add(seg.id));
      })
      .catch(() => setNotes([]));
  }, [job, acquireLock, releaseLock]);

  // Heartbeat лока: пока открыт редактируемый сегмент, продлеваем свой лок
  // (TTL на сервере — 10 мин; вкладка, умершая без снятия, освобождает сегмент)
  const canEditJob = job ? EDITABLE.includes(job.status) : false;
  useEffect(() => {
    if (!activeId || !canEditJob) return;
    const timer = setInterval(() => {
      if (lockedIdRef.current === activeId) void acquireLock(activeId);
    }, 60_000);
    return () => clearInterval(timer);
  }, [activeId, canEditJob, acquireLock]);

  // Уход со страницы: снимаем свой лок (best-effort; TTL подстрахует)
  useEffect(() => {
    return () => {
      const sid = lockedIdRef.current;
      lockedIdRef.current = null;
      if (sid) void api.unlockSegment(sid).catch(() => undefined);
    };
  }, []);

  // Хоткеи редактора: слушатель один (стабильный), логика — через ref,
  // чтобы всегда видеть актуальные сегменты и черновик
  const hotkeysRef = useRef<(e: KeyboardEvent) => void>(() => {});
  useEffect(() => {
    const handler = (e: KeyboardEvent) => hotkeysRef.current(e);
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const toggleHistory = async () => {
    if (!active) return;
    if (historyOpen) {
      setHistoryOpen(false);
      return;
    }
    try {
      setHistory(await api.segmentHistory(active.id));
    } catch {
      setHistory([]);
    }
    setHistoryOpen(true);
  };

  // WS: чужие правки и смены статуса задания
  useUpdates((event: WsEvent) => {
    if (event.type === 'segment_updated' && event.job_id === jobId) {
      setJob((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          segments: prev.segments.map((s) => {
            if (s.id !== event.segment_id) return s;
            if (activeRef.current?.id === s.id) return s; // локальный черновик важнее
            return {
              ...s,
              translation_status: (event.translation_status as Segment['translation_status']) ?? s.translation_status,
              confirmed: (event.confirmed as boolean) ?? s.confirmed,
              target_text: (event.target_text as string) ?? s.target_text,
              // серверная версия после автопропагации: иначе стейл base_updated_at даст ложный 409
              updated_at: (event.updated_at as string) ?? s.updated_at,
            };
          }),
        };
      });
    } else if (event.type === 'note_added' && event.job_id === jobId) {
      // Комментарий к сегменту — вплываем в панель активного сегмента
      const sid = event.segment_id as number;
      const body = event.body as string;
      const user = event.user as string;
      setNotedIds((prev) => new Set(prev).add(sid));
      if (activeRef.current?.id === sid) {
        setNotes((prev) => [...prev, {
          id: -Date.now(), segment_id: sid, position: (event.position as number) ?? 0,
          author: { id: -1, username: user }, body, created_at: new Date().toISOString(),
        }]);
      }
    } else if (event.type === 'segment_locked' && event.job_id === jobId) {
      // Чужой (или свой эхо-) лок — показываем занятость сразу, без перезапроса
      const sid = event.segment_id as number;
      setLocks((prev) => [
        ...prev.filter((l) => l.segment_id !== sid),
        {
          segment_id: sid,
          user: { id: (event.user_id as number) ?? -1, username: (event.user as string) ?? '?' },
          locked_at: (event.locked_at as string) ?? '',
        },
      ]);
    } else if (event.type === 'segment_unlocked' && event.job_id === jobId) {
      const sid = event.segment_id as number;
      if (lockedIdRef.current === sid && event.user !== user?.username) {
        // наш лок снял менеджмент принудительно — забываем, что держим
        lockedIdRef.current = null;
      }
      setLocks((prev) => prev.filter((l) => l.segment_id !== sid));
    } else if (event.type.startsWith('job_')) {
      void load();
    }
  });

  const visibleSegments = useMemo(() => {
    if (!job) return [];
    let list = job.segments;
    if (filter === 'untranslated') list = list.filter((s) => s.translation_status === 'untranslated');
    if (filter === 'unconfirmed') list = list.filter((s) => !s.confirmed);
    if (filter === 'qa' && qaReport) {
      // Только сегменты с замечаниями последнего QA-отчета (FR-09: переход к проблеме)
      const flagged = new Set(qaReport.items.map((item) => item.segment_id));
      list = list.filter((s) => flagged.has(s.id));
    }
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      list = list.filter((s) =>
        s.source_text.toLowerCase().includes(needle) || s.target_text.toLowerCase().includes(needle));
    }
    return list;
  }, [job, filter, search, qaReport]);

  // Должен идти ДО условных return (Rules of Hooks): после загрузки задания
  // число хуков не должно меняться между рендерами.
  const replaceCount = useMemo(() => {
    if (!job || !findText) return 0;
    return job.segments.filter((s) => s.target_text.includes(findText)).length;
  }, [job, findText]);

  // segment_id -> кто держит лок (для бейджей занятости в списке сегментов)
  const lockBySegment = useMemo(() => {
    const map = new Map<number, SegmentLockInfo>();
    for (const l of locks) map.set(l.segment_id, l);
    return map;
  }, [locks]);

  if (!token) return <Navigate to="/login" replace />;
  if (!job) return <div className="muted center">{error || 'Загрузка…'}</div>;

  const canEdit = canEditJob;
  // Чужой лок на активном сегменте: редактор read-only до снятия/TTL
  const activeLock = active ? (lockBySegment.get(active.id) ?? null) : null;
  const blockedBy = activeLock && activeLock.user.id !== (user?.id ?? -1) ? activeLock : null;

  const onChangeDraft = (text: string) => {
    if (!active) return;
    setUndoStack((stack) => [...stack.slice(-50), draft]);
    setRedoStack([]);
    setDraft(text);
    setSaveState('dirty');
  };

  const undo = () => {
    if (!undoStack.length || !active) return;
    const previous = undoStack[undoStack.length - 1];
    setUndoStack((stack) => stack.slice(0, -1));
    setRedoStack((stack) => [...stack, draft]);
    setDraft(previous);
    setSaveState('dirty');
  };

  const redo = () => {
    if (!redoStack.length || !active) return;
    const next = redoStack[redoStack.length - 1];
    setRedoStack((stack) => stack.slice(0, -1));
    setUndoStack((stack) => [...stack, draft]);
    setDraft(next);
    setSaveState('dirty');
  };

  const insertTag = (tag: string) => {
    if (!active) return;
    const el = textareaRef.current;
    const pos = el ? (el.selectionStart ?? draft.length) : draft.length;
    onChangeDraft(draft.slice(0, pos) + tag + draft.slice(pos));
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.selectionStart = el.selectionEnd = pos + tag.length;
      }
    });
  };

  const confirmAndNext = async () => {
    if (!active) return;
    const ok = await persist(active, draft, { confirmed: true });
    if (!ok) return;
    const nextUnconfirmed = job.segments.find((s) => !s.confirmed && s.id !== active.id);
    if (nextUnconfirmed) void selectSegment(nextUnconfirmed);
    else setActiveId(null);
  };

  const goto = (offset: number) => {
    const list = visibleSegments.length ? visibleSegments : job.segments;
    const idx = list.findIndex((s) => s.id === activeId);
    const next = list[idx + offset];
    if (next) void selectSegment(next);
  };

  // Актуальная логика хоткеев (переназначается каждый рендер — см. ref выше).
  // Ctrl+Enter — подтвердить и вниз; Ctrl/Alt+↑↓ — навигация; Alt+T — копировать
  // исходник в перевод; Ctrl+S — сохранить черновик сейчас. Работают при фокусе
  // в любом месте страницы, кроме полей ввода (там — только Ctrl+S).
  hotkeysRef.current = (e: KeyboardEvent) => {
    if (!job || !canEdit) return;
    if (e.ctrlKey && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      if (active && saveState === 'dirty' && !blockedBy) void persist(active, draft);
      return;
    }
    const target = e.target as HTMLElement | null;
    const inTextField = target !== null
      && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT');
    if (inTextField) return; // поиск/комментарии/чужие поля — не перехватываем
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); void confirmAndNext(); return; }
    if ((e.ctrlKey || e.altKey) && e.key === 'ArrowDown') { e.preventDefault(); goto(1); return; }
    if ((e.ctrlKey || e.altKey) && e.key === 'ArrowUp') { e.preventDefault(); goto(-1); return; }
    if (e.altKey && (e.key === 't' || e.key === 'T') && active && !blockedBy) {
      e.preventDefault();
      onChangeDraft(active.source_text);
    }
  };

  const runMt = async () => {
    if (!active || !job) return;
    setMtBusy(true);
    try {
      setMt(await api.mtSuggest(job.id, active.id));
    } catch (err) {
      setMt({ text: err instanceof Error ? err.message : 'MT недоступен', provider: 'unavailable' });
    } finally {
      setMtBusy(false);
    }
  };

  const runConcordance = async () => {
    if (!job || !concordance.trim()) return;
    try {
      // Ручной конкорданс: сегменты, содержащие запрос как слово/фразу
      setConcordanceResults(await api.tmSearch(job.project_id, concordance, job.target_lang, concSide, 'concordance'));
    } catch {
      setConcordanceResults([]);
    }
  };

  const runConcordanceSide = async (side: 'source' | 'target') => {
    if (!job || !concordance.trim()) return;
    try {
      setConcordanceResults(await api.tmSearch(job.project_id, concordance, job.target_lang, side, 'concordance'));
    } catch {
      setConcordanceResults([]);
    }
  };

  const openQa = async () => {
    try {
      setQaReport(await api.jobQa(job.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выполнить QA');
    }
  };

  const openAnalysis = async () => {
    try {
      setAnalysis(await api.jobAnalysis(jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выполнить анализ');
    }
  };

  const runPreTranslate = async (minScore: number) => {
    setPreTrBusy(true);
    try {
      const r = await api.preTranslateJob(jobId, minScore);
      setPreTrOpen(false);
      setNotice(
        `Предперевод: ${r.filled_exact} точных + ${r.filled_fuzzy} нечетких из TM, ` +
        `${r.propagated} повторов распространено${r.skipped ? `, ${r.skipped} оставлено без перевода` : ''}. ` +
        'Черновики не подтверждены — проверьте каждый.',
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выполнить предперевод');
    } finally {
      setPreTrBusy(false);
    }
  };

  const applyReplace = async () => {
    if (!job || !findText || replaceBusy) return;
    setReplaceBusy(true);
    try {
      let done = 0;
      for (const s of job.segments) {
        if (!s.target_text.includes(findText)) continue;
        await api.saveSegment(s.id, {
          target_text: s.target_text.split(findText).join(replaceText),
          base_updated_at: s.updated_at,
        });
        done += 1;
      }
      setReplaceOpen(false);
      setNotice(`Заменено в сегментах: ${done}. Измененные сегменты теряют подтверждение.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось заменить');
    } finally {
      setReplaceBusy(false);
    }
  };

  const runPseudo = async (overwrite: boolean) => {
    if (overwrite && !window.confirm('Перезаписать ВСЕ переводы сегментов псевдо-текстом?')) return;
    setPseudoBusy(true);
    try {
      const r = await api.pseudoTranslateJob(jobId, overwrite);
      setPseudoOpen(false);
      setNotice(
        `Псевдо-перевод: заполнено сегментов ${r.filled}` +
        `${r.skipped_nonempty ? `, пропущено переведенных ${r.skipped_nonempty}` : ''}. ` +
        'Теги и числа сохранены — экспортируйте файл, чтобы проверить пайплайн.',
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выполнить псевдо-перевод');
    } finally {
      setPseudoBusy(false);
    }
  };

  const importHandoff = async (file: File) => {
    setHandoffBusy(true);
    try {
      const r = await api.importJobXliff(jobId, file);
      setNotice(
        `XLIFF импортирован: обновлено ${r.updated}, подтверждено ${r.confirmed}` +
        `${r.skipped_locked ? `, заблокировано другим редактором: ${r.skipped_locked}` : ''}` +
        `${r.skipped ? `, пропущено ${r.skipped}` : ''}.`,
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось импортировать XLIFF');
    } finally {
      setHandoffBusy(false);
    }
  };

  const addNote = async () => {
    if (!active || !noteText.trim()) return;
    try {
      const created = await api.addSegmentNote(active.id, noteText.trim());
      setNotes((prev) => [...prev, created]);
      setNotedIds((prev) => new Set(prev).add(active.id));
      setNoteText('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось добавить комментарий');
    }
  };

  const doSubmit = async () => {
    try {
      await api.submitJob(job.id);
      setQaReport(null);
      navigate('/jobs');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось сдать задание');
      setQaReport(null);
    }
  };

  const contextPrev = active ? job.segments.find((s) => s.position === active.position - 1) : null;
  const contextNext = active ? job.segments.find((s) => s.position === active.position + 1) : null;
  const saveLabel: Record<SaveState, string> = {
    saved: '✓ Сохранено', dirty: 'Правка…', saving: 'Сохранение…',
    offline: '⚠ Нет связи — черновик сохранен локально', error: '⚠ Ошибка сохранения',
  };

  return (
    <>
      <section className="card row wrap">
        <span className="brand">{job.document_name}</span>
        <span className="muted">{job.project_name} · {job.source_lang} → {job.target_lang}</span>
        <span className={`status-chip st-${job.status}`}>{STATUS_LABELS[job.status] ?? job.status}</span>
        <span className="muted small-text">
          {job.confirmed_segments}/{job.total_segments} подтверждено · {job.total_words} слов
        </span>
        {job.is_overdue && <span className="status-chip st-new overdue">просрочено</span>}
        <span className="spacer" />
        <button
          className="btn small"
          title="Двуязычный DOCX (исходник | перевод) для ревью"
          onClick={() => downloadBilingualExport(jobId).catch((e: Error) => setError(e.message))}
        >
          Двуязычный DOCX
        </button>
        <button
          className="btn small"
          title="XLIFF 1.2 пакет для внешних CAT-инструментов (Trados/memoQ/OmegaT)"
          onClick={() => downloadJobXliffHandoff(jobId).catch((e: Error) => setError(e.message))}
        >
          XLIFF-хендофф
        </button>
        {canEdit && (
          <button
            className="btn small"
            disabled={handoffBusy}
            title="Слейте XLIFF, возвращенный внешним переводчиком"
            onClick={() => handoffInputRef.current?.click()}
          >
            {handoffBusy ? 'Импорт…' : 'Импорт XLIFF'}
          </button>
        )}
        <input
          ref={handoffInputRef} type="file" accept=".xliff,.xlf" hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void importHandoff(file);
          }}
        />
        <span className={`save-state save-${saveState}`}>{saveLabel[saveState]}</span>
      </section>

      {job.rework_comment && (
        <div className="alert">Возвращено на доработку: {job.rework_comment}</div>
      )}
      {error && <div className="alert">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      <section className="card row wrap">
        <input
          placeholder="Поиск по сегментам…" value={search}
          onChange={(e) => setSearch(e.target.value)} style={{ minWidth: 200 }}
        />
        {(['all', 'untranslated', 'unconfirmed'] as Filter[]).map((f) => (
          <button key={f} className={`btn small ${filter === f ? 'primary' : ''}`} onClick={() => setFilter(f)}>
            {f === 'all' ? 'Все' : f === 'untranslated' ? 'Непереведенные' : 'Неподтвержденные'}
          </button>
        ))}
        <button className={`btn small ${filter === 'qa' ? 'primary' : ''}`} onClick={() => { setFilter(filter === 'qa' ? 'all' : 'qa'); void openQa(); }}>
          Замечания QA
        </button>
        <button className="btn small" onClick={() => void openAnalysis()}>Анализ</button>
        {canEdit && (
          <button className="btn small" onClick={() => { setNotice(''); setPreTrOpen(true); }}>Предперевод…</button>
        )}
        {canEdit && (
          <button className="btn small" onClick={() => { setNotice(''); setPseudoOpen(true); }}
                  title="Заполнить сегменты синтетическим текстом для проверки экспорта">Псевдо-заполнение…</button>
        )}
        {canEdit && (
          <button className="btn small" onClick={() => { setNotice(''); setReplaceOpen(true); }} title="Ctrl+H">Замена…</button>
        )}
        <span className="spacer" />
        {canEdit && active && (
          <>
            <button className="btn small" onClick={undo} disabled={!undoStack.length}>Отменить</button>
            <button className="btn small" onClick={redo} disabled={!redoStack.length}>Вернуть</button>
            <button className="btn small" onClick={() => goto(-1)}>&#8592; Пред.</button>
            <button className="btn small" onClick={() => goto(1)}>След. &#8594;</button>
          </>
        )}
        {canEdit && (
          <button className="btn small primary" onClick={() => void openQa()}>Сдать задание…</button>
        )}
      </section>

      <div className="editor-grid">
        <section className="card segment-list">
          {visibleSegments.map((seg) => {
            const holder = lockBySegment.get(seg.id);
            const foreignLock = holder && holder.user.id !== (user?.id ?? -1) ? holder : null;
            return (
              <button
                key={seg.id}
                className={`segment-row ${activeId === seg.id ? 'active' : ''}`}
                onClick={() => void selectSegment(seg)}
              >
                <span className="seg-num">{seg.position + 1}</span>
                <span className="seg-texts">
                  <span className="seg-source">{seg.source_text}</span>
                  <span className={`seg-target ${!seg.target_text ? 'empty' : ''}`}>
                    {seg.target_text || '— не переведено —'}
                  </span>
                </span>
                <span className="seg-badges">
                  {foreignLock && (
                    <span className="status-chip st-new" title={`${foreignLock.user.username} редактирует этот сегмент`}>
                      🔒 {foreignLock.user.username}
                    </span>
                  )}
                  {seg.is_mandatory && <span className="status-chip st-new">обяз.</span>}
                  {notedIds.has(seg.id) && (
                    <span className="status-chip st-drafted note-chip" title="Есть комментарии">💬</span>
                  )}
                  {seg.confirmed
                    ? <span className="status-chip st-translated">подтверждено</span>
                    : <span className={`status-chip st-${seg.translation_status}`}>{TR_STATUS_LABELS[seg.translation_status] ?? seg.translation_status}</span>}
                </span>
              </button>
            );
          })}
          {visibleSegments.length === 0 && (
            <div className="muted center" style={{ padding: 20 }}>Нет сегментов по фильтру.</div>
          )}
        </section>

        <aside className="card editor-panel">
          {active ? (
            <>
              <h3 className="panel-title">Segment {active.position + 1} / {job.segments.length}</h3>
              {job.instructions && <div className="muted small-text">{job.instructions}</div>}

              <div className="context-block muted small-text">
                {contextPrev && <div className="context-line">&lt; {contextPrev.source_text}</div>}
                <div className="context-line current">{renderSourceWithTerms(active.source_text, tbHits)}</div>
                {contextNext && <div className="context-line">&gt; {contextNext.source_text}</div>}
              </div>

              {suggestions.length > 0 && (
                <div className="tm-list">
                  {suggestions.map((s, i) => (
                    <button key={i} className="tm-item" disabled={!canEdit}
                            onClick={() => onChangeDraft(s.target_suggestion)} title="Нажмите, чтобы вставить">
                      <span className={`tm-score ${s.match_percentage === 100 ? 'exact' : ''}`}>
                        {s.match_percentage}%
                      </span>
                      <span className="tm-body">
                        {s.target_suggestion}
                        <span className="muted small-text"> · {s.tm_name} · {MATCH_TYPE_LABELS[s.match_type] ?? s.match_type}</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}

              <div className="tm-list">
                {uniqueTags(active.source_text).map((tag) => (
                  <button key={tag} className="tag-chip" disabled={!canEdit}
                          onClick={() => insertTag(tag)} title="Вставить тег">
                    {tag}
                  </button>
                ))}
              </div>

              {tbHits.length > 0 && (
                <div className="tb-list">
                  <div className="muted small-text tb-hint">Термины из этого сегмента (выделены в исходнике):</div>
                  {tbHits.map((h, i) => (
                    <div key={i} className={`tb-item ${h.status === 'forbidden' ? 'tb-forbidden' : ''}`}>
                      <strong>{h.term}</strong> → {h.translation}
                      {h.note && <span className="muted small-text"> · {h.note}</span>}
                      <span className="muted small-text"> · {h.tb_name}</span>
                      {canEdit && (
                        <button className="btn small" style={{ marginLeft: 6 }}
                                onClick={() => insertTag(h.translation)}>
                          Вставить
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="notes-panel">
                <div className="row" style={{ alignItems: 'baseline' }}>
                  <span className="muted small-text">Комментарии ({notes.length}) — обсуждение сегмента:</span>
                </div>
                {notes.map((n) => (
                  <div key={n.id} className={`note-item ${n.author.username === (user?.username ?? '') ? 'mine' : ''}`}>
                    <span className="note-meta">
                      <strong>{n.author.username}</strong>
                      <span className="muted"> · {fmtNoteTime(n.created_at)}</span>
                    </span>
                    <div className="note-body">{n.body}</div>
                  </div>
                ))}
                {notes.length === 0 && (
                  <div className="muted small-text">Комментариев нет. Спросите о неоднозначности, товарных знаках, контексте…</div>
                )}
                <div className="row" style={{ marginTop: 6 }}>
                  <input
                    placeholder="Комментарий к сегменту…"
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') void addNote(); }}
                    style={{ flex: 1 }}
                  />
                  <button className="btn small" disabled={!noteText.trim()} onClick={() => void addNote()}>
                    Добавить
                  </button>
                </div>
              </div>

              <div className="history-panel">
                <button className="btn small" onClick={() => void toggleHistory()}>
                  {historyOpen ? '▾' : '▸'} История {history.length ? `(${history.length})` : ''}
                </button>
                {historyOpen && (
                  <>
                    <div className="muted small-text">
                      История правок сегмента — каждое сохранение, автор и время.
                      {canEdit && ' «Вернуть» кладет старую версию в черновик.'}
                    </div>
                    {history.map((rev) => (
                      <div key={rev.id} className={`note-item ${rev.author.username === (user?.username ?? '') ? 'mine' : ''}`}>
                        <span className="note-meta">
                          <strong>{rev.author.username}</strong>
                          <span className="muted"> · {fmtNoteTime(rev.created_at)}</span>
                        </span>
                        <div className="note-body">
                          {rev.target_text || '—'}
                          {canEdit && (
                            <button
                              className="btn small" style={{ marginLeft: 6 }}
                              title="Вернуть эту версию в черновик"
                              onClick={() => onChangeDraft(rev.target_text)}
                            >
                              Вернуть
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                    {history.length === 0 && (
                      <div className="muted small-text">Правок для этого сегмента пока нет.</div>
                    )}
                  </>
                )}
              </div>

              {canEdit ? (
                blockedBy ? (
                  <div className="alert">
                    🔒 {blockedBy.user.username} редактирует этот сегмент — он остается только для чтения,
                    пока они работают. Лок снимется автоматически после неактивности.
                  </div>
                ) : (
                  <>
                    <textarea
                      ref={textareaRef}
                      value={draft}
                      onChange={(e) => onChangeDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.ctrlKey && e.key === 'Enter') void confirmAndNext();
                        // Alt+↓/↑ — переход к следующему/предыдущему сегменту (стандарт CAT)
                        if (e.altKey && e.key === 'ArrowDown') { e.preventDefault(); goto(1); }
                        if (e.altKey && e.key === 'ArrowUp') { e.preventDefault(); goto(-1); }
                        // Ctrl+H — найти и заменить по всем сегментам задания
                        if (e.ctrlKey && (e.key === 'h' || e.key === 'H')) { e.preventDefault(); setReplaceOpen(true); }
                      }}
                      placeholder="Введите перевод…"
                      rows={4}
                      autoFocus
                    />
                    <div className="row wrap">
                      <button className="btn primary" onClick={() => void confirmAndNext()}>Подтвердить и далее</button>
                      <span className="muted small-text">Ctrl+Enter · Ctrl/Alt+↑/↓ · Alt+T · Ctrl+S</span>
                      <span className="spacer" />
                      <button className="btn small" onClick={() => void runMt()} disabled={mtBusy}>
                        {mtBusy ? 'MT…' : 'Запросить MT'}
                      </button>
                    </div>
                  </>
                )
              ) : (
                <div className="muted">Сегмент только для чтения в статусе «{STATUS_LABELS[job.status] ?? job.status}».</div>
              )}
              {canEdit && (
                <>
                  {mt && (
                    <div className="mt-box">
                      <div className="muted small-text">Машинный перевод ({mt.provider}) — вычитайте перед использованием:</div>
                      <div>{mt.text}</div>
                      <button className="btn small" onClick={() => onChangeDraft(mt.text)}>Вставить MT</button>
                    </div>
                  )}
                  <div className="row" style={{ marginTop: 8 }}>
                    <input
                      placeholder={concSide === 'source' ? 'Конкорданс (исходник)…' : 'Конкорданс (перевод)…'}
                      value={concordance}
                      onChange={(e) => setConcordance(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') void runConcordance(); }}
                      style={{ flex: 1 }}
                    />
                    <button
                      className={`btn small ${concSide === 'source' ? 'primary' : ''}`}
                      title="Поиск по исходникам TM (fuzzy)"
                      onClick={() => { setConcSide('source'); void runConcordanceSide('source'); }}
                    >Исх.</button>
                    <button
                      className={`btn small ${concSide === 'target' ? 'primary' : ''}`}
                      title="Поиск по переводам TM: как это уже переводилось"
                      onClick={() => { setConcSide('target'); void runConcordanceSide('target'); }}
                    >Пер.</button>
                    <button className="btn small" onClick={() => void runConcordance()}>Искать в TM</button>
                  </div>
                  {concordanceResults.length > 0 && (
                    <div className="tm-list">
                      {concordanceResults.map((s, i) => (
                        <button key={i} className="tm-item" onClick={() => onChangeDraft(s.target_suggestion)}>
                          <span className={`tm-score ${s.match_percentage === 100 ? 'exact' : ''}`}>{s.match_percentage}%</span>
                          <span className="tm-body">
                            {s.source_match} → {s.target_suggestion}
                            <span className="muted small-text"> · {s.tm_name}</span>
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </>
          ) : (
            <div className="muted center panel-empty">
              Выберите сегмент для перевода.
              {job.confirmed_segments === job.total_segments && job.total_segments > 0 && (
                <div className="done-note">✓ Все обязательные сегменты подтверждены. Прогоните QA и сдавайте.</div>
              )}
            </div>
          )}
        </aside>
      </div>

      {qaReport && (
        <div className="modal-backdrop" onClick={() => setQaReport(null)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3 className="panel-title">Проверка качества</h3>
            <p className={qaReport.can_submit ? 'done-note' : 'alert'}>
              {qaReport.can_submit
                ? 'Блокирующих ошибок нет. Можно сдавать.'
                : `Блокирующих ошибок: ${qaReport.blocking_count}, предупреждений: ${qaReport.warning_count}.`}
            </p>
            <div className="qa-list">
              {qaReport.items.map((item, i) => (
                <div key={i} className={`qa-item ${item.severity}`}>
                  <button className="btn small" onClick={() => {
                    const seg = job.segments.find((s) => s.id === item.segment_id);
                    if (seg) void selectSegment(seg);
                    setQaReport(null);
                  }}>
                    #{item.position + 1}
                  </button>
                  <span className={`status-chip ${item.severity === 'error' ? 'st-new' : 'st-drafted'}`}>
                    {item.severity}
                  </span>
                  {item.message}
                </div>
              ))}
              {qaReport.items.length === 0 && <div className="muted">Замечаний нет.</div>}
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn" onClick={() => setQaReport(null)}>Закрыть</button>
              <span className="spacer" />
              <button className="btn primary" disabled={!qaReport.can_submit} onClick={() => void doSubmit()}>
                Сдать задание
              </button>
            </div>
          </div>
        </div>
      )}

      {conflict && (
        <div className="modal-backdrop" onClick={() => setConflict(null)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3 className="panel-title">Конфликт версий</h3>
            <p className="muted">Сегмент изменен на сервере. Выберите, какую версию оставить.</p>
            <div className="conflict-grid">
              <div>
                <strong>Версия сервера</strong>
                <div className="conflict-text">{conflict.server.target_text || '—'}</div>
              </div>
              <div>
                <strong>Ваш черновик</strong>
                <div className="conflict-text">{draft || '—'}</div>
              </div>
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn" onClick={() => {
                onChangeDraft(conflict.server.target_text);
                setJob((prev) => prev ? {
                  ...prev,
                  segments: prev.segments.map((s) => (s.id === conflict.server.id ? conflict.server : s)),
                } : prev);
                setSaveState('saved');
                localStorage.removeItem(draftKey(conflict.server.id));
                setConflict(null);
              }}>
                Оставить версию сервера
              </button>
              <span className="spacer" />
              <button className="btn primary" onClick={() => {
                const server = conflict.server;
                setConflict(null);
                void persist({ ...server }, draft);
              }}>
                Перезаписать моей
              </button>
            </div>
          </div>
        </div>
      )}

      {analysis && (
        <div className="modal-backdrop" onClick={() => setAnalysis(null)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3 className="panel-title">Анализ объема — {analysis.source_lang} → {analysis.target_lang}</h3>
            <p className="muted small-text">
              Обязательные сегменты против TM проекта{analysis.tm_names.length ? ` (${analysis.tm_names.join(', ')})` : ''}.
              Взвешенные слова = оценка оплачиваемого объема.
            </p>
            <table className="jobs-table analysis-table">
              <thead>
                <tr>
                  <th>Категория</th>
                  <th>Сегменты</th>
                  <th>Слова</th>
                  <th>Взвешено</th>
                </tr>
              </thead>
              <tbody>
                {analysis.categories.map((c) => (
                  <tr key={c.code}>
                    <td>{c.label}</td>
                    <td>{c.segments}</td>
                    <td>{c.words}</td>
                    <td>{c.weighted}</td>
                  </tr>
                ))}
                <tr className="totals">
                  <td>Итого</td>
                  <td>{analysis.segments}</td>
                  <td>{analysis.words}</td>
                  <td>{analysis.weighted_words}</td>
                </tr>
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn" onClick={() => setAnalysis(null)}>Закрыть</button>
            </div>
          </div>
        </div>
      )}

      {preTrOpen && (
        <div className="modal-backdrop" onClick={() => setPreTrOpen(false)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3 className="panel-title">Предперевод из TM</h3>
            <p className="muted">
              Заполняет пустые сегменты лучшими совпадениями TM и распространенными повторами.
              Результаты — неподтвержденные черновики: проверьте каждый перед подтверждением.
            </p>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" disabled={preTrBusy} onClick={() => void runPreTranslate(100)}>
                Только точные (100%)
              </button>
              <span className="spacer" />
              <button className="btn" disabled={preTrBusy} onClick={() => void runPreTranslate(60)}>
                Точные + нечеткие (≥60%)
              </button>
            </div>
            <p className="muted small-text" style={{ marginTop: 10 }}>
              Консервативный вариант — только точные. Нечеткие черновики требуют больше пост-правки.
            </p>
          </div>
        </div>
      )}

      {pseudoOpen && (
        <div className="modal-backdrop" onClick={() => setPseudoOpen(false)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3 className="panel-title">Псевдо-перевод (тест пайплайна)</h3>
            <p className="muted">
              Заполняет сегменты синтетическим текстом — акцентированные буквы, ~40% длиннее,
              теги/числа/плейсхолдеры сохранены. Используйте, чтобы проверить экспорт и верстку
              <b>до</b> начала перевода.
            </p>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" disabled={pseudoBusy} onClick={() => void runPseudo(false)}>
                Только пустые
              </button>
              <span className="spacer" />
              <button className="btn" disabled={pseudoBusy} onClick={() => void runPseudo(true)}>
                Перезаписать все
              </button>
            </div>
            <p className="muted small-text" style={{ marginTop: 10 }}>
              Результаты — неподтвержденные черновики: QA по-прежнему требует подтверждения по сегментам.
            </p>
          </div>
        </div>
      )}

      {replaceOpen && (
        <div className="modal-backdrop" onClick={() => setReplaceOpen(false)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3 className="panel-title">Найти и заменить в переводах</h3>
            <p className="muted">Применяется к переводам всех сегментов этого задания.</p>
            <div className="column" style={{ gap: 8, marginTop: 8 }}>
              <input
                placeholder="Найти в переводах…"
                value={findText}
                onChange={(e) => setFindText(e.target.value)}
                autoFocus
              />
              <input
                placeholder="Заменить на…"
                value={replaceText}
                onChange={(e) => setReplaceText(e.target.value)}
              />
            </div>
            <p className="muted small-text">
              {findText
                ? `Сегментов с «${findText}»: ${replaceCount}. Измененные сегменты теряют подтверждение.`
                : 'Введите текст для поиска.'}
            </p>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn" onClick={() => setReplaceOpen(false)}>Отмена</button>
              <span className="spacer" />
              <button className="btn primary" disabled={!findText || replaceCount === 0 || replaceBusy}
                      onClick={() => void applyReplace()}>
                {replaceBusy ? 'Замена…' : 'Заменить все'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
