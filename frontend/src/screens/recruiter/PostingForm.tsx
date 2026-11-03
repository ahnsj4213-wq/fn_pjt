import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { fetchCompany } from '@/api/common/auth';
import { createPosting, fetchRecruiterPosting, publishPosting, savePostingTemplate, updatePosting, type PostingIn } from '@/api/recruiter';
import { describeError } from '@/shared/http/errors';
import { Field } from '@/shared/ui/wf';
import { Modal } from '@/shared/ui/overlay';
import { required } from '@/screens/common/signupForm';
import { useToast } from '@/shared/ui/overlay';
import { QueryState } from '@/shared/ui/extra';
import { TemplateModal, type TemplateFill } from './TemplateModal';

const JOBS: [string, string][] = [['DEV', '개발'], ['DATA_AI', '데이터·AI'], ['DESIGN', '디자인'], ['MARKETING', '마케팅'], ['MGMT', '경영지원']];
const thirtyDays = () => { const d = new Date(Date.now() + 30 * 86400000); return d.toISOString().slice(0, 10); };

interface Form { title: string; job_family: string; experience_level: string; employment_type: string; location: string;
  headcount: string; deadline_date: string; duties: string; requirements: string; preferred: string; coding_enabled: boolean;
  coding_time_limit_min: string }
const EMPTY: Form = { title: '', job_family: 'DEV', experience_level: '신입', employment_type: '정규직', location: '', headcount: '1',
  deadline_date: thirtyDays(), duties: '', requirements: '', preferred: '', coding_enabled: false, coding_time_limit_min: '60' };

