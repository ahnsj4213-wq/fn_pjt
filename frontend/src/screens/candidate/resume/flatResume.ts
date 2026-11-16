import type { DesiredJob, ExperienceLevel, ResumeContent } from '@/api/common/resume';
import { fromCareerRows, fromProjectRows, toCareerRows, toProjectRows, type CareerRow, type ProjectRow } from './ExperienceRows';

/**
 * The SC-A10 resume form: 이름, 희망 직무, 최종 학력, 경력 단계, 보유 기술, 경력 (one block per company), 프로젝트·경험
 * (one block per project), 자기소개서. The stored resume keeps its standard sections (RES-03): an education or skill box the
 * candidate did not touch keeps the structured rows it came from; career and project rows are edited one by one.
 */
export interface FlatResume {
  name: string;
  desired_job: DesiredJob | '';
  edu: string;
  experience_level: ExperienceLevel | '';
  skill: string;
  career: CareerRow[];
  projects: ProjectRow[];
  intro: string;
}

const join = (xs: (string | null | undefined)[], sep = ' ') => xs.map((x) => x?.trim()).filter(Boolean).join(sep);

export function toFlat(c: ResumeContent): FlatResume {
  return {
    name: c.name,
    desired_job: c.desired_job ?? '',
    edu: c.education.map((e) => join([e.school, e.major, e.degree, e.period])).join(', '),
    experience_level: c.experience_level ?? (c.career.length ? '' : 'NEW'),
    skill: c.skills.join(', '),
    career: toCareerRows(c.career),
    projects: toProjectRows(c.projects),
    intro: c.intro ?? '',
  };
}

export function fromFlat(f: FlatResume, base: ResumeContent): ResumeContent {
  const was = toFlat(base);
  const changed = (k: 'edu' | 'skill') => f[k].trim() !== was[k].trim();
  return {
    ...base,
    name: f.name.trim(),
    desired_job: f.desired_job || null,
    experience_level: f.experience_level || null,
    intro: f.intro.trim() || null,
    education: !changed('edu') ? base.education
      : f.edu.split(',').map((s) => s.trim()).filter(Boolean).map((school) => ({ school })),
    career: fromCareerRows(f.career),
    projects: fromProjectRows(f.projects),
    skills: !changed('skill') ? base.skills : f.skill.split(',').map((s) => s.trim()).filter(Boolean),
  };
}

/** RES-06: name and at least one education or career row (the API checks it again). */
export function flatMissing(c: ResumeContent): string | null {
  if (!c.name) return '이름을 입력해 주십시오';
  if (!c.education.length && !c.career.length) return '최종 학력이나 경력을 한 줄 이상 입력해 주십시오';
  return null;
}
