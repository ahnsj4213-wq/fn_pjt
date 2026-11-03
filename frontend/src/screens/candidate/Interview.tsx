import { useQuery } from '@tanstack/react-query';
import { useEffect, useReducer, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { fetchMyApplication } from '@/api/candidate';
import {
  enterTextMode, fetchInterviewState, finishTurn, nextTextQuestion, replayQuestion, reportProctor, resumeInterview,
  sendTextAnswer, startAnswer, startInterview, type InterviewState,
} from '@/api/candidate/interview';
import { useSession } from '@/app/session';
import { useGaze } from '@/media/gaze';
import { ANSWER_AUDIO, stopStream, VoiceLink } from '@/media/rtc';
import { FIXED, formatProgress } from '@/shared/copy/fixed';
import { ApiError, NETWORK_ERROR, TIMEOUT_ERROR } from '@/shared/http/client';
import { describeError } from '@/shared/http/errors';
import { createServerClock, displaySeconds } from '@/shared/time/serverClock';
import { Button, Loading, Notice } from '@/shared/ui/components';
import { Badge, TextAreaField } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';
import { applyPath, loadDeviceCheck } from './apply/applyState';
import { useHeartbeat, useTicker } from './interview/hooks';
import { Interviewer } from './interview/Interviewer';
import { initialUi, micOn, progressOf, reduce } from './interview/machine';
import { CodeTool, type ToolHandle } from './interview/tools';

const RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000]; // then fall back to text mode (INT-18)
const QUESTIONS_WAIT_LIMIT = 60; // ~3 minutes at retry_after_sec=3
const GAZE_WARN_MS = 1_500; // SC-A06: a warning after 1.5 s of looking away
const GAZE_SUSPECT = 3;
const RING = 263.9;

const AREA_LABEL: Record<string, string> = {
  PERSONALITY: '인성', MOTIVATION: '지원 동기', JOB_SKILL: '직무 기술', PROJECT: '프로젝트 경험', COLLABORATION: '협업',
};
const areaLabel = (area: string, mode: string) => (mode === 'CODE' ? '라이브 코딩' : AREA_LABEL[area] ?? area);
const fmtLimit = (sec: number) => (sec >= 120 && sec % 60 === 0 ? `${sec / 60}분` : `${sec}초`);

/**
 * SC-A06 AI 면접. Voice mode mirrors data-channel events through the pure reducer in interview/machine.ts; every
 * deadline, the think time and the mic gate belong to the server (TRD 06). Text mode is the fallback the server
 * allows after repeated connection failures. The camera is only shown to the candidate and for the looking-away
 * check in this browser; video is never sent or stored.
 */
