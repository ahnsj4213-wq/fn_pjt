import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import { setCsrfToken } from '@/shared/http/client';

afterEach(() => {
  cleanup();
  setCsrfToken(null);
});
