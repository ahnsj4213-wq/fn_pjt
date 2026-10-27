/** Korean labels for server enum codes (PRD terms: 6.3, 7.1~7.4). Unknown codes fall back to the code. */

export const CRITERION_LABEL: Record<string, string> = {
  EXPERTISE: '직무 전문성',
  LOGIC: '논리성',
  SPECIFICITY: '구체성·관련성',
  DELIVERY: '전달력',
  expertise: '직무 전문성',
  logic: '논리성',
  specificity: '구체성·관련성',
  delivery: '전달력',
  coding: '코딩테스트',
};

export const CANDIDATE_CRITERIA = ['expertise', 'logic', 'specificity', 'delivery'] as const;

export const SLOT_LABEL: Record<string, string> = { PERSONALITY: '인성', RESUME: '이력서', COMPANY: '기업', JOB: '직무' };

export const ATTEMPT_LABEL: Record<string, string> = {
  READY: '응시 전',
  PREPARING: '준비 중',
  INTERVIEWING: '면접 중',
  CODING: '코딩 중',
  INTERRUPTED: '중단됨',
  SUBMITTED: '제출됨',
  EXPIRED: '만료됨',
};

/** Recruiter-side evaluation status (7.2). null = no evaluation yet (OQ-06). */
export const EVAL_LABEL: Record<string, string> = {
  PENDING: '평가 대기',
  RUNNING: '평가 중',
  DONE: '평가 완료',
  ON_HOLD: '평가 보류',
  NONE: '평가 없음',
};

export const SUBMISSION_KIND_LABEL: Record<string, string> = {
  MANUAL: '직접 제출',
  AUTO_TIMEOUT: '시간 만료 자동 제출',
  AUTO_EXPIRED: '공고 마감 자동 제출',
};

export function label(map: Record<string, string>, code: string | null | undefined, fallback = '-'): string {
  if (code === null || code === undefined) return fallback;
  return map[code] ?? code;
}
