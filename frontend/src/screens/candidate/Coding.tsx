import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { fetchCoding, runCode, saveDraft, submitCode, type CodingView, type RunResult } from '@/api/candidate/interview';
import { FIXED } from '@/shared/copy/fixed';
import { CodeEditor } from '@/shared/editor/CodeEditor';
import { formatDateTime } from '@/shared/format';
import { ApiError } from '@/shared/http/client';
import { describeError } from '@/shared/http/errors';
import { createServerClock, displaySeconds } from '@/shared/time/serverClock';
import { Button, Notice, Page } from '@/shared/ui/components';
import { ConfirmButton, QueryState } from '@/shared/ui/extra';
import { ProblemView } from '@/shared/ui/ProblemView';
import css from './coding.module.css';
import { useHeartbeat, useTicker } from './interview/hooks';

type Example = CodingView['problem']['examples'][number];

const AUTOSAVE_MS = 1_500;
const WARN_MS = 5 * 60_000;
const AFTER_DEADLINE_MS = 5_000; // the server auto-submits the last saved code (COD-05)

function clockText(ms: number): string {
  const s = displaySeconds(ms);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * 코딩테스트 (COD-01~06). [실행] runs the public examples and reports only normal/abnormal plus what the program
 * printed, never whether the answer is right (AC-15). Drafts autosave with an increasing revision so a late save
 * can never overwrite a newer one (D-11). The deadline is the server's; the timer only displays it.
 */
export function Coding() {
  const { aid = '' } = useParams();
  const navigate = useNavigate();
  const view = useQuery({ queryKey: ['coding', aid], queryFn: () => fetchCoding(aid), staleTime: Infinity, retry: false });
  const clock = useRef(createServerClock());
  const [code, setCode] = useState<string | null>(null);
  const revision = useRef(0);
  const saved = useRef<string | null>(null);
  const [saveState, setSaveState] = useState<{ at: string | null; error: string | null }>({ at: null, error: null });
  const [run, setRun] = useState<RunResult | null>(null);
  const [timeOver, setTimeOver] = useState(false);
  const [tab, setTab] = useState<'result' | 'cases'>('cases');

  useEffect(() => {
    const v = view.data;
    if (!v) return;
    clock.current.addOneWay(Date.parse(v.server_now));
    revision.current = v.draft.revision;
    saved.current = v.draft.code;
    setCode(v.draft.code);
    setSaveState({ at: v.draft.saved_at, error: null });
  }, [view.data]);

  useHeartbeat(aid, !!view.data && !timeOver);
  useTicker(!!view.data && !timeOver);

  const save = async (text: string) => {
    if (text === saved.current) return;
    revision.current += 1;
    try {
      const r = await saveDraft(aid, text, revision.current);
      revision.current = Math.max(revision.current, r.revision);
      saved.current = text;
      setSaveState({ at: r.saved_at, error: null });
    } catch (e) {
      if (e instanceof ApiError && e.code === 'TIME_OVER') setTimeOver(true);
      setSaveState((s) => ({ ...s, error: describeError(e).message }));
    }
  };

  useEffect(() => {
    if (code === null || timeOver) return;
    const t = setTimeout(() => void save(code), AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [code]); // eslint-disable-line react-hooks/exhaustive-deps

  const deadline = view.data ? Date.parse(view.data.deadline_at) : null;
  const remaining = deadline !== null ? clock.current.remainingMs(deadline) : null;

  useEffect(() => {
    if (remaining === 0 && !timeOver) {
      setTimeOver(true);
      if (code !== null) void save(code);
    }
  }, [remaining === 0]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!timeOver) return;
    const t = setTimeout(() => navigate(`/applications/${encodeURIComponent(aid)}/done`, { replace: true }), AFTER_DEADLINE_MS);
    return () => clearTimeout(t);
  }, [timeOver]); // eslint-disable-line react-hooks/exhaustive-deps

  const exec = useMutation({
    mutationFn: () => runCode(aid, code ?? ''),
    onSuccess: setRun,
  });
  const submit = useMutation({
    mutationFn: async () => {
      revision.current += 1;
      return submitCode(aid, code ?? '', revision.current);
    },
    onSuccess: () => navigate(`/applications/${encodeURIComponent(aid)}/done`, { replace: true }),
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'ALREADY_SUBMITTED') {
        navigate(`/applications/${encodeURIComponent(aid)}/done`, { replace: true });
      }
    },
  });

  // Not in CODING: interrupted -> the interview screen owns the resume gate; finished -> done.
  if (view.error instanceof ApiError && view.error.code === 'INVALID_STATE') {
    const current = view.error.extra.current;
    if (current === 'INTERRUPTED' || current === 'INTERVIEWING') {
      return <Navigate to={`/applications/${encodeURIComponent(aid)}/interview`} replace />;
    }
    if (current === 'SUBMITTED' || current === 'EXPIRED') {
      return <Navigate to={`/applications/${encodeURIComponent(aid)}/done`} replace />;
    }
  }

  return (
    <Page title="코딩테스트">
      <QueryState isPending={view.isPending || code === null} error={view.error} onRetry={() => view.refetch()}>
        {() => {
          const v = view.data!;
          const total = Date.parse(v.deadline_at) - Date.parse(v.started_at);
          const left = remaining !== null && total > 0 ? Math.max(0, Math.min(100, (remaining / total) * 100)) : 100;
          const warn = remaining !== null && remaining <= WARN_MS;
          return (
            <div className={css.shell}>
              <header className={css.bar}>
                {v.problem.level !== null && <span className={css.level} data-testid="coding-level">Lv.{v.problem.level}</span>}
                <h2 className={css.title}>{v.problem.title}</h2>
                <span className={css.chip}>Python 3</span>
                <span className={css.limits} data-testid="coding-limits">
                  실행 시간 {v.problem.time_limit_ms / 1000}초 · 메모리 {v.problem.memory_mb}MB
                </span>
                <span className={css.spacer} />
                <span className={saveState.error ? css.saveError : css.save} data-testid="save-state">
                  {saveState.error ?? (saveState.at ? `자동 저장됨 ${formatDateTime(saveState.at)}` : '아직 저장되지 않았습니다')}
                </span>
                {remaining !== null && (
                  <span className={`${css.timer} ${warn ? css.timerWarn : ''}`} data-testid="coding-timer">남은 시간 {clockText(remaining)}</span>
                )}
                <div className={css.track} role="progressbar" aria-label="남은 시간" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(left)}>
                  <div className={`${css.fill} ${warn ? css.fillWarn : ''}`} style={{ width: `${left}%` }} />
                </div>
              </header>

              <ol className={css.steps} aria-label="진행 상태">
                <li className={`${css.step} ${css.stepDone}`}>AI 면접 완료</li>
                <li className={`${css.step} ${css.stepNow}`} aria-current="step">코딩테스트 진행 중</li>
                <li className={css.step}>제출 후 자동 채점</li>
                <li className={css.step}>AI 면접 평가</li>
              </ol>

              {timeOver && <Notice kind="error">제한시간이 끝났습니다. 마지막으로 저장된 코드가 자동으로 제출됩니다.</Notice>}

              <div className={css.split}>
                <section className={css.problem} aria-label="문제 설명" data-testid="problem-panel" tabIndex={0}>
                  <ProblemView statement={v.problem.statement_md} examples={v.problem.examples} />
                </section>
                <section className={css.work} aria-label="풀이">
                  <div className={css.editor}>
                    <CodeEditor value={code!} onChange={setCode} readOnly={timeOver} label={`${v.language} 코드`} />
                  </div>
                  <div className={css.console}>
                    <div className={css.tabs} role="tablist" aria-label="실행 결과와 테스트케이스">
                      <button type="button" role="tab" aria-selected={tab === 'result'} className={`${css.tab} ${tab === 'result' ? css.tabOn : ''}`} onClick={() => setTab('result')}>실행 결과</button>
                      <button type="button" role="tab" aria-selected={tab === 'cases'} className={`${css.tab} ${tab === 'cases' ? css.tabOn : ''}`} onClick={() => setTab('cases')}>테스트케이스</button>
                    </div>
                    <div className={css.tabBody} role="tabpanel">
                      {tab === 'cases' ? <Cases examples={v.problem.examples} /> : (
                        <>
                          {!v.run_available && <Notice>지금은 코드 실행 환경을 사용할 수 없습니다. 작성과 제출은 할 수 있습니다.</Notice>}
                          {exec.isPending && <p className={css.hint}>예제로 실행하는 중입니다…</p>}
                          {exec.error && <Notice kind="error">{describeError(exec.error).message}</Notice>}
                          {!run && !exec.isPending && !exec.error && <p className={css.hint}>[실행]을 누르면 공개 예제로 돌린 출력을 보여 줍니다. 정답 여부는 알려 주지 않으니 기대 출력과 직접 비교해 보세요.</p>}
                          {run && <RunOutput run={run} examples={v.problem.examples} />}
                        </>
                      )}
                    </div>
                  </div>
                  <div className={css.actions}>
                    <ConfirmButton label="초기화" confirmText="작성한 코드를 지우고 처음 코드로 되돌릴까요?" confirmLabel="되돌리기"
                      onConfirm={() => setCode(v.problem.starter_code)} disabled={timeOver} />
                    <p className={`${css.hint} ${css.actionsHint}`}>최종 제출하면 공개·비공개 테스트로 자동 채점되고, 결과는 AI 면접 평가에 함께 반영됩니다.</p>
                    <Button onClick={() => { setRun(null); setTab('result'); exec.mutate(); }} busy={exec.isPending}
                      disabled={timeOver || !v.run_available} data-testid="run-code">실행</Button>
                    <ConfirmButton label="최종 제출" variant="primary" confirmText="제출하면 더 이상 고칠 수 없습니다. 제출할까요?"
                      confirmLabel="제출" onConfirm={() => submit.mutate()} busy={submit.isPending} disabled={timeOver} />
                  </div>
                  {submit.error && <Notice kind="error">{describeError(submit.error).message}</Notice>}
                </section>
              </div>
            </div>
          );
        }}
      </QueryState>
    </Page>
  );
}

