import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { createMemoryRouter, type RouteObject } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import type { SessionOut } from '@/api/common/auth';
import { SESSION_KEY } from '@/app/session';

/** Renders routes in a memory router with a fresh QueryClient; optionally seeds the session. */
export function renderRoutes(routes: RouteObject[], { path = '/', session }: { path?: string; session?: SessionOut | null } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  if (session !== undefined) queryClient.setQueryData(SESSION_KEY, session);
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...utils, router, queryClient };
}

export const candidateSession: SessionOut = {
  user: { id: 'u1', role: 'CANDIDATE', name: '김지원', company_id: null, company_name: null },
  csrf_token: 'csrf-c',
};

export const recruiterSession: SessionOut = {
  user: { id: 'u2', role: 'RECRUITER', name: '이담당', company_id: 'c1', company_name: '테스트회사' },
  csrf_token: 'csrf-r',
};
