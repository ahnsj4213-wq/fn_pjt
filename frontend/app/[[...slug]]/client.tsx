'use client';

import dynamic from 'next/dynamic';

// The router needs `window`: render the app in the browser only.
const ClientApp = dynamic(() => import('@/ClientApp'), { ssr: false });

export function Screens() {
  return <ClientApp />;
}
