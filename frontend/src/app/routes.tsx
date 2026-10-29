import { lazy, Suspense } from 'react';
import { Outlet, type RouteObject } from 'react-router';
import { Account } from '@/screens/candidate/Account';
import { ApplyFlow } from '@/screens/candidate/apply/ApplyFlow';
import { ApplyComplete } from '@/screens/candidate/ApplyComplete';
import { ApplyResume } from '@/screens/candidate/ApplyResume';
import { ApplyReview } from '@/screens/candidate/ApplyReview';
import { Coding } from '@/screens/candidate/Coding';
import { Consent } from '@/screens/candidate/Consent';
import { Done } from '@/screens/candidate/Done';
import { Guide } from '@/screens/candidate/Guide';
import { Interview } from '@/screens/candidate/Interview';
import { MyApplications } from '@/screens/candidate/MyApplications';
import { MyResume } from '@/screens/candidate/MyResume';
import { PostingDetail } from '@/screens/candidate/PostingDetail';
import { Postings } from '@/screens/candidate/Postings';
import { Precheck } from '@/screens/candidate/Precheck';
import { Result } from '@/screens/candidate/Result';
import { Login } from '@/screens/common/Login';
import { NotFound, RouteError, Unsupported } from '@/screens/common/misc';
import { Signup } from '@/screens/common/Signup';
import { Loading } from '@/shared/ui/components';
import { HomeRedirect, RequireAuth, RequireRole } from './guards';
import { AppLayout, FocusLayout, PublicLayout } from './layouts';

// Recruiter screens are separate lazy chunks (named rc-*), rendered only after RequireRole passes,
// so a candidate session never downloads them (design D 3.2).
const RecruiterHome = lazy(() => import('@/screens/recruiter/Home'));
const PostingForm = lazy(() => import('@/screens/recruiter/PostingForm'));
const PostingPreview = lazy(() => import('@/screens/recruiter/PostingPreview'));
const EvaluationSettings = lazy(() => import('@/screens/recruiter/EvaluationSettings'));
const Applications = lazy(() => import('@/screens/recruiter/Applications'));
const ApplicationDetail = lazy(() => import('@/screens/recruiter/ApplicationDetail'));
const Stats = lazy(() => import('@/screens/recruiter/Stats'));

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
