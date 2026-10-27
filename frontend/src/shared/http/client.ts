/**
 * Same-origin JSON client for the FastAPI backend.
 * - Session: HttpOnly cookie, sent with credentials "same-origin". The SPA never reads it.
 * - CSRF: the synchronizer token from login/me responses is kept in memory only and sent as
 *   X-CSRF-Token on state-changing requests (E 2.6, backend app/auth/deps.py).
 * - Errors: backend body {"error": {code, message, request_id, fields?}} becomes an ApiError.
 */

export interface ApiFieldError {
  loc: string[];
  type: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly fields: ApiFieldError[];
  readonly extra: Record<string, unknown>;

  constructor(status: number, code: string, message: string, opts: {
    requestId?: string | null;
    fields?: ApiFieldError[];
    extra?: Record<string, unknown>;
  } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = opts.requestId ?? null;
    this.fields = opts.fields ?? [];
    this.extra = opts.extra ?? {};
  }
}

/** status 0 means the request never got an HTTP response. */
export const NETWORK_ERROR = 'NETWORK';
export const TIMEOUT_ERROR = 'TIMEOUT';

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const DEFAULT_TIMEOUT_MS = 15_000;

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

interface HttpHandlers {
  /** Called on 401 (except login failures) so the app can clear state and go to /login?next=. */
  onUnauthenticated?: () => void;
}

let handlers: HttpHandlers = {};

export function configureHttp(next: HttpHandlers): void {
  handlers = next;
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** false for calls where 401 is an expected answer (session probe, login). */
  authRedirect?: boolean;
  idempotencyKey?: string;
}

/** Same handling as apiFetch for callers that do their own transport (XHR uploads). */
export function notifyUnauthenticated(err: ApiError): void {
  if (err.status === 401 && err.code !== 'AUTH_FAILED') {
    csrfToken = null;
    handlers.onUnauthenticated?.();
  }
}

export function resolveUrl(path: string): string {
  // Absolute same-origin URL: identical in the browser, and lets tests run under jsdom.
  return new URL(path, globalThis.location.origin).toString();
}

export function toApiError(status: number, data: unknown): ApiError {
  const err = (data as { error?: Record<string, unknown> } | null)?.error;
  if (err && typeof err.code === 'string') {
    const { code, message, request_id, fields, ...extra } = err;
    return new ApiError(status, code, typeof message === 'string' ? message : '', {
      requestId: typeof request_id === 'string' ? request_id : null,
      fields: Array.isArray(fields) ? (fields as ApiFieldError[]) : [],
      extra,
    });
  }
  // Proxy or server errors without the standard body (e.g. 502 from the dev proxy, 413 from Caddy).
  return new ApiError(status, `HTTP_${status}`, '');
}

async function readBody(res: Response): Promise<unknown> {
  const type = res.headers.get('content-type') ?? '';
  if (!type.includes('application/json')) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  let body: string | undefined;
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  if (UNSAFE_METHODS.has(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;

  const timeout = AbortSignal.timeout(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const signal = opts.signal ? AbortSignal.any([opts.signal, timeout]) : timeout;

  let res: Response;
  try {
    res = await fetch(resolveUrl(path), { method, headers, body, credentials: 'same-origin', signal });
  } catch (e) {
    if (opts.signal?.aborted) throw e; // the caller cancelled; not an error to show
    if (timeout.aborted) throw new ApiError(0, TIMEOUT_ERROR, '');
    throw new ApiError(0, NETWORK_ERROR, '');
  }

  if (res.status === 204) return undefined as T;
  const data = await readBody(res);
  if (!res.ok) {
    const err = toApiError(res.status, data);
    if (opts.authRedirect !== false) notifyUnauthenticated(err);
    throw err;
  }
  return data as T;
}
