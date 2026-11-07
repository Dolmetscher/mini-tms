/** API-клиент CAT: каждая функция принимает контекст активного аккаунта. */

export interface User {
  id: number;
  username: string;
}

export interface Membership {
  org_id: number;
  org_name: string;
  role: 'admin' | 'pm' | 'translator';
}

export interface Account {
  key: string; // userId:orgId — префикс локальных данных аккаунта (FR-01)
  base: string;
  token: string;
  userId: number;
  username: string;
  orgId: number;
  orgName: string;
  role: Membership['role'];
}

export interface Project {
  id: number;
  name: string;
  source_lang: string;
  target_langs: string[];
  deadline: string | null;
  instructions: string;
  mt_enabled: boolean;
  status: string;
  manager: User;
  created_at: string;
}

export interface DocumentInfo {
  id: number;
  name: string;
  format: string;
  processing_status: 'processing' | 'ready' | 'failed';
  processing_error: string | null;
  processing_notes: string[];
}

export interface Segment {
  id: number;
  job_id: number;
  position: number;
  source_text: string;
  target_text: string;
  translation_status: 'untranslated' | 'drafted' | 'translated';
  confirmed: boolean;
  is_mandatory: boolean;
  source_words: number;
  updated_at: string;
}

export type JobStatus =
  | 'unassigned' | 'assigned' | 'declined' | 'accepted' | 'in_progress'
  | 'submitted' | 'rework' | 'completed' | 'canceled';

export interface Job {
  id: number;
  project_id: number;
  project_name: string;
  document_id: number;
  document_name: string;
  document_format: string;
  source_lang: string;
  target_lang: string;
  status: JobStatus;
  deadline: string | null;
  project_deadline: string | null;
  instructions: string;
  is_overdue: boolean;
  assignee: User | null;
  total_segments: number;
  confirmed_segments: number;
  total_words: number;
  updated_at: string;
}

export interface JobDetail extends Job {
  rework_comment: string;
  decline_reason: string;
  segments: Segment[];
}

export interface TMSuggestion {
  source_match: string;
  target_suggestion: string;
  match_percentage: number;
  tm_name: string;
  match_type: 'exact' | 'fuzzy' | 'concordance';
}

export interface TBHit {
  term: string;
  translation: string;
  note: string;
  status: string;
  tb_name: string;
}

export interface QAItem {
  segment_id: number;
  position: number;
  code: string;
  severity: 'error' | 'warning';
  message: string;
}

export interface QAReport {
  can_submit: boolean;
  blocking_count: number;
  warning_count: number;
  items: QAItem[];
}

export interface MTSuggestion {
  text: string;
  provider: string;
}

export interface AppNotification {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
}

export interface Member {
  id: number;
  user: User;
  role: Membership['role'];
}

