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
}

export const EMPTY_RESUME: ResumeContent = { name: '', education: [], career: [], projects: [], skills: [], certificates: [] };
