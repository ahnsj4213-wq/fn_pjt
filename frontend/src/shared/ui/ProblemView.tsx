import { Fragment, type ReactNode } from 'react';
import css from './problem.module.css';

export interface ProblemExample { input: string; output: string; note?: string | null }

/** `code` and **bold** inside one line; everything else is text (no HTML is ever injected). */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i} className={css.code}>{part.slice(1, -1)}</code>;
    if (part.startsWith('**') && part.endsWith('**')) return <b key={i}>{part.slice(2, -2)}</b>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

/**
 * The few Markdown pieces a problem statement uses: `###` headings, paragraphs, `- ` lists, `code`, **bold**.
 * Built as React nodes, so nothing in the text can become markup.
 */
export function MiniMarkdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let items: string[] = [];
  const flush = () => {
    if (para.length) { blocks.push(<p key={blocks.length} className={css.p}>{inline(para.join(' '))}</p>); para = []; }
    if (items.length) {
      blocks.push(<ul key={blocks.length} className={css.ul}>{items.map((t, i) => <li key={i}>{inline(t)}</li>)}</ul>);
      items = [];
    }
  };
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    const h = /^#{1,4}\s+(.*)$/.exec(line);
    if (h) { flush(); blocks.push(<h4 key={blocks.length} className={css.h}>{h[1]}</h4>); }
    else if (line.startsWith('- ')) { if (para.length) flush(); items.push(line.slice(2)); }
    else if (!line) flush();
    else { if (items.length) flush(); para.push(line); }
  }
  flush();
  return <div className={css.md}>{blocks}</div>;
}

/** Statement and examples of a coding problem. Each example shows its input and output side by side and, when there is
 *  one, a plain-language explanation of what the input means and why the output follows. */
export function ProblemView({ statement, examples }: { statement: string; examples: ProblemExample[] }) {
  return (
    <div className={css.problem}>
      <MiniMarkdown text={statement} />
      {examples.length > 0 && (
        <section aria-label="예제">
          <h4 className={css.h}>예제</h4>
          {examples.map((ex, i) => (
            <div className={css.ex} key={i} aria-label={`예제 ${i + 1}`}>
              <div className={css.exh}>예제 {i + 1}</div>
              <div className={css.io}>
                <div><div className={css.lbl}>입력</div><pre className={css.pre}>{ex.input.trimEnd()}</pre></div>
                <div><div className={css.lbl}>출력</div><pre className={css.pre}>{ex.output.trimEnd()}</pre></div>
              </div>
              {ex.note && <p className={css.note}><b>설명</b> {ex.note}</p>}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
