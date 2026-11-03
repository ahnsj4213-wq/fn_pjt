import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import {
  deletePostingTemplate, fetchPostingTemplates, fetchRecruiterPosting, fetchRecruiterPostings, importPostingFile,
  type PostingTemplate,
} from '@/api/recruiter';
import { describeError } from '@/shared/http/errors';
import { Modal, useToast } from '@/shared/ui/overlay';

/** What a template fills into the posting form. The deadline is never part of it (screen spec: "마감일은 그대로 둡니다"). */
export interface TemplateFill {
  title?: string | null; job_family?: string | null; experience_level?: string | null; employment_type?: string | null;
  location?: string | null; headcount?: number | null; duties?: string | null; requirements?: string | null; preferred?: string | null;
}

type Tab = 'PREV' | 'BASIC' | 'SAVED' | 'FILE';
const TABS: [Tab, string][] = [['PREV', '이전 공고'], ['BASIC', '기본 템플릿'], ['SAVED', '저장한 템플릿'], ['FILE', '외부 파일']];
const STATUS: Record<string, string> = { DRAFT: '임시 저장', OPEN: '게시 중', CLOSED: '마감' };
const JOB_LABEL: Record<string, string> = { DEV: '개발', DATA_AI: '데이터·AI', DESIGN: '디자인', MARKETING: '마케팅', MGMT: '경영지원' };

/** 기본 템플릿: 직무별 주요 업무 (fixed texts of the screen). */
const BASIC: { job_family: string; title: string; duties: string; requirements: string; preferred: string }[] = [
  { job_family: 'DEV', title: '개발', duties: '서비스 API 설계 및 개발\n데이터베이스 설계와 성능 개선\n배포 자동화와 장애 대응',
    requirements: '프로그래밍 언어 한 가지 이상에 능숙한 분\n자료구조와 알고리즘에 대한 이해', preferred: '클라우드 환경 경험\n코드 리뷰와 협업 경험' },
  { job_family: 'DATA_AI', title: '데이터·AI', duties: '데이터 수집·정제 파이프라인 구축\n분석과 모델 개발\n지표 설계와 리포트 작성',
    requirements: 'SQL과 파이썬 활용 능력\n통계와 머신러닝 기초 지식', preferred: '대용량 데이터 처리 경험\n모델 서비스 운영 경험' },
  { job_family: 'DESIGN', title: '디자인', duties: '서비스 화면 설계와 디자인\n디자인 시스템 관리\n사용자 조사와 개선안 도출',
    requirements: '디자인 도구 활용 능력\n포트폴리오 제출 가능한 분', preferred: '프로토타이핑 경험\n개발자와 협업한 경험' },
  { job_family: 'MARKETING', title: '마케팅', duties: '마케팅 캠페인 기획과 운영\n성과 분석과 개선\n콘텐츠 제작 협업',
    requirements: '데이터 기반으로 성과를 해석할 수 있는 분\n문서 작성과 소통 능력', preferred: '광고 플랫폼 운영 경험\n브랜드 운영 경험' },
  { job_family: 'MGMT', title: '경영지원', duties: '인사·총무·회계 업무 지원\n내부 규정 관리\n부서 간 일정과 예산 조율',
    requirements: '정확하고 꼼꼼하게 일하는 분\n사무 도구 활용 능력', preferred: '관련 자격증\n유사 직무 경험' },
];

