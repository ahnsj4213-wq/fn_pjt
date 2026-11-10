import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { fetchPosting } from '@/api/common/postings';
import { useSession } from '@/app/session';
import { useGaze } from '@/media/gaze';
import { ANSWER_AUDIO, stopStream } from '@/media/rtc';
import { useToast } from '@/shared/ui/overlay';
import { useMicLevel } from '../interview/hooks';
import { applyPath, saveDeviceCheck } from './applyState';
import { Stepper } from './Stepper';

type Kind = 'cam' | 'mic' | 'spk';
type Dev = { id: string; label: string };

const MIC_TEST_MS = 1600;
const CAM_SETTLE_MS = 900;
const VOICE_LEVEL = 0.12; // useMicLevel scale: quiet room noise stays well below this
const MIC_HINT = '버튼을 누르고 "안녕하십니까, 면접을 시작합니다"라고 말해 보십시오.';
const KIND_NAME: Record<Kind, string> = { cam: '카메라', mic: '마이크', spk: '스피커' };

/** Why a device could not be opened, in words the applicant can act on. */
function deviceProblem(name: string, e: unknown): string {
  const n = (e as DOMException | undefined)?.name;
  if (n === 'NotAllowedError' || n === 'SecurityError') return `${name} 사용이 차단되어 있습니다. 주소창 왼쪽의 자물쇠에서 ${name}를 허용한 뒤 다시 시도해 주십시오.`;
  if (n === 'NotFoundError' || n === 'OverconstrainedError') {
    return name === '마이크'
      ? '마이크를 찾지 못했습니다. 이어폰이 마이크를 지원하는지 확인하고, 빼었다 다시 꽂은 뒤 다시 시도해 주십시오. (컴퓨터 설정의 소리 입력 장치에 이어폰이 보여야 합니다)'
      : '카메라를 찾지 못했습니다. 카메라 연결을 확인한 뒤 다시 시도해 주십시오.';
  }
  if (n === 'NotReadableError' || n === 'AbortError') return `${name}를 다른 프로그램(화상 회의, 녹음 앱 등)이 쓰고 있습니다. 그 프로그램을 끝내고 다시 시도해 주십시오.`;
  return `${name}를 사용할 수 없습니다. 연결과 브라우저 권한을 확인한 뒤 다시 시도해 주십시오.`;
}

