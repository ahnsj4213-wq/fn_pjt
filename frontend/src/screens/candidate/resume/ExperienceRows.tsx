import type { Career, Project } from '@/api/common/resume';

/** Boxes of one career or project row; every field a string while it is being typed. */
export interface CareerRow { company: string; role: string; period: string; description: string }
export interface ProjectRow { title: string; role: string; period: string; description: string }

const DESC_MAX = 3000; // backend Career/Project.description
const s = (x: string | null | undefined) => x ?? '';
const orNull = (x: string) => x.trim() || null;

export const toCareerRows = (xs: Career[]): CareerRow[] =>
  xs.map((c) => ({ company: c.company, role: s(c.role), period: s(c.period), description: s(c.description) }));
export const toProjectRows = (xs: Project[]): ProjectRow[] =>
  xs.map((p) => ({ title: p.title, role: s(p.role), period: s(p.period), description: s(p.description) }));
/** Rows without a name are left out (the API needs company/title). */
export const fromCareerRows = (rows: CareerRow[]): Career[] => rows.filter((r) => r.company.trim()).map((r) => ({
  company: r.company.trim(), role: orNull(r.role), period: orNull(r.period), description: orNull(r.description) }));
export const fromProjectRows = (rows: ProjectRow[]): Project[] => rows.filter((r) => r.title.trim()).map((r) => ({
  title: r.title.trim(), role: orNull(r.role), period: orNull(r.period), description: orNull(r.description) }));

interface Props {
  idPrefix: string;
  career: CareerRow[];
  projects: ProjectRow[];
  onCareer: (rows: CareerRow[]) => void;
  onProjects: (rows: ProjectRow[]) => void;
  /** 경력 only for an experienced candidate's resume; a newcomer's has projects only (2026-11-16). */
  showCareer: boolean;
}

/** 경력 and 프로젝트·경험, one block of boxes per row, so each can be checked against the original file beside it. */
export function ExperienceRows({ idPrefix, career, projects, onCareer, onProjects, showCareer }: Props) {
  const put = <T,>(rows: T[], i: number, patch: Partial<T>) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r));
  const drop = <T,>(rows: T[], i: number) => rows.filter((_, j) => j !== i);
  return (
    <>
      {showCareer && <fieldset className="rows">
        <legend>경력 <span className="hint">회사마다 하나씩</span></legend>
        {career.map((r, i) => {
          const id = `${idPrefix}-c${i}`;
          return (
            <div className="row-box" key={i}>
              <div className="row3">
                <div className="field"><label htmlFor={`${id}-co`}>회사명</label>
                  <input id={`${id}-co`} value={r.company} onChange={(e) => onCareer(put(career, i, { company: e.target.value }))} /></div>
                <div className="field"><label htmlFor={`${id}-ro`}>직무</label>
                  <input id={`${id}-ro`} value={r.role} onChange={(e) => onCareer(put(career, i, { role: e.target.value }))} /></div>
                <div className="field"><label htmlFor={`${id}-pe`}>기간</label>
                  <input id={`${id}-pe`} value={r.period} onChange={(e) => onCareer(put(career, i, { period: e.target.value }))} /></div>
              </div>
              <div className="field"><label htmlFor={`${id}-de`}>담당 업무·성과</label>
                <textarea id={`${id}-de`} value={r.description} maxLength={DESC_MAX}
                  onChange={(e) => onCareer(put(career, i, { description: e.target.value }))} />
                <small className="hint">{r.description.length.toLocaleString()} / {DESC_MAX.toLocaleString()}자</small></div>
              <button className="btn btn-sm row-del" type="button" onClick={() => onCareer(drop(career, i))}
                aria-label={`경력 ${i + 1} 삭제`}>삭제</button>
            </div>
          );
        })}
        <button className="btn btn-sm" type="button" style={{ justifySelf: 'start' }}
          onClick={() => onCareer([...career, { company: '', role: '', period: '', description: '' }])}>+ 경력 추가</button>
      </fieldset>}
      <fieldset className="rows">
        <legend>프로젝트·경험 <span className="hint">프로젝트마다 하나씩</span></legend>
        {projects.map((r, i) => {
          const id = `${idPrefix}-p${i}`;
          return (
            <div className="row-box" key={i}>
              <div className="row3">
                <div className="field"><label htmlFor={`${id}-ti`}>프로젝트명</label>
                  <input id={`${id}-ti`} value={r.title} onChange={(e) => onProjects(put(projects, i, { title: e.target.value }))} /></div>
                <div className="field"><label htmlFor={`${id}-ro`}>역할</label>
                  <input id={`${id}-ro`} value={r.role} onChange={(e) => onProjects(put(projects, i, { role: e.target.value }))} /></div>
                <div className="field"><label htmlFor={`${id}-pe`}>기간</label>
                  <input id={`${id}-pe`} value={r.period} onChange={(e) => onProjects(put(projects, i, { period: e.target.value }))} /></div>
              </div>
              <div className="field"><label htmlFor={`${id}-de`}>내용·문제 해결·결과</label>
                <textarea id={`${id}-de`} value={r.description} maxLength={DESC_MAX}
                  onChange={(e) => onProjects(put(projects, i, { description: e.target.value }))} />
                <small className="hint">{r.description.length.toLocaleString()} / {DESC_MAX.toLocaleString()}자</small></div>
              <button className="btn btn-sm row-del" type="button" onClick={() => onProjects(drop(projects, i))}
                aria-label={`프로젝트 ${i + 1} 삭제`}>삭제</button>
            </div>
          );
        })}
        <button className="btn btn-sm" type="button" style={{ justifySelf: 'start' }}
          onClick={() => onProjects([...projects, { title: '', role: '', period: '', description: '' }])}>+ 프로젝트 추가</button>
      </fieldset>
    </>
  );
}
