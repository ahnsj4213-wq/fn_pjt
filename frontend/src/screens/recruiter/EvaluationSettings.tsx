import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import {
  fetchEvalSettings, fetchRecruiterPostings, resetEvalSettings, saveEvalSettings,
  type InterviewSettings, type PlanSlot, type QuestionArea, type Weights,
} from '@/api/recruiter';
import { describeError } from '@/shared/http/errors';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';

const AREAS: [QuestionArea, string][] = [['PERSONALITY', '인성'], ['MOTIVATION', '지원 동기'], ['JOB_SKILL', '직무 기술'],
  ['PROJECT', '프로젝트 경험'], ['COLLABORATION', '협업']];
const AREA_LABEL = Object.fromEntries(AREAS) as Record<QuestionArea, string>;
const CRITERIA: [keyof Weights, string][] = [['logic', '논리성'], ['delivery', '전달력'], ['attitude', '표현 및 태도'], ['expertise', '전문성']];
const DEFAULT_PLAN: PlanSlot[] = AREAS.map(([area]) => ({ area, text: null }));
const MAX_SLOTS = 10;

/** SC-R07 기업 관리 · 면접 질문 관리: areas, questions, weights and interview time of one posting. */
export default function EvaluationSettings() {
  const toast = useToast();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const posts = useQuery({ queryKey: ['recruiter', 'postings'], queryFn: () => fetchRecruiterPostings() });
  const pid = params.get('posting') ?? posts.data?.[0]?.id ?? '';
  const settings = useQuery({ queryKey: ['recruiter', 'settings', pid], queryFn: () => fetchEvalSettings(pid), enabled: Boolean(pid) });

  const [areas, setAreas] = useState<QuestionArea[]>([]);
  const [plan, setPlan] = useState<PlanSlot[]>(DEFAULT_PLAN);
  const [weights, setWeights] = useState<Weights>({ expertise: 30, logic: 30, attitude: 20, delivery: 20 });
  const [iv, setIv] = useState<Omit<InterviewSettings, 'question_plan'>>({ think_sec: 10, answer_sec: 60, followup_max: 2 });
  const [confirmReset, setConfirmReset] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    const s = settings.data;
    if (!s) return;
    setAreas(s.question_areas); setPlan(s.interview.question_plan ?? DEFAULT_PLAN); setWeights(s.weights);
    setIv({ think_sec: s.interview.think_sec, answer_sec: s.interview.answer_sec, followup_max: s.interview.followup_max });
    setConfirmReset(false);
  }, [settings.data]);

  const keys = (settings.data?.coding_enabled ? [...CRITERIA, ['coding', '코딩테스트'] as [keyof Weights, string]] : CRITERIA);
  const total = keys.reduce((n, [k]) => n + (weights[k] ?? 0), 0);
  const locked = settings.data?.locked ?? false;
  const onError = (e: unknown) => toast(describeError(e).message);
  const done = () => qc.invalidateQueries({ queryKey: ['recruiter'] });

  const toggleArea = (a: QuestionArea) => {
    if (!areas.includes(a)) { setAreas([...areas, a]); return; }
    const rest = areas.filter((x) => x !== a);
    if (rest.length === 0) { toast('질문 분야를 하나 이상 골라 주십시오'); return; }
    const moved = plan.filter((s) => s.area === a).length;
    setAreas(rest);
    if (moved) {
      setPlan(plan.map((s) => (s.area === a ? { ...s, area: rest[0] } : s)));
      toast(`${AREA_LABEL[a]} 분야의 질문 ${moved}개를 ${AREA_LABEL[rest[0]]}(으)로 옮겼습니다`);
    }
  };
  const setSlot = (i: number, patch: Partial<PlanSlot>) => setPlan(plan.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const fit = () => {
    if (total === 0) return;
    const scaled = Object.fromEntries(keys.map(([k]) => [k, Math.round(((weights[k] ?? 0) * 100) / total / 5) * 5])) as Record<string, number>;
    const diff = 100 - Object.values(scaled).reduce((a, b) => a + b, 0);
    const big = keys.map(([k]) => k).sort((a, b) => (scaled[b] ?? 0) - (scaled[a] ?? 0))[0];
    scaled[big] += diff;
    setWeights(scaled as unknown as Weights);
  };

  const save = useMutation({
    mutationFn: () => saveEvalSettings(pid, { weights, question_areas: areas, interview: { ...iv, question_plan: plan.map((s) => ({ area: s.area, text: s.text?.trim() || null })) } }),
    onSuccess: () => { toast('저장했습니다. 다음 면접부터 적용됩니다'); setSavedAt(new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })); void done(); },
    onError,
  });
  const reset = useMutation({
    mutationFn: () => resetEvalSettings(pid),
    onSuccess: () => { toast('질문 설정을 처음 상태로 되돌렸습니다'); setConfirmReset(false); void done(); }, onError,
  });
  const trySave = () => {
    if (total !== 100) { toast('가중치 합계를 100%로 맞춘 뒤 저장해 주십시오'); return; }
    save.mutate();
  };

  const n = plan.length;
  const minMin = Math.ceil((n * (iv.think_sec + iv.answer_sec * 0.5)) / 60);
  const maxMin = Math.ceil((n * (iv.think_sec + iv.answer_sec * (1 + iv.followup_max))) / 60);

  return (
    <>
      <div className="ph"><div><h1>면접 질문 관리</h1>
        <p>공고마다 질문 분야와 질문을 정합니다. 칸을 비워 두면 체크한 분야 안에서 AI가 공고와 이력서를 보고 질문을 만듭니다.</p></div></div>
      <QueryState isPending={posts.isPending} error={posts.error} onRetry={() => posts.refetch()}>
        {() => (posts.data!.length === 0 ? <p className="hint">먼저 공고를 만들어 주십시오.</p> : (
          <>
            <div className="filters">
              <select aria-label="공고" value={pid} onChange={(e) => setParams({ posting: e.target.value })}>
                {posts.data!.map((p) => <option key={p.id} value={p.id}>{p.title || '(제목 없음)'}</option>)}
              </select>
            </div>
            <QueryState isPending={settings.isPending} error={settings.error} onRetry={() => settings.refetch()}>
              {() => (
                <div style={{ display: 'grid', gap: 16 }}>
                  {locked && <div className="banner" style={{ margin: 0 }}>첫 면접이 시작된 공고는 질문 설정을 바꿀 수 없습니다.</div>}
                  <div className="card"><h2>질문 분야 <small>고른 분야는 공고 상세의 ‘이 공고의 면접 질문 분야’에도 보입니다</small></h2>
                    <div className="chips" style={{ padding: 0 }}>
                      {AREAS.map(([a, l]) => <label key={a} className="chip"><input type="checkbox" checked={areas.includes(a)} disabled={locked}
                        onChange={() => toggleArea(a)} /> {l}</label>)}
                    </div></div>
                  <div className="card"><h2>질문 목록 <small>비워 두면 AI가 질문을 만듭니다</small></h2>
                    <div style={{ display: 'grid', gap: 8 }}>
                      {plan.map((s, i) => (
                        <div key={i} className="inline">
                          <b style={{ minWidth: 28 }}>Q{i + 1}</b>
                          <select aria-label={`질문 ${i + 1} 분야`} value={s.area} disabled={locked} onChange={(e) => setSlot(i, { area: e.target.value as QuestionArea })}>
                            {AREAS.filter(([a]) => areas.includes(a) || a === s.area).map(([a, l]) => <option key={a} value={a}>{l}</option>)}
                          </select>
                          <input aria-label={`질문 ${i + 1}`} value={s.text ?? ''} maxLength={500} disabled={locked} placeholder="비워 두면 AI가 만듭니다"
                            onChange={(e) => setSlot(i, { text: e.target.value })} style={{ flex: 1 }} />
                          <span className="hint">{s.text?.trim() ? '직접 입력' : 'AI'}</span>
                        </div>
                      ))}
                    </div>
                    <div className="btnrow" style={{ marginTop: 12 }}>
                      <button className="btn btn-sm" type="button" disabled={locked || n >= MAX_SLOTS} onClick={() => setPlan([...plan, { area: areas[0], text: null }])}>질문 칸 추가</button>
                      <button className="btn btn-sm" type="button" disabled={locked || n <= 1} onClick={() => setPlan(plan.slice(0, -1))}>마지막 칸 빼기</button>
                      <span className="hint">질문 {n}개 (최대 {MAX_SLOTS}개)</span>
                    </div></div>
                  <div className="card"><h2>평가 항목 가중치 <small>0~60%, 5% 단위</small></h2>
                    <div className="scores">
                      {keys.map(([k, l]) => (
                        <div className="srow" key={k} style={{ opacity: weights[k] ? 1 : 0.5 }}><span>{l}</span>
                          <input type="range" min={0} max={60} step={5} aria-label={`${l} 가중치`} value={weights[k] ?? 0} disabled={locked}
                            onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })} />
                          <b>{weights[k] ?? 0}</b><small>{weights[k] ? '%' : '평가 안 함'}</small></div>
                      ))}
                    </div>
                    <div className="total" data-testid="weight-total" style={total === 100 ? undefined : { color: 'var(--bad-ink)' }}>
                      <span>합계 <b>{total}%</b>{total === 100 ? ` · 평가 항목 ${keys.filter(([k]) => weights[k]).length}개` : ''}</span>
                      {total !== 100 && <button className="btn btn-sm" type="button" onClick={fit} disabled={locked}>비율 유지하며 100%로 맞추기</button>}
                    </div></div>
                  <div className="card"><h2>면접 설정</h2>
                    <div className="row2">
                      <div className="field"><label htmlFor="iv-think">생각할 시간</label>
                        <select id="iv-think" value={iv.think_sec} disabled={locked} onChange={(e) => setIv({ ...iv, think_sec: Number(e.target.value) as InterviewSettings['think_sec'] })}>
                          {[5, 10, 20, 30].map((v) => <option key={v} value={v}>{v}초</option>)}</select></div>
                      <div className="field"><label htmlFor="iv-answer">답변 제한시간</label>
                        <select id="iv-answer" value={iv.answer_sec} disabled={locked} onChange={(e) => setIv({ ...iv, answer_sec: Number(e.target.value) as InterviewSettings['answer_sec'] })}>
                          {[30, 60, 90, 120].map((v) => <option key={v} value={v}>{v}초</option>)}</select></div>
                    </div>
                    <div className="field"><label htmlFor="iv-follow">꼬리질문 최대 횟수</label>
                      <select id="iv-follow" value={iv.followup_max} disabled={locked} onChange={(e) => setIv({ ...iv, followup_max: Number(e.target.value) })}>
                        <option value={0}>묻지 않기</option>{[1, 2, 3, 4].map((v) => <option key={v} value={v}>{v}회</option>)}</select></div>
                    <p className="hint" style={{ margin: 0 }}>예상 소요 시간: 약 {minMin}~{maxMin}분 (질문 {n}개 기준)</p>
                    <p className="fine" style={{ margin: '8px 0 0' }}>꼬리질문은 답변이 모호하거나, 이력서와 맞지 않거나, 앞뒤 논리가 어긋날 때, 또는 음성 인식 결과가 비거나 뭉개졌을 때 묻습니다.</p></div>
                  <div className="card"><h2>응시자에게 나갈 질문 순서</h2>
                    <ol style={{ margin: 0, paddingLeft: 20 }}>
                      {plan.map((s, i) => <li key={i}><span className="hint">{AREA_LABEL[s.area]}</span> {s.text?.trim() || <i>AI가 만듭니다</i>}</li>)}
                    </ol></div>
                  <div className="btnrow">
                    {confirmReset ? (
                      <div className="confirm"><span>질문 분야, 질문 목록, 면접 설정을 처음 상태로 되돌리시겠습니까? 가중치는 그대로 둡니다.</span>
                        <button className="btn btn-danger" type="button" onClick={() => reset.mutate()} disabled={reset.isPending}>초기화</button>
                        <button className="btn" type="button" onClick={() => setConfirmReset(false)}>취소</button></div>
                    ) : <button className="btn" type="button" disabled={locked} onClick={() => setConfirmReset(true)}>초기화</button>}
                    <button className="btn btn-primary" type="button" disabled={locked || save.isPending} onClick={trySave}>저장</button>
                    {savedAt && <span className="hint">마지막 저장 {savedAt}</span>}
                  </div>
                </div>
              )}
            </QueryState>
          </>
        ))}
      </QueryState>
    </>
  );
}
