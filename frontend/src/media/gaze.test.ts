import { describe, expect, it } from 'vitest';
import { judge } from './gaze';

const face = (noseX: number) => ({ keypoints: [{ x: 0.4, y: 0.4 }, { x: 0.6, y: 0.4 }, { x: noseX, y: 0.5 }] });

describe('looking-away judgement (SC-A06)', () => {
  it('no face is away', () => expect(judge([])).toBe('away'));
  it('facing the screen is ok', () => expect(judge([face(0.5)])).toBe('ok'));
  it('head turned to one side is away', () => {
    expect(judge([face(0.6)])).toBe('away');
    expect(judge([face(0.4)])).toBe('away');
  });
});
