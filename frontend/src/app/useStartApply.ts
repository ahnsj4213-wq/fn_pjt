import { useNavigate } from 'react-router';
import { useToast } from '@/shared/ui/overlay';
import { applyStartPath, setPendingApply, useSession } from './session';

export const NOT_FOR_COMPANY = '기업회원·운영자 계정으로는 지원할 수 없습니다';

/**
 * '지원하기' / 'AI 면접 지원하기' (SC-A02, SC-A03): logged out -> login with the "이어서 지원" notice, company or
 * operator account -> a notice and stay, candidate -> the first apply step.
 */
export function useStartApply(): (postingId: string) => void {
  const { data } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  return (postingId) => {
    if (!data) {
      setPendingApply(postingId);
      navigate('/login?apply=1');
      return;
    }
    if (data.user.role !== 'CANDIDATE') {
      toast(NOT_FOR_COMPANY);
      return;
    }
    navigate(applyStartPath(postingId));
  };
}
