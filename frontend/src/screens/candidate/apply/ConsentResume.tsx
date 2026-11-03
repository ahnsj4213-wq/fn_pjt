import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import {
  applyToPosting, fetchConsent, fetchMyApplications, fetchRegisteredResume, fetchResumeFile, recordConsent,
  saveRegisteredResume, uploadResumeFile, type ResumeFileOut,
} from '@/api/candidate';
import { prepareInterview, reportMedia } from '@/api/candidate/interview';
import { EMPTY_RESUME, type ResumeContent } from '@/api/common/resume';
import { fetchPosting } from '@/api/common/postings';
import { formatDate } from '@/shared/format';
import { ApiError } from '@/shared/http/client';
import { describeError } from '@/shared/http/errors';
import { useToast } from '@/shared/ui/overlay';
import { applyPath, loadDeviceCheck } from './applyState';
import { Stepper } from './Stepper';

const POLL_MS = 1500;
const POLL_LIMIT = 120; // ~3 minutes: OCR of scanned pages, then the INTERACTIVE-lane extraction
const MAX_BYTES = 10 * 1024 * 1024; // RES-02; the API checks it again (413 FILE_TOO_LARGE)
const FILE_EXT = /\.(pdf|png|jpe?g)$/i; // scanned PDFs and photos are read by OCR on the server (RES-04)
const LOW_CONF_BOX: Record<string, keyof Extracted['form']> = { name: 'name', education: 'edu', career: 'career', projects: 'proj', skills: 'skill' };
const DISAGREE_HINT = '동의하지 않으면 AI 면접을 진행할 수 없습니다. 동의 여부는 언제든 바꿀 수 있습니다.';

/** Text boxes of the "파일에서 읽은 정보" review; a changed box replaces that part of the extracted resume. */
interface Extracted { fileId: string; fileName: string; content: ResumeContent; form: Record<'name' | 'edu' | 'career' | 'skill' | 'proj', string>; unsure: Set<string> }

const join = (xs: (string | null | undefined)[]) => xs.filter(Boolean).join(' ');
function toBoxes(c: ResumeContent): Extracted['form'] {
  return {
    name: c.name,
    edu: c.education.map((e) => join([e.school, e.major, e.degree])).join(', '),
    career: c.career.length ? c.career.map((e) => join([e.company, e.role, e.period])).join(', ') : '신입',
    skill: c.skills.join(', '),
    proj: c.projects.map((p) => (p.description ? `${p.title}: ${p.description}` : p.title)).join('\n'),
  };
}
function fromBoxes(x: Extracted): ResumeContent {
  const was = toBoxes(x.content), f = x.form, c = x.content;
  const changed = (k: keyof Extracted['form']) => f[k].trim() !== was[k].trim();
  return {
    ...c,
    name: f.name.trim(),
    education: !changed('edu') ? c.education : f.edu.trim() ? [{ school: f.edu.trim() }] : [],
    career: !changed('career') ? c.career : f.career.trim() && !f.career.trim().startsWith('신입') ? [{ company: f.career.trim() }] : [],
    skills: !changed('skill') ? c.skills : f.skill.split(',').map((s) => s.trim()).filter(Boolean),
    projects: !changed('proj') ? c.projects
      : f.proj.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
        const i = l.indexOf(':');
        return i > 0 ? { title: l.slice(0, i).trim(), description: l.slice(i + 1).trim() } : { title: l };
      }),
  };
}

/** Choices survive a trip to My Page and back (this tab only, no personal data). */
const choiceKey = (pid: string) => `applyChoice:${pid}`;
function loadChoice(pid: string): { agree: boolean | null; share: boolean } {
  try { return JSON.parse(sessionStorage.getItem(choiceKey(pid)) ?? '') as { agree: boolean | null; share: boolean }; } catch { return { agree: null, share: false }; }
}

