/**
 * SC-A06 answer tools: the live-coding editor. Nothing is executed; the last submission inside the
 * answer window is kept with the answer and read by the evaluation together with what was said.
 * Each tool exposes flush(): [답변 완료] and the deadline submit pending work before the turn closes.
 */
import { useQuery } from '@tanstack/react-query';
import { useImperativeHandle, useRef, useState, type Ref } from 'react';
import { fetchTurnProblem, submitTurnCode } from '@/api/candidate/interview';
import { describeError } from '@/shared/http/errors';
import { ProblemView } from '@/shared/ui/ProblemView';

export interface ToolHandle {
  flush(): Promise<void>;
}

const LANGS = [['python', 'Python'], ['java', 'Java'], ['javascript', 'JavaScript']] as const;
const START: Record<string, string> = {
  python: '# 여기에 코드를 작성하십시오\n',
  java: '// 여기에 코드를 작성하십시오\n',
  javascript: '// 여기에 코드를 작성하십시오\n',
};

const submitNote = (again: boolean) => (
  <><b>{again ? '다시 제출했습니다' : '제출했습니다'}</b> · 답변이 끝나면 AI가 코드를 읽고 평가합니다. 답변 시간 안에는 고쳐서 다시 제출할 수 있습니다.</>
);

/** The problem the question points at ("화면의 문제"): statement and examples, open by default, scrollable. */
function ProblemPanel({ aid, turnId }: { aid: string; turnId: string }) {
  const q = useQuery({ queryKey: ['turn-problem', aid, turnId], queryFn: () => fetchTurnProblem(aid, turnId), staleTime: Infinity, retry: 1 });
  return (
    <details className="problem-panel" open>
      <summary>{q.data ? `문제 · ${q.data.title}` : '문제'}</summary>
      <div className="problem-body" aria-label="문제 설명">
        {q.isPending && <p className="hint">문제를 불러오는 중입니다…</p>}
        {q.isError && (
          <p className="hint" role="alert">문제를 불러오지 못했습니다. <button className="link" type="button" onClick={() => void q.refetch()}>다시 불러오기</button></p>
        )}
        {q.data && <ProblemView statement={q.data.statement_md} examples={q.data.examples} />}
      </div>
    </details>
  );
}

/** Submit only while the answer is open (the server refuses earlier or after the grace period). */
export function CodeTool({ aid, turnId, canSubmit, graded = false, ref }: { aid: string; turnId: string; canSubmit: boolean; graded?: boolean; ref?: Ref<ToolHandle> }) {
  const [lang, setLang] = useState('python');
  const [code, setCode] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<number>(0); // number of successful submissions
  const [error, setError] = useState<string | null>(null);
  const lastSent = useRef<string | null>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const ln = useRef<HTMLPreElement>(null);
  const value = code[lang] ?? START[lang];

  const submit = async () => {
    try {
      await submitTurnCode(aid, turnId, lang, value);
      lastSent.current = lang + value;
      setSent((n) => n + 1);
      setError(null);
    } catch (e) {
      setError(describeError(e).message);
    }
  };
  useImperativeHandle(ref, () => ({
    flush: async () => { if (lastSent.current !== lang + value && value.trim() !== START[lang].trim()) await submit(); },
  }));

  return (
    <section className="tool code">
      <div className="tool-h">
        <b>코드 에디터</b>
        <select aria-label="언어" value={lang} onChange={(e) => setLang(e.target.value)}>
          {LANGS.filter(([v]) => !graded || v === 'python').map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <button className="btn btn-primary" type="button" disabled={!canSubmit} onClick={() => void submit()}>
          {sent ? '다시 제출' : '코드 제출'}
        </button>
      </div>
      <ProblemPanel aid={aid} turnId={turnId} />
      <div className="editor">
        <pre className="ln" ref={ln} aria-hidden="true">{value.split('\n').map((_, k) => k + 1).join('\n')}</pre>
        <textarea ref={ta} spellCheck={false} aria-label="코드 입력" value={value}
          onChange={(e) => setCode((c) => ({ ...c, [lang]: e.target.value }))}
          onScroll={(e) => { if (ln.current) ln.current.scrollTop = e.currentTarget.scrollTop; }}
          onKeyDown={(e) => {
            if (e.key !== 'Tab') return;
            e.preventDefault();
            const t = e.currentTarget;
            t.setRangeText('    ', t.selectionStart, t.selectionEnd, 'end');
            setCode((c) => ({ ...c, [lang]: t.value }));
          }} />
      </div>
      <div className="out" role="status">
        {error ?? (sent ? submitNote(sent > 1)
          : graded ? '이 공고는 Python만 지원합니다. 답변이 끝나면 코드를 실행해 테스트 통과율을 채점하고, AI가 효율성, 문제 해결 방식, 코드 품질을 평가합니다. 제출하지 않으면 답변이 끝날 때 자동으로 제출됩니다.'
          : '코드는 실행하지 않습니다. 제출하면 AI가 코드를 읽고 논리, 예외 처리, 가독성을 평가합니다. 제출하지 않으면 답변이 끝날 때 자동으로 제출됩니다.')}
      </div>
      <p className="fine" style={{ margin: 0 }}>음성은 받지 않습니다. 풀이 설명은 코드에 주석으로 적어 주십시오. 주석도 함께 평가됩니다. 생각할 시간부터 코드를 쓸 수 있습니다.</p>
    </section>
  );
}
