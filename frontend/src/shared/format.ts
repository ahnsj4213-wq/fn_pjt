/** Display formatting. Dates are shown in KST; scores keep the server's decimal strings (OQ-10). */

const KST = 'Asia/Seoul';

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-';
  // Plain dates (YYYY-MM-DD, e.g. deadline_date) are already KST calendar dates.
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split('-');
    return `${y}. ${Number(m)}. ${Number(d)}.`;
  }
  return new Intl.DateTimeFormat('ko-KR', { timeZone: KST, year: 'numeric', month: 'numeric', day: 'numeric' }).format(new Date(iso));
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: KST, year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(iso));
}

/**
 * "87.35" -> "87.4" with HALF_UP, using string arithmetic so binary floating point never rounds wrong
 * (screen shows one decimal, OQ-10). TODO(CP-6): use the server's display value once it is sent.
 */
export function formatScore(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!m) return value;
  const [, sign, intPart, frac = ''] = m;
  const digits = (frac + '00').slice(0, 2);
  let tenths = Number(intPart) * 10 + Number(digits[0]);
  if (Number(digits[1]) >= 5) tenths += 1;
  return `${sign}${Math.floor(tenths / 10)}.${tenths % 10}`;
}

export function formatMs(ms: number): string {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
}
