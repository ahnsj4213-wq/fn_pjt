import type { ReactNode } from 'react';
import type { PostingPublic } from '@/api/common/postings';
import { VIRTUAL_NOTICE, dDay, deadlineLabel, lines, posterBackground, posterColor } from './postingDisplay';

/** The posting poster of the wireframe (.poster-img): card on the main page, large image on the detail page. */
export function Poster({ posting: p, children }: { posting: PostingPublic; children?: ReactNode }) {
  return (
    <div className="poster-img" style={{ background: posterBackground(p.company_name, p.image_url) }}>
      <span className="dday">{p.is_virtual && !p.deadline_date ? '테스트용' : dDay(p.deadline_date)}</span>
      <span className="p-logo" style={{ color: posterColor(p.company_name) }} aria-hidden="true">{p.company_name.slice(0, 1)}</span>
      <span className="p-company">{p.company_name}</span>
      <span className="p-title">{p.title}</span>
      <span className="p-foot">
        <span className="p-tag">{p.job_family_label}</span>
        {p.experience_level && <span className="p-tag">{p.experience_level}</span>}
      </span>
      {children}
    </div>
  );
}

/**
 * What a candidate sees for a posting (SC-A03, wireframe postDetailHTML). The recruiter preview (SC-R06) renders the
 * same thing. All posting text is plain text.
 */
export function PostingView({ posting: p, tail }: { posting: PostingPublic; tail?: ReactNode }) {
  return (
    <div className="detail">
      <div><Poster posting={p} /></div>
      <div style={{ minWidth: 0 }}>
        <span className="d-company">{p.company_name}{p.is_virtual && <span className="virtual-badge">테스트용 가상 공고</span>}</span>
        <h1>{p.title}{!p.is_open && ' (마감)'}</h1>
        {p.is_virtual && <p className="virtual-notice" role="note" data-testid="virtual-notice">{VIRTUAL_NOTICE}</p>}
        <dl className="facts">
          <div><dt>경력</dt><dd>{p.experience_level || '—'}</dd></div>
          <div><dt>고용형태</dt><dd>{p.employment_type || '—'}</dd></div>
          <div><dt>근무지</dt><dd>{p.location || '—'}</dd></div>
          <div><dt>모집 인원</dt><dd>{p.headcount ? `${p.headcount}명` : '—'}</dd></div>
          <div><dt>마감</dt><dd>{p.deadline_date || p.is_virtual ? deadlineLabel(p) : '—'}</dd></div>
        </dl>
        <ListSection title="주요 업무" text={p.duties} />
        <ListSection title="자격 요건" text={p.requirements} />
        <ListSection title="우대 사항" text={p.preferred} />
        {p.question_areas.length > 0 && (
          <section className="section">
            <h2>이 공고의 면접 질문 분야</h2>
            <div className="qtypes">{p.question_areas.map((a) => <span key={a.code}>{a.label}</span>)}</div>
          </section>
        )}
        {p.is_virtual && <Sources sources={p.sources} />}
        {tail}
      </div>
    </div>
  );
}

function Sources({ sources }: { sources: PostingPublic['sources'] }) {
  return (
    <section className="section" data-testid="posting-sources">
      <h2>면접 자료 출처 (공식 기술 블로그)</h2>
      {sources.length === 0 ? <p className="hint">연결된 글이 없습니다.</p> : (
        <ul>
          {sources.map((s) => (
            <li key={s.url}>
              <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a>
              <span className="hint"> · {s.published_date ?? '게시일 정보 없음'}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ListSection({ title, text }: { title: string; text: string | null }) {
  const items = lines(text);
  return (
    <section className="section">
      <h2>{title}</h2>
      {items.length ? <ul>{items.map((l, i) => <li key={i}>{l}</li>)}</ul> : <p className="hint">—</p>}
    </section>
  );
}
