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

/** A virtual test posting has no closing date (none is invented): it reads "테스트용", never a real-looking "상시". */
export function deadlineLabel(p: { deadline_date: string | null; is_virtual?: boolean }): string {
  if (!p.deadline_date && p.is_virtual) return '테스트용 · 마감일 없음';
  return dDay(p.deadline_date);
}

export const VIRTUAL_NOTICE =
  '테스트용 가상 공고입니다. 이 회사가 실제로 게시한 채용공고가 아니며, 지원해도 실제 채용으로 이어지지 않습니다. ' +
  '면접 질문은 아래 공식 기술 블로그 글을 자료로 만듭니다.';

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
