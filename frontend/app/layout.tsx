import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import '@fontsource/gothic-a1/500.css';
import '@fontsource/gothic-a1/700.css';
import '@fontsource/gothic-a1/800.css';
import '@fontsource/ibm-plex-sans-kr/400.css';
import '@fontsource/ibm-plex-sans-kr/500.css';
import '@fontsource/ibm-plex-sans-kr/600.css';
import '@/app/wireframe.css';
import '@/app/global.css';

export const metadata: Metadata = {
  title: '모의면접',
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