/** SC-R04 기업 관리 · 공고 작성·수정. 임시 저장은 비어 있어도 되고, 게시할 때 서버가 빠진 항목을 알려 줍니다. */
export default function PostingForm() {
  const { pid } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [v, setV] = useState<Form>(EMPTY);
  const [rowVersion, setRowVersion] = useState<number | undefined>();
  const [picking, setPicking] = useState(false);
  const [naming, setNaming] = useState<string | null>(null);
  const company = useQuery({ queryKey: ['company'], queryFn: fetchCompany });
  const verified = company.data ? company.data.verification === 'APPROVED' && !company.data.suspended : true;
  const existing = useQuery({ queryKey: ['recruiter', 'posting', pid], queryFn: () => fetchRecruiterPosting(pid!), enabled: Boolean(pid) });
  useEffect(() => {
    const p = existing.data;
    if (!p) return;
    setRowVersion(p.row_version);
    setV({ title: p.title ?? '', job_family: p.job_family ?? 'DEV', experience_level: p.experience_level ?? '', employment_type: p.employment_type ?? '',
      location: p.location ?? '', headcount: p.headcount ? String(p.headcount) : '', deadline_date: p.deadline_date ?? '',
      duties: p.duties ?? '', requirements: p.requirements ?? '', preferred: p.preferred ?? '', coding_enabled: p.coding_enabled,
      coding_time_limit_min: p.coding_time_limit_min ? String(p.coding_time_limit_min) : '60' });
  }, [existing.data]);

  const body = (): PostingIn => ({
    title: v.title.trim() || '제목 없는 공고', job_family: v.job_family, experience_level: v.experience_level || null, employment_type: v.employment_type || null,
    location: v.location || null, headcount: v.headcount ? Number(v.headcount) : null, deadline_date: v.deadline_date || null,
    duties: v.duties.trim() || '(주요 업무를 입력해 주십시오)', requirements: v.requirements.trim() || '(자격 요건을 입력해 주십시오)',
    preferred: v.preferred || null, coding_enabled: v.coding_enabled,
    coding_time_limit_min: v.coding_enabled ? Number(v.coding_time_limit_min) || null : null,
    ...(pid ? { row_version: rowVersion } : {}),
  });
  const save = async () => {
    const p = pid ? await updatePosting(pid, body()) : await createPosting(body());
    setRowVersion(p.row_version);
    void qc.invalidateQueries({ queryKey: ['recruiter'] });
    return p;
  };
  const draft = useMutation({ mutationFn: save, onSuccess: () => { toast('임시 저장했습니다'); nav('/recruiter/postings'); }, onError: (e) => toast(describeError(e).message) });
  const publish = useMutation({
    // An already published posting is only saved again; publish would be a state error.
    mutationFn: async () => { const p = await save(); return existing.data?.status === 'OPEN' ? p : publishPosting(p.id); },
    onSuccess: () => { toast('공고를 게시했습니다. 메인 화면과 일정 관리에 바로 들어갑니다'); void qc.invalidateQueries({ queryKey: ['recruiter'] }); nav('/recruiter/postings'); },
    onError: (e) => toast(describeError(e).message),
  });
  const fill = (f: TemplateFill, message: string) => {
    // A template replaces what is typed, except the deadline (and the coding switch, which is not part of a template).
    setV((s) => ({ ...s, title: f.title ?? s.title, job_family: f.job_family ?? s.job_family, experience_level: f.experience_level ?? s.experience_level,
      employment_type: f.employment_type ?? s.employment_type, location: f.location ?? s.location,
      headcount: f.headcount ? String(f.headcount) : s.headcount, duties: f.duties ?? '', requirements: f.requirements ?? '', preferred: f.preferred ?? '' }));
    setPicking(false);
    toast(message);
  };
  const saveTemplate = useMutation({
    mutationFn: (name: string) => savePostingTemplate({ name: name.trim().slice(0, 80), job_family: v.job_family, experience_level: v.experience_level || null,
      employment_type: v.employment_type || null, duties: v.duties || null, requirements: v.requirements || null, preferred: v.preferred || null }),
    onSuccess: () => { toast("템플릿으로 저장했습니다. '템플릿 불러오기'에서 쓸 수 있습니다"); setNaming(null); void qc.invalidateQueries({ queryKey: ['recruiter', 'posting-templates'] }); },
    onError: (e) => toast(describeError(e).message),
  });
  const jobLabel = JOBS.find(([k]) => k === v.job_family)?.[1] ?? '';
  const onSaveTemplate = () => setNaming(`${jobLabel} · ${v.title.trim() || '제목 없는 공고'}`);
  const set = (k: keyof Form) => (e: { target: { value: string } }) => setV((s) => ({ ...s, [k]: e.target.value }));
  const busy = draft.isPending || publish.isPending;
  const title = !pid ? '새 공고 등록' : existing.data?.status === 'DRAFT' ? '공고 이어서 작성' : '공고 수정';

  return (
    <>
      <Link to="/recruiter/postings" className="back" style={{ margin: 0 }}>‹ 공고 목록</Link>
      <div className="ph"><div><h1>{title}</h1><p>공고 문구는 자유롭게 쓰면 됩니다. 공고 내용은 면접 질문을 만들 때도 쓰입니다.</p></div></div>
      <QueryState isPending={Boolean(pid) && existing.isPending} error={existing.error} onRetry={() => existing.refetch()}>
        {() => (
          <form className="card form" noValidate onSubmit={(e) => { e.preventDefault(); if (!v.title.trim()) { toast(required('공고 제목')); return; } publish.mutate(); }}>
            <Field label="공고 제목" required id="pf-title" value={v.title} onChange={set('title')} placeholder="예: 백엔드 개발자 (신입)" />
            <div className="row2">
              <div className="field"><label htmlFor="pf-cat">직무 카테고리</label>
                <select id="pf-cat" value={v.job_family} onChange={set('job_family')}>{JOBS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
              <Field label="경력" id="pf-career" value={v.experience_level} onChange={set('experience_level')} />
            </div>
            <div className="row2">
              <Field label="고용형태" id="pf-type" value={v.employment_type} onChange={set('employment_type')} />
              <Field label="근무지" id="pf-loc" value={v.location} onChange={set('location')} />
            </div>
            <div className="row2">
              <Field label="모집 인원" id="pf-hc" type="number" min={1} max={99} value={v.headcount} onChange={set('headcount')} />
              <Field label="마감일" id="pf-end" type="date" value={v.deadline_date} onChange={set('deadline_date')} />
            </div>
            <div className="field"><label htmlFor="pf-duties">주요 업무 <span className="hint">한 줄에 하나씩</span></label>
              <textarea id="pf-duties" value={v.duties} onChange={set('duties')} /></div>
            <div className="field"><label htmlFor="pf-reqs">자격 요건</label><textarea id="pf-reqs" value={v.requirements} onChange={set('requirements')} /></div>
            <div className="field"><label htmlFor="pf-prefs">우대 사항</label><textarea id="pf-prefs" value={v.preferred} onChange={set('preferred')} /></div>
            <label className="check"><input type="checkbox" checked={v.coding_enabled} onChange={(e) => setV((s) => ({ ...s, coding_enabled: e.target.checked }))} /> 코딩테스트 포함</label>
            {v.coding_enabled && (
              <div className="row2">
                <Field label="코딩테스트 제한 시간(분)" id="pf-limit" type="number" min={10} max={180} value={v.coding_time_limit_min} onChange={set('coding_time_limit_min')} />
                <p className="hint" style={{ margin: 'auto 0 0' }}>문제는 서버가 하~중 난이도에서 정해 줍니다. 면접 질문이 모두 끝나면 코딩테스트가 이어서 시작됩니다.</p>
              </div>)}
            <div className="btnrow">
              <button className="btn" type="button" disabled={busy} onClick={() => setPicking(true)}>템플릿 불러오기</button>
              <button className="btn" type="button" disabled={busy || saveTemplate.isPending} onClick={onSaveTemplate}>템플릿으로 저장</button>
              <button className="btn" type="button" disabled={busy} onClick={() => draft.mutate()}>임시 저장</button>
              {pid && <button className="btn" type="button" disabled={busy} onClick={() => nav(`/recruiter/postings/${pid}/preview`)}>미리보기</button>}
              <button className="btn btn-primary" type="submit" disabled={busy || !verified} aria-busy={busy || undefined}>
                {existing.data?.status === 'OPEN' ? '수정 내용 게시' : '게시하기'}</button>
            </div>
            {!verified && <p className="hint" style={{ margin: 0 }}>기업 인증이 승인되면 게시할 수 있습니다. 지금은 임시 저장만 됩니다.</p>}
          </form>
        )}
      </QueryState>
      {naming !== null && (
        <Modal title="템플릿으로 저장" onClose={() => setNaming(null)}>
          <form className="form" noValidate aria-label="템플릿으로 저장"
            onSubmit={(e) => { e.preventDefault(); if (!naming.trim()) { toast(required('템플릿 이름')); return; } saveTemplate.mutate(naming); }}>
            <Field label="템플릿 이름" id="tpl-name" value={naming} onChange={(e) => setNaming(e.target.value)} />
            <p className="hint" style={{ margin: 0 }}>마감일은 저장하지 않습니다. 불러올 때마다 새로 정합니다.</p>
            <div className="btnrow"><button className="btn btn-primary" type="submit" disabled={saveTemplate.isPending}>저장</button></div>
          </form>
        </Modal>)}
      {picking && <TemplateModal onPick={fill} onClose={() => setPicking(false)} />}
    </>
  );
}
