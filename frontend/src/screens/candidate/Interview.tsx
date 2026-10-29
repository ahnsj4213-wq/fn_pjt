import { useEffect, useReducer, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import {
  enterTextMode, fetchInterviewState, finishTurn, nextTextQuestion, reportProctor, resumeInterview, sendTextAnswer,
  type InterviewState,
} from '@/api/candidate/interview';
import { ANSWER_AUDIO, stopStream, VoiceLink } from '@/media/rtc';
import { FIXED, formatProgress } from '@/shared/copy/fixed';
import { ApiError, NETWORK_ERROR, TIMEOUT_ERROR } from '@/shared/http/client';
import { describeError } from '@/shared/http/errors';
import { createServerClock, displaySeconds } from '@/shared/time/serverClock';
import { Button, Loading, Notice, Page, uiStyles as ui } from '@/shared/ui/components';
import { Badge, TextAreaField } from '@/shared/ui/extra';
import css from './candidate.module.css';
import { useHeartbeat, useTicker } from './interview/hooks';
import { initialUi, micOn, reduce } from './interview/machine';

const RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000]; // then fall back to text mode (INT-18)

/**
 * AI 면접 (INT-04~11, INT-13, INT-15, INT-16, INT-18). Voice mode mirrors data-channel events through the pure reducer
 * in interview/machine.ts; every deadline and the mic gate belong to the server (TRD 06). Text mode is the fallback
 * the server allows after repeated connection failures.
 */
