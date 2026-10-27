import { FIXED, MESSAGES } from '@/shared/copy/fixed';
import { ApiError, NETWORK_ERROR, TIMEOUT_ERROR, type ApiFieldError } from '@/shared/http/client';

export type ErrorKind =
  | 'network'
  | 'unauthenticated'
  | 'authFailed'
  | 'roleForbidden'
  | 'forbidden'
  | 'notFound'
  | 'conflict'
  | 'validation'
  | 'tooMany'
  | 'unavailable'
  | 'server'
  | 'unknown';

export interface DescribedError {
  kind: ErrorKind;
  code: string;
  /** Text for the screen. Never includes submitted values (they may hold personal data). */
  message: string;
  /** 422: field name -> message. Field names drop the "body"/"query"/"path" prefix. */
  fieldErrors: Record<string, string>;
  /** 422 errors that belong to the whole form (model-level validators). */
  formErrors: string[];
}

/** Codes whose wording is fixed on the client regardless of the server message. */
const FIXED_BY_CODE: Record<string, string> = {
  AUTH_FAILED: MESSAGES.AUTH_FAILED,
  UNAUTHENTICATED: MESSAGES.SESSION_EXPIRED,
  CAPACITY_FULL: FIXED.CAPACITY_FULL,
  NOT_FOUND: MESSAGES.NOT_FOUND,
  CSRF_REJECTED: MESSAGES.CSRF_REJECTED,
  AI_UNAVAILABLE: MESSAGES.AI_UNAVAILABLE,
  EMAIL_UNAVAILABLE: MESSAGES.EMAIL_UNAVAILABLE,
};

const FIELD_TYPE_MESSAGES: Record<string, string> = {
  missing: '필수 입력 항목입니다.',
  value_error: '입력 형식이 올바르지 않습니다.',
  string_type: '입력 형식이 올바르지 않습니다.',
  string_too_short: '입력이 너무 짧습니다.',
  string_too_long: '입력이 너무 깁니다.',
  extra_forbidden: '허용되지 않는 항목입니다.',
};

const LOCATION_PREFIXES = new Set(['body', 'query', 'path', 'header', 'cookie']);

export function mapFieldErrors(fields: ApiFieldError[]): Pick<DescribedError, 'fieldErrors' | 'formErrors'> {
  const fieldErrors: Record<string, string> = {};
  const formErrors: string[] = [];
  for (const f of fields) {
    const loc = f.loc.filter((part, i) => !(i === 0 && LOCATION_PREFIXES.has(part)));
    const message = FIELD_TYPE_MESSAGES[f.type] ?? MESSAGES.VALIDATION;
    if (loc.length === 0) {
      if (!formErrors.includes(message)) formErrors.push(message);
    } else {
      const name = loc.join('.');
      fieldErrors[name] ??= message;
    }
  }
  return { fieldErrors, formErrors };
}

function kindOf(err: ApiError): ErrorKind {
  if (err.code === NETWORK_ERROR || err.code === TIMEOUT_ERROR) return 'network';
  switch (err.status) {
    case 401:
      return err.code === 'AUTH_FAILED' ? 'authFailed' : 'unauthenticated';
    case 403:
      return err.code === 'ROLE_FORBIDDEN' ? 'roleForbidden' : 'forbidden';
    case 404:
      return 'notFound';
    case 409:
      return 'conflict';
    case 413:
    case 415:
    case 422:
      return 'validation';
    case 429:
      return 'tooMany';
    case 503:
      return 'unavailable';
    default:
      return err.status >= 500 ? 'server' : 'unknown';
  }
}

const DEFAULT_BY_KIND: Record<ErrorKind, string> = {
  network: MESSAGES.NETWORK,
  unauthenticated: MESSAGES.SESSION_EXPIRED,
  authFailed: MESSAGES.AUTH_FAILED,
  roleForbidden: MESSAGES.FORBIDDEN,
  forbidden: MESSAGES.FORBIDDEN,
  notFound: MESSAGES.NOT_FOUND,
  conflict: MESSAGES.SERVER_ERROR,
  validation: MESSAGES.VALIDATION,
  tooMany: MESSAGES.TOO_MANY,
  unavailable: MESSAGES.SERVICE_UNAVAILABLE,
  server: MESSAGES.SERVER_ERROR,
  unknown: MESSAGES.SERVER_ERROR,
};

/**
 * Standard error handling (design D 3.11): picks the kind and the text to show.
 * Order: client-fixed wording for known codes > server message (Korean, safe text) > default per kind.
 */
export function describeError(error: unknown): DescribedError {
  if (!(error instanceof ApiError)) {
    return { kind: 'unknown', code: 'UNKNOWN', message: MESSAGES.SERVER_ERROR, fieldErrors: {}, formErrors: [] };
  }
  const kind = kindOf(error);
  let message = FIXED_BY_CODE[error.code];
  if (!message && error.code === TIMEOUT_ERROR) message = MESSAGES.TIMEOUT;
  if (!message && kind !== 'network' && kind !== 'server' && error.message) message = error.message;
  message ??= DEFAULT_BY_KIND[kind];
  const mapped = kind === 'validation' ? mapFieldErrors(error.fields) : { fieldErrors: {}, formErrors: [] };
  return { kind, code: error.code, message, ...mapped };
}
