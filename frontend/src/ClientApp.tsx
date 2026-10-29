'use client';

import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { createQueryClient } from '@/app/queryClient';
import { routes } from '@/app/routes';
import { loginPath, setSession } from '@/app/session';
import { configureHttp } from '@/shared/http/client';

/**
 * The screens are a client-side app (react-router) inside Next.js: Next serves the page and proxies /api to the
 * API container (next.config.ts); everything after that runs in the browser, as before.
 */
const queryClient = createQueryClient();
const router = createBrowserRouter(routes);

// 401 on any API call (except login/session probe): drop cached data and go to /login?next=<here>.
configureHttp({
  onUnauthenticated: () => {
    const here = window.location.pathname + window.location.search;
    queryClient.clear();
    setSession(queryClient, null);
    if (!window.location.pathname.startsWith('/login')) void router.navigate(loginPath(here, 'expired'), { replace: true });
  },
});

export default function ClientApp() {
  return (
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>
  );
}