export interface WsEvent {
  type: string;
  ts: string;
  job_id?: number;
  segment_id?: number;
  translation_status?: string;
  confirmed?: boolean;
  target_text?: string;
  [key: string]: unknown;
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

export async function apiRequest<T>(acc: Account, path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string> | undefined) ?? {}),
  };
  headers['Authorization'] = `Bearer ${acc.token}`;
  headers['X-Org-Id'] = String(acc.orgId);
  let res: Response;
  try {
    res = await fetch(`${acc.base}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, 'No connection');
  }
  if (!res.ok) {
    let detail = res.statusText;
    let body: unknown;
    try {
      body = await res.json();
      if (body && typeof body === 'object' && 'detail' in body) detail = String((body as { detail: unknown }).detail);
    } catch {
      // тело не JSON
    }
    throw new ApiError(res.status, detail, body);
  }
  return (await res.json()) as T;
}

function q(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') search.set(k, String(v));
  }
  const s = search.toString();
  return s ? `?${s}` : '';
}

export interface LoginResponse {
  access_token: string;
  user: User;
  memberships: Membership[];
}

export async function login(base: string, username: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${base.replace(/\/+$/, '')}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new ApiError(res.status, 'Invalid username or password');
  return (await res.json()) as LoginResponse;
}

export const api = {
  me: (acc: Account) => apiRequest<LoginResponse>(acc, '/api/me'),
  jobs: (acc: Account, params: { status?: string; q?: string; mine?: boolean } = {}) =>
    apiRequest<Job[]>(acc, `/api/jobs${q(params)}`),
  job: (acc: Account, id: number) => apiRequest<JobDetail>(acc, `/api/jobs/${id}`),
  acceptJob: (acc: Account, id: number) => apiRequest<Job>(acc, `/api/jobs/${id}/accept`, { method: 'POST' }),
  declineJob: (acc: Account, id: number, reason: string) =>
    apiRequest<Job>(acc, `/api/jobs/${id}/decline`, { method: 'POST', body: JSON.stringify({ reason }) }),
  submitJob: (acc: Account, id: number) => apiRequest<Job>(acc, `/api/jobs/${id}/submit`, { method: 'POST' }),
  jobQa: (acc: Account, id: number) => apiRequest<QAReport>(acc, `/api/jobs/${id}/qa`),
  saveSegment: (
    acc: Account,
    id: number,
    body: { target_text?: string; confirmed?: boolean; base_updated_at?: string },
  ) => apiRequest<Segment>(acc, `/api/segments/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  tmSearch: (acc: Account, projectId: number, text: string, targetLang: string) =>
    apiRequest<TMSuggestion[]>(acc, `/api/projects/${projectId}/tm/search${q({ text, target_lang: targetLang })}`),
  tbLookup: (acc: Account, projectId: number, text: string, targetLang: string) =>
    apiRequest<TBHit[]>(acc, `/api/projects/${projectId}/tb/lookup${q({ text, target_lang: targetLang })}`),
  mtSuggest: (acc: Account, jobId: number, segmentId: number) =>
    apiRequest<MTSuggestion>(acc, `/api/jobs/${jobId}/segments/${segmentId}/mt`),
  notifications: (acc: Account) => apiRequest<AppNotification[]>(acc, '/api/notifications'),
  markNotificationRead: (acc: Account, id: number) =>
    apiRequest<AppNotification>(acc, `/api/notifications/${id}/read`, { method: 'POST' }),
  markAllNotificationsRead: (acc: Account) =>
    apiRequest<{ updated: number }>(acc, '/api/notifications/read-all', { method: 'POST' }),
  projects: (acc: Account) => apiRequest<Project[]>(acc, '/api/projects'),
  projectJobs: (acc: Account, projectId: number) =>
    apiRequest<Job[]>(acc, `/api/jobs${q({ project_id: projectId })}`),
  createProject: (
    acc: Account,
    body: {
      name: string; source_lang: string; target_langs: string[];
      deadline?: string | null; instructions?: string; mt_enabled?: boolean;
    },
  ) => apiRequest<Project>(acc, '/api/projects', { method: 'POST', body: JSON.stringify(body) }),
  uploadDocument: (acc: Account, projectId: number, file: { uri: string; name: string; mime: string }) => {
    const form = new FormData();
    // React Native FormData: поле uri ведет на локальный файл (expo-document-picker)
    form.append('file', { uri: file.uri, name: file.name, type: file.mime } as unknown as Blob);
    return apiRequest<DocumentInfo>(acc, `/api/projects/${projectId}/documents`, {
      method: 'POST',
      body: form,
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  members: (acc: Account, orgId: number) => apiRequest<Member[]>(acc, `/api/orgs/${orgId}/members`),
  assignJob: (acc: Account, id: number, assigneeId: number, deadline?: string | null) =>
    apiRequest<Job>(acc, `/api/jobs/${id}/assign`, {
      method: 'POST',
      body: JSON.stringify({ assignee_id: assigneeId, deadline: deadline ?? null }),
    }),
  completeJob: (acc: Account, id: number) => apiRequest<Job>(acc, `/api/jobs/${id}/complete`, { method: 'POST' }),
  returnJob: (acc: Account, id: number, comment: string) =>
    apiRequest<Job>(acc, `/api/jobs/${id}/return`, { method: 'POST', body: JSON.stringify({ comment }) }),
  cancelJob: (acc: Account, id: number) => apiRequest<Job>(acc, `/api/jobs/${id}/cancel`, { method: 'POST' }),
};

export function wsUrl(acc: Account): string {
  const base = acc.base.replace(/^http/, 'ws');
  return `${base}/ws/updates?token=${encodeURIComponent(acc.token)}&org_id=${acc.orgId}`;
}

export function tagTokens(text: string): string[] {
  return [...new Set(text.match(/<x id="[^"]+"\s*\/>|<g id="[^"]+">|<\/g>/g) ?? [])];
}
