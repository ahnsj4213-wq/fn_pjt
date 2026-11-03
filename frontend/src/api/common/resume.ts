/** Standard resume fields (RES-03), shared by the candidate editor and the recruiter snapshot view.
 * Mirrors backend app/resumes/schemas.py ResumeContent (extra="forbid"). */
export interface Education {
  school: string;
  major?: string | null;
  degree?: string | null;
  period?: string | null;
}

export interface Career {
  company: string;
  role?: string | null;
  period?: string | null;
  description?: string | null;
}

export interface Project {
  title: string;
  role?: string | null;
  period?: string | null;
  description?: string | null;
}

export interface Certificate {
  name: string;
  issuer?: string | null;
  date?: string | null;
}

export interface ResumeContent {
  name: string;
  education: Education[];
  career: Career[];
  projects: Project[];
  skills: string[];
  certificates: Certificate[];
  /** SC-A10 form fields; kept on the resume, never sent to the AI. */
  desired_job?: DesiredJob | null;
  experience_level?: ExperienceLevel | null;
  intro?: string | null;
}

export type DesiredJob = 'DEV' | 'DATA_AI' | 'DESIGN' | 'MARKETING' | 'MGMT';
export type ExperienceLevel = 'NEW' | 'Y1_3' | 'Y3_5' | 'Y5_PLUS';
export const DESIRED_JOBS: [DesiredJob, string][] = [['DEV', '개발'], ['DATA_AI', '데이터·AI'], ['DESIGN', '디자인'],
  ['MARKETING', '마케팅'], ['MGMT', '경영지원']];
export const EXPERIENCE_LEVELS: [ExperienceLevel, string][] = [['NEW', '신입'], ['Y1_3', '1~3년'], ['Y3_5', '3~5년'],
  ['Y5_PLUS', '5년 이상']];

export const EMPTY_RESUME: ResumeContent = { name: '', education: [], career: [], projects: [], skills: [], certificates: [] };
