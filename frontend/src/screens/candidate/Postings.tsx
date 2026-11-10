import { useQuery } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { fetchJobFamilies, fetchPostings, type PostingPublic } from '@/api/common/postings';
import { useSession } from '@/app/session';
import { useStartApply } from '@/app/useStartApply';
import { dDay } from '@/shared/posting/postingDisplay';
import { QueryState } from '@/shared/ui/extra';
import { GuideModal, shouldShowGuide } from './GuideModal';

const TOP_DEADLINES = 5;

/** The day part of a deadline, as the list shows it ("11.18"). */
const shortDate = (date: string) => date.slice(5).replace('-', '.');

/** SC-A02 메인 (team spec S-A01 `.careers`): job shortcuts, the posting rows, and a side column. */
export function Postings() {
  const { data: session } = useSession();
  const [family, setFamily] = useState('');
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [guide, setGuide] = useState(false);
  const families = useQuery({ queryKey: ['job-families'], queryFn: fetchJobFamilies });
  const list = useQuery({ queryKey: ['postings', family, q], queryFn: () => fetchPostings({ job_family: family, q }) });

  useEffect(() => {
    document.title = 'Huge';
    if (shouldShowGuide()) setGuide(true); // first visit opens the guide once (SC-A14)
  }, []);

  const total = (families.data ?? []).reduce((n, f) => n + f.count, 0);
  const label = family ? `${families.data?.find((f) => f.code === family)?.label ?? ''} 공고` : '채용 중인 공고';
  const items = (list.data ?? []).filter((p) => !p.has_applied); // a posting already applied to lives in 지원 현황, not here
  const closing = items.filter((p) => p.deadline_date).sort((a, b) => a.deadline_date!.localeCompare(b.deadline_date!)).slice(0, TOP_DEADLINES);
  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    setQ(search.trim());
  };

  return (
    <div className="careers">
      <aside className="fdrawer">
        <section className="side-card">
          <h2>직무 바로가기</h2>
          <div className="quick" role="group" aria-label="직무 카테고리">
            <button type="button" aria-pressed={family === ''} onClick={() => setFamily('')}>전체<span className="n">{total}</span></button>
            {(families.data ?? []).map((f) => (
              <button key={f.code} type="button" aria-pressed={family === f.code} onClick={() => setFamily(f.code)}>
                {f.label}<span className="n">{f.count}</span>
              </button>
            ))}
          </div>
        </section>
      </aside>

      <section className="clist">
        <h1>공고</h1>
        <form className="search" role="search" onSubmit={onSearch}>
          <input type="search" aria-label="공고명·회사명 검색" placeholder="공고명·회사명 검색" value={search}
            onChange={(e) => setSearch(e.target.value)} />
        </form>
        <p className="fine">
          공고의 직무·자격요건과 내 이력서를 바탕으로 AI가 실제 면접처럼 질문합니다.{' '}
          <button type="button" className="link" onClick={() => setGuide(true)}>처음이십니까? 이용 방법 보기</button>
        </p>
        {!session && (
          <p className="fine">채용담당자이십니까? <Link to="/login?tab=recruiter" className="link">기업회원으로 공고 등록하기</Link></p>
        )}
        <div className="clist-head"><h2>{label} {items.length}건</h2></div>
        <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
          {() => (
            <div data-testid="posting-list">
              {items.length === 0
                ? <p className="hint">{q ? '검색 결과가 없습니다' : '이 직무의 공고가 아직 없습니다'}</p>
                : items.map((p) => <PostingRow key={p.id} posting={p} />)}
            </div>
          )}
        </QueryState>
        <p className="sample-note">화면에 보이는 기업과 공고는 화면 설계용 예시입니다.</p>
      </section>

      <aside className="side-r">
        <section className="side-card">
          <h2>내 AI 면접</h2>
          {session ? (
            <>
              <p className="fine">응시 현황과 결과 리포트를 확인할 수 있습니다.</p>
              <Link to={session.user.role === 'CANDIDATE' ? '/me/applications' : '/'} className="btn btn-block">지원 현황 보기</Link>
            </>
          ) : (
            <>
              <p className="fine">로그인하면 응시 현황과 결과 리포트를 여기서 바로 볼 수 있습니다.</p>
              <Link to="/login" className="btn btn-block">로그인</Link>
            </>
          )}
        </section>
        <section className="side-card">
          <h2>마감 임박 TOP {TOP_DEADLINES}</h2>
          {closing.length === 0 ? <p className="fine">마감일이 있는 공고가 없습니다.</p> : (
            <ol className="top5">
              {closing.map((p) => (
                <li key={p.id}><b>{dDay(p.deadline_date)}</b><Link to={`/postings/${encodeURIComponent(p.id)}`}>{p.title}</Link></li>
              ))}
            </ol>
          )}
          <p className="fine">지금 목록에 나온 공고 기준입니다.</p>
        </section>
      </aside>

      {guide && <GuideModal onClose={() => setGuide(false)} />}
    </div>
  );
}

function PostingRow({ posting: p }: { posting: PostingPublic }) {
  const navigate = useNavigate();
  const startApply = useStartApply();
  const detail = `/postings/${encodeURIComponent(p.id)}`;
  const meta = [p.employment_type, p.location, p.headcount ? `${p.headcount}명` : null].filter(Boolean) as string[];
  return (
    // The empty parts of the row open the detail too; the links and buttons stay the keyboard targets.
    <article className="crow" onClick={(e) => { if (!(e.target as HTMLElement).closest('a,button')) navigate(detail); }}>
      <div className="crow-main">
        <span className="crow-co"><span className="mini-logo" aria-hidden="true">{p.company_name.slice(0, 2)}</span>{p.company_name}</span>
        <h3><Link to={detail} aria-label={`${p.title} 공고 보기`}>{p.title}</Link></h3>
        {meta.length > 0 && <ul className="crow-meta">{meta.map((m) => <li key={m}>{m}</li>)}</ul>}
        <span className="tags">
          <span className="tag">{p.job_family_label}</span>
          {p.experience_level && <span className="tag alt">{p.experience_level}</span>}
        </span>
      </div>
      <div className="crow-side">
        {p.deadline_date && <span className="crow-dday">마감 {dDay(p.deadline_date)} ({shortDate(p.deadline_date)})</span>}
        {p.has_applied
          ? <Link to="/me/applications" className="btn" aria-label={`${p.title} 지원 현황`}>지원 현황</Link>
          : <button type="button" className="btn btn-primary" aria-label={`${p.title} 지원하기`} onClick={() => startApply(p.id)}>지원하기</button>}
      </div>
    </article>
  );
}
