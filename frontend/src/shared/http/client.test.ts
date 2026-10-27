import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { FIXED, MESSAGES } from '@/shared/copy/fixed';
import { ApiError, apiFetch, configureHttp, getCsrfToken, setCsrfToken } from './client';
import { describeError, mapFieldErrors } from './errors';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => {
  server.resetHandlers();
  configureHttp({});
});
afterAll(() => server.close());

const errorBody = (code: string, message: string, extra: Record<string, unknown> = {}) =>
  ({ error: { code, message, request_id: 'r1', ...extra } });

async function catchError(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error('expected rejection');
}

describe('apiFetch', () => {
  it('sends X-CSRF-Token on state-changing requests only, with same-origin credentials', async () => {
    const seen: Record<string, string | null> = {};
    server.use(
      http.get('*/api/x', ({ request }) => {
        seen.get = request.headers.get('x-csrf-token');
        return HttpResponse.json({ ok: true });
      }),
      http.post('*/api/x', ({ request }) => {
        seen.post = request.headers.get('x-csrf-token');
        seen.credentials = request.credentials;
        return HttpResponse.json({ ok: true });
      }),
    );
    setCsrfToken('tok-123');
    await apiFetch('/api/x');
    await apiFetch('/api/x', { method: 'POST', body: { a: 1 } });
    expect(seen.get).toBeNull();
    expect(seen.post).toBe('tok-123');
    expect(seen.credentials).toBe('same-origin');
  });

  it('401 UNAUTHENTICATED clears the CSRF token and calls the redirect handler', async () => {
    const onUnauthenticated = vi.fn();
    configureHttp({ onUnauthenticated });
    setCsrfToken('tok');
    server.use(http.get('*/api/y', () => HttpResponse.json(errorBody('UNAUTHENTICATED', '로그인이 필요합니다.'), { status: 401 })));
    const err = await catchError(apiFetch('/api/y'));
    expect(err.status).toBe(401);
    expect(onUnauthenticated).toHaveBeenCalledOnce();
    expect(getCsrfToken()).toBeNull();
    expect(describeError(err).kind).toBe('unauthenticated');
    expect(describeError(err).message).toBe(MESSAGES.SESSION_EXPIRED);
  });

  it('401 AUTH_FAILED (login) does not redirect and shows the single USR-04 message', async () => {
    const onUnauthenticated = vi.fn();
    configureHttp({ onUnauthenticated });
    server.use(http.post('*/api/auth/login', () => HttpResponse.json(errorBody('AUTH_FAILED', 'x'), { status: 401 })));
    const err = await catchError(apiFetch('/api/auth/login', { method: 'POST', body: {} }));
    expect(onUnauthenticated).not.toHaveBeenCalled();
    expect(describeError(err)).toMatchObject({ kind: 'authFailed', message: MESSAGES.AUTH_FAILED });
  });

  it('maps 403 ROLE_FORBIDDEN, 409 CAPACITY_FULL and 503 to their standard kinds and wording', async () => {
    server.use(
      http.get('*/api/role', () => HttpResponse.json(errorBody('ROLE_FORBIDDEN', '담당자 계정으로는 지원자 기능을 사용할 수 없습니다.'), { status: 403 })),
      http.post('*/api/start', () => HttpResponse.json(errorBody('CAPACITY_FULL', 'server text'), { status: 409 })),
      http.get('*/api/ai', () => HttpResponse.json(errorBody('AI_UNAVAILABLE', ''), { status: 503 })),
    );
    expect(describeError(await catchError(apiFetch('/api/role')))).toMatchObject({
      kind: 'roleForbidden', message: MESSAGES.ROLE_FORBIDDEN_CANDIDATE_AREA,
    });
    expect(describeError(await catchError(apiFetch('/api/start', { method: 'POST' })))).toMatchObject({
      kind: 'conflict', message: FIXED.CAPACITY_FULL,
    });
    expect(describeError(await catchError(apiFetch('/api/ai')))).toMatchObject({
      kind: 'unavailable', message: MESSAGES.AI_UNAVAILABLE,
    });
  });

  it('maps 422 fields[{loc,type}] to field names without echoing values', async () => {
    server.use(http.post('*/api/auth/signup', () => HttpResponse.json(errorBody('VALIDATION', '입력값을 확인해 주세요.', {
      fields: [
        { loc: ['body', 'phone'], type: 'value_error' },
        { loc: ['body', 'name'], type: 'missing' },
        { loc: ['body'], type: 'value_error' },
      ],
    }), { status: 422 })));
    const d = describeError(await catchError(apiFetch('/api/auth/signup', { method: 'POST', body: {} })));
    expect(d.kind).toBe('validation');
    expect(d.fieldErrors).toEqual({ phone: '입력 형식이 올바르지 않습니다.', name: '필수 입력 항목입니다.' });
    expect(d.formErrors).toEqual(['입력 형식이 올바르지 않습니다.']);
  });

  it('turns a failed connection into a network error', async () => {
    server.use(http.get('*/api/down', () => HttpResponse.error()));
    const d = describeError(await catchError(apiFetch('/api/down')));
    expect(d).toMatchObject({ kind: 'network', message: MESSAGES.NETWORK });
  });

  it('returns undefined for 204', async () => {
    server.use(http.post('*/api/auth/logout', () => new HttpResponse(null, { status: 204 })));
    await expect(apiFetch('/api/auth/logout', { method: 'POST' })).resolves.toBeUndefined();
  });
});

describe('mapFieldErrors', () => {
  it('keeps the first message per field and joins nested locations', () => {
    expect(mapFieldErrors([
      { loc: ['body', 'weights', 'logic'], type: 'string_too_long' },
      { loc: ['body', 'weights', 'logic'], type: 'missing' },
    ]).fieldErrors).toEqual({ 'weights.logic': '입력이 너무 깁니다.' });
  });
});
