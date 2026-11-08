// Русские подписи для значений из API (статусы,severity, типы совпадений)
// и общий форматтер дат. Подписи для категории LQA живут в JobsPage,
// для категорий ставок — в types.ts (RATE_CATEGORY_LABELS).

export const STATUS_LABELS: Record<string, string> = {
  unassigned: 'не назначено',
  assigned: 'назначено',
  declined: 'отклонено',
  accepted: 'принято',
  in_progress: 'в работе',
  submitted: 'сдано',
  rework: 'доработка',
  completed: 'завершено',
  canceled: 'отменено',
};

export const TR_STATUS_LABELS: Record<string, string> = {
  untranslated: 'не переведено',
  drafted: 'черновик',
  translated: 'переведено',
};

export const SEVERITY_LABELS: Record<string, string> = {
  minor: 'незнач.',
  major: 'сущест.',
  critical: 'крит.',
};

export const MATCH_TYPE_LABELS: Record<string, string> = {
  exact: 'точное',
  fuzzy: 'нечеткое',
};

export const PROC_STATUS_LABELS: Record<string, string> = {
  processing: 'обработка',
  ready: 'готов',
  failed: 'ошибка',
};

export const TB_STATUS_LABELS: Record<string, string> = {
  preferred: 'основной',
  admitted: 'допустимый',
  forbidden: 'запрещен',
};

/** Дата-время в формате дд.мм.гггг чч:мм по локали ru.
 * Значения без зоны считаем UTC (бэкенд отдает наивный UTC). */
export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`);
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}
