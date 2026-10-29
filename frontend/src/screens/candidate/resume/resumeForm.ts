import { z } from 'zod';
import type { ResumeContent } from '@/api/common/resume';

const max = (n: number) => z.string().max(n, `${n}자 이하로 입력해 주세요.`);
const required = (n: number, msg: string) => z.string().trim().min(1, msg).max(n, `${n}자 이하로 입력해 주세요.`);

/** Same limits as backend ResumeContent (TRD 04 1.3). RES-06 minimum is checked separately. */
export const resumeFormSchema = z.object({
  name: required(50, '성명을 입력해 주세요.'),
  education: z.array(z.object({ school: required(200, '학교명을 입력해 주세요.'), major: max(200), degree: max(100), period: max(100) }))
    .max(20, '학력은 20건까지 입력할 수 있습니다.'),
  career: z.array(z.object({ company: required(200, '회사명을 입력해 주세요.'), role: max(200), period: max(100), description: max(3000) }))
    .max(30, '경력은 30건까지 입력할 수 있습니다.'),
  projects: z.array(z.object({ title: required(200, '프로젝트명을 입력해 주세요.'), role: max(200), period: max(100), description: max(3000) }))
    .max(30, '프로젝트는 30건까지 입력할 수 있습니다.'),
  certificates: z.array(z.object({ name: required(200, '자격증명을 입력해 주세요.'), issuer: max(200), date: max(50) }))
    .max(30, '자격증은 30건까지 입력할 수 있습니다.'),
  skillsText: z.string().max(5000),
});

export type ResumeForm = z.infer<typeof resumeFormSchema>;

export const RES06_MESSAGE = '성명과 학력 또는 경력을 1건 이상 입력해 주세요.'; // RES-06 (backend RESUME_MIN_FIELDS)

export function meetsMinimum(f: Pick<ResumeForm, 'name' | 'education' | 'career'>): boolean {
  return f.name.trim().length > 0 && (f.education.length > 0 || f.career.length > 0);
}

const s = (v: string | null | undefined) => v ?? '';
const n = (v: string) => (v.trim() === '' ? null : v.trim());

export function toForm(c: ResumeContent): ResumeForm {
  return {
    name: c.name ?? '',
    education: c.education.map((e) => ({ school: e.school, major: s(e.major), degree: s(e.degree), period: s(e.period) })),
    career: c.career.map((e) => ({ company: e.company, role: s(e.role), period: s(e.period), description: s(e.description) })),
    projects: c.projects.map((e) => ({ title: e.title, role: s(e.role), period: s(e.period), description: s(e.description) })),
    certificates: c.certificates.map((e) => ({ name: e.name, issuer: s(e.issuer), date: s(e.date) })),
    skillsText: c.skills.join(', '),
  };
}

export function toContent(f: ResumeForm): ResumeContent {
  return {
    name: f.name.trim(),
    education: f.education.map((e) => ({ school: e.school.trim(), major: n(e.major), degree: n(e.degree), period: n(e.period) })),
    career: f.career.map((e) => ({ company: e.company.trim(), role: n(e.role), period: n(e.period), description: n(e.description) })),
    projects: f.projects.map((e) => ({ title: e.title.trim(), role: n(e.role), period: n(e.period), description: n(e.description) })),
    certificates: f.certificates.map((e) => ({ name: e.name.trim(), issuer: n(e.issuer), date: n(e.date) })),
    skills: f.skillsText.split(',').map((x) => x.trim()).filter(Boolean).slice(0, 100),
  };
}
