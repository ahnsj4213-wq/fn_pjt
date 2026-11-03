import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { fetchJobFamilies, fetchPostings, type PostingPublic } from '@/api/common/postings';
import { useSession } from '@/app/session';
import { useStartApply } from '@/app/useStartApply';
import { Poster } from '@/shared/posting/PostingView';
import { QueryState } from '@/shared/ui/extra';
import { GuideModal, shouldShowGuide } from './GuideModal';

/** SC-A02 메인 (wireframe renderMain): category chips and a horizontal snap slider of posting posters. */
export function Postings() {
  const { data: session } = useSession();
  const [family, setFamily] = useState('');
  const [guide, setGuide] = useState(false);
  const families = useQuery({ queryKey: ['job-families'], queryFn: fetchJobFamilies });
  const list = useQuery({ queryKey: ['postings', family], queryFn: () => fetchPostings({ job_family: family }) });

  useEffect(() => {
    document.title = '모의면접';
    if (shouldShowGuide()) setGuide(true); // first visit opens the guide once (SC-A14)
  }, []);

  const total = (families.data ?? []).reduce((n, f) => n + f.count, 0);
  const label = family ? `${families.data?.find((f) => f.code === family)?.label ?? ''} 공고` : '채용 중인 공고';
  const items = list.data ?? [];

  return (
    <div className="wrap">
      <section className="hero">
        <h1>지원할 공고를 고르고, 그 회사 면접을 미리 연습하십시오</h1>
        <p>공고의 직무·자격요건과 내 이력서를 바탕으로 AI가 실제 면접처럼 질문합니다.</p>
        <p style={{ margin: '6px 0 0' }}>
          <button type="button" className="link" onClick={() => setGuide(true)}>처음이십니까? 이용 방법 보기</button>
        </p>
        {!session && (
          <p className="fine" style={{ margin: '4px 0 0' }}>
            채용담당자이십니까? <Link to="/login?tab=recruiter" className="link">기업회원으로 공고 등록하기</Link>
          </p>
        )}
      </section>

      <div className="chips" role="group" aria-label="직무 카테고리">
        <button type="button" className="chip" aria-pressed={family === ''} onClick={() => setFamily('')}>
          전체<span className="n">{total}</span>
        </button>
        {(families.data ?? []).map((f) => (
          <button key={f.code} type="button" className="chip" aria-pressed={family === f.code} onClick={() => setFamily(f.code)}>
            {f.label}<span className="n">{f.count}</span>
          </button>
        ))}
      </div>

      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => <Slider key={family} label={label} items={items} />}
      </QueryState>
      <p className="sample-note">화면에 보이는 기업과 공고는 화면 설계용 예시입니다.</p>

      {guide && <GuideModal onClose={() => setGuide(false)} />}
    </div>
  );
}

/** Slider head, track and page dots: pages follow the scroll position (wireframe setupSlider). */
function Slider({ label, items }: { label: string; items: PostingPublic[] }) {
  const track = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ page: 0, pages: 1, atStart: true, atEnd: true });

  const paint = useCallback(() => {
    const t = track.current;
    if (!t || !t.clientWidth) return;
    const pages = Math.max(1, Math.ceil((t.scrollWidth - 4) / t.clientWidth));
    setPos({
      pages,
      page: Math.min(Math.round(t.scrollLeft / t.clientWidth), pages - 1),
      atStart: t.scrollLeft <= 2,
      atEnd: t.scrollLeft + t.clientWidth >= t.scrollWidth - 2,
    });
  }, []);

  useEffect(() => {
    paint();
    window.addEventListener('resize', paint);
    return () => window.removeEventListener('resize', paint);
  }, [paint, items]);

  const by = (dir: number) => track.current?.scrollBy({ left: dir * track.current.clientWidth });

  return (
    <>
      <div className="slider-head">
        <h2>{label}</h2>
        <span className="count">{items.length}건</span>
        <div className="arrows">
          <button type="button" className="arrow" aria-label="이전 공고" disabled={pos.atStart} onClick={() => by(-1)}>‹</button>
          <button type="button" className="arrow" aria-label="다음 공고" disabled={pos.atEnd} onClick={() => by(1)}>›</button>
        </div>
      </div>
      <div className="track" ref={track} onScroll={() => requestAnimationFrame(paint)} data-testid="posting-list">
        {items.length === 0
          ? <p className="hint">이 직무의 공고가 아직 없습니다</p>
          : items.map((p) => <PostingCard key={p.id} posting={p} />)}
      </div>
      <div className="dots">
        {pos.pages > 1 && Array.from({ length: pos.pages }, (_, i) => (
          <button key={i} type="button" className={`dot${i === pos.page ? ' on' : ''}`} aria-label={`${i + 1}번째 묶음`}
            onClick={() => track.current?.scrollTo({ left: i * track.current.clientWidth })} />
        ))}
      </div>
    </>
  );
}

function PostingCard({ posting: p }: { posting: PostingPublic }) {
  const navigate = useNavigate();
  const startApply = useStartApply();
  const detail = `/postings/${encodeURIComponent(p.id)}`;
  const meta = [p.location, p.employment_type, p.headcount ? `모집 ${p.headcount}명` : null].filter(Boolean) as string[];
  return (
    // The empty parts of the poster open the detail too; the two buttons stay the keyboard targets.
    <div className="poster" onClick={(e) => { if (!(e.target as HTMLElement).closest('a,button')) navigate(detail); }}>
      <Poster posting={p}>
        <span className="p-acts">
          <Link to={detail} className="pbtn" aria-label={`${p.title} 공고 보기`}>공고 보기</Link>
          {p.has_applied
            ? <Link to="/me/applications" className="pbtn pri" aria-label={`${p.title} 지원 현황`}>지원 현황</Link>
            : <button type="button" className="pbtn pri" aria-label={`${p.title} 지원하기`} onClick={() => startApply(p.id)}>지원하기</button>}
        </span>
      </Poster>
      <span className="poster-meta">
        {meta.flatMap((m, i) => (i > 0 ? ['·', <span key={m}>{m}</span>] : [<span key={m}>{m}</span>]))}
      </span>
    </div>
  );
}
