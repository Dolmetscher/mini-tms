// Русские подписи значений из API (статусы/severity/типы) для мобильного клиента.
// Даты: fmtDateTime в theme.ts.

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

export const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
  job_assigned: 'Назначено новое задание',
  job_accepted: 'Задание принято',
  job_declined: 'Задание отклонено',
  job_submitted: 'Задание сдано',
  job_returned: 'Задание возвращено на доработку',
  job_reassigned: 'Задание переназначено',
  job_canceled: 'Задание отменено',
  deadline_reminder: 'Напоминание о дедлайне',
};
