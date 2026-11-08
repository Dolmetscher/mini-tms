import type {
  AlignProposal, AlignPair, AnalysisReport, AppNotification, DocumentInfo, FeedbackItem, FeedbackKind,
  HandoffImportResult, Job, JobDetail, LQAError, LQAReport, LoginResponse, Member, MTSuggestion,
  PreTranslateResult, ProductivityRow, Project, ProjectQuote, PseudoTranslateResult, QaRegexRule,
  QaRegexTestResult, QAReport, RateCard, Resource, Segment, SegmentLockResponse, SegmentNote,
  SegmentRevision, TBEntry, TBHit, TBInfo, TMEntry, TMInfo, TMSuggestion, User,
} from './types';

/** Дефолтный адрес API: env-переменная сборки → origin страницы (SPA с бэкенда
 *  работает «одним URL» без ручного ввода адреса) → localhost:8000 для vite dev. */
function defaultApiBase(): string {
  const env = import.meta.env.VITE_API_BASE as string | undefined;
  if (env) return env;
  if (typeof window !== 'undefined' && window.location.protocol.startsWith('http')
      && !window.location.origin.startsWith('http://localhost:5173')) {
    return window.location.origin;
  }
  return 'http://localhost:8000';
}

let API_BASE =
  localStorage.getItem('tms_api_base') ??
  defaultApiBase();

/** Адрес сервера в рантайме (десктоп/SPA): хранится в localStorage. */
export function getApiBase(): string {
  return API_BASE;
}

export function setApiBase(base: string): void {
  // Меняем и модульную переменную: все последующие запросы и WebSocket
  // сразу идут на новый адрес, без перезагрузки страницы.
  API_BASE = base.replace(/\/+$/, '');
  localStorage.setItem('tms_api_base', API_BASE);
}

let authToken: string | null = localStorage.getItem('tms_token');
let activeOrgId: number | null = Number(localStorage.getItem('tms_org')) || null;

export function storeToken(token: string | null): void {
  authToken = token;
  if (token) localStorage.setItem('tms_token', token);
  else localStorage.removeItem('tms_token');
}

export function setActiveOrg(orgId: number | null): void {
  activeOrgId = orgId;
  if (orgId) localStorage.setItem('tms_org', String(orgId));
  else localStorage.removeItem('tms_org');
}

export function getActiveOrg(): number | null {
  return activeOrgId;
}

export function wsUrl(): string {
  const base = API_BASE.replace(/^http/, 'ws');
  const org = activeOrgId ?? 0;
  return `${base}/ws/updates?token=${encodeURIComponent(authToken ?? '')}&org_id=${org}`;
}

export function exportJobUrl(jobId: number): string {
  return `${API_BASE}/api/jobs/${jobId}/export`;
}

/** Скачивание файла с авторизацией: обычная <a href> не передает токен, поэтому
 * запрашиваем файл fetch-ом с заголовками и сохраняем как blob.
 * filename* (RFC 5987, UTF-8) имеет приоритет над ASCII-fallback в filename="". */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const headers = new Headers();
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  if (activeOrgId) headers.set('X-Org-Id', String(activeOrgId));
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers });
  } catch {
    throw new ApiError(0, 'No connection');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body && typeof body === 'object' && 'detail' in body) detail = String(body.detail);
    } catch {
      // тело не JSON
    }
    throw new ApiError(res.status, detail);
  }
  const disposition = res.headers.get('Content-Disposition') ?? '';
  const utf8Name = /filename\*=UTF-8''([^;]+)/.exec(disposition)?.[1];
  const asciiName = /filename="([^"]+)"/.exec(disposition)?.[1];
  const filename = (utf8Name && decodeURIComponent(utf8Name)) || asciiName || fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function downloadJobExport(jobId: number): Promise<void> {
  return downloadFile(`/api/jobs/${jobId}/export`, `job-${jobId}-export`);
}

export async function downloadTmExport(tmId: number): Promise<void> {
  return downloadFile(`/api/tm/tms/${tmId}/export`, `tm-${tmId}.tmx`);
}

