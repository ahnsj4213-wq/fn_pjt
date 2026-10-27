import type { ResumeContent } from '@/api/common/resume';
import { uiStyles as s } from '@/shared/ui/components';
import { PlainText } from '@/shared/ui/extra';

const join = (...parts: (string | null | undefined)[]) => parts.filter(Boolean).join(' · ');

/** Read-only standard-field resume (RES-08 snapshot, candidate preview). Plain text only. */
export function ResumeView({ resume }: { resume: ResumeContent }) {
  return (
    <div>
      <h3>성명</h3>
      <p>{resume.name}</p>
      <h3>학력</h3>
      {resume.education.length === 0 ? <p className={s.muted}>-</p> : (
        <ul>{resume.education.map((e, i) => <li key={i}>{join(e.school, e.major, e.degree, e.period)}</li>)}</ul>
      )}
      <h3>경력</h3>
      {resume.career.length === 0 ? <p className={s.muted}>-</p> : resume.career.map((c, i) => (
        <div key={i} className={s.card}>
          <strong>{join(c.company, c.role, c.period)}</strong>
          {c.description && <PlainText text={c.description} />}
        </div>
      ))}
      <h3>프로젝트</h3>
      {resume.projects.length === 0 ? <p className={s.muted}>-</p> : resume.projects.map((p, i) => (
        <div key={i} className={s.card}>
          <strong>{join(p.title, p.role, p.period)}</strong>
          {p.description && <PlainText text={p.description} />}
        </div>
      ))}
      <h3>기술 스택</h3>
      <p>{resume.skills.length ? resume.skills.join(', ') : '-'}</p>
      <h3>자격증</h3>
      {resume.certificates.length === 0 ? <p className={s.muted}>-</p> : (
        <ul>{resume.certificates.map((c, i) => <li key={i}>{join(c.name, c.issuer, c.date)}</li>)}</ul>
      )}
    </div>
  );
}
