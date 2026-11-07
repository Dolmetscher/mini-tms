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
