import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { deleteMyMedia, fetchMyReport, fetchRecruiterReportView, type MyReport, type RecruiterReportView, type ReportAnswer, type ReportPoint } from '@/api/candidate/report';
import { ApiError } from '@/shared/http/client';
import { formatDate } from '@/shared/format';
import { describeError } from '@/shared/http/errors';
import { Loading, Notice } from '@/shared/ui/components';
import { useToast } from '@/shared/ui/overlay';

const AI_LABEL = { PASS: '합격 추천', HOLD: '보류', FAIL: '불합격 추천' } as const;
const AI_KIND = { PASS: 'good', HOLD: 'hold', FAIL: 'bad' } as const;

const Bar = ({ v, low }: { v: number; low?: boolean }) => <span className={`sbar${low ? ' low' : ''}`}><i style={{ width: `${v}%` }} /></span>;

/** The answer text with its verified quotes marked: 가점 (green) and 감점 (red) phrases, and hedging phrases. */
function Marked({ a }: { a: ReportAnswer }) {
  const text = a.text ?? '';
  type M = { s: number; e: number; cls: 'pos' | 'neg'; title: string };
  const marks: M[] = a.highlights.map((h) => ({ s: h.start, e: h.end, cls: h.kind === 'good' ? 'pos' : 'neg', title: h.criterion }));
  for (const p of a.negative) {
    let i = text.indexOf(p);
    while (i >= 0) { marks.push({ s: i, e: i + p.length, cls: 'neg', title: '부정적인 표현' }); i = text.indexOf(p, i + p.length); }
  }
  marks.sort((x, y) => x.s - y.s || y.e - x.e);
  const out: ReactNode[] = [];
  let at = 0;
  for (const m of marks) {
    if (m.s < at) continue; // overlapping marks: the first one wins
    if (m.s > at) out.push(text.slice(at, m.s));
    out.push(<mark key={`${m.s}-${m.cls}`} className={m.cls} title={m.title}>{text.slice(m.s, m.e)}</mark>);
    at = m.e;
  }
  out.push(text.slice(at));
  return <p>{out}</p>;
}

function Evidence({ a }: { a: ReportAnswer }) {
  if (!a.evidence.length) return null;
  return (
    <div className="evs"><span className="hint">점수 근거</span>
      {a.evidence.map((e) => (
        <p key={e.criterion}><span className={`sg ${e.kind === 'good' ? 'up' : 'dn'}`} aria-label={e.kind === 'good' ? '가점' : '감점'}>{e.kind === 'good' ? '▲' : '▼'}</span>
          <span><b>{e.label}</b> · '{e.quote}' {e.comment}</span></p>
      ))}</div>
  );
}

function AnswerBlock({ a, label }: { a: ReportAnswer; label?: string }) {
  if (a.no_answer) return <p className="hint" style={{ margin: 0 }}>답변이 없었습니다.</p>;
  return (
    <>
      <div className="stt"><span className="hint">{label ?? '답변 원문'}{a.seconds != null ? ` · ${a.seconds}초` : ''}
        <span className="mk-key"> <mark className="pos">가점 구절</mark> <mark className="neg">감점 구절</mark></span></span>
        <Marked a={a} /><Evidence a={a} /></div>
      {a.criteria.length > 0 && (
        <div className="qby">{a.criteria.map((c) => <div key={c.key}><span>{c.label}</span><Bar v={c.score} low={c.score < 65} /><b>{c.score}</b></div>)}</div>
      )}
    </>
  );
}

const Feedback = ({ a }: { a: ReportAnswer }) => (a.good || a.fix) ? (
  <div className="qfb">
    {a.good && <p><span className="pill good">잘한 점</span> {a.good}</p>}
    {a.fix && <p><span className="pill bad">보완할 점</span> {a.fix}</p>}
  </div>
) : null;

