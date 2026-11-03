/**
 * What the SC-A04 check leaves for the next steps: which devices passed and which ones to use. Session storage only
 * (this tab), device ids are not personal data. The interview reopens the camera and mic with these ids.
 */
export interface DeviceCheck {
  at: number;
  cam: string | null;
  mic: string | null;
  spk: string | null;
}

const KEY = 'deviceCheck';
const VALID_MS = 30 * 60 * 1000; // the server keeps a media check for 30 minutes too (PRECHECK_VALID)

export function saveDeviceCheck(c: Omit<DeviceCheck, 'at'>): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...c, at: Date.now() }));
  } catch {
    // Storage blocked: the consent step sends the candidate back to the check.
  }
}

export function loadDeviceCheck(): DeviceCheck | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as DeviceCheck;
    return Date.now() - c.at < VALID_MS ? c : null;
  } catch {
    return null;
  }
}

export const applyPath = (pid: string, step: '' | 'consent' = '') =>
  `/postings/${encodeURIComponent(pid)}/apply${step ? `/${step}` : ''}`;
