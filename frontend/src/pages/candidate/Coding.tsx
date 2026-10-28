import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { fetchCoding, runCode, saveDraft, submitCode, type RunResult } from '@/api/candidate/interview';
import { FIXED } from '@/shared/copy/fixed';
import { CodeEditor } from '@/shared/editor/CodeEditor';
import { formatDateTime } from '@/shared/format';
import { ApiError } from '@/shared/http/client';
import { describeError } from '@/shared/http/errors';
import { createServerClock, displaySeconds } from '@/shared/time/serverClock';
import { Button, Notice, Page, uiStyles as ui } from '@/shared/ui/components';
import { ConfirmButton, PlainText, QueryState } from '@/shared/ui/extra';
import css from './candidate.module.css';
import { useHeartbeat, useTicker } from './interview/hooks';

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
          return (
            <div className={css.stack}>
              <div className={`${ui.row} ${ui.between}`}>
                <h2>{v.problem.title}</h2>
                {remaining !== null && (
                  <span className={`${css.timer} ${remaining <= WARN_MS ? css.timerWarn : ''}`} data-testid="coding-timer">
                    남은 시간 {clockText(remaining)}
                  </span>
                )}
              </div>
              {timeOver && <Notice kind="error">제한시간이 끝났습니다. 마지막으로 저장된 코드가 자동으로 제출됩니다.</Notice>}
              <div className={css.split}>
                <section className={css.stack} aria-label="문제">
                  <PlainText text={v.problem.statement_md} />
                  {v.problem.examples.map((ex, i) => (
                    <div key={i}>
                      <strong>예제 {i + 1}</strong>
                      <p className={ui.small}>입력</p>
                      <pre className={css.example}>{ex.input}</pre>
                      <p className={ui.small}>출력</p>
                      <pre className={css.example}>{ex.output}</pre>
                    </div>
                  ))}
                </section>
                <section className={css.stack} aria-label="코드">
                  <div className={css.editorWrap}>
                    <CodeEditor value={code!} onChange={setCode} readOnly={timeOver} label={`${v.language} 코드`} />
                  </div>
                  <p className={ui.small} data-testid="save-state">
                    {saveState.error ? <span className={css.fail}>{saveState.error}</span>
                      : saveState.at ? `자동 저장됨 ${formatDateTime(saveState.at)}` : '아직 저장되지 않았습니다'}
                  </p>
                  <div className={ui.row}>
                    <Button onClick={() => { setRun(null); exec.mutate(); }} busy={exec.isPending}
                      disabled={timeOver || !v.run_available} data-testid="run-code">실행</Button>
                    <ConfirmButton label="최종 제출" variant="primary" confirmText="제출하면 더 이상 고칠 수 없습니다. 제출할까요?"
                      confirmLabel="제출" onConfirm={() => submit.mutate()} busy={submit.isPending} disabled={timeOver} />
                  </div>
                  {!v.run_available && <Notice>지금은 코드 실행 환경을 사용할 수 없습니다. 작성과 제출은 할 수 있습니다.</Notice>}
                  {exec.error && <Notice kind="error">{describeError(exec.error).message}</Notice>}
                  {submit.error && <Notice kind="error">{describeError(submit.error).message}</Notice>}
                  {run && <RunOutput run={run} />}
                </section>
              </div>
            </div>
          );
        }}
      </QueryState>
    </Page>
  );
}

function RunOutput({ run }: { run: RunResult }) {
  if (run.verdict === 'ENV_ERROR') {
    return <Notice kind="error">실행 환경에 문제가 있어 실행하지 못했습니다. 잠시 후 다시 실행해 주세요.</Notice>;
  }
  return (
    <section className={css.stack} aria-label="실행 결과" data-testid="run-result">
      <p className={run.verdict === 'NORMAL' ? css.ok : css.fail}>
        {run.verdict === 'NORMAL' ? FIXED.RUN_OK : FIXED.RUN_FAIL}
        {run.duration_ms !== null && <span className={ui.muted}> ({run.duration_ms}ms)</span>}
      </p>
      {run.examples.map((ex, i) => (
        <div key={i}>
          <strong>예제 {i + 1} 출력</strong>
          <pre className={css.output}>{ex.stdout || ex.stderr || '(출력 없음)'}</pre>
        </div>
      ))}
      {run.examples.length === 0 && <pre className={css.output}>{run.stdout || run.stderr || '(출력 없음)'}</pre>}
      {run.truncated && <p className={ui.small}>출력이 길어 앞부분만 보여 줍니다.</p>}
    </section>
  );
}