function Points({ list, kind, go }: { list: ReportPoint[]; kind: 'good' | 'bad'; go: (no: number) => void }) {
  return (
    <ol className="fb">
      {list.map((p) => (
        <li key={p.rank}><span className={`rank ${kind}`}>{p.rank}순위</span>
          <div><b>{p.text}</b>
            {p.refs.map((r) => <div key={r}><button className="link" type="button" onClick={() => go(r)}>관련 질문 Q{r} 보기</button></div>)}</div></li>
      ))}
    </ol>
  );
}

function DataCard({ aid, r }: { aid: string; r: MyReport }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [ask, setAsk] = useState(false);
  const del = useMutation({
    mutationFn: () => deleteMyMedia(aid),
    onSuccess: () => { toast('면접 영상과 음성을 영구 삭제했습니다'); setAsk(false); void qc.invalidateQueries({ queryKey: ['my-report', aid] }); },
    onError: (e) => { toast(describeError(e).message); setAsk(false); },
  });
  if (r.media.audio_deleted) {
    return (
      <section className="card delcard"><h2>내 면접 데이터</h2>
        <p className="fine" style={{ margin: 0 }}><b>면접 영상과 음성 원본을 영구 삭제했습니다.</b> 이 리포트의 점수와 텍스트만 남아 있습니다.
          {r.share_result ? ` ${r.company_name} 담당자도 더 이상 음성을 들을 수 없습니다.` : ''}</p></section>
    );
  }
  return (
    <section className="card delcard"><h2>내 면접 데이터</h2>
      <p className="fine" style={{ margin: '0 0 10px' }}>면접 영상과 음성 원본은 분석이 끝나고 {r.media.audio_auto_delete_days}일 뒤 자동으로 영구 삭제됩니다. 카메라 영상은 처음부터 저장하지 않습니다. 지금 바로 지울 수도 있습니다. 삭제해도 리포트의 점수와 텍스트는 남습니다.</p>
      {ask ? (
        <div className="delask" role="alert"><b>영상과 음성을 영구 삭제하시겠습니까?</b><span>삭제하면 되돌릴 수 없습니다.</span>
          <div><button className="btn btn-danger" type="button" onClick={() => del.mutate()} disabled={del.isPending}>영구 삭제</button>
            <button className="btn" type="button" onClick={() => setAsk(false)}>취소</button></div></div>
      ) : (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn" type="button" onClick={() => setAsk(true)}>영상·음성 지금 삭제</button>
          <button className="btn" type="button" onClick={() => toast('리포트까지 지우면 기록이 모두 사라집니다. 지금은 영상·음성 삭제만 할 수 있습니다')}>리포트까지 모두 삭제</button>
        </div>
      )}
    </section>
  );
}