function Io({ label, text, error = false }: { label: string; text: string; error?: boolean }) {
  return (
    <div>
      <div className={css.ioLabel}>{label}</div>
      <pre className={`${css.pre} ${error ? css.err : ''}`}>{text}</pre>
    </div>
  );
}

function Cases({ examples }: { examples: Example[] }) {
  return (
    <>
      <p className={css.hint}>[실행]에는 아래 공개 예제만 쓰입니다. 제출하면 더 크고 다양한 비공개 테스트로도 채점합니다.</p>
      {examples.map((ex, i) => (
        <div className={css.case} key={i}>
          <div className={css.caseHead}>예제 {i + 1}</div>
          <div className={css.ioRow}>
            <Io label="입력" text={ex.input.trimEnd()} />
            <Io label="기대 출력" text={ex.output.trimEnd()} />
          </div>
        </div>
      ))}
    </>
  );
}

/** What the program printed next to the published example, with no verdict: comparing them is the candidate's own step (AC-15). */
function RunOutput({ run, examples }: { run: RunResult; examples: Example[] }) {
  if (run.verdict === 'ENV_ERROR') {
    return <Notice kind="error">실행 환경에 문제가 있어 실행하지 못했습니다. 잠시 후 다시 실행해 주세요.</Notice>;
  }
  return (
    <div className={css.case} data-testid="run-result">
      <p className={run.verdict === 'NORMAL' ? css.ok : css.fail}>
        {run.verdict === 'NORMAL' ? FIXED.RUN_OK : FIXED.RUN_FAIL}
        {run.duration_ms !== null && <span className={css.limits}> ({run.duration_ms}ms)</span>}
      </p>
      {run.examples.map((ex, i) => (
        <div className={css.case} key={i}>
          <div className={css.caseHead}>예제 {i + 1}</div>
          <div className={css.ioRow}>
            <Io label="입력" text={ex.input.trimEnd()} />
            <Io label="기대 출력" text={examples[i]?.output.trimEnd() ?? ''} />
            <Io label="내 출력" text={ex.stdout || ex.stderr || '(출력 없음)'} error={!ex.stdout && !!ex.stderr} />
          </div>
        </div>
      ))}
      {run.examples.length === 0 && <pre className={css.pre}>{run.stdout || run.stderr || '(출력 없음)'}</pre>}
      {run.truncated && <p className={css.hint}>출력이 길어 앞부분만 보여 줍니다.</p>}
    </div>
  );
}
