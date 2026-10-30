import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { fetchRegisteredResume, fetchResumeFile, uploadResumeFile, type ResumeFileOut } from '@/api/candidate';
import { EMPTY_RESUME, type ResumeContent } from '@/api/common/resume';
import { describeError } from '@/shared/http/errors';
import { Notice, Page, uiStyles as s } from '@/shared/ui/components';
import { QueryState } from '@/shared/ui/extra';
import { useApplyFlow } from './apply/ApplyFlow';
import { ResumeEditor } from './resume/ResumeEditor';

const POLL_MS = 1500;
const POLL_LIMIT = 40; // ~1 minute: extraction runs in the INTERACTIVE lane
const MAX_BYTES = 10 * 1024 * 1024; // RES-02; the API checks it again (413 FILE_TOO_LARGE)

/** Apply step 2: resume (RES-01~07). Start from the registered resume, a PDF extraction, or blank. */
export function ApplyResume() {
  const { pid = '' } = useParams();
  const navigate = useNavigate();
  const flow = useApplyFlow();
  const registered = useQuery({ queryKey: ['resume'], queryFn: fetchRegisteredResume });
  const [extracted, setExtracted] = useState<{ content: ResumeContent; fileId: string } | null>(null);
  const [upload, setUpload] = useState<{ busy: boolean; message: string | null; error: boolean }>({ busy: false, message: null, error: false });
  const [editorKey, setEditorKey] = useState(0);

  if (!flow.consentId) return <Navigate to={`/postings/${encodeURIComponent(pid)}/apply/consent`} replace />;

  const onFile = async (file: File) => {
    if (file.size > MAX_BYTES) {
      setUpload({ busy: false, message: '파일 크기는 10MB 이하여야 합니다.', error: true });
      return;
    }
    setUpload({ busy: true, message: '파일을 올리고 내용을 읽는 중입니다.', error: false });
    try {
      let out: ResumeFileOut = await uploadResumeFile(file);
      for (let i = 0; out.extraction_status === 'PENDING' && i < POLL_LIMIT; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        out = await fetchResumeFile(out.file_id);
      }
      if (out.extraction_status === 'DONE' && out.fields) {
        setExtracted({ content: { ...EMPTY_RESUME, ...out.fields, name: out.fields.name ?? '' }, fileId: out.file_id });
        setEditorKey((k) => k + 1);
        setUpload({ busy: false, message: 'PDF에서 읽은 내용을 채웠습니다. 확인하고 고쳐 주세요.', error: false });
      } else {
        setUpload({ busy: false, message: '파일에서 내용을 읽지 못했습니다. 직접 입력해 주세요.', error: true });
      }
    } catch (e) {
      setUpload({ busy: false, message: describeError(e).message, error: true });
    }
  };

  return (
    <Page title="이력서 확인" narrow>
      <QueryState isPending={registered.isPending} error={registered.error}>
        {() => {
          const initial = extracted?.content ?? flow.draft?.content ?? registered.data!.resume?.content ?? EMPTY_RESUME;
          return (
            <div className={s.card}>
              <label className={s.label}>
                PDF 이력서로 채우기 (선택, 10MB 이하)
                <input type="file" accept="application/pdf" disabled={upload.busy}
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) void onFile(f); }} />
              </label>
              {upload.message && <Notice kind={upload.error ? 'error' : 'info'}>{upload.message}</Notice>}
              <ResumeEditor key={editorKey} initial={initial} submitLabel="다음"
                onSubmit={async (content) => {
                  flow.setDraft({ content, fileId: extracted?.fileId ?? null, origin: extracted ? 'PDF' : 'MANUAL' });
                  navigate(`/postings/${encodeURIComponent(pid)}/apply/review`);
                }} />
            </div>
          );
        }}
      </QueryState>
    </Page>
  );
}
