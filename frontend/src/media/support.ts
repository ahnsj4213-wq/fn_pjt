/**
 * Browser capability check for the interview (NFR-8.7, 8.4). Feature detection decides blocking;
 * the UA hint is only used for the mobile guidance.
 * TODO(06-webrtc-interview): devices, level meter, rtcClient (iceServers: [] fixed), downlink audio player.
 */
export interface SupportResult {
  ok: boolean;
  reasons: string[];
}

interface UaData {
  mobile?: boolean;
}

export function checkBrowserSupport(win: Window = window): SupportResult {
  const reasons: string[] = [];
  const nav = win.navigator as Navigator & { userAgentData?: UaData };
  if (!win.isSecureContext) reasons.push('보안 연결(HTTPS)로 접속해야 마이크를 사용할 수 있습니다.');
  if (!nav.mediaDevices?.getUserMedia) reasons.push('이 브라우저는 마이크·카메라를 지원하지 않습니다.');
  if (typeof (win as Window & { RTCPeerConnection?: unknown }).RTCPeerConnection !== 'function') {
    reasons.push('이 브라우저는 실시간 음성 연결(WebRTC)을 지원하지 않습니다.');
  }
  if (nav.userAgentData?.mobile) reasons.push('모바일 기기에서는 응시할 수 없습니다. PC에서 접속해 주세요.');
  return { ok: reasons.length === 0, reasons };
}
