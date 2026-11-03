import { lazy, Suspense } from 'react';
import { Navigate, Outlet, useParams, type RouteObject } from 'react-router';
import { RecruiterShell } from '@/screens/recruiter/PendingBanner';
import { Account } from '@/screens/candidate/Account';
import { Check } from '@/screens/candidate/apply/Check';
import { ConsentResume } from '@/screens/candidate/apply/ConsentResume';
import { Coding } from '@/screens/candidate/Coding';
import { Done } from '@/screens/candidate/Done';
import { Interview } from '@/screens/candidate/Interview';
import { MyApplications } from '@/screens/candidate/MyApplications';
import { MyPageLayout } from '@/screens/candidate/my/MyPageLayout';
import { MyProfile } from '@/screens/candidate/my/MyProfile';
import { MySchedule } from '@/screens/candidate/my/MySchedule';
import { MyResume } from '@/screens/candidate/MyResume';
import { PostingDetail } from '@/screens/candidate/PostingDetail';
import { Postings } from '@/screens/candidate/Postings';
import { Report } from '@/screens/candidate/Report';
import { Result } from '@/screens/candidate/Result';
import { Login } from '@/screens/common/Login';
import { NotFound, RouteError, Unsupported } from '@/screens/common/misc';
import { ResetPassword } from '@/screens/common/ResetPassword';
import { ScheduleResponse } from '@/screens/common/ScheduleResponse';
import { OperatorLayout } from '@/screens/operator/OperatorLayout';
import { Loading } from '@/shared/ui/components';
import { RequireAuth, RequireRole } from './guards';
import { AppLayout, FocusLayout } from './layouts';

// Recruiter screens are separate lazy chunks (named rc-*), rendered only after RequireRole passes,
// so a candidate session never downloads them (design D 3.2).
const RecruiterHome = lazy(() => import('@/screens/recruiter/Home'));
const RecruiterPostings = lazy(() => import('@/screens/recruiter/Postings'));
const PostingForm = lazy(() => import('@/screens/recruiter/PostingForm'));
const PostingPreview = lazy(() => import('@/screens/recruiter/PostingPreview'));
const EvaluationSettings = lazy(() => import('@/screens/recruiter/EvaluationSettings'));
const Applications = lazy(() => import('@/screens/recruiter/Applications'));
const ApplicationDetail = lazy(() => import('@/screens/recruiter/ApplicationDetail'));
const Stats = lazy(() => import('@/screens/recruiter/Stats'));
const Schedule = lazy(() => import('@/screens/recruiter/Schedule'));
const Inquiries = lazy(() => import('@/screens/recruiter/Inquiries'));
const OperatorCompanies = lazy(() => import('@/screens/operator/Companies'));
const OperatorPostings = lazy(() => import('@/screens/operator/Postings'));

/** The old per-posting settings address now opens 면접 질문 관리 (SC-R07) on that posting. */
function SettingsRedirect() {
  const { pid = '' } = useParams();
  return <Navigate to={`/recruiter/questions?posting=${encodeURIComponent(pid)}`} replace />;
}

/** Route table = design D 3.3. */
export const routes: RouteObject[] = [
  {
    errorElement: <RouteError />,
    children: [
      {
        // Top bar for everyone; the main page and posting details need no login (SC-A02, SC-A03).
        element: <AppLayout />,
        children: [
          { index: true, element: <Postings /> },
          { path: 'postings', element: <Navigate to="/" replace /> },
          { path: 'postings/:pid', element: <PostingDetail /> },
          { path: 'login', element: <Login /> },
          { path: 'signup', element: <Navigate to="/login?signup=1" replace /> },
          { path: 'reset-password', element: <ResetPassword /> },
          { path: 'schedule-response', element: <ScheduleResponse /> },
          { path: 'unsupported', element: <Unsupported /> },
        ],
      },
      {
        element: <RequireAuth><AppLayout /></RequireAuth>,
        children: [
          {
            element: <RequireRole role="CANDIDATE"><Outlet /></RequireRole>,
            children: [
              // SC-A04 환경 점검 -> SC-A05 개인정보 동의와 이력서 -> SC-A06 AI 면접.
              { path: 'postings/:pid/apply', element: <Check /> },
              { path: 'postings/:pid/apply/consent', element: <ConsentResume /> },
              {
                // SC-A09 / A10 / A11 / A15 share the My Page side menu.
                path: 'me',
                element: <MyPageLayout />,
                children: [
                  { index: true, element: <Navigate to="/me/applications" replace /> },
                  { path: 'applications', element: <MyApplications /> },
                  { path: 'resume', element: <MyResume /> },
                  { path: 'profile', element: <MyProfile /> },
                  { path: 'schedule', element: <MySchedule /> },
                  { path: 'account', element: <Account /> },
                ],
              },
              { path: 'me/applications/:aid/report', element: <Report /> },
              { path: 'me/applications/:aid/result', element: <Result /> },
              { path: 'applications/:aid/done', element: <Done /> },
            ],
          },
          {
            path: 'recruiter',
            element: (
              <RequireRole role="RECRUITER">
                <Suspense fallback={<Loading />}><RecruiterShell /></Suspense>
              </RequireRole>
            ),
            children: [
              { index: true, element: <RecruiterHome /> },
              { path: 'postings', element: <RecruiterPostings /> },
              { path: 'postings/new', element: <PostingForm /> },
              { path: 'postings/:pid/edit', element: <PostingForm /> },
              { path: 'postings/:pid/preview', element: <PostingPreview /> },
              { path: 'schedule', element: <Schedule /> },
              { path: 'inquiries', element: <Inquiries /> },
              { path: 'questions', element: <EvaluationSettings /> },
              { path: 'postings/:pid/settings', element: <SettingsRedirect /> },
              { path: 'applications', element: <Applications /> },
              { path: 'applications/:aid', element: <ApplicationDetail /> },
              { path: 'applications/:aid/report', element: <Report mode="recruiter" /> },
              { path: 'stats', element: <Stats /> },
            ],
          },
          {
            // SC-O01 운영자 · 기업 인증. The server (require_operator) is the real control.
            path: 'operator',
            element: <RequireRole role="OPERATOR"><OperatorLayout /></RequireRole>,
            children: [
              { index: true, element: <Suspense fallback={<Loading />}><OperatorCompanies /></Suspense> },
              { path: 'postings', element: <Suspense fallback={<Loading />}><OperatorPostings /></Suspense> },
            ],
          },
        ],
      },
      {
        // Focus layout: no menu during the interview and coding test.
        element: <RequireAuth><RequireRole role="CANDIDATE"><FocusLayout /></RequireRole></RequireAuth>,
        children: [
          { path: 'applications/:aid/interview', element: <Interview /> },
          { path: 'applications/:aid/coding', element: <Coding /> },
        ],
      },
      { path: '*', element: <NotFound /> },
    ],
  },
];