export function Interview() {
  const { aid = '' } = useParams();
  const navigate = useNavigate();
  const startedHere = !!(useLocation().state as { started?: boolean } | null)?.started;
  const [ui_, dispatch] = useReducer(reduce, initialUi);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [textState, setTextState] = useState<InterviewState | null>(null);
  const [gateLabel, setGateLabel] = useState('면접 이어서 하기');
  const link = useRef<VoiceLink | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const live = useRef(true);

  const afterInterview = (next: 'CODING' | 'SUBMITTED' | 'DONE') => {
    link.current?.close();
    link.current = null;
    navigate(`/applications/${encodeURIComponent(aid)}/${next === 'CODING' ? 'coding' : 'done'}`, { replace: true });
  };

  /** Routes on the server's view of the attempt. Returns true when the voice interview should (re)connect. */
  const route = (st: InterviewState): boolean => {
    if (st.attempt_status === 'CODING') { afterInterview('CODING'); return false; }
    if (st.attempt_status === 'SUBMITTED' || st.attempt_status === 'EXPIRED') { afterInterview('SUBMITTED'); return false; }
    if (st.attempt_status === 'READY' || st.attempt_status === 'PREPARING') {
      navigate(`/applications/${encodeURIComponent(aid)}/guide`, { replace: true });
      return false;
    }
    if (st.mode === 'TEXT' && st.attempt_status === 'INTERVIEWING') {
      setTextState(st);
      dispatch({ type: 'text_mode' });
      return false;
    }
    return st.attempt_status === 'INTERVIEWING';
  };

  const connect = async () => {
    dispatch({ type: 'connecting' });
    link.current?.close();
    link.current = null;
    try {
      const st = await resumeInterview(aid); // INTERRUPTED -> back to INTERVIEWING (or CODING) with a slot
      if (!route(st)) return;
      stream.current ??= await navigator.mediaDevices.getUserMedia({ audio: ANSWER_AUDIO }).catch(() => null);
      if (!stream.current) {
        dispatch({ type: 'media_error', message: '마이크를 사용할 수 없습니다. 브라우저 권한을 허용한 뒤 다시 시도해 주세요.' });
        return;
      }
      stream.current.getAudioTracks().forEach((t) => { t.enabled = false; }); // off until answer_opened (C11)
      const l = await VoiceLink.open({
        aid, purpose: 'INTERVIEW', stream: stream.current,
        onEvent: (ev) => dispatch({ type: 'event', ev }),
        onState: (s) => { if (s === 'lost' && live.current) dispatch({ type: 'lost' }); },
      });
      if (!live.current) { l.close(); return; }
      link.current = l;
      setAudioBlocked(l.audioBlocked);
      dispatch({ type: 'connected' });
    } catch (e) {
      if (!live.current) return;
      if (e instanceof ApiError && e.status !== 503 && e.code !== TIMEOUT_ERROR && e.code !== NETWORK_ERROR) {
        dispatch({ type: 'error', message: describeError(e).message });
      } else {
        dispatch({ type: 'lost' });
      }
    }
  };

  // Entry: route on the current state; connect at once only when we came straight from [면접 시작] (user gesture
  // already given, so remote audio may autoplay). Otherwise ask for a click first (C2).
  useEffect(() => {
    live.current = true;
    fetchInterviewState(aid).then((st) => {
      if (st.attempt_status === 'INTERRUPTED') {
        if (st.interrupted_from === 'CODING') setGateLabel('코딩테스트 이어서 하기');
        dispatch({ type: 'gate' });
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
    if (ui_.phase !== 'RECONNECTING') return;
    link.current?.close();
    link.current = null;
    if (ui_.attempts > RECONNECT_DELAYS_MS.length) {
      enterTextMode(aid).then((st) => {
        setNotice('음성 연결이 계속 끊겨 글로 답변하는 방식으로 이어서 진행합니다.');
        setTextState(st);
        dispatch({ type: 'text_mode' });
      }).catch(() => {
        setNotice('음성 연결이 끊겼습니다. 네트워크를 확인한 뒤 다시 연결해 주세요.');
        dispatch({ type: 'reconnect_failed' });
      });
      return;
    }
    const t = setTimeout(() => void connect(), RECONNECT_DELAYS_MS[ui_.attempts - 1]);
    return () => clearTimeout(t);
  }, [ui_.phase, ui_.attempts]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { link.current?.setMic(micOn(ui_)); }, [ui_.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ui_.phase === 'COMPLETED' && ui_.next) afterInterview(ui_.next);
    if (ui_.phase === 'SUPERSEDED' || ui_.phase === 'ERROR') {
      link.current?.close();
      link.current = null;
    }
  }, [ui_.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  // X-06: leaving the tab is recorded and warned about, never penalised automatically.
  useEffect(() => {
    let leftAt = 0;
    const onVis = () => {
      if (document.visibilityState === 'hidden') {
        leftAt = Date.now();
        reportProctor(aid, 'LEAVE').catch(() => undefined);
      } else if (leftAt) {
        reportProctor(aid, 'RETURN', Date.now() - leftAt).catch(() => undefined);
        setNotice('면접 중에는 다른 창으로 이동하지 마세요. 이동한 기록이 남습니다.');
        leftAt = 0;
      }
    };
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, [aid]);

  const inSession = !['BOOT', 'RESUME_GATE', 'SUPERSEDED', 'MEDIA_ERROR', 'COMPLETED', 'ERROR'].includes(ui_.phase);
  useHeartbeat(aid, inSession);
  useTicker(ui_.phase === 'ANSWERING' || ui_.phase === 'CLOSING');

  const onDone = () => {
    if (!ui_.turn) return;
    dispatch({ type: 'done_clicked' });
    finishTurn(aid, ui_.turn.id).catch(() => undefined); // STALE_TURN: the server already moved on
  };

  const onGate = () => {
    setNotice(null);
    void connect(); // resume first; route() moves on to coding when the attempt was interrupted there
  };

  const remaining = ui_.deadlineAt && link.current ? link.current.clock.remainingMs(ui_.deadlineAt) : null;
  const turn = ui_.turn;

  return (
    <Page title="AI 면접">
      {notice && <Notice>{notice}</Notice>}
      {audioBlocked && (
        <Notice>
          질문 음성이 재생되지 않았습니다.{' '}
          <Button onClick={() => { void link.current?.unblockAudio().then((ok) => setAudioBlocked(!ok)); }}>소리 켜기</Button>
        </Notice>
      )}
      {ui_.phase === 'BOOT' && <Loading />}
      {ui_.phase === 'RESUME_GATE' && (
        <div className={css.stack}>
          <p>면접이 중단된 상태입니다. 준비되면 이어서 진행해 주세요. 끊긴 질문은 처음부터 다시 나옵니다.</p>
          <Button variant="primary" onClick={onGate} data-testid="resume-interview">{gateLabel}</Button>
        </div>
      )}
      {(ui_.phase === 'CONNECTING' || ui_.phase === 'RECONNECTING') && (
        <Loading label={ui_.phase === 'CONNECTING' ? '면접 서버에 연결하고 있습니다' : '연결이 끊겨 다시 연결하고 있습니다'} />
      )}
      {ui_.phase === 'MEDIA_ERROR' && (
        <Notice kind="error">{ui_.error} <Button onClick={() => void connect()}>다시 시도</Button></Notice>
      )}
      {ui_.phase === 'ERROR' && <Notice kind="error">{ui_.error}</Notice>}
      {ui_.phase === 'SUPERSEDED' && <Notice kind="error">다른 창에서 면접이 열려 이 화면의 연결을 닫았습니다.</Notice>}
      {ui_.phase === 'TEXT' && textState && <TextInterview aid={aid} initial={textState} onFinished={afterInterview} />}

      {['WAITING', 'PRESENTING', 'ANSWERING', 'CLOSING'].includes(ui_.phase) && (
        <div className={css.stack} data-testid="voice-interview" data-phase={ui_.phase}>
          {turn ? (
            <>
              <div className={css.progress}>
                <span data-testid="progress">{formatProgress(turn.mainNo)}</span>
                {turn.isFollowup && <Badge kind="info">{FIXED.FOLLOWUP_BADGE}</Badge>}
              </div>
              <p className={css.question} data-testid="question">{turn.text}</p>
            </>
          ) : <Loading label="질문을 준비하고 있습니다" />}
          {ui_.phase === 'PRESENTING' && <p className={css.muted}>질문을 읽고 있습니다. 다 읽으면 답변 시간이 시작됩니다.</p>}
          {(ui_.phase === 'ANSWERING' || ui_.phase === 'CLOSING') && (
            <>
              <div className={ui.row}>
                <span className={css.listening}>{ui_.phase === 'ANSWERING' ? FIXED.ANSWER_PROMPT : '답변을 정리하고 있습니다'}</span>
                {remaining !== null && (
                  <span className={`${css.timer} ${ui_.warned ? css.timerWarn : ''}`} data-testid="timer" aria-live="off">
                    {displaySeconds(remaining)}초
                  </span>
                )}
              </div>
              <div className={css.captions} aria-live="polite" data-testid="captions">{ui_.captions.join(' ')}</div>
              <Button variant="primary" onClick={onDone} disabled={ui_.phase !== 'ANSWERING'} data-testid="answer-done">
                답변 완료
              </Button>
            </>
          )}
          {ui_.phase === 'WAITING' && turn && <Loading label="다음 질문을 준비하고 있습니다" />}
          {ui_.last && (
            <section aria-label="직전 답변">
              <h3>직전 답변</h3>
              <p className={css.captions} data-testid="last-answer">{ui_.last.noAnswer ? FIXED.NO_ANSWER : ui_.last.text}</p>
            </section>
          )}
        </div>
      )}
    </Page>
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
    <div className={css.stack} data-testid="text-interview">
      {error && <Notice kind="error">{error}</Notice>}
      {!t ? <Loading label="질문을 준비하고 있습니다" /> : (
        <>
          <div className={css.progress}>
            <span data-testid="progress">{formatProgress(t.main_no, st.total_main)}</span>
            {t.is_followup && <Badge kind="info">{FIXED.FOLLOWUP_BADGE}</Badge>}
            {remaining !== null && <span className={css.timer} data-testid="timer">{displaySeconds(remaining)}초</span>}
          </div>
          <p className={css.question} data-testid="question">{t.question_text}</p>
          <TextAreaField label="답변" value={answer} maxLength={2000} onChange={(e) => setAnswer(e.target.value)} />
          <Button variant="primary" onClick={() => void submit()} busy={busy} data-testid="text-answer-submit">답변 완료</Button>
        </>
      )}
    </div>
  );
}
