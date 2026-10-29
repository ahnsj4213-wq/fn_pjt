import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';
import { fetchJobFamilies, fetchPostings } from '@/api/common/postings';
import { formatDate } from '@/shared/format';
import { Page, TextField, uiStyles as s } from '@/shared/ui/components';
import { Badge, EmptyState, QueryState, SelectField } from '@/shared/ui/extra';

/** Public postings (JOB-07 search, JOB-08 job-family filter). */
export function Postings() {
  const [q, setQ] = useState('');
  const [family, setFamily] = useState('');
  const families = useQuery({ queryKey: ['job-families'], queryFn: fetchJobFamilies, staleTime: Infinity });
  const list = useQuery({ queryKey: ['postings', q, family], queryFn: () => fetchPostings({ q, job_family: family }) });

  return (
    <Page title="채용공고">
      <div className={s.row}>
        <TextField label="검색" value={q} onChange={(e) => setQ(e.target.value)} placeholder="공고 제목·회사" />
        <SelectField label="직무" value={family} onChange={(e) => setFamily(e.target.value)} placeholder="전체"
          options={(families.data ?? []).map((f) => ({ value: f.code, label: f.label }))} />
      </div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (list.data!.length === 0 ? <EmptyState>조건에 맞는 공고가 없습니다.</EmptyState> : (
          <ul className={s.cards} data-testid="posting-list">
            {list.data!.map((p) => (
              <li key={p.id} className={s.card}>
                <Link to={`/postings/${encodeURIComponent(p.id)}`}><strong>{p.title}</strong></Link>
                <p className={s.muted}>
                  {p.company_name} · {p.job_family_label} · 마감 {formatDate(p.deadline_date)}
                </p>
                {p.coding_enabled && <Badge kind="info">코딩테스트 포함</Badge>}
                {p.has_applied && <Badge>지원 완료</Badge>}
                {!p.is_open && <Badge kind="warn">마감</Badge>}
              </li>
            ))}
          </ul>
        ))}
      </QueryState>
    </Page>
  );
}