export async function downloadTmExportCsv(tmId: number): Promise<void> {
  return downloadFile(`/api/tm/tms/${tmId}/export-csv`, `tm-${tmId}.csv`);
}

export async function downloadTbExport(tbId: number): Promise<void> {
  return downloadFile(`/api/tb/tbs/${tbId}/export`, `tb-${tbId}.csv`);
}

export async function downloadTbExportTbx(tbId: number): Promise<void> {
  return downloadFile(`/api/tb/tbs/${tbId}/export-tbx`, `tb-${tbId}.tbx`);
}

export async function downloadBilingualExport(jobId: number): Promise<void> {
  return downloadFile(`/api/jobs/${jobId}/export-bilingual`, `job-${jobId}-bilingual.docx`);
}

export async function downloadJobXliffHandoff(jobId: number): Promise<void> {
  return downloadFile(`/api/jobs/${jobId}/export-xliff`, `job-${jobId}-handoff.xliff`);
}

export async function downloadJobQuoteExport(jobId: number, rateCardId?: number): Promise<void> {
  return downloadFile(`/api/jobs/${jobId}/quote/export${q({ rate_card_id: rateCardId })}`, `job-${jobId}-quote.docx`);
}

export async function downloadProjectDelivery(projectId: number): Promise<void> {
  return downloadFile(`/api/projects/${projectId}/deliver`, `project-${projectId}-delivery.zip`);
}

export async function downloadProjectInvoice(projectId: number, rateCardId?: number): Promise<void> {
  return downloadFile(`/api/projects/${projectId}/invoice/export${q({ rate_card_id: rateCardId })}`, `invoice-project-${projectId}.docx`);
}

export async function downloadOrgBackup(orgId: number): Promise<void> {
  return downloadFile(`/api/orgs/${orgId}/export`, `mini-tms-backup-org${orgId}.json`);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  if (activeOrgId) headers.set('X-Org-Id', String(activeOrgId));
  if (options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch {
    throw new ApiError(0, 'No connection');
  }
  if (!res.ok) {
    let detail = res.statusText;
    let body: unknown;
    try {
      body = await res.json();
      if (typeof body === 'object' && body && 'detail' in body) detail = String((body as { detail: unknown }).detail);
    } catch {
      // тело не JSON
    }
    throw new ApiError(res.status, detail, body);
  }
  return (await res.json()) as T;
}

/** Страница списка: элементы + X-Total-Count (пагинация больших ресурсов). */
export interface Page<T> {
  items: T[];
  total: number;
}

async function requestPage<T>(path: string): Promise<Page<T>> {
  const headers = new Headers();
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  if (activeOrgId) headers.set('X-Org-Id', String(activeOrgId));
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers });
  } catch {
    throw new ApiError(0, 'No connection');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body === 'object' && body && 'detail' in body) detail = String((body as { detail: unknown }).detail);
    } catch {
      // тело не JSON
    }
    throw new ApiError(res.status, detail);
  }
  const items = (await res.json()) as T[];
  return { items, total: Number(res.headers.get('X-Total-Count') ?? items.length) };
}

function q(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const str = search.toString();
  return str ? `?${str}` : '';
}