/** SC-R05 템플릿 불러오기 (작은 창). Picking an entry fills the form and closes the window. */
export function TemplateModal({ onPick, onClose }: { onPick: (f: TemplateFill, message: string) => void; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>('PREV');
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const prev = useQuery({ queryKey: ['recruiter', 'postings', 'all'], queryFn: () => fetchRecruiterPostings(), enabled: tab === 'PREV' });
  const saved = useQuery({ queryKey: ['recruiter', 'posting-templates'], queryFn: fetchPostingTemplates, enabled: tab === 'SAVED' });
  const remove = useMutation({
    mutationFn: deletePostingTemplate,
    onSuccess: () => { toast('템플릿을 지웠습니다'); void qc.invalidateQueries({ queryKey: ['recruiter', 'posting-templates'] }); },
    onError: (e) => toast(describeError(e).message),
  });
  const fromPosting = useMutation({
    mutationFn: (id: string) => fetchRecruiterPosting(id),
    onSuccess: (p) => onPick({ title: p.title, job_family: p.job_family, experience_level: p.experience_level, employment_type: p.employment_type,
      location: p.location, headcount: p.headcount, duties: p.duties, requirements: p.requirements, preferred: p.preferred }, `「${p.title ?? '제목 없음'}」 공고 내용을 불러왔습니다`),
    onError: (e) => setError(describeError(e).message),
  });
  const fromFile = useMutation({
    mutationFn: importPostingFile,
    onSuccess: (r) => onPick({ title: r.title, duties: r.duties, requirements: r.requirements, preferred: r.preferred },
      `${r.file_name}에서 공고 내용을 읽어 왔습니다. 칸마다 확인해 주십시오`),
    onError: (e) => setError(describeError(e).message),
  });
  const pickSaved = (t: PostingTemplate) => onPick({ job_family: t.job_family, experience_level: t.experience_level, employment_type: t.employment_type,
    duties: t.duties, requirements: t.requirements, preferred: t.preferred }, '템플릿을 불러왔습니다');

  return (
    <Modal title="템플릿 불러오기" onClose={onClose}>
      <div className="tabs" role="tablist" aria-label="불러올 곳">
        {TABS.map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => { setTab(k); setError(null); }}>{l}</button>
        ))}
      </div>
      {error && <div className="notice bad" role="alert">{error}</div>}
      <div role="tabpanel" style={{ display: 'grid', gap: 8, maxHeight: 340, overflow: 'auto' }}>
        {tab === 'PREV' && (prev.isPending ? <p className="hint">불러오는 중입니다</p>
          : prev.data!.length === 0 ? <p className="hint">불러올 이전 공고가 없습니다.</p>
            : prev.data!.map((p) => (
              <button key={p.id} type="button" className="btn" style={{ textAlign: 'left' }} onClick={() => fromPosting.mutate(p.id)}>
                <b>{p.title ?? '제목 없음'}</b> <span className="hint">{STATUS[p.status] ?? p.status} · {JOB_LABEL[p.job_family ?? ''] ?? '직무 미정'}{p.experience_level ? ` · ${p.experience_level}` : ''}</span>
              </button>)))}
        {tab === 'BASIC' && BASIC.map((b) => (
          <button key={b.job_family} type="button" className="btn" style={{ textAlign: 'left', whiteSpace: 'normal' }}
            onClick={() => onPick({ job_family: b.job_family, duties: b.duties, requirements: b.requirements, preferred: b.preferred }, '템플릿을 불러왔습니다')}>
            <b>{b.title}</b> <span className="hint">{b.duties.split('\n').join(' · ')}</span>
          </button>))}
        {tab === 'SAVED' && (saved.isPending ? <p className="hint">불러오는 중입니다</p>
          : saved.data!.length === 0 ? <p className="hint">저장한 템플릿이 없습니다. 공고 작성 화면에서 '템플릿으로 저장'을 눌러 만들 수 있습니다.</p>
            : saved.data!.map((t) => (
              <div key={t.id} style={{ display: 'flex', gap: 6 }}>
                <button type="button" className="btn" style={{ textAlign: 'left', flex: 1 }} onClick={() => pickSaved(t)}>
                  <b>{t.name}</b> <span className="hint">{[JOB_LABEL[t.job_family ?? ''], t.experience_level, t.employment_type].filter(Boolean).join(' · ')}</span>
                </button>
                <button type="button" className="btn" aria-label={`${t.name} 지우기`} onClick={() => remove.mutate(t.id)}>지우기</button>
              </div>)))}
        {tab === 'FILE' && (
          <div style={{ display: 'grid', gap: 8 }}>
            <p className="hint" style={{ margin: 0 }}>DOCX, PDF 공고 파일을 올립니다. (HWP는 한글에서 DOCX나 PDF로 저장해 올려 주십시오.)</p>
            <input ref={file} type="file" accept=".docx,.pdf,.hwp" aria-label="공고 파일" hidden
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) { setError(null); fromFile.mutate(f); } }} />
            <button type="button" className="btn" disabled={fromFile.isPending} onClick={() => file.current?.click()}>
              {fromFile.isPending ? '파일을 읽는 중입니다' : '파일 고르기'}</button>
          </div>)}
      </div>
      <p className="hint" style={{ margin: 0 }}>불러오면 지금 입력한 내용이 템플릿 내용으로 바뀝니다. 마감일은 그대로 둡니다.</p>
    </Modal>
  );
}
