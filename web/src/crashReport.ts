import { api, ApiError } from './api/client';

/** Автосбор JS-ошибок (крошечный self-hosted «Sentry»): необработанные
 * исключения и unhandledrejection уходят в /api/feedback как баг-репорт
 * с пометкой [auto] — стек, страница, браузер. Контекст прикладывается
 * сам, пользователю жать ничего не надо.
 *
 * Защита от шума и петель:
 *  - дедуп: одинаковая ошибка (сообщение + файл:строка) шлется раз за сессию;
 *  - капа: не больше MAX_PER_SESSION репортов за сессию (бэкенд и так режет
 *    по 10/час на пользователя);
 *  - если отправка сама падает (нет токена/сервера) 2 раза — репортер
 *    снимает слушатели и замолкает навсегда: ошибка репортера не должна
 *    порождать новые ошибки.
 */

const MAX_PER_SESSION = 10;
const STACK_LIMIT = 1500;
const MESSAGE_LIMIT = 3500;

const seen = new Set<string>();
let sent = 0;
let failures = 0;
let installed = false;

function truncate(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

async function send(message: string): Promise<void> {
  try {
    await api.sendFeedback({ kind: 'bug', message, page: window.location.pathname });
    sent += 1;
  } catch {
    failures += 1;
    if (failures >= 2) uninstall();
  }
}

function reportOnce(key: string, message: string): void {
  if (!installed || sent >= MAX_PER_SESSION || seen.has(key)) return;
  seen.add(key);
  void send(truncate(message, MESSAGE_LIMIT));
}

function onError(event: ErrorEvent): void {
  const { message, filename, lineno, colno, error } = event;
  const stack = error instanceof Error && error.stack ? `\nСтек: ${truncate(error.stack, STACK_LIMIT)}` : '';
  reportOnce(
    `${message}|${filename}:${lineno}`,
    `[auto] Ошибка JS: ${message}\nГде: ${filename}:${lineno}:${colno}${stack}\nUA: ${navigator.userAgent}`,
  );
}

function onRejection(event: PromiseRejectionEvent): void {
  const { reason } = event;
  let text: string;
  if (reason instanceof ApiError) {
    text = `[auto] Необработанная ошибка API (${reason.status}): ${reason.message}`;
  } else if (reason instanceof Error) {
    text = `[auto] Необработанный rejection: ${reason.message}\nСтек: ${truncate(reason.stack ?? '', STACK_LIMIT)}`;
  } else {
    text = `[auto] Необработанный rejection: ${String(reason)}`;
  }
  reportOnce(text, `${text}\nUA: ${navigator.userAgent}`);
}

export function installCrashReporter(): void {
  if (installed) return;
  installed = true;
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
}

function uninstall(): void {
  if (!installed) return;
  installed = false;
  window.removeEventListener('error', onError);
  window.removeEventListener('unhandledrejection', onRejection);
}