/** SC-A08 결과 리포트 (slides 30~34). Open as soon as the evaluation is done; the 합격·불합격 result is a separate screen. */
export function Report({ mode = 'candidate' }: { mode?: 'candidate' | 'recruiter' }) {
  const { aid = '' } = useParams();
  const recruiter = mode === 'recruiter';
  const backTo = recruiter ? `/recruiter/applications/${encodeURIComponent(aid)}` : '/me/applications';
  const backLabel = recruiter ? '‹ 응시자 상세' : '‹ 마이페이지 · 지원 현황';
  const navigate = useNavigate();
  const report = useQuery({
    queryKey: [recruiter ? 'recruiter-report' : 'my-report', aid], queryFn: () => (recruiter ? fetchRecruiterReportView(aid) : fetchMyReport(aid)),
    retry: (n, e) => !(e instanceof ApiError && e.code !== 'REPORT_NOT_READY') && n < 3,
    refetchInterval: (q) => (q.state.error instanceof ApiError && q.state.error.code === 'REPORT_NOT_READY' ? 3000 : false),
  });
  const err = report.error;
  const goQ = (no: number) => {
    const d = document.getElementById(`rq-${no}`) as HTMLDetailsElement | null;
    if (!d) return;
    d.open = true;
    d.scrollIntoView?.({ behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  };

  if (report.isPending || (err instanceof ApiError && err.code === 'REPORT_NOT_READY')) {
    return (
      <div className="wrap"><div className="stub wide">
        <Link to={backTo} className="back">{backLabel}</Link>
        <h1>리포트를 만들고 있습니다</h1>
        <p className="sub">20초 안팎이 걸립니다. 이 화면을 열어 두면 리포트가 완성되는 대로 바로 바뀝니다.</p>
        <Loading label="리포트를 만드는 중입니다" />
      </div></div>
    );
  }
  if (err) {
    return (
      <div className="wrap"><div className="stub wide"><Link to={backTo} className="back">{backLabel}</Link>
        <Notice kind="error">{describeError(err).message}</Notice>
        <button className="btn" type="button" onClick={() => void report.refetch()}>다시 시도</button></div></div>
    );
  }
  const r = report.data!;
  const when = r.interviewed_at ? new Date(r.interviewed_at) : null;
  const dateText = when ? `${when.getFullYear()}년 ${when.getMonth() + 1}월 ${when.getDate()}일` : formatDate(null);
  const limit = r.attitude.limit_sec;

  return (
    <div className="wrap report">
      <Link to={backTo} className="back">{backLabel}</Link>
      <header className="rhead">
        <div><span className="eyebrow">모의면접 결과 리포트</span><h1>{r.posting_title}</h1>
          <p>{r.company_name} · {r.candidate_name}님 · {dateText} · 질문 {r.main_count}개 + 꼬리질문 {r.followup_count}개
            {r.avg_answer_sec != null ? ` · 평균 답변 ${r.avg_answer_sec}초` : ''}</p></div>
        {recruiter ? (
          <div className="rbtns">
            <span className={`pill ${AI_KIND[(r as RecruiterReportView).ai_reference ?? 'HOLD']}`} title="총점으로 코드가 계산한 참고값입니다. 결정은 담당자가 합니다">
              AI 추천 · 담당자에게만 보입니다 · {(r as RecruiterReportView).ai_reference ? AI_LABEL[(r as RecruiterReportView).ai_reference!] : '—'}</span>
          </div>
        ) : (
          <div className="rbtns">
            <button className="btn" type="button" onClick={() => navigate(`/postings/${encodeURIComponent(r.posting_id)}/apply`)}>다시 면접 보기</button>
            <button className="btn btn-primary" type="button" onClick={() => navigate('/')}>다른 공고 보기</button>
          </div>
        )}
      </header>

      <section className="rsum">
        <div className="card total-card">
          <span className="lbl">총점</span>
          <div className="big"><b>{r.total.toFixed(1)}</b><span>/ 100</span></div>
          <span className={`pill ${r.grade_kind}`}>{r.grade}</span>
          <div className="areas">
            {r.criteria.map((c) => <div className="arow" key={c.key}><span>{c.label}</span><Bar v={c.score} /><b>{c.score}</b><small>×{c.weight}%</small></div>)}
            {r.coding && <div className="arow"><span>코딩테스트</span><Bar v={r.coding.score} /><b>{r.coding.score}</b><small>×{r.coding.weight ?? 0}%</small></div>}
          </div>
          <p className="fine">총점은 기업이 정한 평가 기준의 항목별 점수에 가중치를 곱해 더한 값입니다.</p>
        </div>
        <div className="card"><h2>총평</h2>
          <p className="summary">{r.summary}</p>
          <div className="qstrip">
            {r.chips.map((c) => (
              <button key={c.label} type="button" className={`qchip${c.followup ? ' fu' : ''}`} onClick={() => goQ(c.no)}><span>{c.label}</span><b>{c.score ?? '—'}</b></button>
            ))}
          </div>
        </div>
      </section>

      <section className="grid2">
        <div className="card"><h2>좋았던 점 <small>강점 1~3순위</small></h2><Points list={r.strengths} kind="good" go={goQ} /></div>
        <div className="card"><h2>개선할 점 <small>단점 1~3순위</small></h2><Points list={r.improvements} kind="bad" go={goQ} /></div>
      </section>

      <h2 className="sec">영역별 평가</h2>
      <section className="grid2">
        {r.areas.map((a) => (
          <div className="card area" key={a.key}>
            <h2>{a.label} <span className="ascore">{a.score}점</span></h2><p className="adesc">{a.desc}</p>
            {a.items.map((it) => (
              <div className="item" key={it.label}><div className="ih"><span>{it.label}</span><Bar v={it.score} low={it.score < 65} /><b>{it.score}</b></div>
                {it.comment && <p>{it.comment}</p>}</div>
            ))}
          </div>
        ))}
        <div className="card area">
          <h2>답변 습관 <span className="ascore" style={{ fontSize: 12, fontWeight: 500 }}>점수에는 반영되지 않습니다</span></h2>
          <p className="adesc">감지된 부정적인 표현 · 질문별 답변시간</p>
          <div className="extra"><span className="hint">감지된 부정적 표현</span>
            <div className="negs">{r.attitude.negative.length ? r.attitude.negative.map((w) => <mark className="neg" key={w}>{w}</mark>) : <span className="hint">없었습니다</span>}</div>
            <span className="hint">질문별 답변시간{limit ? ` (제한 ${limit}초)` : ''}</span>
            <div className="times">{r.attitude.times.map((t) => (
              <div key={t.label}><span>{t.label}</span><span className={`sbar${limit && t.sec >= limit * 0.95 ? ' low' : ''}`}><i style={{ width: `${limit ? Math.min(100, (t.sec / limit) * 100) : 50}%` }} /></span><b>{t.sec}초</b></div>
            ))}</div></div>
        </div>
      </section>

      <h2 className="sec">표정과 긴장도 흐름 <small>카메라 영상의 표정을 분석한 결과</small></h2>
      <section className="card emo">
        <div className="emo-stats">
          <div><span className="hint">표정·긴장도 분석</span><b>준비 중</b></div>
          <div><span className="hint">시선 이탈 경고</span><b>{r.proctor.leave_count}회</b></div>
          <div><span className="hint">화면을 벗어난 시간</span><b>{Math.round(r.proctor.away_ms / 1000)}초</b></div>
        </div>
        <p className="fine" style={{ margin: 0 }}>표정과 긴장도 그래프는 아직 제공하지 않습니다. 카메라 영상은 저장하지 않고, 시선 이탈 경고 횟수만 기록합니다.</p>
      </section>

      <h2 className="sec">질문별 평가 <small>답변은 음성을 텍스트로 바꾼(STT) 원문입니다</small></h2>
      <section className="qeval">
        {r.questions.map((q, i) => (
          <details className="card qcard" id={`rq-${q.no}`} key={q.no} open={i === 0}>
            <summary><span className="qn">{q.label}</span>
              <span className="qq">{q.text}<small>{q.type_label}{q.answer?.seconds != null ? ` · 답변 ${q.answer.seconds}초` : ''}{q.followup ? ' · 꼬리질문 1개' : ''}</small></span>
              <b className="qs">{q.answer?.score != null ? `${q.answer.score}점` : '—'}</b></summary>
            <div className="qbody">
              {q.answer ? <AnswerBlock a={q.answer} /> : <p className="hint" style={{ margin: 0 }}>이 질문에는 답하지 않았습니다.</p>}
              {q.answer?.code && (
                <div className="toolres"><span className="hint">제출한 코드{q.answer.code_language ? ` · ${q.answer.code_language}` : ''}</span><pre className="codeview">{q.answer.code}</pre></div>
              )}
              {q.answer && <Feedback a={q.answer} />}
              {q.followup && (
                <div className="fucard">
                  <div className="fuh"><span className="pill none">꼬리질문 {q.followup.label}</span><b>{q.followup.text}</b>
                    <span className="qs">{q.followup.answer.score != null ? `${q.followup.answer.score}점` : '—'}</span></div>
                  {q.followup.why && <p className="why"><span className="hint">AI가 이 질문을 한 이유</span>{q.followup.why}</p>}
                  <AnswerBlock a={q.followup.answer} label="답변 원문" />
                  <Feedback a={q.followup.answer} />
                </div>
              )}
            </div>
          </details>
        ))}
      </section>

      {!recruiter && <DataCard aid={aid} r={r} />}
      <p className="sample-note">{r.notice}</p>
    </div>
  );
}
