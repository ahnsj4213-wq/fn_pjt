import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { leavePrepare, prepareInterview, reportMedia, requestUtterance, startInterview } from '@/api/candidate/interview';
import type { ServerEvent } from '@/media/dcEvents';
import { ANSWER_AUDIO, stopStream, VoiceLink } from '@/media/rtc';
import { ApiError } from '@/shared/http/client';
import { describeError } from '@/shared/http/errors';
import { Button, Notice, Page, uiStyles as ui } from '@/shared/ui/components';
import css from './candidate.module.css';
import { useHeartbeat, useMicLevel } from './interview/hooks';

type Voice = 'idle' | 'connecting' | 'listening' | 'ok' | 'fail';

const QUESTIONS_WAIT_LIMIT = 40;

const PRECHECK_REASON: Record<string, string> = {
  NO_SPEECH: '목소리가 들리지 않았습니다. 마이크를 확인하고 다시 말해 주세요.',
  NOT_RECOGNIZED: '말을 알아듣지 못했습니다. 조금 더 크고 또렷하게 말해 주세요.',
  STT_UNAVAILABLE: '음성 인식 서버를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.',
};

/**
 * 장비 점검 (INT-01, INT-17, AC-08): camera preview and mic level stay local; the voice path is checked end to end
 * (WebRTC -> server gate -> VAD -> STT) with one short utterance. The server refuses to start without both checks.
 */
