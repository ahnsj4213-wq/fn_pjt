import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import { fetchGuide } from '@/api/candidate/interview';
import { Notice, Page, uiStyles as s } from '@/shared/ui/components';
import { QueryState } from '@/shared/ui/extra';

/** 면접 안내 (INT-01 before precheck): rules the candidate should know before the timer starts. */
export function Guide() {
  const { aid = '' } = useParams();
  const guide = useQuery({ queryKey: ['guide', aid], queryFn: () => fetchGuide(aid) });
  return (
    <Page title="AI 면접 안내" narrow>
      <QueryState isPending={guide.isPending} error={guide.error} onRetry={() => guide.refetch()}>
        {() => {
          const g = guide.data!;
          return (
            <>
              <ul>
                <li>질문은 모두 {g.main_count}개이며, 답변에 따라 꼬리질문이 최대 {g.followup_max_total}번 나올 수 있습니다.</li>
                <li>질문은 음성으로 읽어 드립니다. 다 읽은 뒤에 마이크가 켜지고 답변 시간이 시작됩니다.</li>
                <li>답변 시간은 기본 질문 {g.main_answer_sec}초, 꼬리질문 {g.followup_answer_sec}초입니다. {g.warning_sec}초 남으면 알려 드립니다.</li>
                <li>말을 멈춰도 답변은 끝나지 않습니다. 다 말했으면 [답변 완료]를 눌러 주세요.</li>
                <li>지난 질문으로 돌아갈 수 없습니다.</li>
                <li>카메라 화면은 본인 확인용으로만 보여지며 저장하거나 전송하지 않습니다.</li>
                {g.coding.enabled && (
                  <li>면접이 끝나면 바로 코딩테스트({g.coding.language ?? 'Python'}, {g.coding.time_limit_min}분)가 이어집니다.</li>
                )}
              </ul>
              <Notice>조용한 곳에서 이어폰 없이 스피커와 마이크를 켜고 진행해 주세요.</Notice>
              <div className={s.row}>
                <Link to={`/applications/${encodeURIComponent(aid)}/precheck`} data-testid="go-precheck">장비 점검 시작</Link>
                <Link to="/me/applications">나중에 하기</Link>
              </div>
            </>
          );
        }}
      </QueryState>
    </Page>
  );
}