/** SC-A05 개인정보 동의와 이력서: one consent choice, optional result sharing, the resume, then [면접 시작]. */
export function ConsentResume() {
  const { pid = '' } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const check = loadDeviceCheck();
  const posting = useQuery({ queryKey: ['posting', pid], queryFn: () => fetchPosting(pid) });
  const doc = useQuery({ queryKey: ['consent'], queryFn: fetchConsent });
  const resume = useQuery({ queryKey: ['resume'], queryFn: fetchRegisteredResume });
  const [agree, setAgree] = useState<boolean | null>(() => loadChoice(pid).agree);
  const [share, setShare] = useState<boolean>(() => loadChoice(pid).share);
  const [reading, setReading] = useState<string[] | null>(null);
  const [extracted, setExtracted] = useState<Extracted | null>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try { sessionStorage.setItem(choiceKey(pid), JSON.stringify({ agree, share })); } catch { /* blocked */ }
  }, [pid, agree, share]);

  if (!check) return <Navigate to={applyPath(pid)} replace />;

  const reg = resume.data?.resume ?? null;
  const p = posting.data;

  const take = async (files: FileList | null) => {
    if (!files?.length) return;
    const list = [...files];
    const pdf = list.find((f) => FILE_EXT.test(f.name));
    if (!pdf) { toast('PDF, PNG, JPG 파일만 읽을 수 있습니다. 다른 파일로 올리거나 이력서를 직접 만들어 주십시오'); return; }
    if (pdf.size > MAX_BYTES) { toast('파일 크기는 10MB 이하여야 합니다'); return; }
    if (list.length > 1) toast(`${pdf.name} 파일로 이력서를 읽습니다`);
    setError(null);
    setReading([pdf.name]);
    try {
      let out: ResumeFileOut = await uploadResumeFile(pdf);
      for (let i = 0; out.extraction_status === 'PENDING' && i < POLL_LIMIT; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        out = await fetchResumeFile(out.file_id);
      }
      if (out.extraction_status === 'DONE' && out.fields) {
        const content = { ...EMPTY_RESUME, ...out.fields, name: out.fields.name ?? '' };
        const unsure = new Set(out.low_conf_fields.map((f) => LOW_CONF_BOX[f.split('.')[0]]).filter(Boolean));
        setExtracted({ fileId: out.file_id, fileName: pdf.name, content, form: toBoxes(content), unsure });
      } else {
        // RES-04: OCR could not read enough (blurred or tilted scan, engine unavailable): the candidate types it instead.
        setError(out.extraction_status === 'NO_TEXT'
          ? '파일에서 글자를 충분히 읽지 못했습니다. 더 선명한 파일을 올리거나 이력서를 직접 만들어 주십시오.'
          : '파일에서 내용을 읽지 못했습니다. 다른 파일을 올리거나 이력서를 직접 만들어 주십시오.');
      }
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setReading(null);
    }
  };

  const register = async () => {
    if (!extracted) return;
    const content = fromBoxes(extracted);
    if (!content.name || (!content.education.length && !content.career.length)) {
      setError('이름과 학력 또는 경력을 입력해 주십시오.');
      return;
    }
    setBusy(true);
    try {
      await saveRegisteredResume({ content, file_id: extracted.fileId });
      await qc.invalidateQueries({ queryKey: ['resume'] });
      setExtracted(null);
      setError(null);
      toast('파일에서 읽은 내용으로 이력서를 등록했습니다');
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(false);
    }
  };

  const toResume = () => navigate(`/me/resume?return=${encodeURIComponent(applyPath(pid, 'consent'))}`);

  /** Consent -> application (or the one already made) -> question composing -> device check -> interview. */
  const start = async () => {
    if (!doc.data || !reg) return;
    setBusy(true);
    setError(null);
    try {
      const consent = await recordConsent(pid, {
        version: doc.data.version, answers: Object.fromEntries(doc.data.items.map((i) => [i.code, true])),
      });
      let aid: string;
      try {
        aid = (await applyToPosting(pid, {
          consent_id: consent.consent_id, resume: reg.content, resume_file_id: reg.file_id,
          update_registered: false, share_result: share,
        })).application_id;
      } catch (e) {
        if (!(e instanceof ApiError) || e.code !== 'ALREADY_APPLIED') throw e;
        const mine = (await fetchMyApplications()).find((a) => a.posting.id === pid);
        if (!mine || !['READY', 'PREPARING'].includes(mine.attempt_status)) throw e;
        aid = mine.id;
      }
      await prepareInterview(aid);
      await reportMedia(aid, { mic_ok: true, camera_ok: true, speaker_ok: true });
      void qc.invalidateQueries({ queryKey: ['my-applications'] });
      navigate(`/applications/${encodeURIComponent(aid)}/interview`, { state: { started: true }, replace: true });
    } catch (e) {
      setError(describeError(e).message);
      setBusy(false);
    }
  };

  const ready = agree === true && !!reg && !reading && !extracted;
  const goLabel = busy ? '면접을 준비하고 있습니다' : ready ? '면접 시작'
    : agree !== true ? '개인정보에 동의하면 시작할 수 있습니다' : '이력서를 등록하면 시작할 수 있습니다';
  const box = (k: keyof Extracted['form'], label: string, area = false) => {
    const props = {
      id: `ex-${k}`, value: extracted!.form[k],
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        setExtracted((x) => x && { ...x, form: { ...x.form, [k]: e.target.value } }),
    };
    return (
      <div className="field">
        <label htmlFor={`ex-${k}`}>{label}</label>{area ? <textarea {...props} /> : <input {...props} />}
        {extracted!.unsure.has(k) && <small className="hint warn">흐리게 읽힌 부분이 있습니다. 원본과 같은지 확인해 주십시오</small>}
      </div>
    );
  };

  return (
    <div className="wrap"><div className="stub xwide">
      <Stepper cur={1} />
      <h1>개인정보 동의와 이력서</h1>
      <p className="sub">{p ? `${p.company_name} · ${p.title}` : ''}</p>
      <section className="cs">
        <h2>개인정보 수집·이용 동의 <em className="req">필수</em></h2>
        <div className="tblw"><table className="t terms">
          <thead><tr><th>수집 항목</th><th>이용 목적</th><th>보관 기간</th></tr></thead>
          <tbody>
            {(doc.data?.items ?? []).map((i) => (
              <tr key={i.code}><td>{i.label}</td><td>{i.purpose ?? ''}</td><td>{i.retention ?? ''}</td></tr>
            ))}
          </tbody>
        </table></div>
        <div className="radios big" role="radiogroup" aria-label="개인정보 수집·이용 동의">
          <label><input type="radio" name="agree" checked={agree === true} onChange={() => setAgree(true)} /> 동의합니다</label>
          <label><input type="radio" name="agree" checked={agree === false} onChange={() => setAgree(false)} /> 동의하지 않습니다</label>
        </div>
        <p className="hint">{agree === false ? DISAGREE_HINT : ''}</p>
        <label className="consent" htmlFor="consent" style={{ margin: 0 }}>
          <input type="checkbox" id="consent" checked={share} onChange={(e) => setShare(e.target.checked)} />
          <span>
            면접 결과(음성, 답변, 점수)를 {p?.company_name ?? '지원 기업'}에 공유합니다 (선택)
            <small>{share ? '채용담당자가 응시자 목록에서 결과를 보고 합격 여부를 정할 수 있습니다.'
              : '공유하지 않으면 연습 모드로 진행되고, 기업에는 결과가 보이지 않습니다.'}</small>
          </span>
        </label>
      </section>
      <section className="cs">
        <h2>이력서 <em className="req">필수</em></h2>
        {error && <div className="notice bad" role="alert" style={{ margin: 0 }}>{error}</div>}
        {reading ? (
          <div className="ocr"><span className="spin" aria-hidden="true" /><div>
            <b>{reading.join(', ')}</b><p className="hint">파일을 읽고 이력서 정보를 뽑고 있습니다. 스캔한 PDF와 사진은 글자 인식(OCR)으로 읽습니다.</p>
          </div></div>
        ) : extracted ? (
          <div className="extract">
            <div className="ex-h"><b>파일에서 읽은 정보입니다</b><span className="hint">잘못 읽은 부분이 있으면 바로 고쳐 주십시오</span></div>
            <div className="row2">{box('name', '이름')}{box('edu', '학력')}</div>
            <div className="row2">{box('career', '경력')}{box('skill', '보유 기술')}</div>
            {box('proj', '주요 프로젝트', true)}
            <div className="mfoot" style={{ justifyContent: 'flex-start' }}>
              <button className="btn btn-primary" type="button" disabled={busy} onClick={() => void register()}>이 내용으로 이력서 등록</button>
              <button className="btn" type="button" onClick={() => setExtracted(null)}>다른 파일 올리기</button>
            </div>
          </div>
        ) : (
          <>
            {reg && (
              <>
                <div className="myres">
                  <div>
                    <b>{reg.content.name}님 이력서</b>
                    <span className="hint">
                      {reg.content.career.length ? reg.content.career[0].company : '신입'} · {formatDate(reg.updated_at)} 수정
                      {reg.source === 'PDF' ? ' · PDF에서 읽음' : ''}
                    </span>
                  </div>
                  <div className="myres-a">
                    <span className="pill good">이 이력서로 지원</span>
                    <button className="btn" type="button" onClick={toResume}>불러와 수정</button>
                  </div>
                </div>
                <p className="hint" style={{ margin: 0 }}>다른 이력서로 지원하려면 아래에 파일을 올려 주십시오.</p>
              </>
            )}
            <label className={`drop${over ? ' over' : ''}`} htmlFor="cs-file"
              onDragEnter={(e) => { e.preventDefault(); setOver(true); }} onDragOver={(e) => { e.preventDefault(); setOver(true); }}
              onDragLeave={(e) => { e.preventDefault(); setOver(false); }}
              onDrop={(e) => { e.preventDefault(); setOver(false); void take(e.dataTransfer.files); }}>
              <input id="cs-file" type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" multiple
                onChange={(e) => { void take(e.target.files); e.target.value = ''; }} />
              <b>이력서·자기소개서 파일을 끌어다 놓거나 눌러서 고르십시오</b>
              <span className="hint">PDF, PNG, JPG · 여러 개 가능 · 파일당 10MB까지</span>
            </label>
            {!reg && !resume.isPending && (
              <>
                <div className="or"><span>또는</span></div>
                <button className="btn" type="button" style={{ justifySelf: 'start' }} onClick={toResume}>이력서 직접 만들기</button>
              </>
            )}
          </>
        )}
      </section>
      <div className="mfoot">
        <button className="btn btn-lg" type="button" onClick={() => navigate(applyPath(pid))}>이전 단계</button>
        <button className="btn btn-primary btn-lg" type="button" disabled={!ready || busy} onClick={() => void start()}
          data-testid="start-interview">{goLabel}</button>
      </div>
    </div></div>
  );
}
