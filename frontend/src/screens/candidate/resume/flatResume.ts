import type { DesiredJob, ExperienceLevel, ResumeContent } from '@/api/common/resume';

/**
 * The SC-A10 resume form (wireframe resumeFormHTML): 이름, 희망 직무, 최종 학력, 경력, 보유 기술, 주요 프로젝트·경험,
 * 자기소개서. The stored resume keeps its standard sections (RES-03): a box the candidate did not touch keeps the
 * structured rows it came from; a changed box is parsed back into rows.
 */
export interface FlatResume {
  name: string;
  desired_job: DesiredJob | '';
  edu: string;
  experience_level: ExperienceLevel | '';
  skill: string;
  proj: string;
  intro: string;
}

export const CAREER_PREFIX = '경력 · ';
const join = (xs: (string | null | undefined)[], sep = ' ') => xs.map((x) => x?.trim()).filter(Boolean).join(sep);

export function toFlat(c: ResumeContent): FlatResume {
  const careerLines = c.career.map((w) => {
    const head = CAREER_PREFIX + join([w.company, w.role, w.period], ' / ');
    return w.description ? `${head}: ${w.description}` : head;
  });
  const projectLines = c.projects.map((p) => (p.description ? `${p.title}: ${p.description}` : p.title));
  return {
    name: c.name,
    desired_job: c.desired_job ?? '',
    edu: c.education.map((e) => join([e.school, e.major, e.degree, e.period])).join(', '),
    experience_level: c.experience_level ?? (c.career.length ? '' : 'NEW'),
    skill: c.skills.join(', '),
    proj: [...careerLines, ...projectLines].join('\n'),
    intro: c.intro ?? '',
  };
}

function splitLine(l: string): [string, string | undefined] {
  const i = l.indexOf(':');
  return i > 0 ? [l.slice(0, i).trim(), l.slice(i + 1).trim() || undefined] : [l.trim(), undefined];
}

export function fromFlat(f: FlatResume, base: ResumeContent): ResumeContent {
  const was = toFlat(base);
  const changed = (k: 'edu' | 'skill' | 'proj') => f[k].trim() !== was[k].trim();
  let { career, projects } = base;
  if (changed('proj')) {
    const lines = f.proj.split('\n').map((l) => l.trim()).filter(Boolean);
    career = lines.filter((l) => l.startsWith(CAREER_PREFIX)).map((l) => {
      const [head, description] = splitLine(l.slice(CAREER_PREFIX.length));
      const [company, role, period] = head.split(' / ').map((x) => x.trim());
      return { company: company || head, role: role || null, period: period || null, description: description ?? null };
    });
    projects = lines.filter((l) => !l.startsWith(CAREER_PREFIX)).map((l) => {
      const [title, description] = splitLine(l);
      return { title, description: description ?? null };
    });
  }
  return {
    ...base,
    name: f.name.trim(),
    desired_job: f.desired_job || null,
    experience_level: f.experience_level || null,
    intro: f.intro.trim() || null,
    education: !changed('edu') ? base.education
      : f.edu.split(',').map((s) => s.trim()).filter(Boolean).map((school) => ({ school })),
    career,
    projects,
    skills: !changed('skill') ? base.skills : f.skill.split(',').map((s) => s.trim()).filter(Boolean),
  };
}

/** RES-06: name and at least one education or career row (the API checks it again). */
export function flatMissing(c: ResumeContent): string | null {
  if (!c.name) return '이름을 입력해 주십시오';
  if (!c.education.length && !c.career.length) return '최종 학력이나 경력을 한 줄 이상 입력해 주십시오';
  return null;
}