export function Interview() {
  const { aid = '' } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const startedHere = !!(useLocation().state as { started?: boolean } | null)?.started;
  const me = useSession().data?.user.name ?? '';
  const app = useQuery({ queryKey: ['my-application', aid], queryFn: () => fetchMyApplication(aid) });
  const [ui, dispatch] = useReducer(reduce, initialUi);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [textState, setTextState] = useState<InterviewState | null>(null);
  const [gateLabel, setGateLabel] = useState('면접 이어서 하기');
  const [followupMax, setFollowupMax] = useState<number | null>(null);
  const [warnings, setWarnings] = useState(0);
  const [warned, setWarned] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const link = useRef<VoiceLink | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const tool = useRef<ToolHandle>(null);
  const flushedTurn = useRef<string | null>(null);
  const live = useRef(true);
  const check = loadDeviceCheck();

  const afterInterview = (next: 'CODING' | 'SUBMITTED' | 'DONE') => {
    link.current?.close();
    link.current = null;
    navigate(`/applications/${encodeURIComponent(aid)}/${next === 'CODING' ? 'coding' : 'done'}`, { replace: true });
  };

  const toApplyStart = async () => {
    const a = app.data ?? await fetchMyApplication(aid);
    navigate(applyPath(a.posting.id), { replace: true });
  };

  /** Routes on the server's view of the attempt. Returns true when the voice interview should (re)connect. */
  const route = (st: InterviewState): boolean => {
    setFollowupMax(st.followup_max);
    setWarnings(st.gaze_warnings);
    if (st.attempt_status === 'CODING') { afterInterview('CODING'); return false; }
    if (st.attempt_status === 'SUBMITTED' || st.attempt_status === 'EXPIRED') { afterInterview('SUBMITTED'); return false; }
    if (st.attempt_status === 'READY') { void toApplyStart(); return false; }
    if (st.mode === 'TEXT' && st.attempt_status === 'INTERVIEWING') {
      setTextState(st);
      dispatch({ type: 'text_mode' });
      return false;
    }
    return st.attempt_status === 'INTERVIEWING';
  };

  /** Camera for the picture-in-picture and the looking-away check; mic for the answers. Devices from SC-A04. */
  const openMedia = async (): Promise<MediaStream | null> => {
    if (stream.current) return stream.current;
    // `ideal` device ids: a device that went away falls back to the default one instead of failing.
    const audio = check?.mic ? { ...ANSWER_AUDIO, deviceId: check.mic } : ANSWER_AUDIO;
    const s = await navigator.mediaDevices.getUserMedia({ audio, video: check?.cam ? { deviceId: check.cam } : true })
      .catch(() => navigator.mediaDevices.getUserMedia({ audio: ANSWER_AUDIO, video: true }))
      .catch(() => navigator.mediaDevices.getUserMedia({ audio: ANSWER_AUDIO }).catch(() => null));
    if (!s) return null;
    stream.current = s;
    setHasVideo(s.getVideoTracks().length > 0);
    if (video.current) video.current.srcObject = s;
    return s;
  };

  const connect = async () => {
    dispatch({ type: 'connecting' });
    link.current?.close();
    link.current = null;
    try {
      const st = await resumeInterview(aid); // INTERRUPTED -> back to INTERVIEWING (or CODING) with a slot
      if (!route(st)) return;
      const s = await openMedia();
      if (!s || s.getAudioTracks().length === 0) {
        dispatch({ type: 'media_error', message: '마이크를 사용할 수 없습니다. 브라우저 권한을 허용한 뒤 다시 시도해 주십시오.' });
        return;
      }
      const audioOnly = new MediaStream(s.getAudioTracks());
      audioOnly.getAudioTracks().forEach((t) => { t.enabled = false; }); // off until answer_opened (C11)
      const l = await VoiceLink.open({
        aid, purpose: 'INTERVIEW', stream: audioOnly,
        onEvent: (ev) => dispatch({ type: 'event', ev }),
        onState: (x) => { if (x === 'lost' && live.current) dispatch({ type: 'lost' }); },
      });
      if (!live.current) { l.close(); return; }
      const sink = (l.audio as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }).setSinkId;
      if (check?.spk && sink) await sink.call(l.audio, check.spk).catch(() => undefined);
      link.current = l;
      setAudioBlocked(l.audioBlocked);
      dispatch({ type: 'connected', followups: st.followups_used, mainCount: st.total_main });
    } catch (e) {
      if (!live.current) return;
      if (e instanceof ApiError && e.status !== 503 && e.code !== TIMEOUT_ERROR && e.code !== NETWORK_ERROR) {
        dispatch({ type: 'error', message: describeError(e).message });
      } else {
        dispatch({ type: 'lost' });
      }
    }
  };

  /** PREPARING: questions are composed in the background; start as soon as they are ready. */
  const startWhenReady = async () => {
    dispatch({ type: 'preparing' });
    for (let i = 0; live.current; i++) {
      try {
        await startInterview(aid);
        break;
      } catch (e) {
        if (e instanceof ApiError && e.code === 'PRECHECK_REQUIRED') {
          toast('환경 점검이 오래되어 다시 점검해 주십시오');
          void toApplyStart();
          return;
        }
        if (!(e instanceof ApiError) || e.code !== 'QUESTIONS_NOT_READY' || i >= QUESTIONS_WAIT_LIMIT) {
          dispatch({ type: 'error', message: describeError(e).message });
          return;
        }
        await new Promise((r) => setTimeout(r, Number(e.extra.retry_after_sec ?? 3) * 1000));
      }
    }
    if (!live.current) return;
    if (startedHere) void connect();
    else {
      setGateLabel('면접 시작');
      dispatch({ type: 'gate' });
    }
  };

  // Entry: route on the current state; connect at once only when we came straight from [면접 시작] (user gesture
  // already given, so remote audio may autoplay). Otherwise ask for a click first (C2).
  useEffect(() => {
    live.current = true;
    fetchInterviewState(aid).then((st) => {
      setFollowupMax(st.followup_max);
      setWarnings(st.gaze_warnings);
      if (st.attempt_status === 'INTERRUPTED') {
        if (st.interrupted_from === 'CODING') setGateLabel('코딩테스트 이어서 하기');
        dispatch({ type: 'gate' });
      } else if (st.attempt_status === 'PREPARING') {
        void startWhenReady();
      } else if (route(st)) {
        if (startedHere) void connect();
        else dispatch({ type: 'gate' });
      }
    }).catch((e) => dispatch({ type: 'error', message: describeError(e).message }));
    return () => {
      live.current = false;
      link.current?.close();
      link.current = null;
      stopStream(stream.current);
      stream.current = null;
    };
  }, [aid]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reconnect with backoff; after the last try ask the server for text mode (it decides, T12).
  useEffect(() => {
    if (ui.phase !== 'RECONNECTING') return;
    link.current?.close();
    link.current = null;
    if (ui.attempts > RECONNECT_DELAYS_MS.length) {
      enterTextMode(aid).then((st) => {
        setNotice('음성 연결이 계속 끊겨 글로 답변하는 방식으로 이어서 진행합니다.');
        setTextState(st);
        dispatch({ type: 'text_mode' });
      }).catch(() => {
        setNotice('음성 연결이 끊겼습니다. 네트워크를 확인한 뒤 다시 연결해 주십시오.');
        dispatch({ type: 'reconnect_failed' });
      });
      return;
    }
    const t = setTimeout(() => void connect(), RECONNECT_DELAYS_MS[ui.attempts - 1]);
    return () => clearTimeout(t);
  }, [ui.phase, ui.attempts]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { link.current?.setMic(micOn(ui)); }, [ui.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ui.phase === 'COMPLETED' && ui.next) afterInterview(ui.next);
    if (ui.phase === 'SUPERSEDED' || ui.phase === 'ERROR') {
      link.current?.close();
      link.current = null;
    }
  }, [ui.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  // X-06: leaving the tab is recorded and warned about, never penalised automatically.
  useEffect(() => {
    let leftAt = 0;
    const onVis = () => {
      if (document.visibilityState === 'hidden') {
        leftAt = Date.now();
        reportProctor(aid, 'LEAVE').catch(() => undefined);
      } else if (leftAt) {
        reportProctor(aid, 'RETURN', Date.now() - leftAt).catch(() => undefined);
        setNotice('면접 중에는 다른 창으로 이동하지 마십시오. 이동한 기록이 남습니다.');
        leftAt = 0;
      }
    };
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, [aid]);

  const voicePhases = ['WAITING', 'PRESENTING', 'THINKING', 'ANSWERING', 'CLOSING'];
  const inVoice = voicePhases.includes(ui.phase);
  const inSession = !['BOOT', 'RESUME_GATE', 'SUPERSEDED', 'MEDIA_ERROR', 'COMPLETED', 'ERROR'].includes(ui.phase);
  useHeartbeat(aid, inSession);
  useTicker(inVoice);

  // Looking away for 1.5 s -> one warning, recorded on the server (SC-A06). Looking back clears the banner.
  const gaze = useGaze(video, inVoice && hasVideo);
  useEffect(() => {
    if (gaze !== 'away' || !inVoice) { setWarned(false); return; }
    const t = setTimeout(() => {
      setWarned(true);
      reportProctor(aid, 'GAZE_AWAY', GAZE_WARN_MS)
        .then((r) => setWarnings(r.gaze_warnings))
        .catch(() => setWarnings((n) => n + 1));
    }, GAZE_WARN_MS);
    return () => clearTimeout(t);
  }, [gaze, inVoice, aid]);

  // Video element appears with the voice layout; attach the stream whenever it (re)mounts.
  useEffect(() => { if (video.current && stream.current) video.current.srcObject = stream.current; }, [inVoice]);

  const turn = ui.turn;
  const clock = link.current?.clock;
  const remainMs = (at: number | null) => (at && clock ? Math.max(0, clock.remainingMs(at)) : null);
  const thinkLeft = ui.phase === 'THINKING' && !ui.replaying ? remainMs(ui.thinkUntil) : null;
  const answerLeft = ui.phase === 'ANSWERING' && !ui.interjecting ? remainMs(ui.deadlineAt) : null;

  // The deadline submits pending code or drawing (inside the server's grace period).
  useEffect(() => {
    if (turn && answerLeft !== null && answerLeft <= 300 && flushedTurn.current !== turn.id) {
      flushedTurn.current = turn.id;
      void tool.current?.flush();
    }
  }, [answerLeft, turn]);

  const onDone = async () => {
    if (!turn) return;
    dispatch({ type: 'done_clicked' });
    flushedTurn.current = turn.id;
    await tool.current?.flush().catch(() => undefined);
    finishTurn(aid, turn.id).catch(() => undefined); // STALE_TURN: the server already moved on
  };

  const quit = () => {
    link.current?.close();
    link.current = null;
    toast('면접을 중단했습니다');
    navigate(app.data ? `/postings/${encodeURIComponent(app.data.posting.id)}` : '/me/applications');
  };

  const onGate = () => {
    setNotice(null);
    void connect(); // resume first; route() moves on to coding when the attempt was interrupted there
  };

  const p = app.data?.posting;
  const head = (
    <div className="run-head">
      <span className="co">{p?.title ?? ''}<small>{p ? `${p.company_name} 모의면접` : ''}</small></span>
      <button className="btn" type="button" onClick={quit}>면접 그만두기</button>
    </div>
  );

  if (!inVoice) {
    return (
      <div className="wrap run">
        {head}
        {notice && <div className="notice" style={{ margin: 0 }}>{notice}</div>}
        {ui.phase === 'BOOT' && <Loading />}
        {ui.phase === 'PREPARING' && (
          <div className="ocr" role="status"><span className="spin" aria-hidden="true" /><div>
            <b>질문을 준비하고 있습니다</b><p className="hint">이력서와 공고를 보고 맞춤 질문을 만들고 있습니다. 잠시만 기다려 주십시오.</p>
          </div></div>
        )}
        {ui.phase === 'RESUME_GATE' && (
          <div className="stub" style={{ margin: 0 }}>
            <p>{turn || ui.attempts ? '면접이 중단된 상태입니다. 준비되면 이어서 진행해 주십시오. 끊긴 질문은 처음부터 다시 나옵니다.'
              : '준비되면 면접을 시작해 주십시오. 면접관이 바로 첫 질문을 읽습니다.'}</p>
            <button className="btn btn-primary btn-lg" type="button" onClick={onGate} data-testid="resume-interview">{gateLabel}</button>
          </div>
        )}
        {(ui.phase === 'CONNECTING' || ui.phase === 'RECONNECTING') && (
          <Loading label={ui.phase === 'CONNECTING' ? '면접 서버에 연결하고 있습니다' : '연결이 끊겨 다시 연결하고 있습니다'} />
        )}
        {ui.phase === 'MEDIA_ERROR' && (
          <div className="notice bad" role="alert">{ui.error} <button className="btn" type="button" onClick={() => void connect()}>다시 시도</button></div>
        )}
        {ui.phase === 'ERROR' && <div className="notice bad" role="alert">{ui.error}</div>}
        {ui.phase === 'SUPERSEDED' && <div className="notice bad" role="alert">다른 창에서 면접이 열려 이 화면의 연결을 닫았습니다.</div>}
        {ui.phase === 'TEXT' && textState && <TextInterview aid={aid} initial={textState} onFinished={afterInterview} />}
      </div>
    );
  }

  // ---- voice layout (wireframe renderRun) ----
  const speaking = ui.phase === 'PRESENTING' || ui.replaying || ui.interjecting;
  const mainCount = ui.mainCount ?? turn?.mainCount ?? 0;
  const thinkTotal = (turn?.thinkSec ?? 10) * 1000;
  const answerTotal = (turn?.limitSec ?? 60) * 1000;
  const frac = thinkLeft !== null ? 1 - thinkLeft / thinkTotal : answerLeft !== null ? 1 - answerLeft / answerTotal
    : ui.phase === 'WAITING' || ui.phase === 'CLOSING' ? 1 : 0;
  const progress = progressOf(ui, frac);
  const inQ = turn ? Math.max(0, progress * mainCount - (turn.mainNo - 1)) : 0;
  const ringLeft = thinkLeft ?? answerLeft;
  const ringTotal = thinkLeft !== null ? thinkTotal : answerTotal;
  const low = answerLeft !== null && answerLeft <= 10_000;
  const toolKind = !turn ? null : turn.answerMode === 'CODE' ? 'code' : null;
  const canSubmit = ui.phase === 'ANSWERING';
  const last = !!turn && turn.mainNo === mainCount && (turn.isFollowup || (followupMax !== null && ui.followups >= followupMax));

  const phaseText: [string, string] = speaking && !ui.interjecting
    ? [turn?.isFollowup ? '면접관이 꼬리질문을 하고 있습니다' : '면접관이 질문하고 있습니다', '질문을 끝까지 들어 주십시오']
    : ui.interjecting ? ['면접관이 말하는 중', '말씀이 끝나면 남은 시간 동안 이어서 답변합니다']
      : ui.phase === 'THINKING' ? ['생각할 시간', `${turn?.thinkSec ?? 10}초가 지나면 답변이 시작됩니다`]
        : ui.phase === 'ANSWERING' ? ['답변 중', `제한시간 ${fmtLimit(turn?.limitSec ?? 60)} · 다 말했으면 '답변 완료'를 눌러 주십시오`]
          : ui.phase === 'CLOSING' ? ['답변을 정리하고 있습니다', '잠시만 기다려 주십시오']
            : ['답변을 분석하고 있습니다', 'AI가 답변 내용을 보고 꼬리질문이 필요한지 판단합니다'];

  const bubble = ui.interjecting
    ? { cls: ' cut', type: '면접관이 끼어들었습니다', text: ui.interjectText ?? '' }
    : ui.phase === 'WAITING' && ui.last
      ? { cls: ' think', type: '답변 분석 중', text: '답변 잘 들었습니다.' }
      : turn ? {
        cls: turn.isFollowup ? ' fu' : '',
        type: turn.isFollowup ? `꼬리질문 · 질문 ${turn.mainNo} 답변에 이어서` : `질문 ${turn.mainNo} · ${areaLabel(turn.area, turn.answerMode)}`,
        text: turn.text,
      } : { cls: ' think', type: '', text: '면접관이 첫 질문을 준비하고 있습니다.' };

  return (
    <div className="wrap run" data-testid="voice-interview" data-phase={ui.phase}>
      {head}
      {notice && <div className="notice" style={{ margin: 0 }}>{notice}</div>}
      {audioBlocked && (
        <div className="notice" style={{ margin: 0 }}>
          질문 음성이 재생되지 않았습니다.{' '}
          <button className="btn" type="button" onClick={() => { void link.current?.unblockAudio().then((ok) => setAudioBlocked(!ok)); }}>소리 켜기</button>
        </div>
      )}
      <div className="progress" aria-label="면접 진행률">
        <div className="prog-label">
          <span data-testid="progress">
            {turn && <><b>질문 {turn.mainNo}{turn.isFollowup ? ' · 꼬리질문' : ''}</b> / {mainCount}{ui.followups ? ` · 꼬리질문 ${ui.followups}개` : ''}</>}
          </span>
          <span>전체 {Math.round(progress * 100)}%</span>
        </div>
        <div className="segs" style={{ gridTemplateColumns: `repeat(${Math.max(1, mainCount)}, 1fr)` }}>
          {Array.from({ length: mainCount }, (_, k) => (
            <div className="seg" key={k}>
              <i style={{ width: `${!turn ? 0 : k < turn.mainNo - 1 ? 100 : k === turn.mainNo - 1 ? Math.min(100, inQ * 100) : 0}%` }} />
            </div>
          ))}
        </div>
      </div>
      <div className={`run-body${toolKind ? ' has-tool' : ''}`}>
        <section className="stage">
          <div className={`cam${gaze === 'away' ? ' away' : ''}`}>
            <video ref={video} autoPlay muted playsInline aria-label="내 카메라 화면" />
            {ui.phase === 'ANSWERING' && <span className="rec">REC</span>}
            {ui.phase === 'ANSWERING' && <span className="mic on" title="음성 감지"><i /><i /><i /><i /></span>}
            <span className="gaze">
              {!hasVideo || gaze === 'unavailable' ? '시선 확인 불가' : gaze === 'away' ? '시선 이탈' : gaze === 'loading' ? '시선 확인 중' : '시선 정상'}
            </span>
            <span className="me">{me}</span>
          </div>
          <Interviewer speaking={speaking} />
          <div className={`bubble${bubble.cls}`}>
            <small>{bubble.type}</small>
            <p data-testid="question">{bubble.text}</p>
            {speaking && <span className="tts"><span className="wave"><i /><i /><i /><i /></span>음성으로 읽는 중</span>}
          </div>
          <div className="timer">
            <div className={`ring${low ? ' low' : ''}`}>
              <svg viewBox="0 0 100 100">
                <circle className="bg" cx="50" cy="50" r="42" />
                <circle className="fg" cx="50" cy="50" r="42" strokeDasharray={RING}
                  style={{ strokeDashoffset: ringLeft === null ? 0 : (RING * (1 - ringLeft / ringTotal)).toFixed(1) }} />
              </svg>
              <span data-testid="timer" aria-live="off">{ringLeft === null ? '···' : displaySeconds(ringLeft)}</span>
            </div>
            <div className="phase"><b>{phaseText[0]}</b><span>{phaseText[1]}</span></div>
          </div>
          {(ui.phase === 'ANSWERING' || ui.phase === 'CLOSING') && (
            <div className="sttlive">
              <span className="hint">실시간 자막 · 말한 내용이 글자로 바뀝니다</span>
              <p data-testid="captions" aria-live="polite">{ui.captions.length ? ui.captions.join(' ') : '말씀을 기다리고 있습니다'}</p>
            </div>
          )}
          {thinkLeft !== null && thinkLeft <= 3000 && (
            <div className="countdown"><div>{Math.max(1, Math.ceil(thinkLeft / 1000))}<em>곧 답변이 시작됩니다</em></div></div>
          )}
          {warned && (
            <div className="warn" role="alert">
              {warnings >= GAZE_SUSPECT
                ? `시선 이탈이 반복되고 있습니다. 다른 화면을 보는 것으로 의심돼 기록됩니다. (경고 ${warnings}회)`
                : `시선이 화면을 벗어났습니다. 화면을 봐 주십시오. (경고 ${warnings}회)`}
            </div>
          )}
        </section>
        {turn && toolKind === 'code' && <CodeTool key={turn.id} ref={tool} aid={aid} turnId={turn.id} canSubmit={canSubmit} />}
      </div>
      <div className="run-actions">
        {ui.phase === 'THINKING' && !ui.replaying && turn && (
          <>
            {ui.replayLeft > 0 && (
              <button className="btn btn-lg" type="button" onClick={() => { replayQuestion(aid, turn.id).catch(() => undefined); }}>질문 다시 듣기</button>
            )}
            <button className="btn btn-lg" type="button" onClick={() => { startAnswer(aid, turn.id).catch(() => undefined); }}>바로 답변 시작</button>
          </>
        )}
        {ui.phase === 'ANSWERING' && (
          <>
            <button className="btn btn-primary btn-lg" type="button" onClick={() => void onDone()} disabled={ui.interjecting}
              data-testid="answer-done">{last ? '답변 완료하고 면접 끝내기' : '답변 완료'}</button>
          </>
        )}
      </div>
    </div>
  );
}

/** Text mode (INT-18): the same questions and time limits, typed answers. */
function TextInterview({ aid, initial, onFinished }: {
  aid: string;
  initial: InterviewState;
  onFinished: (next: 'CODING' | 'DONE') => void;
}) {
  const [st, setSt] = useState(initial);
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clock = useRef(createServerClock());
  useTicker(!!st.turn?.deadline_at);

  const apply = (s: InterviewState) => {
    clock.current.addOneWay(Date.parse(s.server_now));
    setSt(s);
    if (s.attempt_status !== 'INTERVIEWING') onFinished(s.next_after_interview);
  };

  const advance = async () => {
    setBusy(true);
    setError(null);
    try {
      apply(await nextTextQuestion(aid));
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    clock.current.addOneWay(Date.parse(initial.server_now));
    if (!initial.turn || initial.turn.phase !== 'ANSWERING') void advance();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    if (!st.turn) return;
    setBusy(true);
    setError(null);
    try {
      await sendTextAnswer(aid, st.turn.turn_id, answer);
      setAnswer('');
      apply(await nextTextQuestion(aid));
    } catch (e) {
      if (e instanceof ApiError && (e.code === 'TURN_CLOSED' || e.code === 'STALE_TURN')) {
        apply(await nextTextQuestion(aid).catch(() => st));
      } else {
        setError(describeError(e).message);
      }
    } finally {
      setBusy(false);
    }
  };

  const t = st.turn;
  const remaining = t?.deadline_at ? clock.current.remainingMs(Date.parse(t.deadline_at)) : null;
  return (
    <div className="card" style={{ display: 'grid', gap: 12 }} data-testid="text-interview">
      {error && <Notice kind="error">{error}</Notice>}
      {!t ? <Loading label="질문을 준비하고 있습니다" /> : (
        <>
          <div className="prog-label">
            <span data-testid="progress">{formatProgress(t.main_no, st.total_main)}</span>
            {t.is_followup && <Badge kind="info">{FIXED.FOLLOWUP_BADGE}</Badge>}
            {remaining !== null && <span data-testid="timer">{displaySeconds(remaining)}초</span>}
          </div>
          <p data-testid="question">{t.question_text}</p>
          <TextAreaField label="답변" value={answer} maxLength={2000} onChange={(e) => setAnswer(e.target.value)} />
          <Button variant="primary" onClick={() => void submit()} busy={busy} data-testid="text-answer-submit">답변 완료</Button>
        </>
      )}
    </div>
  );
}
