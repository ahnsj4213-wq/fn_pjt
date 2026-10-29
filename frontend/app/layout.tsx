import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import '@/app/global.css';

export const metadata: Metadata = {
  title: 'AI 채용 플랫폼',
  // No favicon request and no external fonts or scripts: everything ships in the bundle (AC-22).
  icons: { icon: 'data:,' },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <div id="root">{children}</div>
      </body>
    </html>
  );
}