export function Precheck() {
  const { aid = '' } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const video = useRef<HTMLVideoElement>(null);
  const link = useRef<VoiceLink | null>(null);
  const started = useRef(false);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [voice, setVoice] = useState<Voice>('idle');
  const [voiceMsg, setVoiceMsg] = useState<string | null>(null);
  const [startMsg, setStartMsg] = useState<{ text: string; error: boolean } | null>(null);
  const level = useMicLevel(stream);

  const prepare = useMutation({ mutationFn: () => prepareInterview(aid) });
  useEffect(() => { prepare.mutate(); }, [aid]); // eslint-disable-line react-hooks/exhaustive-deps
  useHeartbeat(aid, prepare.isSuccess);

  const openMedia = async () => {
    setMediaError(null);
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: ANSWER_AUDIO, video: true });
      setStream((old) => { stopStream(old); return s; });
      await reportMedia(aid, { mic_ok: s.getAudioTracks().length > 0, camera_ok: s.getVideoTracks().length > 0 });
    } catch (e) {
      setMediaError(e instanceof ApiError ? describeError(e).message
        : '카메라와 마이크를 사용할 수 없습니다. 브라우저 주소창의 권한 설정에서 허용한 뒤 다시 시도해 주세요.');
    }
  };
  useEffect(() => { if (prepare.isSuccess) void openMedia(); }, [prepare.isSuccess]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (video.current && stream) video.current.srcObject = stream; }, [stream]);
  useEffect(() => () => {
    link.current?.close();
    if (!started.current) setStream((s) => { stopStream(s); return null; });
  }, []);

  const onEvent = (ev: ServerEvent) => {
    if (ev.type === 'precheck_listen') setVoice('listening');
    if (ev.type === 'precheck_result') {
      setVoice(ev.ok ? 'ok' : 'fail');
      setVoiceMsg(ev.ok ? `"${ev.text}" 라고 들렸습니다.` : PRECHECK_REASON[ev.reason] ?? PRECHECK_REASON.NOT_RECOGNIZED);
    }
  };

  const checkVoice = async () => {
    if (!stream) return;
    setVoice('connecting');
    setVoiceMsg(null);
    try {
      if (!link.current) {
        link.current = await VoiceLink.open({
          aid, purpose: 'PRECHECK', stream: new MediaStream(stream.getAudioTracks()), onEvent,
          onState: (st) => { if (st === 'lost') { link.current?.close(); link.current = null; } },
        });
      }
      await requestUtterance(aid);
    } catch (e) {
      link.current?.close();
      link.current = null;
      setVoice('fail');
      setVoiceMsg(e instanceof ApiError ? describeError(e).message : '음성 연결에 실패했습니다. 다시 시도해 주세요.');
    }
  };

  const start = useMutation({
    mutationFn: async () => {
      for (let i = 0; ; i++) {
        try {
          return await startInterview(aid);
        } catch (e) {
          if (!(e instanceof ApiError) || e.code !== 'QUESTIONS_NOT_READY' || i >= QUESTIONS_WAIT_LIMIT) throw e;
          setStartMsg({ text: '질문을 준비하고 있습니다. 잠시만 기다려 주세요.', error: false });
          const wait = Number(e.extra.retry_after_sec ?? 3) * 1000;
          await new Promise((r) => setTimeout(r, wait));
        }
      }
    },
    onSuccess: () => {
      started.current = true;
      link.current?.close();
      link.current = null;
      stopStream(stream); // the interview opens its own audio-only stream
      void qc.invalidateQueries({ queryKey: ['my-applications'] });
      navigate(`/applications/${encodeURIComponent(aid)}/interview`, { state: { started: true }, replace: true });
    },
    onError: (e) => setStartMsg({ text: describeError(e).message, error: true }),
  });

  const leave = async () => {
    await leavePrepare(aid).catch(() => undefined);
    navigate('/me/applications');
  };

  if (prepare.isError) {
    return (
      <Page title="장비 점검" narrow>
        <Notice kind="error">{describeError(prepare.error).message}</Notice>
        <Link to="/me/applications">지원 현황</Link>
      </Page>
    );
  }

  const mediaOk = !!stream && stream.getAudioTracks().length > 0 && stream.getVideoTracks().length > 0;
  const capacityFull = start.error instanceof ApiError && start.error.code === 'CAPACITY_FULL';
  return (
    <Page title="장비 점검">
      <div className={css.split}>
        <section className={css.stack} aria-label="카메라와 마이크">
          <video ref={video} className={css.preview} autoPlay muted playsInline aria-label="내 카메라 화면" />
          <div className={css.meter} role="meter" aria-label="마이크 입력" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={Math.round(level * 100)}>
            <div className={css.meterBar} style={{ width: `${Math.round(level * 100)}%` }} />
          </div>
          {mediaError && (
            <Notice kind="error">{mediaError} <Button onClick={() => void openMedia()}>다시 시도</Button></Notice>
          )}
        </section>
        <section className={css.stack} aria-label="점검 항목">
          <ul className={css.checks}>
            <li>카메라·마이크: {mediaOk ? <span className={css.ok}>확인됨</span> : <span className={css.muted}>확인 전</span>}</li>
            <li data-testid="voice-check">
              음성 연결: {voice === 'ok' ? <span className={css.ok}>확인됨</span>
                : voice === 'fail' ? <span className={css.fail}>다시 필요</span>
                  : voice === 'listening' ? <span className={css.listening}>듣고 있어요. "안녕하세요"라고 말해 주세요.</span>
                    : voice === 'connecting' ? '연결 중…' : '확인 전'}
            </li>
          </ul>
          {voiceMsg && <Notice kind={voice === 'ok' ? 'success' : 'error'}>{voiceMsg}</Notice>}
          <Button onClick={() => void checkVoice()} disabled={!mediaOk || voice === 'connecting' || voice === 'listening'}
            data-testid="check-voice">
            {voice === 'idle' ? '음성 연결 점검' : '다시 점검'}
          </Button>
          {startMsg && <Notice kind={startMsg.error ? 'error' : 'info'}>{startMsg.text}</Notice>}
          <div className={ui.row}>
            {capacityFull ? (
              <Link to="/me/applications">지원 현황으로</Link>
            ) : (
              <Button variant="primary" onClick={() => { setStartMsg(null); start.mutate(); }} busy={start.isPending}
                disabled={!mediaOk || voice !== 'ok'} data-testid="start-interview">
                면접 시작
              </Button>
            )}
            <Button onClick={() => void leave()} disabled={start.isPending}>나중에 하기</Button>
          </div>
        </section>
      </div>
    </Page>
  );
}
