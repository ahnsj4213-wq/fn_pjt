import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { fetchDashboard, fetchStats, fetchUpcoming, type DashboardPosting } from '@/api/recruiter';
import { kstParts } from '@/shared/kst';
import { KIND } from './Schedule';
import { QueryState } from '@/shared/ui/extra';

const STATUS: Record<string, [string, string]> = { OPEN: ['게시 중', 'good'], CLOSED: ['마감', 'none'], DRAFT: ['작성 중', 'hold'] };
const JOB: Record<string, string> = { DEV: '개발', DATA_AI: '데이터·AI', DESIGN: '디자인', MARKETING: '마케팅', MGMT: '경영지원' };
const monthLabel = (m: string) => `${Number(m.slice(5))}월`;

function BarChart({ rows, unit }: { rows: [string, number][]; unit: string }) {
  const max = Math.max(1, ...rows.map((r) => r[1]));
  const w = 360, h = 150, bw = w / Math.max(rows.length, 1);
  return (
    <div className="chart small">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label="월별 응시자 수">
        <line className="grid" x1="0" x2={w} y1={h - 22} y2={h - 22} />
        {rows.map(([label, v], i) => {
          const bh = Math.round(((h - 50) * v) / max);
          return (
            <g key={label}>
              <title>{`${label} ${v}${unit}`}</title>
              <rect className="bar" x={i * bw + bw * 0.2} width={bw * 0.6} y={h - 22 - bh} height={bh} rx="3" />
              <text className="val" x={i * bw + bw / 2} y={h - 26 - bh} textAnchor="middle">{v}</text>
              <text x={i * bw + bw / 2} y={h - 6} textAnchor="middle">{label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

const Kpi = ({ label, value, sub, up }: { label: string; value: string; sub: string; up?: boolean }) => (
  <div className="kpi"><span>{label}</span><b>{value}</b><small className={up ? 'up' : ''}>{sub}</small></div>
);

/** SC-R13 기업 관리 · 대시보드. */
export default function Home() {
  const dash = useQuery({ queryKey: ['recruiter', 'dashboard'], queryFn: fetchDashboard });
  const stats = useQuery({ queryKey: ['recruiter', 'stats'], queryFn: () => fetchStats() });
  const upcoming = useQuery({ queryKey: ['recruiter', 'schedule', 'upcoming'], queryFn: () => fetchUpcoming(4) });
  const now = new Date();
  return (
    <>
      <div className="ph"><div><h1>대시보드</h1><p>{now.getFullYear()}년 {now.getMonth() + 1}월 기준</p></div></div>
      <QueryState isPending={dash.isPending || stats.isPending} error={dash.error ?? stats.error}
        onRetry={() => { void dash.refetch(); void stats.refetch(); }}>
        {() => {
          const d = dash.data!, s = stats.data!;
          const diff = d.applicants_this_month - d.applicants_last_month;
          const posted = d.postings.filter((p: DashboardPosting) => p.applicants > 0);
          const passRate = new Map(s.pass_rate_by_posting.map((p) => [p.posting_id, p]));
          return (
            <>
              <div className="kpis">
                <Kpi label="이번 달 응시자" value={`${d.applicants_this_month}명`} up={diff > 0}
                  sub={diff === 0 ? '지난달과 같습니다' : `지난달보다 ${Math.abs(diff)}명 ${diff > 0 ? '늘었습니다' : '줄었습니다'}`} />
                <Kpi label="게시 중인 공고" value={`${d.open_postings}개`} sub={`조회수 합계 ${d.views_total.toLocaleString()}회`} />
                <Kpi label="결과 검토 대기" value={`${d.awaiting_review}명`} sub="면접을 마쳤고 아직 결정하지 않았습니다" />
                <Kpi label="면접 완료율" value={d.completion_rate == null ? '—' : `${d.completion_rate}%`} sub="지원자 중 면접을 끝까지 마친 비율" />
              </div>
              <div className="grid2">
                <div className="card"><h2>공고별 합격률 <small>합격 ÷ 담당자가 결정한 응시자</small></h2>
                  <div className="scores hrows">
                    {posted.length === 0 && <span className="hint">아직 응시자가 없습니다</span>}
                    {posted.map((p: DashboardPosting) => {
                      const r = passRate.get(p.id);
                      const rate = r && r.decided ? Math.round((r.passed * 100) / r.decided) : null;
                      return (
                        <div className="srow" key={p.id}><span title={p.title ?? ''}>{p.title}</span>
                          <span className="bar"><i style={{ width: `${rate ?? 0}%` }} /></span><b>{rate == null ? '—' : `${rate}%`}</b>
                          <small>합격 {r?.passed ?? 0} / 결정 {r?.decided ?? 0}</small></div>
                      );
                    })}
                  </div>
                  <p className="fine" style={{ margin: '12px 0 0' }}>직무마다 기준이 달라서 공고를 섞은 전체 합격률은 보여 주지 않습니다.</p></div>
                <div className="card"><h2>직무별 평균 점수 <small>끝까지 답변한 응시자 · 100점 만점</small></h2>
                  <div className="scores hrows">
                    {s.avg_score_by_job_family.length === 0 && <span className="hint">아직 평가가 끝난 응시자가 없습니다</span>}
                    {s.avg_score_by_job_family.map((j) => (
                      <div className="srow" key={j.job_family}><span>{JOB[j.job_family] ?? j.job_family}</span>
                        <span className="bar"><i style={{ width: `${Number(j.avg)}%` }} /></span><b>{Math.round(Number(j.avg))}</b><small>응시 {j.n}명</small></div>
                    ))}
                  </div></div>
              </div>
              <div className="layout2">
                <div className="card"><h2>공고별 성과 <small>응시 전환율 = 응시자 ÷ 조회수</small></h2><div className="tblw"><table className="t">
                  <thead><tr><th>공고</th><th>상태</th><th className="num">조회수</th><th className="num">응시자</th><th className="num">응시 전환율</th><th className="num">평균 점수</th></tr></thead>
                  <tbody>
                    {d.postings.length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--muted)', padding: 28 }}>게시한 공고가 없습니다</td></tr>}
                    {d.postings.map((p: DashboardPosting) => {
                      const [label, kind] = STATUS[p.status] ?? [p.status, 'none'];
                      return (
                        <tr key={p.id}><td>{p.title}</td><td><span className={`pill ${kind}`}>{label}</span></td>
                          <td className="num">{p.views.toLocaleString()}</td><td className="num">{p.applicants}</td>
                          <td className="num">{p.views ? `${((p.applicants / p.views) * 100).toFixed(1)}%` : '—'}</td>
                          <td className="num">{p.avg_score ?? '—'}</td></tr>
                      );
                    })}
                  </tbody></table></div></div>
                <div style={{ display: 'grid', gap: 16 }}>
                  <div className="card"><h2>다가오는 일정 <Link className="link" to="/recruiter/schedule">전체 보기</Link></h2>
                    <div className="evlist">
                      {(upcoming.data ?? []).length === 0 && <span className="hint">다가오는 일정이 없습니다</span>}
                      {(upcoming.data ?? []).map((e) => {
                        const p = kstParts(e.starts_at);
                        return (
                          <div key={e.id}><span className="when">{Number(p.ymd.slice(5, 7))}.{Number(p.ymd.slice(8))}<br />{p.hm}</span>
                            <Link to="/recruiter/schedule" style={{ textDecoration: 'none', color: 'inherit' }}>
                              <span className={`ev ${KIND[e.kind][1]}`} style={{ display: 'inline-block' }}>{KIND[e.kind][0]}</span> {e.content}</Link></div>
                        );
                      })}
                    </div>
                    <div className="total" style={{ marginTop: 14 }}>
                      <span>결과를 검토하지 않은 응시자 <b>{d.awaiting_review}명</b></span>
                      <Link className="link" to="/recruiter/applications?decision=UNDECIDED">응시자 보기</Link></div></div>
                  <div className="card chart small"><h2>월별 응시자 수 <small>최근 6개월</small></h2>
                    <BarChart unit="명" rows={s.monthly_applicants.slice(-6).map((m) => [monthLabel(m.month), m.count])} /></div>
                </div>
              </div>
            </>
          );
        }}
      </QueryState>
    </>
  );
}
