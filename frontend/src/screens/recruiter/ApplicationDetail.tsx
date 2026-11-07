import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router';
import { useSession } from '@/app/session';
import {
  createNote, fetchApplication, fetchCodingResult, fetchApplicationResume, fetchDecisionHistory, fetchNotes, fetchReport, reEvaluate, resumeFileUrl, saveDecision,
  type ApplicationDetail as Detail, type CriterionKey,
} from '@/api/recruiter';
import { formatDate, formatDateTime } from '@/shared/format';
import { describeError } from '@/shared/http/errors';
import { PlainText, QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';
import { AI_REF, DECISION } from './Applications';
import { MailModal } from './MailModal';

const CRITERIA: Record<CriterionKey, string> = { EXPERTISE: '전문성', LOGIC: '논리성', ATTITUDE: '표현 및 태도', DELIVERY: '전달력' };
const SLOT: Record<string, string> = { PERSONALITY: '인성', MOTIVATION: '지원 동기', JOB_SKILL: '직무 기술', PROJECT: '프로젝트 경험', COLLABORATION: '협업', RESUME: '이력서', COMPANY: '기업', JOB: '직무', CUSTOM: '공고 질문' };

/** The problem the server gave this applicant: level, tags and origin stay on the recruiter's side (ADR-0021). */
function CodingProblemInfo({ id }: { id: string }) {
  const q = useQuery({ queryKey: ['recruiter', 'coding-result', id], queryFn: () => fetchCodingResult(id) });
  const p = q.data?.problem;
  if (!p) return null;
  const s = q.data?.submission;
  return (
    <div data-testid="coding-problem-info">
      <h3 style={{ margin: '0 0 4px' }}>출제 문제</h3>
      <p className="fine" style={{ margin: 0 }}>
        {p.level !== null && <b>Lv.{p.level} </b>}{p.title}{p.tags.length > 0 && ` · ${p.tags.join(', ')}`} · {p.source?.origin === 'ORIGINAL' ? '자체 제작' : '출처 기록 없음'}
        {s?.total != null && ` · 자동 채점 ${s.passed}/${s.total}`}
      </p>
      {p.expected && <p className="fine" style={{ margin: '4px 0 0' }}>기대 수준: {p.expected}</p>}
    </div>
  );
}

function Resume({ id }: { id: string }) {
  const r = useQuery({ queryKey: ['recruiter', 'resume', id], queryFn: () => fetchApplicationResume(id) });
  return (
    <div className="card"><h2>이력서 <small>지원 시점에 저장된 내용입니다</small>
      {r.data?.has_file && <a className="link" href={resumeFileUrl(id)} target="_blank" rel="noreferrer">원본 파일 보기</a>}</h2>
      <QueryState isPending={r.isPending} error={r.error} onRetry={() => r.refetch()}>
        {() => {
          const c = r.data!.snapshot;
          return (
            <div className="rescard"><dl>
              <div><dt>이름</dt><dd>{c.name}</dd></div>
              <div><dt>학력</dt><dd>{c.education.map((e) => [e.school, e.major, e.degree].filter(Boolean).join(' ')).join(' / ') || '—'}</dd></div>
              <div><dt>경력</dt><dd>{c.career.map((e) => [e.company, e.role, e.period].filter(Boolean).join(' ')).join(' / ') || '신입'}</dd></div>
              <div><dt>보유 기술</dt><dd>{c.skills.join(', ') || '—'}</dd></div>
              <div><dt>주요 프로젝트</dt><dd>{c.projects.map((p) => p.title).join(' / ') || '—'}</dd></div>
            </dl></div>
          );
        }}
      </QueryState>
    </div>
  );
}

function Report({ id }: { id: string }) {
  const rep = useQuery({ queryKey: ['recruiter', 'report', id], queryFn: () => fetchReport(id) });
  return (
    <div id="report" className="card"><h2>평가 리포트 <small>점수는 서버 코드가 계산했고, 인용은 답변 원문과 대조했습니다</small></h2>
      <QueryState isPending={rep.isPending} error={rep.error} onRetry={() => rep.refetch()}>
        {() => {
          const r = rep.data!;
          return (
            <div style={{ display: 'grid', gap: 16 }}>
              <div className="scores">
                {r.criteria.map((c) => (
                  <div className="srow" key={c.key}><span>{CRITERIA[c.key]}</span>
                    <span className="bar"><i style={{ width: `${Number(c.score)}%` }} /></span><b>{Math.round(Number(c.score))}</b><small>가중치 {c.weight}%</small></div>
                ))}
                {r.coding && <div className="srow"><span>코딩테스트</span><span className="bar"><i style={{ width: `${Number(r.coding.score)}%` }} /></span>
                  <b>{Math.round(Number(r.coding.score))}</b><small>가중치 {r.coding.weight ?? 0}%</small></div>}
              </div>
              {r.coding && <CodingProblemInfo id={id} />}
              <div className="total"><span>AI 종합 점수</span><b style={{ fontSize: 20 }}>{Math.round(Number(r.total_score))}점</b></div>
              {r.insufficient_count > 0 && <p className="hint" style={{ margin: 0 }}>근거가 부족해 점수를 내지 못한 항목이 {r.insufficient_count}개 있습니다.</p>}
              <div><b>요약</b><PlainText text={r.summary} /></div>
              {r.strengths.length > 0 && <div><b>강점</b><ul>{r.strengths.map((s) => <li key={s}>{s}</li>)}</ul></div>}
              {r.improvements.length > 0 && <div><b>보완할 점</b><ul>{r.improvements.map((s) => <li key={s}>{s}</li>)}</ul></div>}
              <div style={{ display: 'grid', gap: 10 }}>
                {r.questions.map((q) => (
                  <details key={q.seq}><summary><b>Q{q.seq}</b> <span className="hint">{SLOT[q.slot_type] ?? q.slot_type}</span> {q.question_text}</summary>
                    {q.answers.map((a, i) => (
                      <div key={i} style={{ margin: '8px 0 0 12px' }}>
                        <span className="hint">{a.role === 'MAIN' ? '답변' : '꼬리질문 답변'}{a.no_answer ? ' · 답변 없음' : ''}</span>
                        <PlainText text={a.display_text} />
                        {a.items.filter((it) => it.quote).map((it) => (
                          <p key={it.key} className="fine" style={{ margin: '4px 0' }}>{CRITERIA[it.key]} {it.score}점 — “{it.quote!.text}” {it.comment}</p>
                        ))}
                      </div>
                    ))}
                  </details>
                ))}
              </div>
              <p className="fine" style={{ margin: 0 }}>시선 이탈 {r.proctor.leave_count}회 · 모델 {r.versions.model} · 루브릭 {r.versions.rubric}</p>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}

/** 메모 (SC-R09 11): visible to this company's recruiters only. */
function Notes({ id }: { id: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState('');
  const notes = useQuery({ queryKey: ['recruiter', 'notes', id], queryFn: () => fetchNotes(id) });
  const add = useMutation({
    mutationFn: () => createNote(id, text.trim()),
    onSuccess: () => { toast('메모를 저장했습니다'); setText(''); void qc.invalidateQueries({ queryKey: ['recruiter', 'notes', id] }); },
    onError: (e) => toast(describeError(e).message),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) { toast('메모 내용을 입력해 주십시오'); return; }
    add.mutate();
  };
  return (
    <div className="card"><h2>메모 <small>우리 회사 담당자에게만 보입니다</small></h2>
      {notes.data?.map((n) => (
        <div key={n.id} style={{ marginBottom: 10 }}><span className="hint">{n.author.name} · {formatDateTime(n.created_at)}</span><PlainText text={n.body} /></div>
      ))}
      <form className="form" onSubmit={submit} noValidate aria-label="메모 쓰기">
        <div className="field"><label htmlFor="note-body">메모</label>
          <textarea id="note-body" value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} /></div>
        <div className="btnrow"><button className="btn" type="submit" disabled={add.isPending}>메모 저장</button></div>
      </form>
    </div>
  );
}

function History({ id }: { id: string }) {
  const h = useQuery({ queryKey: ['recruiter', 'history', id], queryFn: () => fetchDecisionHistory(id) });
  if (!h.data?.length) return null;
  return (
    <div className="card"><h2>결정 이력</h2><div className="evlist">
      {h.data.map((r, i) => (
        <div key={i}><span className="when">{formatDate(r.decided_at)}</span>
          <span>{DECISION[r.from][0]} → <b>{DECISION[r.to][0]}</b> <span className="hint">{r.decided_by.name}</span></span></div>
      ))}</div></div>
  );
}

/** SC-R09 기업 관리 · 응시자 상세 (+ SC-R10 평가 리포트). Pass/fail is the recruiter's own click; the AI line is a reference. */
export default function ApplicationDetail() {
  const { aid = '' } = useParams();
  const toast = useToast();
  const qc = useQueryClient();
  const company = useSession().data?.user.company_name ?? '';
  const [mailing, setMailing] = useState(false);
  const app = useQuery({ queryKey: ['recruiter', 'application', aid], queryFn: () => fetchApplication(aid) });
  const report = useQuery({ queryKey: ['recruiter', 'report', aid], queryFn: () => fetchReport(aid), enabled: app.data?.eval_status === 'DONE' });
  const refresh = () => qc.invalidateQueries({ queryKey: ['recruiter'] });
  const decide = useMutation({
    mutationFn: (d: { decision: 'PASS' | 'HOLD' | 'FAIL'; v: Detail }) => saveDecision(aid, { decision: d.decision, expected_version: d.v.decision_version }),
    onSuccess: (_r, d) => { toast(`${d.v.candidate.name}님을 ${DECISION[d.decision][0]}(으)로 정했습니다`); void refresh(); },
    onError: (e) => { toast(describeError(e).message); void refresh(); },
  });
  const again = useMutation({
    mutationFn: () => reEvaluate(aid),
    onSuccess: () => { toast('다시 평가를 요청했습니다'); void refresh(); },
    onError: (e) => toast(describeError(e).message),
  });
  return (
    <>
      <Link to="/recruiter/applications" className="back" style={{ margin: 0 }}>‹ 응시자 목록</Link>
      <QueryState isPending={app.isPending} error={app.error} onRetry={() => app.refetch()}>
        {() => {
          const a = app.data!;
          const ai = report.data?.ai_reference ? AI_REF[report.data.ai_reference] : null;
          return (
            <>
              <div className="card det-head">
                <span className="avatar" aria-hidden="true">{a.candidate.name.slice(0, 1)}</span>
                <div><h1>{a.candidate.name}</h1>
                  <p>{a.posting.title} · {formatDate(a.applied_at)} 응시{a.text_mode_used ? ' · 글로 답변한 문항 있음' : ''}</p></div>
                <div className="decide">
                  <span className="dlabel">담당자 결정</span>
                  {(['PASS', 'HOLD', 'FAIL'] as const).map((d) => (
                    <button key={d} type="button" className={`btn${a.decision === d ? ` on-${d === 'PASS' ? 'good' : d === 'HOLD' ? 'hold' : 'bad'}` : ''}`} aria-pressed={a.decision === d}
                      disabled={!a.can_decide || decide.isPending} onClick={() => decide.mutate({ decision: d, v: a })}>{DECISION[d][0]}</button>
                  ))}
                  {a.eval_status === 'DONE' && <Link to={`/recruiter/applications/${encodeURIComponent(aid)}/report`} className="btn">평가 리포트</Link>}
                  <button type="button" className="btn btn-primary" onClick={() => setMailing(true)}>메일 보내기</button>
                  {a.can_re_evaluate && <button type="button" className="btn" onClick={() => again.mutate()} disabled={again.isPending}>다시 평가</button>}
                </div>
              </div>
              {a.eval_status !== 'DONE' && (a.attempt_status === 'INTERRUPTED' || a.attempt_status === 'EXPIRED') && (
                <div className="banner" style={{ margin: 0 }}>면접을 끝까지 마치지 않아 종합 점수가 없습니다 (질문 {a.answered_main_count}/{a.main_count ?? '-'})</div>)}
              {!a.can_decide && a.attempt_status !== 'INTERRUPTED' && <div className="banner" style={{ margin: 0 }}>
                {a.eval_status === 'ON_HOLD' ? '평가가 보류되었습니다. 다시 평가를 요청해 주십시오.' : '평가가 끝나면 결정할 수 있습니다.'}</div>}
              {ai && <div className="airow"><b>AI 예상 합격</b> <span className={`pill ${ai[1]}`}>{ai[0]}</span>
                <span className="hint">75점 이상 합격 추천 · 65~74점 보류 · 점수로 낸 참고값이며 응시자에게는 보이지 않습니다</span></div>}
              {a.decision === 'HOLD' && <p className="fine" style={{ margin: 0 }}>보류는 담당자만 보는 상태입니다. 지원자에게는 계속 ‘검토 중’으로 보이고, 합격·불합격으로 정하면 결과가 공개됩니다.</p>}
              <div className="grid2">
                <Resume id={aid} />
                <div className="card"><h2>개인 정보</h2>
                  <dl className="pinfo"><div><dt>연락처</dt><dd>{a.candidate.phone}</dd></div><div><dt>이메일</dt><dd>{a.candidate.email}</dd></div>
                    <div><dt>제출</dt><dd>{a.submitted_at ? formatDateTime(a.submitted_at) : '—'}</dd></div></dl></div>
              </div>
              {mailing && <MailModal applicationId={aid} name={a.candidate.name} email={a.candidate.email} company={company} decision={a.decision} onClose={() => setMailing(false)} />}
              {a.eval_status === 'DONE' && <Report id={aid} />}
              <Notes id={aid} />
              <History id={aid} />
            </>
          );
        }}
      </QueryState>
    </>
  );
}