/** 0.8 s 440 Hz tone as a WAV blob URL, so it can be routed to the chosen speaker with setSinkId. */
function toneUrl(): string {
  const rate = 22050;
  const n = Math.round(rate * 0.8);
  const buf = new DataView(new ArrayBuffer(44 + n * 2));
  const str = (o: number, s: string) => { for (let i = 0; i < s.length; i++) buf.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); buf.setUint32(4, 36 + n * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  buf.setUint32(16, 16, true); buf.setUint16(20, 1, true); buf.setUint16(22, 1, true);
  buf.setUint32(24, rate, true); buf.setUint32(28, rate * 2, true); buf.setUint16(32, 2, true); buf.setUint16(34, 16, true);
  str(36, 'data'); buf.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const fade = Math.min(1, i / 800, (n - i) / 800);
    buf.setInt16(44 + i * 2, Math.sin((2 * Math.PI * 440 * i) / rate) * 0.4 * fade * 32767, true);
  }
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

/** SC-A04 면접 환경 점검: camera, mic and speaker checked in the browser. Nothing is recorded or sent. */
export function Check() {
  const { pid = '' } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const me = useSession().data?.user.name ?? '';
  const posting = useQuery({ queryKey: ['posting', pid], queryFn: () => fetchPosting(pid) });
  const video = useRef<HTMLVideoElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [devs, setDevs] = useState<Record<Kind, Dev[]>>({ cam: [], mic: [], spk: [] });
  const [sel, setSel] = useState<Record<Kind, string>>({ cam: '', mic: '', spk: '' });
  const [ok, setOk] = useState<Record<Kind, boolean>>({ cam: false, mic: false, spk: false });
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [micTesting, setMicTesting] = useState(false);
  const [micFailed, setMicFailed] = useState(false);
  const [spkAsk, setSpkAsk] = useState(false);
  const level = useMicLevel(stream);
  const face = useGaze(video, ok.cam); // the face guide reads the real picture, nothing is sent
  const peak = useRef(0);
  const audio = useRef<HTMLAudioElement | null>(null);

  if (micTesting) peak.current = Math.max(peak.current, level);

  const listDevices = async () => {
    const all = await navigator.mediaDevices.enumerateDevices();
    const pick = (k: MediaDeviceKind, fallback: string) => {
      const list = all.filter((d) => d.kind === k && d.deviceId !== 'communications')
        .map((d, i) => ({ id: d.deviceId, label: d.label || `${fallback} ${i + 1}` }));
      return list.length ? list : [{ id: '', label: `기본 ${fallback}` }];
    };
    setDevs({ cam: pick('videoinput', '카메라'), mic: pick('audioinput', '마이크'), spk: pick('audiooutput', '스피커') });
  };

  /** Camera and microphone are asked separately when the pair fails, so the message can name the one that is the problem. */
  const open = async (cam: string, mic: string) => {
    setMediaError(null);
    const md = navigator.mediaDevices;
    if (!md?.getUserMedia) {
      setStream((old) => { stopStream(old); return null; });
      setMediaError('보안 연결(https 또는 localhost)로 접속해야 카메라와 마이크를 쓸 수 있습니다. 주소창의 주소를 확인해 주십시오.');
      return;
    }
    const audioC = (id: string): MediaTrackConstraints => (id ? { ...ANSWER_AUDIO, deviceId: { exact: id } } : ANSWER_AUDIO);
    const videoC = (id: string): MediaTrackConstraints | boolean => (id ? { deviceId: { exact: id } } : true);
    const one = async (kind: 'audio' | 'video', id: string): Promise<{ s: MediaStream | null; err: unknown }> => {
      const ask = (useId: string) => md.getUserMedia(kind === 'audio' ? { audio: audioC(useId) } : { video: videoC(useId) });
      try { return { s: await ask(id), err: null }; } catch (e) {
        // A device that was unplugged since the list was made: fall back to the default one.
        if (id && (e as DOMException).name === 'OverconstrainedError') {
          try { return { s: await ask(''), err: null }; } catch (e2) { return { s: null, err: e2 }; }
        }
        return { s: null, err: e };
      }
    };
    let s: MediaStream | null = null;
    let camErr: unknown = null;
    let micErr: unknown = null;
    try {
      s = await md.getUserMedia({ video: videoC(cam), audio: audioC(mic) });
    } catch {
      const [v, a] = await Promise.all([one('video', cam), one('audio', mic)]);
      camErr = v.err; micErr = a.err;
      const tracks = [...(v.s?.getTracks() ?? []), ...(a.s?.getTracks() ?? [])];
      s = tracks.length ? new MediaStream(tracks) : null;
    }
    setStream((old) => { stopStream(old); return s; });
    if (s) {
      const v = s.getVideoTracks()[0]?.getSettings().deviceId ?? cam;
      const a = s.getAudioTracks()[0]?.getSettings().deviceId ?? mic;
      setSel((p) => ({ ...p, cam: v, mic: a }));
    }
    const problems = [camErr && deviceProblem('카메라', camErr), micErr && deviceProblem('마이크', micErr)].filter(Boolean);
    setMediaError(problems.length ? problems.join(' ') : null);
    await listDevices().catch(() => undefined); // labels are only filled in after permission; the lists are useful even after a failure
  };

  // Plugging in an earphone after the page opened: look again.
  useEffect(() => {
    const md = navigator.mediaDevices;
    if (!md?.addEventListener) return;
    const again = () => { void listDevices().catch(() => undefined); if (mediaError) void open('', ''); };
    md.addEventListener('devicechange', again);
    return () => md.removeEventListener('devicechange', again);
  });

  useEffect(() => {
    void open('', '');
    return () => { setStream((s) => { stopStream(s); return null; }); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (video.current) video.current.srcObject = stream;
    if (!stream?.getVideoTracks().length) return;
    const t = setTimeout(() => setOk((p) => ({ ...p, cam: true })), CAM_SETTLE_MS);
    return () => clearTimeout(t);
  }, [stream]);

  useEffect(() => () => { audio.current?.pause(); }, []);

  const change = (k: Kind, id: string) => {
    setSel((p) => ({ ...p, [k]: id }));
    setOk((p) => ({ ...p, [k]: false }));
    setSpkAsk(false);
    setMicFailed(false);
    if (k === 'cam') void open(id, sel.mic);
    if (k === 'mic') void open(sel.cam, id);
    toast(`${devs[k].find((d) => d.id === id)?.label ?? KIND_NAME[k]}로 바꿨습니다. 다시 점검해 주십시오`);
  };

  const testMic = () => {
    peak.current = 0;
    setMicFailed(false);
    setMicTesting(true);
    setTimeout(() => {
      setMicTesting(false);
      const heard = peak.current >= VOICE_LEVEL;
      setOk((p) => ({ ...p, mic: heard }));
      setMicFailed(!heard);
    }, MIC_TEST_MS);
  };

  const playTone = async () => {
    audio.current?.pause();
    const a = new Audio(toneUrl());
    audio.current = a;
    const sink = (a as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }).setSinkId;
    if (sel.spk && sink) await sink.call(a, sel.spk).catch(() => undefined);
    a.onended = () => URL.revokeObjectURL(a.src);
    await a.play().catch(() => undefined);
    setSpkAsk(true);
  };

  const all = ok.cam && ok.mic && ok.spk;
  const next = () => {
    saveDeviceCheck({ cam: sel.cam || null, mic: sel.mic || null, spk: sel.spk || null });
    navigate(applyPath(pid, 'consent'));
  };

  const p = posting.data;
  const row = (k: Kind, body: React.ReactNode) => (
    <div className="chk">
      <div className="chk-h">
        <b>{KIND_NAME[k]}</b>
        <span className={`pill ${ok[k] ? 'good' : 'none'}`} data-testid={`st-${k}`}>{ok[k] ? '정상' : '확인 전'}</span>
      </div>
      <div className="field">
        <label htmlFor={`dv-${k}`} className="sr">{KIND_NAME[k]} 장치</label>
        <select id={`dv-${k}`} value={sel[k]} onChange={(e) => change(k, e.target.value)}>
          {devs[k].map((d) => <option key={d.id || 'default'} value={d.id}>{d.label}</option>)}
        </select>
      </div>
      {body}
    </div>
  );
  const bars = Math.round((micTesting ? level : 0) * 16);

  if (posting.data?.has_applied) return <Navigate to="/me/applications" replace />; // one application per posting (JOB-02)

  return (
    <div className="wrap"><div className="stub xwide">
      <Stepper cur={0} />
      <h1>면접 환경 점검</h1>
      <p className="sub">
        {p ? `${p.company_name} · ${p.title} ` : ''}면접 전에 카메라, 마이크, 스피커가 잘 되는지 확인합니다. 다른 장치를 쓰려면 목록에서 바꿔 주십시오.
      </p>
      {mediaError && (
        <div className="notice bad" role="alert">
          {mediaError}{' '}
          <button className="btn" type="button" onClick={() => void open(sel.cam, sel.mic)}>다시 시도</button>
        </div>
      )}
      <div className="checkgrid">
        <div className="preview">
          <video ref={video} autoPlay muted playsInline aria-label="내 카메라 화면" />
          <span className="me">{me}</span>
          <span className="frame" aria-hidden="true" />
          <span className="ptag">
            {!ok.cam ? '카메라 연결 중…' : face === 'away' ? '얼굴이 잘 보이지 않습니다. 점선 안에 맞춰 주십시오'
              : face === 'ok' ? '얼굴이 화면 가운데에 있습니다' : '카메라가 연결되었습니다'}
          </span>
        </div>
        <div className="chks">
          {row('cam', <p className="hint">얼굴이 점선 안에 들어오도록 자리를 맞춰 주십시오.</p>)}
          {row('mic', (
            <>
              <div className={`meter${micTesting ? ' on' : ''}`} aria-hidden="true">
                {Array.from({ length: 16 }, (_, i) => (
                  <i key={i} className={i < bars ? 'lit' : ''} style={micTesting ? { animation: 'none', height: `${20 + (i < bars ? 80 : 0)}%` } : undefined} />
                ))}
              </div>
              <div className="chk-a">
                <button className="btn" type="button" onClick={testMic} disabled={micTesting || !stream?.getAudioTracks().length}>마이크 테스트</button>
                <span className={`hint${ok.mic ? ' ok' : ''}`}>
                  {micTesting ? '듣고 있습니다…' : ok.mic ? '목소리가 잘 들립니다.'
                    : micFailed ? '목소리가 들리지 않았습니다. 마이크를 확인하고 조금 더 크게 말해 주십시오.' : MIC_HINT}
                </span>
              </div>
            </>
          ))}
          {row('spk', (
            <div className="chk-a">
              {ok.spk ? <span className="hint ok">테스트 소리가 잘 들렸습니다.</span>
                : spkAsk ? (
                  <>
                    <span className="hint">소리가 들렸습니까?</span>
                    <button className="btn" type="button" onClick={() => { setOk((x) => ({ ...x, spk: true })); setSpkAsk(false); }}>들립니다</button>
                    <button className="btn" type="button" onClick={() => { setSpkAsk(false); toast('볼륨을 높이거나 목록에서 다른 스피커를 골라 주십시오'); }}>들리지 않습니다</button>
                  </>
                ) : (
                  <>
                    <button className="btn" type="button" onClick={() => void playTone()}>테스트 소리 재생</button>
                    <span className="hint">면접관 목소리가 이 장치로 나옵니다.</span>
                  </>
                )}
            </div>
          ))}
        </div>
      </div>
      <div className="mfoot">
        <button className="btn btn-lg" type="button" onClick={() => navigate(`/postings/${encodeURIComponent(pid)}`)}>이전 단계</button>
        <button className="btn btn-primary btn-lg" type="button" disabled={!all} onClick={next} data-testid="check-next">
          {all ? '다음 단계' : '점검을 마치면 넘어갈 수 있습니다'}
        </button>
      </div>
    </div></div>
  );
}
