import { lazy, Suspense } from 'react';
import { Outlet, type RouteObject } from 'react-router';
import { Account } from '@/pages/candidate/Account';
import { ApplyFlow } from '@/pages/candidate/apply/ApplyFlow';
import { ApplyComplete } from '@/pages/candidate/ApplyComplete';
import { ApplyResume } from '@/pages/candidate/ApplyResume';
import { ApplyReview } from '@/pages/candidate/ApplyReview';
import { Coding } from '@/pages/candidate/Coding';
import { Consent } from '@/pages/candidate/Consent';
import { Done } from '@/pages/candidate/Done';
import { Guide } from '@/pages/candidate/Guide';
import { Interview } from '@/pages/candidate/Interview';
import { MyApplications } from '@/pages/candidate/MyApplications';
import { MyResume } from '@/pages/candidate/MyResume';
import { PostingDetail } from '@/pages/candidate/PostingDetail';
import { Postings } from '@/pages/candidate/Postings';
import { Precheck } from '@/pages/candidate/Precheck';
import { Result } from '@/pages/candidate/Result';
import { Login } from '@/pages/common/Login';
import { NotFound, RouteError, Unsupported } from '@/pages/common/misc';
import { Signup } from '@/pages/common/Signup';
import { Loading } from '@/shared/ui/components';
import { HomeRedirect, RequireAuth, RequireRole } from './guards';
import { AppLayout, FocusLayout, PublicLayout } from './layouts';

// Recruiter screens are separate lazy chunks (named rc-*), rendered only after RequireRole passes,
// so a candidate session never downloads them (design D 3.2).
const RecruiterHome = lazy(() => import('@/pages/recruiter/Home'));
const PostingForm = lazy(() => import('@/pages/recruiter/PostingForm'));
const PostingPreview = lazy(() => import('@/pages/recruiter/PostingPreview'));
const EvaluationSettings = lazy(() => import('@/pages/recruiter/EvaluationSettings'));
const Applications = lazy(() => import('@/pages/recruiter/Applications'));
const ApplicationDetail = lazy(() => import('@/pages/recruiter/ApplicationDetail'));
const Stats = lazy(() => import('@/pages/recruiter/Stats'));

/** Route table = design D 3.3. */
export const routes: RouteObject[] = [
  {
    errorElement: <RouteError />,
    children: [
      { index: true, element: <HomeRedirect /> },
      {
        element: <PublicLayout />,
        children: [
          { path: 'login', element: <Login /> },
          { path: 'signup', element: <Signup /> },
          { path: 'unsupported', element: <Unsupported /> },
        ],
      },
      {
        element: <RequireAuth><AppLayout /></RequireAuth>,
        children: [
          // Both roles may read public postings; applying is candidate-only (consolidation T21, AC-02).
          { path: 'postings', element: <Postings /> },
          { path: 'postings/:pid', element: <PostingDetail /> },
          {
            element: <RequireRole role="CANDIDATE"><Outlet /></RequireRole>,
            children: [
              {
                // The apply steps share consent id + resume draft in memory (ApplyFlow).
                path: 'postings/:pid/apply',
                element: <ApplyFlow />,
                children: [
                  { path: 'consent', element: <Consent /> },
                  { path: 'resume', element: <ApplyResume /> },
                  { path: 'review', element: <ApplyReview /> },
                  { path: 'complete', element: <ApplyComplete /> },
                ],
              },
              { path: 'me/applications', element: <MyApplications /> },
              { path: 'me/applications/:aid/result', element: <Result /> },
              { path: 'me/resume', element: <MyResume /> },
              { path: 'me/account', element: <Account /> },
              { path: 'applications/:aid/guide', element: <Guide /> },
              { path: 'applications/:aid/precheck', element: <Precheck /> },
              { path: 'applications/:aid/done', element: <Done /> },
            ],
          },
          {
            path: 'recruiter',
            element: (
              <RequireRole role="RECRUITER">
                <Suspense fallback={<Loading />}><Outlet /></Suspense>
              </RequireRole>
            ),
            children: [
              { index: true, element: <RecruiterHome /> },
              { path: 'postings/new', element: <PostingForm /> },
              { path: 'postings/:pid/edit', element: <PostingForm /> },
              { path: 'postings/:pid/preview', element: <PostingPreview /> },
              { path: 'postings/:pid/settings', element: <EvaluationSettings /> },
              { path: 'applications', element: <Applications /> },
              { path: 'applications/:aid', element: <ApplicationDetail /> },
              { path: 'stats', element: <Stats /> },
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
