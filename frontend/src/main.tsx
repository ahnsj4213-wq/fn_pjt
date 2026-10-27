import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { createQueryClient } from '@/app/queryClient';
import { routes } from '@/app/routes';
import { loginPath, setSession } from '@/app/session';
import { configureHttp } from '@/shared/http/client';
import '@/app/global.css';

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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
