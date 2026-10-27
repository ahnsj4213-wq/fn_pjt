import { createContext, useContext, useState } from 'react';
import { Outlet } from 'react-router';
import type { ResumeContent } from '@/api/common/resume';

/**
 * Apply wizard state (consent -> resume -> review -> complete). Memory only: resume content is personal
 * data and is never written to localStorage/sessionStorage (design D 3.4). A reload restarts at consent.
 */
export interface ResumeDraft {
  content: ResumeContent;
  fileId: string | null;
  origin: 'REGISTERED' | 'PDF' | 'PDF_NO_TEXT' | 'MANUAL';
}

interface ApplyState {
  consentId: string | null;
  setConsentId: (id: string | null) => void;
  draft: ResumeDraft | null;
  setDraft: (d: ResumeDraft | null) => void;
}

const Ctx = createContext<ApplyState | null>(null);

export function ApplyFlow() {
  const [consentId, setConsentId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ResumeDraft | null>(null);
  return (
    <Ctx.Provider value={{ consentId, setConsentId, draft, setDraft }}>
      <Outlet />
    </Ctx.Provider>
  );
}

export function useApplyFlow(): ApplyState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApplyFlow outside ApplyFlow');
  return v;
}
