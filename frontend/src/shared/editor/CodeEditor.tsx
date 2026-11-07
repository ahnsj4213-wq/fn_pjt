import { python } from '@codemirror/lang-python';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { EditorState } from '@codemirror/state';
import { tags as t } from '@lezer/highlight';
import { basicSetup, EditorView } from 'codemirror';
import { useEffect, useRef } from 'react';

/** Editor colors come from the app theme variables, so the text keeps its contrast in light and in dark mode. */
const appTheme = EditorView.theme({
  '&': { color: 'var(--ink)', backgroundColor: 'var(--surface)', fontSize: '14px' },
  '.cm-scroller': { minHeight: '320px' },
  '.cm-content': { minHeight: '320px', caretColor: 'var(--accent)', padding: '10px 0', lineHeight: '1.65' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--accent)', borderLeftWidth: '2px' },
  '.cm-gutters': { backgroundColor: 'var(--bg)', color: 'var(--muted)', border: 'none', borderRight: '1px solid var(--line)' },
  '.cm-activeLine': { backgroundColor: 'var(--accent-soft)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--accent-soft)', color: 'var(--ink)' },
  '&.cm-focused': { outline: '2px solid var(--accent)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: 'var(--accent-soft)' },
});
const appHighlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.operatorKeyword], color: 'var(--accent)', fontWeight: '600' },
  { tag: [t.string, t.special(t.string)], color: 'var(--warn-ink, #b4690e)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--warn-ink, #b4690e)' },
  { tag: [t.comment, t.lineComment], color: 'var(--muted)', fontStyle: 'italic' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.variableName)], color: 'var(--ink)', fontWeight: '600' },
  { tag: [t.className, t.typeName], color: 'var(--accent)' },
]);

/**
 * CodeMirror 6 Python editor, bundled locally (COD-02, AC-22). Used by the candidate coding screen
 * and, read-only, by the recruiter's submitted-code view.
 * TODO(CSP): confirm no securitypolicyviolation under the production CSP; otherwise wire EditorView.cspNonce.
 */
export function CodeEditor({ value, onChange, readOnly = false, label }: {
  value: string;
  onChange?: (code: string) => void;
  readOnly?: boolean;
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const changeRef = useRef(onChange);
  changeRef.current = onChange;

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          python(),
          appTheme,
          syntaxHighlighting(appHighlight),
          EditorState.tabSize.of(4),
          EditorState.readOnly.of(readOnly),
          EditorView.editable.of(!readOnly),
          EditorView.contentAttributes.of({ 'aria-label': label }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) changeRef.current?.(u.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // The document is owned by CodeMirror after mount; external value changes go through the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly, label]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div ref={host} />;
}
