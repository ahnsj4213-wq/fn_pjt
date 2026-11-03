import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { DialogHost } from '@/shared/ui/overlay';

const HIDE_KEY = 'guideHidden';

/** Scenes and drawings of the wireframe guide popup (openGuide). */
const GUIDE: { t: string; d: string; art: ReactNode }[] = [
  {
    t: '공고 고르기',
    d: '직무 카테고리를 고르면 그 직무의 공고만 보입니다. 공고 이미지를 눌러 주요 업무와 자격요건을 확인하십시오.',
    art: (
      <>
        <rect className="g2" x="20" y="34" width="56" height="70" rx="8" /><rect className="g1" x="92" y="24" width="56" height="80" rx="8" /><rect className="g2" x="164" y="34" width="56" height="70" rx="8" />
        <rect className="g3" x="100" y="34" width="18" height="18" rx="4" /><rect className="g3" x="100" y="64" width="38" height="6" rx="3" /><rect className="g3" x="100" y="76" width="28" height="6" rx="3" />
        <rect className="g4" x="60" y="114" width="40" height="12" rx="6" /><rect className="g5" x="106" y="114" width="40" height="12" rx="6" /><rect className="g4" x="152" y="114" width="40" height="12" rx="6" />
      </>
    ),
  },
  {
    t: '이력서 작성하기',
    d: '이력서를 한 번 써 두면 모든 공고에 쓰입니다. AI가 공고와 이력서를 함께 보고 맞춤 질문을 만듭니다.',
    art: (
      <>
        <rect className="g2" x="70" y="14" width="100" height="116" rx="8" /><circle className="g1" cx="96" cy="40" r="12" /><rect className="g4" x="114" y="34" width="40" height="6" rx="3" /><rect className="g4" x="114" y="44" width="28" height="5" rx="2.5" />
        <rect className="g4" x="84" y="66" width="72" height="5" rx="2.5" /><rect className="g4" x="84" y="78" width="60" height="5" rx="2.5" /><rect className="g4" x="84" y="90" width="68" height="5" rx="2.5" /><rect className="g1" x="84" y="106" width="44" height="12" rx="6" />
      </>
    ),
  },
  {
    t: 'AI 모의면접 보기',
    d: 'AI 면접관이 질문하면, 생각할 시간 뒤 카운트가 끝나고 답변 녹화가 시작됩니다. 화면을 계속 바라봐 주십시오.',
    art: (
      <>
        <rect className="g2" x="16" y="14" width="208" height="116" rx="10" /><rect className="g6" x="166" y="22" width="50" height="36" rx="6" /><circle className="g3" cx="120" cy="56" r="20" /><rect className="g1" x="96" y="78" width="48" height="26" rx="10" />
        <rect className="g4" x="70" y="110" width="100" height="8" rx="4" /><rect className="g1" x="70" y="110" width="46" height="8" rx="4" /><rect className="g1" x="24" y="22" width="60" height="6" rx="3" />
      </>
    ),
  },
  {
    t: '결과 리포트 확인하기',
    d: '총점과 영역별 점수, 강점과 개선점 1~3순위, 질문별 답변 원문을 보고 다음 면접을 준비하십시오.',
    art: (
      <>
        <rect className="g2" x="40" y="14" width="160" height="116" rx="10" /><rect className="g1" x="56" y="30" width="36" height="22" rx="4" />
        <rect className="g4" x="104" y="34" width="80" height="6" rx="3" /><rect className="g4" x="104" y="46" width="56" height="5" rx="2.5" />
        <rect className="g4" x="56" y="70" width="128" height="7" rx="3.5" /><rect className="g1" x="56" y="70" width="100" height="7" rx="3.5" />
        <rect className="g4" x="56" y="86" width="128" height="7" rx="3.5" /><rect className="g1" x="56" y="86" width="84" height="7" rx="3.5" />
        <rect className="g4" x="56" y="102" width="128" height="7" rx="3.5" /><rect className="g5" x="56" y="102" width="62" height="7" rx="3.5" />
      </>
    ),
  },
];

/** True on the first visit until "다시 보지 않기" is checked (SC-A14). Per-browser convenience only. */
export function shouldShowGuide(): boolean {
  try {
    return localStorage.getItem(HIDE_KEY) !== '1';
  } catch {
    return false;
  }
}

/** SC-A14 이용 방법 안내: swipeable scenes; arrow keys, dots, 이전/다음 turn them. */
export function GuideModal({ onClose }: { onClose: () => void }) {
  const track = useRef<HTMLDivElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  const [i, setI] = useState(0);
  const [hide, setHide] = useState(!shouldShowGuide());
  const last = i === GUIDE.length - 1;

  const goTo = useCallback((n: number) => {
    const t = track.current;
    if (!t) return;
    const k = Math.max(0, Math.min(n, GUIDE.length - 1));
    const smooth = !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (typeof t.scrollTo === 'function') t.scrollTo({ left: k * t.clientWidth, behavior: smooth ? 'smooth' : 'auto' });
    setI(k);
  }, []);

  useEffect(() => {
    next.current?.focus();
  }, []);

  const onHide = (checked: boolean) => {
    setHide(checked);
    try {
      localStorage.setItem(HIDE_KEY, checked ? '1' : '0');
    } catch {
      // Storage blocked: the guide may open again next time.
    }
  };

  return (
    <DialogHost labelledBy="g-title" onClose={onClose}>
      <div className="gdialog" onKeyDown={(e) => {
        if (e.key === 'ArrowRight') goTo(i + 1);
        if (e.key === 'ArrowLeft') goTo(i - 1);
      }}>
        <div className="ghead">
          <span id="g-title">모의면접 이용 방법</span>
          <button type="button" className="icon-btn" onClick={onClose}>닫기</button>
        </div>
        <div className="gtrack" ref={track}
          onScroll={(e) => { const t = e.currentTarget; if (t.clientWidth) setI(Math.round(t.scrollLeft / t.clientWidth)); }}>
          {GUIDE.map((g, n) => (
            <section key={g.t} className="gslide" aria-label={`${n + 1} / ${GUIDE.length}`} aria-hidden={n !== i}>
              <svg viewBox="0 0 240 144" aria-hidden="true">{g.art}</svg>
              <span className="gstep">{n + 1} / {GUIDE.length}</span>
              <h3>{g.t}</h3>
              <p>{g.d}</p>
            </section>
          ))}
        </div>
        <div className="gdots">
          {GUIDE.map((g, n) => (
            <button key={g.t} type="button" className={`dot${n === i ? ' on' : ''}`} aria-label={`${n + 1}번째 안내`}
              onClick={() => goTo(n)} />
          ))}
        </div>
        <div className="gfoot">
          <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={hide} onChange={(e) => onHide(e.target.checked)} />다시 보지 않기
          </label>
          <span className="spacer" />
          <button type="button" className="btn" disabled={i === 0} onClick={() => goTo(i - 1)}>이전</button>
          <button type="button" className="btn btn-primary" ref={next} onClick={() => (last ? onClose() : goTo(i + 1))}>
            {last ? '시작하기' : '다음'}
          </button>
        </div>
      </div>
    </DialogHost>
  );
}
