/** Company notification inbox (backend app/recruiter/notifications.py, SC-R12). */
import { apiFetch } from '@/shared/http/client';

export interface NotificationItem {
  id: string;
  kind: 'DEADLINE' | 'APPLY' | 'SCHEDULE';
  text: string;
  created_at: string;
  unread: boolean;
  application_id: string | null;
  posting_id: string | null;
  posting_open: boolean | null;
  done_text: string | null;
}
export interface Inbox { unread: number; items: NotificationItem[] }

export const fetchInbox = () => apiFetch<Inbox>('/api/recruiter/notifications');
export const markAllRead = () => apiFetch<void>('/api/recruiter/notifications/read', { method: 'POST' });
export const extendPosting = (id: string) => apiFetch<{ done_text: string }>(`/api/recruiter/notifications/${encodeURIComponent(id)}/extend`, { method: 'POST' });
export const closePostingNow = (id: string) => apiFetch<{ done_text: string }>(`/api/recruiter/notifications/${encodeURIComponent(id)}/close`, { method: 'POST' });
