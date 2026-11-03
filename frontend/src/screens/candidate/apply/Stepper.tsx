const STEPS = ['환경 점검', '개인정보 동의 · 이력서', 'AI 면접'];

/** The three apply steps on top of SC-A04 and SC-A05. */
export function Stepper({ cur }: { cur: number }) {
  return (
    <ol className="stepper" aria-label="지원 단계">
      {STEPS.map((s, i) => (
        <li key={s} className={i < cur ? 'done' : i === cur ? 'on' : ''} aria-current={i === cur ? 'step' : undefined}>
          <span>{i + 1}</span>{s}
        </li>
      ))}
    </ol>
  );
}
