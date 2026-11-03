/** The applicant's answer to a mailed interview date, from the link in the mail (no login). */
import { apiFetch } from '@/shared/http/client';

export interface ScheduleInfo { company_name: string; title: string; starts_at: string; place: string | null; status: string }

export const fetchScheduleInfo = (token: string) => apiFetch<ScheduleInfo>(`/api/schedule-response?token=${encodeURIComponent(token)}`);
export const answerSchedule = (token: string, answer: 'ACCEPT' | 'RESCHEDULE') =>
  apiFetch<ScheduleInfo>('/api/schedule-response', { method: 'POST', body: { token, answer } });
