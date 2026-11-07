import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { fetchRegisteredResume, fetchResumeFile, saveRegisteredResume, uploadResumeFile, type ResumeFileOut } from '@/api/candidate';
import { DESIRED_JOBS, EMPTY_RESUME, EXPERIENCE_LEVELS, type ResumeContent } from '@/api/common/resume';
import { safeNext } from '@/app/session';
import { formatDate } from '@/shared/format';
import { describeError } from '@/shared/http/errors';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';
import { Field } from '@/shared/ui/wf';
import { flatMissing, fromFlat, toFlat, type FlatResume } from './resume/flatResume';

const POLL_MS = 1500;
const POLL_LIMIT = 120; // ~3 minutes: OCR of scanned pages, then the extraction model
const MAX_BYTES = 10 * 1024 * 1024; // RES-02; the API checks it again
const FILE_EXT = /\.(pdf|docx|png|jpe?g)$/i;

/**
 * SC-A10 마이페이지 · 이력서, and SC-A13 이력서 작성 (가입 직후, ?welcome=1) with the same form.
 * From the apply step (?return=) saving goes back there. An attached file is read on the server (text, OCR or DOCX)
 * and what it found fills the form for the candidate to check before saving.
 */
export function MyResume() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [params] = useSearchParams();
  const back = safeNext(params.get('return'));
  const welcome = params.get('welcome') === '1';
  const resume = useQuery({ queryKey: ['resume'], queryFn: fetchRegisteredResume });
  const reg = resume.data?.resume ?? null;

  const [base, setBase] = useState<ResumeContent>(EMPTY_RESUME);
  const [form, setForm] = useState<FlatResume>(toFlat(EMPTY_RESUME));
  const [fileId, setFileId] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!resume.data) return;
    const c = { ...EMPTY_RESUME, ...(resume.data.resume?.content ?? {}) };
    setBase(c);
    setForm(toFlat(c));
    setFileId(resume.data.resume?.file_id ?? null);
  }, [resume.data]);

  const set = (k: keyof FlatResume) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const attach = async (file: File | undefined) => {
    if (!file) return;
    if (!FILE_EXT.test(file.name)) { toast('PDF, DOCX, PNG, JPG 파일만 올릴 수 있습니다'); return; }
    if (file.size > MAX_BYTES) { toast('파일 크기는 10MB 이하여야 합니다'); return; }
    setError(null);
    setReading(true);
    try {
      let out: ResumeFileOut = await uploadResumeFile(file);
      for (let i = 0; out.extraction_status === 'PENDING' && i < POLL_LIMIT; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        out = await fetchResumeFile(out.file_id);
      }
      setFileId(out.file_id);
      setFileName(file.name);
      if (out.extraction_status === 'DONE' && out.fields) {
        // What the file says replaces the standard sections and the cover letter; the choices only this form has stay as typed.
        const c: ResumeContent = { ...EMPTY_RESUME, ...out.fields, name: out.fields.name || form.name,
          desired_job: form.desired_job || null, experience_level: null, intro: out.fields.intro || form.intro || null };
        setBase(c);
        setForm(toFlat(c));
        toast('파일에서 읽은 내용을 양식에 채웠습니다. 확인한 뒤 저장해 주십시오');
      } else if (out.extraction_status === 'PENDING') {
        setError('AI 서버가 응답하지 않아 파일을 아직 읽지 못했습니다. 잠시 후 파일을 다시 올리거나 아래 양식을 직접 채워 주십시오.');
      } else {
        setError(out.extraction_status === 'NO_TEXT'
          ? '파일은 첨부했지만 글자를 충분히 읽지 못했습니다. 아래 양식을 직접 채워 주십시오.'
          : '파일은 첨부했지만 내용을 읽지 못했습니다. 아래 양식을 직접 채워 주십시오.');
      }
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setReading(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const content = fromFlat(form, base);
    const missing = flatMissing(content);
    if (missing) { setError(missing); return; }
    setBusy(true);
    setError(null);
    try {
      await saveRegisteredResume({ content, file_id: fileId });
      await qc.invalidateQueries({ queryKey: ['resume'] });
      toast('이력서를 저장했습니다');
      if (back) navigate(back);
      else if (welcome) navigate('/');
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setBusy(false);
    }
  };

  const fields = (
    <>
      {error && <div className="notice" role="alert" style={{ margin: 0 }}>{error}</div>}
      <div className="row2">
        <Field label="이름" id="rs-name" value={form.name} onChange={set('name')} />
        <div className="field"><label htmlFor="rs-cat">희망 직무</label>
          <select id="rs-cat" value={form.desired_job} onChange={set('desired_job')}>
            <option value="">선택해 주십시오</option>
            {DESIRED_JOBS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></div>
      </div>
      <div className="row2">
        <Field label="최종 학력" id="rs-edu" value={form.edu} onChange={set('edu')} />
        <div className="field"><label htmlFor="rs-career">경력</label>
          <select id="rs-career" value={form.experience_level} onChange={set('experience_level')}>
            <option value="">선택해 주십시오</option>
            {EXPERIENCE_LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></div>
      </div>
      <Field label="보유 기술" id="rs-skill" value={form.skill} onChange={set('skill')} hint="쉼표로 나눠 적어 주십시오" />
      <div className="field"><label htmlFor="rs-proj">주요 프로젝트·경험</label>
        <textarea id="rs-proj" value={form.proj} onChange={set('proj')} aria-describedby="rs-proj-hint" />
        <small id="rs-proj-hint" className="hint">한 줄에 하나씩 '이름: 설명'으로 적어 주십시오. 회사 경력은 '경력 · 회사 / 직무 / 기간: 설명'입니다.</small></div>
      <div className="field"><label htmlFor="rs-intro">자기소개서 (선택)</label>
        <textarea id="rs-intro" value={form.intro} onChange={set('intro')} placeholder="지원 동기나 강점을 자유롭게 적어 주십시오" /></div>
      <div className="field"><label htmlFor="rs-file">기존 이력서·자기소개서 파일 (선택)</label>
        <input id="rs-file" type="file" accept=".pdf,.docx,.png,.jpg,.jpeg" disabled={reading}
          onChange={(e) => { void attach(e.target.files?.[0]); e.target.value = ''; }} />
        {reading && <small className="hint" role="status">파일을 읽고 있습니다. 잠시만 기다려 주십시오</small>}
        {!reading && fileId && <small className="hint">첨부됨: {fileName ?? '등록한 이력서 파일'}</small>}</div>
    </>
  );

  if (welcome) {
    return (
      <div className="wrap"><div className="stub wide">
        <h1>이력서 작성</h1>
        <p className="sub">한 번 작성해 두면 모든 공고의 AI 면접에 사용됩니다. 지금 쓰지 않아도 지원할 때 올릴 수 있습니다.</p>
        <form className="form" onSubmit={submit} noValidate aria-label="이력서">
          {fields}
          <div className="row2">
            <button className="btn" type="button" style={{ padding: 12, fontSize: 15 }}
              onClick={() => { toast('이력서는 마이페이지에서 언제든 쓸 수 있습니다'); navigate('/'); }}>건너뛰고 공고 먼저 보기</button>
            <button className="btn btn-primary" type="submit" disabled={busy || reading}>저장하고 공고 보기</button>
          </div>
        </form>
      </div></div>
    );
  }

  return (
    <>
      <div className="ph"><div>
        <h1>이력서</h1>
        <p>{resume.isPending ? '' : reg ? `${formatDate(reg.updated_at)}에 마지막으로 수정했습니다. 모든 공고의 AI 면접에 이 이력서가 쓰입니다.`
          : '아직 등록한 이력서가 없습니다. 아래 양식을 채우거나 파일을 올려 주십시오.'}</p>
      </div></div>
      {back && <div className="notice" style={{ margin: 0 }}>저장하면 지원하던 공고의 개인정보 동의 단계로 돌아갑니다.</div>}
      <QueryState isPending={resume.isPending} error={resume.error} onRetry={() => resume.refetch()}>
        {() => (
          <form className="card form" onSubmit={submit} noValidate aria-label="이력서">
            {fields}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {back && <button className="btn" type="button" onClick={() => navigate(back)}>저장하지 않고 돌아가기</button>}
              <button className="btn btn-primary" type="submit" disabled={busy || reading}>
                {back ? '저장하고 지원 계속하기' : reg ? '수정 내용 저장' : '이력서 등록'}
              </button>
            </div>
          </form>
        )}
      </QueryState>
    </>
  );
}
