/**
 * Looking-away detection for SC-A06 (X-06: warned and recorded, never scored automatically). Runs entirely in the
 * browser with MediaPipe's face detector served from this app (public/vision, AC-22); no frame leaves the device.
 *
 * "Away" = no face in the picture, or the head turned far to one side (nose tip off the middle of the eyes).
 */
import { useEffect, useState, type RefObject } from 'react';

export type Gaze = 'loading' | 'ok' | 'away' | 'unavailable';

const SAMPLE_MS = 200;
const YAW_LIMIT = 0.42; // |nose - eye midpoint| / eye distance; about 30 degrees of head turn

type Detector = { detectForVideo(v: HTMLVideoElement, t: number): { detections: { keypoints: { x: number; y: number }[] }[] }; close(): void };

async function createDetector(): Promise<Detector> {
  const { FaceDetector, FilesetResolver } = await import('@mediapipe/tasks-vision');
  const files = await FilesetResolver.forVisionTasks('/vision');
  const opts = (delegate: 'GPU' | 'CPU') => ({
    baseOptions: { modelAssetPath: '/vision/blaze_face_short_range.tflite', delegate },
    runningMode: 'VIDEO' as const,
    minDetectionConfidence: 0.5,
  });
  return FaceDetector.createFromOptions(files, opts('GPU')).catch(() => FaceDetector.createFromOptions(files, opts('CPU')));
}

/** Pure decision, exported for tests. Keypoints: right eye, left eye, nose tip, mouth, right ear, left ear. */
export function judge(faces: { keypoints: { x: number; y: number }[] }[]): 'ok' | 'away' {
  const k = faces[0]?.keypoints;
  if (!k || k.length < 3) return 'away';
  const [re, le, nose] = k;
  const eyeDist = Math.hypot(le.x - re.x, le.y - re.y);
  if (eyeDist < 1e-3) return 'away';
  const yaw = Math.abs(nose.x - (re.x + le.x) / 2) / eyeDist;
  return yaw > YAW_LIMIT ? 'away' : 'ok';
}

export function useGaze(video: RefObject<HTMLVideoElement | null>, enabled: boolean): Gaze {
  const [gaze, setGaze] = useState<Gaze>('loading');
  useEffect(() => {
    if (!enabled) return;
    let detector: Detector | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    let live = true;
    createDetector().then((d) => {
      if (!live) { d.close(); return; }
      detector = d;
      timer = setInterval(() => {
        const v = video.current;
        if (!v || v.readyState < 2 || v.videoWidth === 0) return;
        try {
          setGaze(judge(d.detectForVideo(v, performance.now()).detections));
        } catch {
          setGaze('unavailable');
        }
      }, SAMPLE_MS);
    }).catch(() => { if (live) setGaze('unavailable'); });
    return () => {
      live = false;
      if (timer) clearInterval(timer);
      detector?.close();
    };
  }, [video, enabled]);
  return gaze;
}
