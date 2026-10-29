import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

// Module boundaries (design D 3.2, SEC-04 defence in depth). Patterns cover both the "@/..." alias and
// relative paths. The server still enforces every rule (SEC-03); this only stops accidental imports.
// Any "recruiter" / "candidate" / "media" path segment, so sibling-relative imports like
// "../recruiter/Home" are caught as well as "@/api/recruiter".
const RECRUITER_MODULES = ['@/pages/recruiter', '@/pages/recruiter/*', '@/api/recruiter', '@/api/recruiter/*',
  '**/recruiter', '**/recruiter/**'];
const CANDIDATE_MODULES = ['@/pages/candidate', '@/pages/candidate/*', '@/api/candidate', '@/api/candidate/*',
  '@/media', '@/media/*', '**/candidate', '**/candidate/**', '**/media', '**/media/**'];
const APP_LAYER_MODULES = ['@/app', '@/app/*', '@/pages', '@/pages/*', '**/app/**', '**/pages/**'];

const restrict = (group, message) => ['error', { patterns: [{ group, message }] }];

export default defineConfig(
  { ignores: ['dist', '.next', 'next-env.d.ts', 'coverage', 'node_modules', 'playwright-report', 'test-results'] },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      // XSS (E SC-30): no raw HTML sinks, no string evaluation.
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      'no-restricted-syntax': [
        'error',
        { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: 'react/no-danger: 원시 HTML 렌더링 금지 (SC-30). 텍스트로만 표시한다.' },
        { selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]", message: 'innerHTML/outerHTML 대입 금지 (SC-30).' },
        { selector: "CallExpression[callee.property.name='insertAdjacentHTML']", message: 'insertAdjacentHTML 금지 (SC-30).' },
        // INT-08 / 8.5: the camera is a local preview only; nothing is ever recorded.
        { selector: "NewExpression[callee.name='MediaRecorder']", message: '녹화 금지 (INT-08, 8.5).' },
      ],
    },
  },
  {
    files: ['src/pages/candidate/**/*.{ts,tsx}', 'src/api/candidate/**/*.{ts,tsx}', 'src/media/**/*.{ts,tsx}'],
    rules: { 'no-restricted-imports': restrict(RECRUITER_MODULES, '지원자 코드는 담당자 모듈을 import할 수 없다 (SEC-04, 설계안 D 3.2).') },
  },
  {
    files: ['src/pages/recruiter/**/*.{ts,tsx}', 'src/api/recruiter/**/*.{ts,tsx}'],
    rules: { 'no-restricted-imports': restrict(CANDIDATE_MODULES, '담당자 코드는 지원자 모듈을 import할 수 없다 (설계안 D 3.2).') },
  },
  {
    files: ['src/shared/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': restrict([...APP_LAYER_MODULES, ...RECRUITER_MODULES, ...CANDIDATE_MODULES],
        'shared는 app·pages·역할별 api·media에 의존하지 않는다 (설계안 D 3.2).'),
    },
  },
  {
    files: ['*.{js,mjs,ts}', 'scripts/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
);
