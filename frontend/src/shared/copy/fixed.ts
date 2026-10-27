/**
 * PRD fixed wording. Screens and E2E tests import these constants so the text exists in one place
 * (design D 3.12). Values match backend/app/core/constants.py and the backend error messages.
 */
export const FIXED = {
  CAPACITY_FULL: '이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요.', // INT-13
  ANSWER_PROMPT: '답변해 주세요', // INT-05
  FOLLOWUP_BADGE: '꼬리질문', // INT-07
  NO_ANSWER: '답변 없음', // INT-06, INT-09
  INSUFFICIENT_EVIDENCE: '근거 부족', // EVL-02
  EVAL_ON_HOLD: '평가 보류', // EVL-06 (recruiter screens only)
  RUN_OK: '정상 작동', // COD-03
  RUN_FAIL: '비정상 작동', // COD-03
  CODING_ALL_PASSED: '정답을 모두 맞힘', // 6.6
  CODING_NOT_ALL_PASSED: '맞히지 못함', // 6.6 (literal PRD wording; reading is an open question)
  CANDIDATE_FEEDBACK_NOTICE: '이 피드백은 AI가 만든 참고 자료입니다', // RPT-04
  RECRUITER_AI_NOTICE: 'AI 결과는 참고 자료이며 최종 결정은 담당자가 합니다', // ADM-03
} as const;

/** INT-07: main-question progress such as "3 / 10". Follow-ups keep the same number. */
export function formatProgress(current: number, total = 10): string {
  return `${current} / ${total}`;
}

export const DECISION_LABEL = { UNDECIDED: '검토 전', PASS: '합격', FAIL: '불합격' } as const; // 7.3
export const POSTING_STATUS_LABEL = { DRAFT: '임시저장', OPEN: '진행 중', CLOSED: '마감' } as const; // 7.4

/** Standard UI messages ("what happened + what to do", design D 3.12). */
export const MESSAGES = {
  AUTH_FAILED: '이메일 또는 비밀번호가 올바르지 않습니다.', // USR-04 (same text as backend AUTH_FAILED)
  SESSION_EXPIRED: '로그인 시간이 만료되었습니다. 다시 로그인해 주세요.',
  ROLE_FORBIDDEN_CANDIDATE_AREA: '담당자 계정으로는 지원자 기능을 사용할 수 없습니다.', // USR-05
  ROLE_FORBIDDEN_RECRUITER_AREA: '담당자만 사용할 수 있는 기능입니다.',
  FORBIDDEN: '이 기능을 사용할 권한이 없습니다.',
  CSRF_REJECTED: '요청을 확인할 수 없습니다. 새로고침 후 다시 시도해 주세요.',
  NOT_FOUND: '요청한 정보를 찾을 수 없습니다.',
  VALIDATION: '입력값을 확인해 주세요.',
  TOO_MANY: '요청이 많습니다. 잠시 후 다시 시도해 주세요.',
  AI_UNAVAILABLE: 'AI 서버를 사용할 수 없어 지금은 면접을 시작할 수 없습니다. 잠시 후 다시 시도해 주세요.',
  SERVICE_UNAVAILABLE: '지금은 이 기능을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.',
  NETWORK: '서버에 연결할 수 없습니다. 네트워크를 확인한 뒤 다시 시도해 주세요.',
  TIMEOUT: '서버 응답이 늦어지고 있습니다. 잠시 후 다시 시도해 주세요.',
  SERVER_ERROR: '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.',
  EMAIL_UNAVAILABLE: '사용할 수 없는 이메일입니다.',
} as const;
