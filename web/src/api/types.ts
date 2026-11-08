export type Role = 'admin' | 'pm' | 'translator';
export type JobStatus =
  | 'unassigned' | 'assigned' | 'declined' | 'accepted' | 'in_progress'
  | 'submitted' | 'rework' | 'completed' | 'canceled';

export interface User {
  id: number;
  username: string;
}

export interface Membership {
  org_id: number;
  org_name: string;
  role: Role;
}

export interface LoginResponse {
  access_token: string;
  user: User;
  memberships: Membership[];
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
  project_id: number;
  name: string;
  format: 'txt' | 'docx' | 'xliff';
  processing_status: 'processing' | 'ready' | 'failed';
  processing_error: string | null;
  processing_notes: string[];
  created_at: string;
}

export interface TMRef { id: number; name: string; source_lang: string; target_lang: string; }
export interface TBRef { id: number; name: string; source_lang: string; target_lang: string; }

export interface Resource {
  id: number;
  tm: TMRef | null;
  tb: TBRef | null;
  use_for_search: boolean;
  use_for_write: boolean;
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
  // Свежие локи сегментов («кто редактирует — тот блокирует»)
  locks: SegmentLockInfo[];
}

export interface SegmentLockInfo {
  segment_id: number;
  user: User;
  locked_at: string;
}

export interface SegmentLockResponse {
  segment_id: number;
  locked_by: User;
  locked_at: string;
  ttl_seconds: number;
}

export interface LockConflictBody {
  detail: {
    message: string;
    locked_by: User;
    locked_at: string;
    ttl_seconds: number;
  };
}

export interface Member {
  id: number;
  user: User;
  role: Role;
}

export interface TMInfo { id: number; name: string; source_lang: string; target_lang: string; entry_count: number; }
export interface TBInfo { id: number; name: string; source_lang: string; target_lang: string; entry_count: number; }
export interface TMEntry { id: number; source_text: string; target_text: string; }
export interface TBEntry { id: number; term: string; translation: string; note: string; status: 'preferred' | 'admitted' | 'forbidden'; }

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

// --- RX-проверки QA: правила на регулярных выражениях ---

export interface QaRegexRule {
  id: number;
  name: string;
  pattern: string;
  side: 'source' | 'target';
  must_match: boolean;
  severity: 'warning' | 'error';
  case_sensitive: boolean;
  enabled: boolean;
  description: string;
  created_at: string;
}

export interface QaRegexTestResult {
  matches: string[];
  error: string | null;
}

export interface MTSuggestion {
  text: string;
  provider: string;
}

export interface AnalysisCategory {
  code: string;
  label: string;
  segments: number;
  words: number;
  weighted: number;
}

export interface AnalysisReport {
  job_id: number;
  source_lang: string;
  target_lang: string;
  tm_names: string[];
  segments: number;
  words: number;
  weighted_words: number;
  categories: AnalysisCategory[];
}

export interface PreTranslateResult {
  filled_exact: number;
  filled_fuzzy: number;
  propagated: number;
  skipped: number;
}

// --- LQA (оценка качества перевода) ---

export type LQACategory = 'accuracy' | 'terminology' | 'fluency' | 'style' | 'locale' | 'consistency';
export type LQASeverity = 'minor' | 'major' | 'critical';

export interface LQAError {
  id: number;
  job_id: number;
  segment_id: number;
  position: number;
  category: string;
  severity: string;
  penalty: number;
  comment: string;
  author: User;
  created_at: string;
}

export interface LQACategoryStat {
  category: string;
  label: string;
  count: number;
  penalty: number;
}

export interface LQAReport {
  job_id: number;
  words: number;
  errors: LQAError[];
  error_count: number;
  penalty_points: number;
  score: number;
  pass_score: number;
  passed: boolean;
  by_category: LQACategoryStat[];
}

// --- Комментарии к сегментам ---

export interface SegmentNote {
  id: number;
  segment_id: number;
  position: number;
  author: User;
  body: string;
  created_at: string;
}

// --- История правок сегмента (track changes) ---

export interface SegmentRevision {
  id: number;
  segment_id: number;
  position: number;
  author: User;
  target_text: string;
  created_at: string;
}

// --- Биллинг: прайс-листы и котировки ---

export interface RateCard {
  id: number;
  name: string;
  source_lang: string;
  target_lang: string;
  currency: string;
  rates: Record<string, number>;
  min_charge: number;
  created_at: string;
}

export interface QuoteLine {
  code: string;
  label: string;
  segments: number;
  words: number;
  rate: number;
  amount: number;
}

export interface JobQuote {
  job_id: number;
  document_name: string;
  target_lang: string;
  rate_card: string;
  currency: string;
  lines: QuoteLine[];
  subtotal: number;
  min_charge: number;
  min_charge_applied: boolean;
  total: number;
}

export interface ProjectQuote {
  project_id: number;
  project_name: string;
  source_lang: string;
  jobs: JobQuote[];
  totals: { currency: string; total: number }[];
  missing_pairs: string[];
}

export const RATE_CATEGORY_CODES = [
  'exact', 'fuzzy_95_99', 'fuzzy_85_94', 'fuzzy_75_84',
  'fuzzy_60_74', 'repetition', 'no_match',
] as const;

export const RATE_CATEGORY_LABELS: Record<string, string> = {
  exact: 'Exact 100%',
  fuzzy_95_99: 'Fuzzy 95–99%',
  fuzzy_85_94: 'Fuzzy 85–94%',
  fuzzy_75_84: 'Fuzzy 75–84%',
  fuzzy_60_74: 'Fuzzy 60–74%',
  repetition: 'Repetitions',
  no_match: 'No match',
};

// --- Отчеты ---

export interface ProductivityRow {
  user: User;
  jobs_assigned: number;
  jobs_completed: number;
  jobs_overdue: number;
  words_confirmed: number;
  lqa_jobs: number;
  avg_lqa_score: number | null;
}


export interface AppNotification {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
}

export interface WsEvent {
  type: string;
  ts: string;
  [key: string]: unknown;
}

export type FeedbackKind = 'bug' | 'idea' | 'question' | 'other';

export interface FeedbackItem {
  id: number;
  kind: FeedbackKind;
  message: string;
  page: string;
  author: User;
  created_at: string;
}
