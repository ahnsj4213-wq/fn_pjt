import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { fetchInterviewSummary } from '@/api/candidate/interview';
import { QueryState } from '@/shared/ui/extra';

const POLL_MS = 5_000;
const EXPECTED_MS = 20_000; // "20초 안팎": the bar moves with time while the evaluation job runs (GPU evaluation lane)
const GEN_STEPS = ['답변 음성을 텍스트로 정리', '평가 항목별 채점', '답변 근거 확인', '총평과 우선순위 피드백 작성'];

/** SC-A07 면접 완료: what was submitted (counts only, no scores) and how far the report is. */
export function Done() {
  const { aid = '' } = useParams();
  const navigate = useNavigate();
  const [t0] = useState(() => Date.now());
  const [now, setNow] = useState(t0);
  const summary = useQuery({
    queryKey: ['interview-summary', aid], queryFn: () => fetchInterviewSummary(aid),
    refetchInterval: (q) => (q.state.data?.eval_status === 'DONE' ? false : POLL_MS),
  });
  const s = summary.data;
  const done = s?.eval_status === 'DONE';
  const held = s?.eval_status === 'ON_HOLD';

  useEffect(() => {
    if (done || held) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [done, held]);

  // PENDING waits in the queue (first step); RUNNING advances with time but never reaches the end before DONE.
  const f = done ? 1 : s?.eval_status === 'RUNNING' ? Math.min(0.92, 0.25 + ((now - t0) / EXPECTED_MS) * 0.67)
    : Math.min(0.2, ((now - t0) / EXPECTED_MS) * 0.5);
  const toReport = () => navigate(`/me/applications/${encodeURIComponent(aid)}/report`);
  const sec = s?.duration_sec ?? null;

  return (
    <div className="wrap"><div className="stub wide">
      <span className="stub-tag">면접 완료</span>
      <h1>면접이 완료되었습니다</h1>
      <p className="sub">
        {done ? '리포트가 완성되었습니다.'
          : '최종 리포트 작성까지 20초 안팎이 걸립니다. 이 화면을 열어 두면 리포트가 완성되는 대로 바로 바뀝니다.'}
      </p>
      {held ? (
        <div className="notice" role="status">평가가 지연되고 있습니다. 담당자가 확인한 뒤 다시 평가하며, 면접을 다시 볼 필요는 없습니다.</div>
      ) : (
        <div className="gen" aria-live="polite">
          <div className="genbar"><i style={{ width: `${f * 100}%` }} /></div>
          <ol className="gensteps">
            {GEN_STEPS.map((t, k) => (
              <li key={t} className={f >= (k + 1) / GEN_STEPS.length ? 'done' : f >= k / GEN_STEPS.length ? 'on' : ''}>{t}</li>
            ))}
          </ol>
        </div>
      )}
      <QueryState isPending={summary.isPending} error={summary.error} onRetry={() => summary.refetch()}>
        {() => (
          <div className="ready" data-testid="interview-summary">
            <div><span>공고</span><span>{s!.posting.company_name} · {s!.posting.title}</span></div>
            <div><span>답변한 질문</span><span>{s!.answered_main} / {s!.main_count ?? '-'} · 꼬리질문 {s!.followups_used}개</span></div>
            <div><span>면접관 끼어들기</span><span>{s!.interjections}회</span></div>
            <div><span>시선</span><span>이탈 경고 {s!.gaze_warnings}회{s!.gaze_suspected ? ' · 부정행위 의심 기록' : ''}</span></div>
            <div><span>결과 공유</span><span>{s!.share_result ? `${s!.posting.company_name}에 공유됨` : '연습 모드 (공유 안 함)'}</span></div>
            <div><span>소요 시간</span><span>{sec === null ? '-' : `${Math.floor(sec / 60)}분 ${sec % 60}초`}</span></div>
          </div>
        )}
      </QueryState>
      <p className="fine" style={{ marginBottom: 16 }}>
        면접 음성 원본은 분석이 끝나고 30일 뒤 자동으로 영구 삭제됩니다. 카메라 영상은 처음부터 저장하지 않습니다.
      </p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn btn-primary btn-lg" type="button" onClick={toReport} data-testid="go-result">
          {done ? '리포트 보기' : '기다리지 않고 리포트 보기'}
        </button>
        <button className="btn btn-lg" type="button" onClick={() => navigate('/')}>공고 목록으로</button>
      </div>
    </div></div>
  );
}
