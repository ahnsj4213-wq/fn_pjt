import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { fetchResumeFile, resumeFileContentUrl } from '@/api/candidate';

/**
 * The candidate's own uploaded resume beside the form filled from it (ADR-0032), so each box can be checked against the file:
 * a PDF in the browser's own viewer, a photo as an image, a DOCX (no browser shows one) as a link. Only the owner can open it
 * (the API answers 404 to anyone else), and only our pages may frame it.
 */
export function OriginalFile({ fileId, name }: { fileId: string; name?: string | null }) {
  const info = useQuery({ queryKey: ['resume-file', fileId], queryFn: () => fetchResumeFile(fileId) });
  const url = resumeFileContentUrl(fileId);
  const kind = info.data?.media_type ?? '';
  let body: ReactNode;
  if (info.isPending) body = <p className="hint">원본 파일을 불러오고 있습니다</p>;
  else if (info.error) body = <p className="hint">원본 파일을 불러오지 못했습니다. 새 탭에서 열어 보십시오</p>;
  else if (kind === 'application/pdf') body = <iframe className="orig-frame" src={url} title="원본 이력서 파일" />;
  else if (kind.startsWith('image/')) body = <img className="orig-img" src={url} alt="원본 이력서 파일" />;
  else body = <p className="hint">이 형식(DOCX)은 화면에서 미리 볼 수 없습니다. 내려받아 확인해 주십시오</p>;
  return (
    <aside className="orig" aria-label="원본 파일">
      <div className="orig-h">
        <b>원본 파일{name ? <span className="hint"> · {name}</span> : null}</b>
        <a className="link" href={url} target="_blank" rel="noreferrer">새 탭에서 열기</a>
      </div>
      {body}
    </aside>
  );
}

/** The form on the left, the original on the right (one column on a narrow screen); just the form without a file. */
export function WithOriginal({ fileId, name, children }: { fileId: string | null | undefined; name?: string | null; children: ReactNode }) {
  if (!fileId) return <>{children}</>;
  return (
    <div className="with-orig">
      <div className="with-orig-form">{children}</div>
      <OriginalFile fileId={fileId} name={name} />
    </div>
  );
}
