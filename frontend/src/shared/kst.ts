/** Korea time helpers for the calendar: the server stores UTC, people think in KST. */
const fmt = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

export function kstParts(iso: string): { ymd: string; hm: string } {
  const p = Object.fromEntries(fmt.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return { ymd: `${p.year}-${p.month}-${p.day}`, hm: `${p.hour}:${p.minute}` };
}

/** '2026-11-11' + '14:00' typed in Korea -> an ISO instant. */
export const fromKst = (ymd: string, hm: string) => new Date(`${ymd}T${hm || '00:00'}:00+09:00`).toISOString();

export const todayKst = () => kstParts(new Date().toISOString()).ymd;