export const api = {
  // auth
  login: (username: string, password: string) =>
    request<LoginResponse>('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  me: () => request<LoginResponse>('/api/me'),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ access_token: string }>('/api/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // projects & documents
  projects: () => request<Project[]>('/api/projects'),
  project: (id: number) => request<Project>(`/api/projects/${id}`),
  createProject: (body: {
    name: string; source_lang: string; target_langs: string[];
    deadline?: string | null; instructions?: string; mt_enabled?: boolean;
  }) => request<Project>('/api/projects', { method: 'POST', body: JSON.stringify(body) }),
  updateProject: (id: number, body: Record<string, unknown>) =>
    request<Project>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  uploadDocument: (projectId: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<DocumentInfo>(`/api/projects/${projectId}/documents`, { method: 'POST', body: fd });
  },
  document: (id: number) => request<DocumentInfo>(`/api/documents/${id}`),
  documents: (projectId: number) => request<DocumentInfo[]>(`/api/projects/${projectId}/documents`),

  // resources
  resources: (projectId: number) => request<Resource[]>(`/api/projects/${projectId}/resources`),
  connectResource: (projectId: number, body: { tm_id?: number; tb_id?: number; use_for_search: boolean; use_for_write?: boolean }) =>
    request<Resource>(`/api/projects/${projectId}/resources`, { method: 'POST', body: JSON.stringify(body) }),
  disconnectResource: (projectId: number, resourceId: number) =>
    request<void>(`/api/projects/${projectId}/resources/${resourceId}`, { method: 'DELETE' }),
  tmSearch: (
    projectId: number, text: string, targetLang: string,
    side: 'source' | 'target' = 'source', mode: 'match' | 'concordance' = 'match',
  ) =>
    request<TMSuggestion[]>(`/api/projects/${projectId}/tm/search${q({ text, target_lang: targetLang, side, mode })}`),
  tbLookup: (projectId: number, text: string, targetLang: string) =>
    request<TBHit[]>(`/api/projects/${projectId}/tb/lookup${q({ text, target_lang: targetLang })}`),

  // jobs
  jobs: (params: { status?: string; project_id?: number; mine?: boolean; q?: string; sort?: string } = {}) =>
    request<Job[]>(`/api/jobs${q(params)}`),
  job: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  assignJob: (id: number, assignee_id: number, deadline?: string | null) =>
    request<Job>(`/api/jobs/${id}/assign`, { method: 'POST', body: JSON.stringify({ assignee_id, deadline: deadline ?? null }) }),
  acceptJob: (id: number) => request<Job>(`/api/jobs/${id}/accept`, { method: 'POST' }),
  declineJob: (id: number, reason: string) =>
    request<Job>(`/api/jobs/${id}/decline`, { method: 'POST', body: JSON.stringify({ reason }) }),
  submitJob: (id: number) => request<Job>(`/api/jobs/${id}/submit`, { method: 'POST' }),
  completeJob: (id: number) => request<Job>(`/api/jobs/${id}/complete`, { method: 'POST' }),
  returnJob: (id: number, comment: string) =>
    request<Job>(`/api/jobs/${id}/return`, { method: 'POST', body: JSON.stringify({ comment }) }),
  cancelJob: (id: number) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  jobQa: (id: number) => request<QAReport>(`/api/jobs/${id}/qa`),
  jobAnalysis: (id: number) => request<AnalysisReport>(`/api/jobs/${id}/analysis`),
  preTranslateJob: (id: number, minScore: number) =>
    request<PreTranslateResult>(`/api/jobs/${id}/pre-translate`, {
      method: 'POST', body: JSON.stringify({ min_score: minScore }),
    }),
  // Псевдо-перевод: заполнить сегменты синтетикой для проверки пайплайна
  pseudoTranslateJob: (id: number, overwrite: boolean) =>
    request<PseudoTranslateResult>(`/api/jobs/${id}/pseudo-translate`, {
      method: 'POST', body: JSON.stringify({ overwrite }),
    }),
  // XLIFF-хендофф: возврат пакета из внешнего CAT-инструмента
  importJobXliff: (id: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<HandoffImportResult>(`/api/jobs/${id}/import-xliff`, { method: 'POST', body: fd });
  },
  saveSegment: (id: number, body: { target_text?: string; confirmed?: boolean; base_updated_at?: string }) =>
    request<Segment>(`/api/segments/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  // Локи «кто редактирует — тот блокирует»: захват/продление (heartbeat) и снятие
  lockSegment: (id: number) =>
    request<SegmentLockResponse>(`/api/segments/${id}/lock`, { method: 'POST' }),
  unlockSegment: (id: number) =>
    request<void>(`/api/segments/${id}/lock`, { method: 'DELETE' }),
  mtSuggest: (jobId: number, segmentId: number) =>
    request<MTSuggestion>(`/api/jobs/${jobId}/segments/${segmentId}/mt`),

  // LQA (оценка качества) и комментарии к сегментам
  jobLqa: (id: number) => request<LQAReport>(`/api/jobs/${id}/lqa`),
  addLqaError: (jobId: number, body: { segment_id: number; category: string; severity: string; comment: string }) =>
    request<LQAError>(`/api/jobs/${jobId}/lqa`, { method: 'POST', body: JSON.stringify(body) }),
  deleteLqaError: (jobId: number, errorId: number) =>
    request<void>(`/api/jobs/${jobId}/lqa/${errorId}`, { method: 'DELETE' }),
  segmentNotes: (segmentId: number) => request<SegmentNote[]>(`/api/segments/${segmentId}/notes`),
  addSegmentNote: (segmentId: number, body: string) =>
    request<SegmentNote>(`/api/segments/${segmentId}/notes`, { method: 'POST', body: JSON.stringify({ body }) }),
  segmentHistory: (segmentId: number) => request<SegmentRevision[]>(`/api/segments/${segmentId}/history`),

  // Биллинг: прайс-листы и котировки
  rateCards: () => request<RateCard[]>('/api/rates'),
  createRateCard: (body: { name: string; source_lang: string; target_lang: string; currency?: string; rates?: Record<string, number>; min_charge?: number }) =>
    request<RateCard>('/api/rates', { method: 'POST', body: JSON.stringify(body) }),
  updateRateCard: (id: number, body: { name?: string; currency?: string; rates?: Record<string, number>; min_charge?: number }) =>
    request<RateCard>(`/api/rates/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteRateCard: (id: number) => request<void>(`/api/rates/${id}`, { method: 'DELETE' }),
  projectQuote: (projectId: number, rateCardId?: number) =>
    request<ProjectQuote>(`/api/projects/${projectId}/quote${q({ rate_card_id: rateCardId })}`),

  // Отчеты
  productivity: (updatedAfter?: string) =>
    request<ProductivityRow[]>(`/api/reports/productivity${q({ updated_after: updatedAfter })}`),

  // RX-проверки QA: правила на регулярных выражениях (создание — pm/admin)
  qaRules: () => request<QaRegexRule[]>('/api/qa-rules'),
  createQaRule: (body: {
    name: string; pattern: string; side: 'source' | 'target'; must_match: boolean;
    severity: 'warning' | 'error'; case_sensitive: boolean; enabled: boolean; description?: string;
  }) => request<QaRegexRule>('/api/qa-rules', { method: 'POST', body: JSON.stringify(body) }),
  updateQaRule: (id: number, body: Partial<Omit<QaRegexRule, 'id' | 'created_at'>>) =>
    request<QaRegexRule>(`/api/qa-rules/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteQaRule: (id: number) => request<void>(`/api/qa-rules/${id}`, { method: 'DELETE' }),
  testQaRule: (pattern: string, caseSensitive: boolean, text: string) =>
    request<QaRegexTestResult>('/api/qa-rules/test', {
      method: 'POST',
      body: JSON.stringify({ pattern, case_sensitive: caseSensitive, text }),
    }),

  // TM / TB
  tms: () => request<TMInfo[]>('/api/tm/tms'),
  createTM: (body: { name: string; source_lang: string; target_lang: string }) =>
    request<TMInfo>('/api/tm/tms', { method: 'POST', body: JSON.stringify(body) }),
  // Выравнивание параллельных файлов: предложение пар и подтверждение
  alignFiles: (tmId: number, sourceFile: File, targetFile: File) => {
    const fd = new FormData();
    fd.append('source_file', sourceFile);
    fd.append('target_file', targetFile);
    return request<AlignProposal>(`/api/tm/tms/${tmId}/align`, { method: 'POST', body: fd });
  },
  confirmAlignment: (tmId: number, pairs: Pick<AlignPair, 'source' | 'target'>[]) =>
    request<{ added: number; updated: number; skipped: number }>(`/api/tm/tms/${tmId}/align/confirm`, {
      method: 'POST', body: JSON.stringify({ pairs }),
    }),
  tmEntries: (tmId: number, search?: string, offset?: number, limit?: number) =>
    requestPage<TMEntry>(`/api/tm/tms/${tmId}/entries${q({ q: search, offset, limit })}`),
  upsertTMEntry: (tmId: number, body: { source_text: string; target_text: string }) =>
    request<TMEntry>(`/api/tm/tms/${tmId}/entries`, { method: 'POST', body: JSON.stringify(body) }),
  updateTMEntry: (tmId: number, entryId: number, target_text: string) =>
    request<TMEntry>(`/api/tm/tms/${tmId}/entries/${entryId}`, { method: 'PATCH', body: JSON.stringify({ target_text }) }),
  deleteTMEntry: (tmId: number, entryId: number) =>
    request<void>(`/api/tm/tms/${tmId}/entries/${entryId}`, { method: 'DELETE' }),
  importTMX: (tmId: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ added: number; updated: number; skipped: number }>(`/api/tm/tms/${tmId}/import-tmx`, { method: 'POST', body: fd });
  },
  importTM: (tmId: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ added: number; updated: number; skipped: number }>(`/api/tm/tms/${tmId}/import-csv`, { method: 'POST', body: fd });
  },
  tbs: () => request<TBInfo[]>('/api/tb/tbs'),
  createTB: (body: { name: string; source_lang: string; target_lang: string }) =>
    request<TBInfo>('/api/tb/tbs', { method: 'POST', body: JSON.stringify(body) }),
  tbEntries: (tbId: number, search?: string, offset?: number, limit?: number) =>
    requestPage<TBEntry>(`/api/tb/tbs/${tbId}/entries${q({ q: search, offset, limit })}`),
  addTBEntry: (tbId: number, body: { term: string; translation: string; note?: string; status?: string }) =>
    request<TBEntry>(`/api/tb/tbs/${tbId}/entries`, { method: 'POST', body: JSON.stringify(body) }),
  updateTBEntry: (tbId: number, entryId: number, body: { term?: string; translation?: string; note?: string; status?: string }) =>
    request<TBEntry>(`/api/tb/tbs/${tbId}/entries/${entryId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteTBEntry: (tbId: number, entryId: number) =>
    request<void>(`/api/tb/tbs/${tbId}/entries/${entryId}`, { method: 'DELETE' }),
  importTB: (tbId: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ added: number; skipped: number }>(`/api/tb/tbs/${tbId}/import-csv`, { method: 'POST', body: fd });
  },
  importTBX: (tbId: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ added: number }>(`/api/tb/tbs/${tbId}/import-tbx`, { method: 'POST', body: fd });
  },

  // org
  members: (orgId: number) => request<Member[]>(`/api/orgs/${orgId}/members`),

  // notifications
  notifications: () => request<AppNotification[]>('/api/notifications'),
  markNotificationRead: (id: number) =>
    request<AppNotification>(`/api/notifications/${id}/read`, { method: 'POST' }),
  markAllNotificationsRead: () =>
    request<{ updated: number }>('/api/notifications/read-all', { method: 'POST' }),

  // обратная связь (кнопка «Feedback»): отправка — все, лента — pm/admin
  sendFeedback: (body: { kind: FeedbackKind; message: string; page?: string }) =>
    request<FeedbackItem>('/api/feedback', { method: 'POST', body: JSON.stringify(body) }),
  feedback: (limit?: number) => request<FeedbackItem[]>(`/api/feedback${q({ limit })}`),

  // контакты для внешних уведомлений (email / Telegram)
  updateMyContact: (body: { email?: string; telegram_chat_id?: string }) =>
    request<User>('/api/me', { method: 'PATCH', body: JSON.stringify(body) }),
};

export function userLabel(user: User | null): string {
  return user ? user.username : '—';
}
