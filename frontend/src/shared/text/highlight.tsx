import { Fragment } from 'react';

export interface QuoteRange {
  start: number | null;
  end: number | null;
  text: string;
}

export interface Segment {
  text: string;
  marked: boolean;
}

/**
 * Splits `source` into plain/marked segments for verified quote ranges (ADR-0008: the server stores the
 * original range and quote.text is that substring). A range is used only if it is in bounds and
 * source.slice(start, end) === quote.text; anything else is skipped rather than guessed.
 * Overlapping ranges are merged.
 */
export function splitByQuotes(source: string, quotes: QuoteRange[]): Segment[] {
  const valid = quotes
    .filter((q): q is QuoteRange & { start: number; end: number } =>
      q.start !== null && q.end !== null && q.start >= 0 && q.end <= source.length && q.start < q.end
      && source.slice(q.start, q.end) === q.text)
    .map((q) => [q.start, q.end] as [number, number])
    .sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const r of valid) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  const out: Segment[] = [];
  let pos = 0;
  for (const [a, b] of merged) {
    if (a > pos) out.push({ text: source.slice(pos, a), marked: false });
    out.push({ text: source.slice(a, b), marked: true });
    pos = b;
  }
  if (pos < source.length) out.push({ text: source.slice(pos), marked: false });
  return out;
}

/** Renders the answer as text nodes with <mark> around verified quotes (never as HTML, SC-31). */
export function HighlightedText({ text, quotes, markClassName }: { text: string; quotes: QuoteRange[]; markClassName?: string }) {
  return (
    <>
      {splitByQuotes(text, quotes).map((seg, i) => (
        <Fragment key={i}>{seg.marked ? <mark className={markClassName}>{seg.text}</mark> : seg.text}</Fragment>
      ))}
    </>
  );
}
