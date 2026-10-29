import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { withdrawAccount } from '@/api/candidate';
import { useSession } from '@/app/session';
import { describeError } from '@/shared/http/errors';
import { Notice, Page, TextField, uiStyles as s } from '@/shared/ui/components';
import { ConfirmButton } from '@/shared/ui/extra';

/**
 * 계정 · 회원 탈퇴 (USR-07, 8.5). Withdrawal deletes the account, the registered resume and every application's
 * interview content at once; consent and decision records stay without personal data (docs/data-retention.md 5절).
 */
export function Account() {
  const { data } = useSession();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const withdraw = useMutation({
    mutationFn: () => withdrawAccount(password),
    onSuccess: () => {
      qc.clear();
      navigate('/login?withdrawn=1', { replace: true });
    },
  });
  const err = withdraw.error ? describeError(withdraw.error) : null;

  return (
    <Page title="계정" narrow>
      <div className={s.card}>
        <p><strong>{data?.user.name}</strong></p>
      </div>
      <h2>회원 탈퇴</h2>
      <ul>
        <li>계정 정보(이름·이메일·연락처)와 등록 이력서·업로드한 파일을 바로 삭제합니다.</li>
        <li>지원한 공고의 이력서 사본, 면접 답변·음성, 코딩 제출, 평가 결과도 함께 삭제합니다.</li>
        <li>개인정보 동의 기록과 채용 결정 기록은 개인정보 없이 남습니다.</li>
        <li>면접이나 코딩테스트를 진행 중이면 끝낸 뒤 탈퇴할 수 있습니다.</li>
        <li>삭제한 데이터는 되돌릴 수 없습니다.</li>
      </ul>
      <form onSubmit={(e) => e.preventDefault()}>
        <TextField label="비밀번호 확인" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} error={err?.code === 'REAUTH_FAILED' ? err.message : undefined} />
        {err && err.code !== 'REAUTH_FAILED' && <Notice kind="error">{err.message}</Notice>}
        <ConfirmButton label="회원 탈퇴" confirmText="정말 탈퇴할까요? 삭제한 데이터는 되돌릴 수 없습니다."
          confirmLabel="탈퇴" onConfirm={() => withdraw.mutate()} busy={withdraw.isPending} disabled={!password} />
      </form>
    </Page>
  );
}
