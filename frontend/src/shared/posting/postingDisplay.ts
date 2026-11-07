/** Small display helpers shared by the posting card, the detail page and the recruiter preview. */

const KST_DAY_MS = 24 * 60 * 60 * 1000;

function kstToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(new Date());
}

/** "D-3", "D-day" or "마감" for a KST calendar deadline (YYYY-MM-DD). */
export function dDay(deadline: string | null | undefined): string {
  if (!deadline) return '상시';
  const days = Math.round((Date.parse(deadline) - Date.parse(kstToday())) / KST_DAY_MS);
  if (days < 0) return '마감';
  return days === 0 ? 'D-day' : `D-${days}`;
}

/** Posting text written one item per line -> list items (SC-A03 주요 업무 · 자격 요건 · 우대 사항). */
export function lines(text: string | null | undefined): string[] {
  return (text ?? '').split('\n').map((l) => l.replace(/^[-•·*]\s*/, '').trim()).filter(Boolean);
}

/** Violet tones of the team palette; a company keeps one colour everywhere (no image: SC-R04 "기본 디자인"). */
const POSTER_COLORS = ['#5B2BB8', '#4A1FA0', '#7C4DDB', '#3F1F8C', '#6A3CC9', '#2F1A6B'];

export function posterColor(company: string): string {
  let h = 0;
  for (const ch of company) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  return POSTER_COLORS[h % POSTER_COLORS.length];
}

/** Poster background: the posting image when there is one, else the company colour. */
export function posterBackground(company: string, imageUrl: string | null | undefined): string {
  const color = posterColor(company);
  return imageUrl ? `${color} center / cover no-repeat url("${imageUrl}")` : color;
}
