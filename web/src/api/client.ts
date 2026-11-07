import type {
  AnalysisReport, AppNotification, DocumentInfo, Job, JobDetail, LoginResponse, Member,
  MTSuggestion, PreTranslateResult, Project, QAReport, Resource, Segment, TBEntry, TBHit,
  TBInfo, TMEntry, TMInfo, TMSuggestion, User,
} from './types';

let API_BASE =
  localStorage.getItem('tms_api_base') ??
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  'http://localhost:8000';

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

export async function downloadTbExport(tbId: number): Promise<void> {
  return downloadFile(`/api/tb/tbs/${tbId}/export`, `tb-${tbId}.csv`);
}

export async function downloadBilingualExport(jobId: number): Promise<void> {
  return downloadFile(`/api/jobs/${jobId}/export-bilingual`, `job-${jobId}-bilingual.docx`);
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
  saveSegment: (id: number, body: { target_text?: string; confirmed?: boolean; base_updated_at?: string }) =>
    request<Segment>(`/api/segments/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  mtSuggest: (jobId: number, segmentId: number) =>
    request<MTSuggestion>(`/api/jobs/${jobId}/segments/${segmentId}/mt`),

  // TM / TB
  tms: () => request<TMInfo[]>('/api/tm/tms'),
  createTM: (body: { name: string; source_lang: string; target_lang: string }) =>
    request<TMInfo>('/api/tm/tms', { method: 'POST', body: JSON.stringify(body) }),
  tmEntries: (tmId: number, search?: string) =>
    request<TMEntry[]>(`/api/tm/tms/${tmId}/entries${q({ q: search })}`),
  upsertTMEntry: (tmId: number, body: { source_text: string; target_text: string }) =>
    request<TMEntry>(`/api/tm/tms/${tmId}/entries`, { method: 'POST', body: JSON.stringify(body) }),
  importTMX: (tmId: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ added: number; updated: number; skipped: number }>(`/api/tm/tms/${tmId}/import-tmx`, { method: 'POST', body: fd });
  },
  tbs: () => request<TBInfo[]>('/api/tb/tbs'),
  createTB: (body: { name: string; source_lang: string; target_lang: string }) =>
    request<TBInfo>('/api/tb/tbs', { method: 'POST', body: JSON.stringify(body) }),
  tbEntries: (tbId: number, search?: string) =>
    request<TBEntry[]>(`/api/tb/tbs/${tbId}/entries${q({ q: search })}`),
  addTBEntry: (tbId: number, body: { term: string; translation: string; note?: string; status?: string }) =>
    request<TBEntry>(`/api/tb/tbs/${tbId}/entries`, { method: 'POST', body: JSON.stringify(body) }),
  importTB: (tbId: number, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ added: number; skipped: number }>(`/api/tb/tbs/${tbId}/import-csv`, { method: 'POST', body: fd });
  },

  // org
  members: (orgId: number) => request<Member[]>(`/api/orgs/${orgId}/members`),

  // notifications
  notifications: () => request<AppNotification[]>('/api/notifications'),
  markNotificationRead: (id: number) =>
    request<AppNotification>(`/api/notifications/${id}/read`, { method: 'POST' }),
  markAllNotificationsRead: () =>
    request<{ updated: number }>('/api/notifications/read-all', { method: 'POST' }),
};

export function userLabel(user: User | null): string {
  return user ? user.username : '—';
}
