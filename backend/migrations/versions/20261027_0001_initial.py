"""Initial schema: accounts, postings, applications (3 separate states), interview snapshots,
coding, evaluation, decisions, audit, durable job queue.

Source: docs/trd/03-data-model.md (derived from agent B proposal + review round 1).
Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = r"""
-- ── accounts ────────────────────────────────────────────────────────────────
CREATE TABLE companies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  intro text,                                   -- grounding for company questions (6.1)
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  role text NOT NULL CHECK (role IN ('CANDIDATE','RECRUITER','OPERATOR')),
  company_id uuid REFERENCES companies(id),
  email text CHECK (email = lower(email)),
  password_hash text,
  name text,
  phone text,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DISABLED','WITHDRAWN')),
  created_at timestamptz NOT NULL DEFAULT now(),
  withdrawn_at timestamptz,
  CHECK ((role = 'RECRUITER') = (company_id IS NOT NULL)),
  CHECK (status = 'WITHDRAWN' OR (email IS NOT NULL AND password_hash IS NOT NULL AND name IS NOT NULL)),
  CHECK (role <> 'CANDIDATE' OR status = 'WITHDRAWN' OR phone ~ '^010-[0-9]{4}-[0-9]{4}$'),
  UNIQUE (id, company_id)
);
-- No birth date / gender / photo columns exist (USR-02, 8.5); tests/test_schema.py asserts it.
CREATE UNIQUE INDEX ux_users_email ON users(email) WHERE email IS NOT NULL;

CREATE TABLE sessions (
  token_hash bytea PRIMARY KEY,                 -- SHA-256 of the cookie token; raw token never stored
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role text NOT NULL,
  company_id uuid,
  csrf_token text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL               -- last_seen + 2h (USR-06)
);
CREATE INDEX ix_sessions_user ON sessions(user_id);

CREATE TABLE auth_throttle (                    -- SC-04: keyed by HMAC(email) and client IP
  key text PRIMARY KEY,
  failures int NOT NULL DEFAULT 0,
  window_started_at timestamptz NOT NULL DEFAULT now(),
  locked_until timestamptz
);

-- ── postings, coding problems, evaluation settings ─────────────────────────
CREATE TABLE coding_problems (                  -- registered by the team via CLI (OQ-14); immutable once used
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title text NOT NULL,
  statement_md text NOT NULL,
  language text NOT NULL DEFAULT 'python' CHECK (language = 'python'),   -- X-10
  time_limit_ms int NOT NULL CHECK (time_limit_ms BETWEEN 100 AND 20000),
  memory_mb int NOT NULL CHECK (memory_mb BETWEEN 32 AND 512),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE coding_testcases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  problem_id uuid NOT NULL REFERENCES coding_problems(id),
  ordinal smallint NOT NULL,
  visibility text NOT NULL CHECK (visibility IN ('PUBLIC','HIDDEN')),
  input text NOT NULL,
  expected_output text NOT NULL,
  UNIQUE (problem_id, ordinal)
);

CREATE TABLE job_postings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES companies(id),
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','OPEN','CLOSED')),
  title text,
  job_family text,
  requirements text,
  preferred text,
  duties text,
  headcount int CHECK (headcount > 0),          -- display only (JOB-01)
  deadline_at timestamptz,
  coding_enabled boolean NOT NULL DEFAULT false,
  coding_problem_id uuid REFERENCES coding_problems(id),
  coding_language text CHECK (coding_language = 'python'),
  coding_time_limit_min smallint CHECK (coding_time_limit_min BETWEEN 10 AND 180),  -- OQ-17
  current_settings_id uuid,
  settings_locked_at timestamptz,               -- first INTERVIEWING entry (JOB-06, OQ-05)
  published_at timestamptz,
  closed_at timestamptz,
  close_reason text CHECK (close_reason IN ('MANUAL','DEADLINE')),
  deleted_at timestamptz,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  row_version int NOT NULL DEFAULT 1,
  UNIQUE (id, company_id),
  FOREIGN KEY (created_by, company_id) REFERENCES users(id, company_id),
  CHECK (status = 'DRAFT' OR (title IS NOT NULL AND job_family IS NOT NULL AND deadline_at IS NOT NULL
         AND current_settings_id IS NOT NULL)),
  CHECK (status = 'DRAFT' OR NOT coding_enabled OR (coding_problem_id IS NOT NULL
         AND coding_language IS NOT NULL AND coding_time_limit_min IS NOT NULL)),
  CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL))
);
CREATE INDEX ix_postings_company ON job_postings(company_id, status, deadline_at) WHERE deleted_at IS NULL;
CREATE INDEX ix_postings_open_deadline ON job_postings(deadline_at) WHERE status = 'OPEN';

CREATE TABLE posting_eval_settings (            -- insert-only; one row per version
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  posting_id uuid NOT NULL,
  company_id uuid NOT NULL,
  version int NOT NULL,
  w_expertise smallint NOT NULL CHECK (w_expertise BETWEEN 0 AND 100),
  w_logic smallint NOT NULL CHECK (w_logic BETWEEN 0 AND 100),
  w_specificity smallint NOT NULL CHECK (w_specificity BETWEEN 0 AND 100),
  w_delivery smallint NOT NULL CHECK (w_delivery BETWEEN 0 AND 100),
  w_coding smallint CHECK (w_coding BETWEEN 0 AND 100),   -- NULL when coding is off (6.4)
  custom_question text CHECK (char_length(custom_question) BETWEEN 5 AND 500),  -- at most 1 (X-09)
  rubric_version text NOT NULL,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (posting_id, version),
  UNIQUE (id, posting_id),
  FOREIGN KEY (posting_id, company_id) REFERENCES job_postings(id, company_id),
  CHECK (w_expertise + w_logic + w_specificity + w_delivery + COALESCE(w_coding, 0) = 100)   -- ADM-05, AC-21
);
ALTER TABLE job_postings ADD CONSTRAINT fk_postings_current_settings
  FOREIGN KEY (current_settings_id, id) REFERENCES posting_eval_settings(id, posting_id) DEFERRABLE INITIALLY DEFERRED;

-- ── consent and resumes ────────────────────────────────────────────────────
CREATE TABLE consent_documents (
  version text PRIMARY KEY,                     -- e.g. consent-v0-draft (OQ-04, legal review pending)
  body_md text NOT NULL,
  items jsonb NOT NULL,                         -- [{code,label,required}]
  is_draft boolean NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE consent_records (                  -- insert-only (SEC-02)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id uuid NOT NULL REFERENCES users(id),
  posting_id uuid NOT NULL REFERENCES job_postings(id),
  document_version text NOT NULL REFERENCES consent_documents(version),
  answers jsonb NOT NULL,                       -- {"purpose": true, ...} per item
  all_required_agreed boolean NOT NULL,
  agreed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_consent_candidate ON consent_records(candidate_id, posting_id);

CREATE TABLE resume_files (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES users(id),
  purpose text NOT NULL CHECK (purpose IN ('REGISTERED','APPLICATION','UPLOAD')),
  storage_key text NOT NULL UNIQUE,
  size_bytes int NOT NULL CHECK (size_bytes BETWEEN 1 AND 10485760),       -- RES-02
  sha256 bytea NOT NULL,
  page_count int,
  text_extractable boolean NOT NULL,            -- false -> manual input guidance (RES-04)
  extracted_text text,
  extraction_status text NOT NULL DEFAULT 'PENDING' CHECK (extraction_status IN ('PENDING','DONE','NO_TEXT','FAILED')),
  extracted_fields jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE resumes (                          -- registered resume on My Page (one per candidate)
  candidate_id uuid PRIMARY KEY REFERENCES users(id),
  content jsonb NOT NULL,                       -- {name, education[], career[], projects[], skills[], certificates[]}
  source text NOT NULL CHECK (source IN ('PDF','MANUAL')),
  file_id uuid REFERENCES resume_files(id),
  schema_version smallint NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ── applications: attempt / evaluation / decision kept apart (PRD 7) ──────
CREATE TABLE applications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL,
  posting_id uuid NOT NULL,
  candidate_id uuid NOT NULL REFERENCES users(id),
  consent_record_id uuid NOT NULL REFERENCES consent_records(id),
  resume_snapshot jsonb NOT NULL,               -- frozen at application time (RES-07)
  resume_snapshot_text text NOT NULL,           -- flattened snapshot, grounding source for QST-03
  resume_file_id uuid REFERENCES resume_files(id),
  attempt_status text NOT NULL DEFAULT 'READY' CHECK (attempt_status IN
    ('READY','PREPARING','INTERVIEWING','CODING','INTERRUPTED','SUBMITTED','EXPIRED')),
  eval_status text CHECK (eval_status IN ('PENDING','RUNNING','DONE','ON_HOLD')),   -- NULL = no evaluation (OQ-06)
  decision_status text NOT NULL DEFAULT 'UNDECIDED' CHECK (decision_status IN ('UNDECIDED','PASS','FAIL')),
  eval_settings_id uuid,
  answered_main_count smallint NOT NULL DEFAULT 0 CHECK (answered_main_count BETWEEN 0 AND 10),
  interrupted_from text CHECK (interrupted_from IN ('INTERVIEWING','CODING')),
  applied_at timestamptz NOT NULL DEFAULT now(),
  interview_started_at timestamptz,
  submitted_at timestamptz,
  expired_at timestamptz,
  eval_done_at timestamptz,
  first_decided_at timestamptz,
  decided_at timestamptz,
  decided_by uuid,
  withdrawn_at timestamptz,
  audio_purged_at timestamptz,
  content_purged_at timestamptz,
  UNIQUE (posting_id, candidate_id),            -- JOB-02, AC-04
  UNIQUE (id, company_id),
  FOREIGN KEY (posting_id, company_id) REFERENCES job_postings(id, company_id),
  FOREIGN KEY (eval_settings_id, posting_id) REFERENCES posting_eval_settings(id, posting_id),
  FOREIGN KEY (decided_by, company_id) REFERENCES users(id, company_id),
  CHECK (attempt_status IN ('SUBMITTED','EXPIRED') OR eval_status IS NULL),
  CHECK (attempt_status <> 'SUBMITTED' OR eval_status IS NOT NULL OR withdrawn_at IS NOT NULL),
  CHECK (decision_status = 'UNDECIDED' OR eval_status = 'DONE'
         OR (attempt_status = 'EXPIRED' AND eval_status IS NULL)),   -- 7.3; EXPIRED w/o answers: OQ-07
  CHECK ((decision_status = 'UNDECIDED') = (decided_at IS NULL)),
  CHECK ((attempt_status = 'INTERRUPTED') = (interrupted_from IS NOT NULL)),
  CHECK (attempt_status NOT IN ('INTERVIEWING','CODING','SUBMITTED') OR eval_settings_id IS NOT NULL)
);
CREATE INDEX ix_app_recruiter_list ON applications(company_id, posting_id, decision_status, eval_status);
CREATE INDEX ix_app_candidate ON applications(candidate_id, applied_at DESC);
CREATE INDEX ix_app_active ON applications(attempt_status)
  WHERE attempt_status IN ('PREPARING','INTERVIEWING','CODING','INTERRUPTED');

CREATE TABLE state_transitions (                -- single source for allowed transitions; code loads the same table
  machine text NOT NULL,
  from_state text NOT NULL,                     -- '-' stands for NULL
  to_state text NOT NULL,
  PRIMARY KEY (machine, from_state, to_state)
);
CREATE TABLE application_state_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  application_id uuid NOT NULL REFERENCES applications(id),
  machine text NOT NULL,
  from_state text,
  to_state text NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_state_events_app ON application_state_events(application_id, id);

-- ── interview ──────────────────────────────────────────────────────────────
CREATE TABLE interviews (                       -- one row per application = one attempt (INT-12)
  application_id uuid PRIMARY KEY,
  company_id uuid NOT NULL,
  question_set_id uuid,
  precheck_media_ok_at timestamptz,             -- INT-01 (client-reported mic/camera)
  precheck_webrtc_ok_at timestamptz,            -- INT-17 (server-verified connection + recognised utterance)
  current_question_id uuid,
  current_phase text CHECK (current_phase IN ('PRESENTING','ANSWERING','FINALIZING','DECIDING','DONE')),
  answer_opened_at timestamptz,
  answer_deadline_at timestamptz,               -- server clock (INT-06)
  presentation_count smallint NOT NULL DEFAULT 0,
  followups_used smallint NOT NULL DEFAULT 0 CHECK (followups_used BETWEEN 0 AND 4),   -- 6.2
  text_mode_since timestamptz,                  -- INT-18
  last_heartbeat_at timestamptz,
  interruption_count smallint NOT NULL DEFAULT 0,
  started_at timestamptz,
  ended_at timestamptz,
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id)
);
CREATE TABLE interview_slots (                  -- global concurrency cap (INT-13); lease renewed by the voice runtime
  application_id uuid PRIMARY KEY REFERENCES applications(id),
  acquired_at timestamptz NOT NULL DEFAULT now(),
  lease_expires_at timestamptz NOT NULL
);

CREATE TABLE question_bank (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('PERSONALITY','COMPANY','JOB')),
  job_family text,
  company_id uuid REFERENCES companies(id),     -- AI-made questions stay inside their company (SC-16)
  text text NOT NULL,
  text_norm_hash bytea NOT NULL,
  source text NOT NULL CHECK (source IN ('SEED','AI')),
  usage_count int NOT NULL DEFAULT 0,
  last_used_at timestamptz,
  embedding real[],
  embedding_model text,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (kind <> 'JOB' OR job_family IS NOT NULL),
  CHECK (source <> 'AI' OR company_id IS NOT NULL)
);
CREATE UNIQUE INDEX ux_qbank_exact ON question_bank(kind, COALESCE(job_family, ''),
  COALESCE(company_id, '00000000-0000-0000-0000-000000000000'), text_norm_hash);
CREATE INDEX ix_qbank_pick ON question_bank(kind, job_family, usage_count) WHERE is_active;

CREATE TABLE question_sets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL,
  company_id uuid NOT NULL,
  settings_id uuid NOT NULL,                    -- custom-question version used to compose
  status text NOT NULL CHECK (status IN ('COMPOSING','READY','SUPERSEDED')),
  composer_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  ready_at timestamptz,
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id)
);
CREATE UNIQUE INDEX ux_qset_live ON question_sets(application_id) WHERE status IN ('COMPOSING','READY');

CREATE TABLE interview_questions (              -- asked-question snapshot (QST-06): UPDATE/DELETE forbidden
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question_set_id uuid NOT NULL REFERENCES question_sets(id),
  application_id uuid NOT NULL,
  company_id uuid NOT NULL,
  seq smallint NOT NULL CHECK (seq BETWEEN 1 AND 10),        -- follow-ups share their parent's number (INT-07)
  role text NOT NULL CHECK (role IN ('MAIN','FOLLOWUP')),
  parent_id uuid REFERENCES interview_questions(id),
  slot_type text NOT NULL CHECK (slot_type IN ('PERSONALITY','RESUME','COMPANY','JOB')),
  source text NOT NULL CHECK (source IN ('BANK','AI','CUSTOM','BANK_FALLBACK')),
  bank_question_id uuid REFERENCES question_bank(id),
  text text NOT NULL,
  grounding jsonb,                              -- resume/answer quotes the question relies on (QST-03, 6.2)
  answer_limit_sec smallint NOT NULL,
  prompt_version text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((role = 'MAIN') = (parent_id IS NULL)),
  CHECK ((role = 'MAIN' AND answer_limit_sec = 60) OR (role = 'FOLLOWUP' AND answer_limit_sec = 45)),
  CHECK (slot_type = CASE WHEN seq <= 2 THEN 'PERSONALITY' WHEN seq <= 4 THEN 'RESUME'
                          WHEN seq <= 6 THEN 'COMPANY' ELSE 'JOB' END),       -- fixed order (6.1)
  CHECK (source <> 'CUSTOM' OR (slot_type = 'JOB' AND role = 'MAIN')),
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id)
);
CREATE UNIQUE INDEX ux_iq_main ON interview_questions(question_set_id, seq) WHERE role = 'MAIN';
CREATE UNIQUE INDEX ux_iq_followup ON interview_questions(parent_id) WHERE role = 'FOLLOWUP';   -- <= 1 per main
CREATE UNIQUE INDEX ux_iq_custom ON interview_questions(question_set_id) WHERE source = 'CUSTOM';

CREATE TABLE question_tts_assets (              -- cache next to (not inside) the snapshot
  question_id uuid PRIMARY KEY REFERENCES interview_questions(id),
  audio_key text NOT NULL,
  duration_ms int NOT NULL,
  voice_model text NOT NULL
);

CREATE TABLE interview_turns (                  -- one presentation of a question = one turn (ADR-0009)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL REFERENCES applications(id),
  question_id uuid NOT NULL REFERENCES interview_questions(id),
  presentation_no smallint NOT NULL CHECK (presentation_no BETWEEN 1 AND 3),   -- re-ask cap 2 (T11)
  state text NOT NULL CHECK (state IN ('PRESENTING','ANSWERING','FINALIZING','CLOSED','ABANDONED')),
  input_mode text NOT NULL DEFAULT 'VOICE' CHECK (input_mode IN ('VOICE','TEXT')),
  presented_at timestamptz NOT NULL DEFAULT now(),
  tts_ended_at timestamptz,
  opened_at timestamptz,
  deadline_at timestamptz,
  closed_at timestamptz,
  end_reason text CHECK (end_reason IN ('BUTTON','TIMEOUT','DISCONNECT')),
  UNIQUE (question_id, presentation_no)
);
CREATE UNIQUE INDEX ux_turn_live ON interview_turns(application_id) WHERE state IN ('PRESENTING','ANSWERING','FINALIZING');

CREATE TABLE interview_answers (                -- confirmed answers only (OQ-21)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id uuid NOT NULL UNIQUE REFERENCES interview_questions(id),   -- button/timeout race: first wins
  application_id uuid NOT NULL,
  company_id uuid NOT NULL,
  input_mode text NOT NULL CHECK (input_mode IN ('VOICE','TEXT')),      -- INT-18
  end_reason text NOT NULL CHECK (end_reason IN ('BUTTON','TIMEOUT')),   -- X-02
  is_no_answer boolean NOT NULL,                -- silence or STT failure (INT-06, INT-09)
  stt_failed boolean NOT NULL DEFAULT false,
  transcript text,                              -- what the candidate saw as the final caption
  eval_text text,                               -- redacted text used for evaluation and quote checks (EVL-07)
  audio_key text,
  audio_duration_ms int,
  stt_model text,
  turn_id uuid NOT NULL UNIQUE REFERENCES interview_turns(id),
  opened_at timestamptz NOT NULL,
  confirmed_at timestamptz NOT NULL,
  reask_count smallint NOT NULL DEFAULT 0,
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id),
  CHECK (NOT is_no_answer OR COALESCE(transcript, '') = ''),
  CHECK (input_mode = 'VOICE' OR audio_key IS NULL)
);
CREATE INDEX ix_answers_app ON interview_answers(application_id);

CREATE TABLE followup_decisions (               -- why a follow-up was asked or skipped (6.2, tuning)
  main_question_id uuid PRIMARY KEY REFERENCES interview_questions(id),
  application_id uuid NOT NULL REFERENCES applications(id),
  decision text NOT NULL CHECK (decision IN ('ASK','SKIP')),
  reason text NOT NULL,
  skip_cause text CHECK (skip_cause IN ('RULE','LLM','TIMEOUT','INVALID','LIMIT','INFRA')),
  latency_ms int,
  decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE proctor_events (                   -- INT-16 (P2): warn and record only
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  application_id uuid NOT NULL REFERENCES applications(id),
  kind text NOT NULL CHECK (kind IN ('LEAVE','RETURN')),
  occurred_at timestamptz NOT NULL,
  away_ms int
);

-- ── coding test ────────────────────────────────────────────────────────────
CREATE TABLE coding_attempts (
  application_id uuid PRIMARY KEY,
  company_id uuid NOT NULL,
  problem_id uuid NOT NULL REFERENCES coding_problems(id),
  language text NOT NULL,
  started_at timestamptz NOT NULL,
  deadline_at timestamptz NOT NULL,             -- server clock keeps running (7.1)
  draft_code text NOT NULL DEFAULT '' CHECK (octet_length(draft_code) <= 65536),
  draft_revision int NOT NULL DEFAULT 0,
  draft_saved_at timestamptz,
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id)
);
CREATE TABLE coding_runs (                      -- [실행] with public examples only (COD-03)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL REFERENCES applications(id),
  code_sha256 bytea NOT NULL,
  status text NOT NULL CHECK (status IN ('RUNNING','DONE','ERROR')),
  verdict text CHECK (verdict IN ('OK','RUNTIME_ERROR','TIMEOUT','MEMORY_LIMIT','OUTPUT_LIMIT','SANDBOX_ERROR')),
  stdout_head text,
  stderr_head text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE UNIQUE INDEX ux_coding_run_inflight ON coding_runs(application_id) WHERE status = 'RUNNING';
CREATE TABLE coding_submissions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL UNIQUE,          -- exactly one final submission (COD-04)
  company_id uuid NOT NULL,
  code text NOT NULL,
  code_sha256 bytea NOT NULL,
  kind text NOT NULL CHECK (kind IN ('MANUAL','AUTO_TIMEOUT','AUTO_EXPIRED')),   -- COD-05
  submitted_at timestamptz NOT NULL DEFAULT now(),
  grading_status text NOT NULL DEFAULT 'PENDING' CHECK (grading_status IN ('PENDING','DONE')),
  compile_ok boolean,
  passed smallint,
  total smallint,
  score numeric(5,2),
  graded_at timestamptz,
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id),
  CHECK (grading_status = 'PENDING' OR (total > 0 AND passed BETWEEN 0 AND total AND score IS NOT NULL))
);
CREATE TABLE coding_test_results (
  submission_id uuid NOT NULL REFERENCES coding_submissions(id),
  testcase_id uuid NOT NULL REFERENCES coding_testcases(id),
  passed boolean NOT NULL,
  outcome text NOT NULL,
  time_ms int,
  PRIMARY KEY (submission_id, testcase_id)
);

-- ── evaluation, reports, decisions ─────────────────────────────────────────
CREATE TABLE evaluation_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL,
  company_id uuid NOT NULL,
  run_no smallint NOT NULL,
  job_id bigint NOT NULL,
  lease_token bigint NOT NULL,                  -- fencing token copied from the job claim
  trigger text NOT NULL CHECK (trigger IN ('SUBMIT','AUTO_RETRY','RECRUITER','OPERATOR')),
  status text NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','TIMED_OUT','ABORTED_INFRA','FENCED_OUT')),
  model_name text NOT NULL,
  model_digest text,
  prompt_version text NOT NULL,
  rubric_version text NOT NULL,
  eval_settings_id uuid NOT NULL,
  scoring_version text NOT NULL,                -- EVL-08
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  error_code text,
  UNIQUE (application_id, run_no),
  UNIQUE (id, application_id),
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id)
);
CREATE TABLE answer_evaluations (               -- answer (main or follow-up) x criterion
  run_id uuid NOT NULL REFERENCES evaluation_runs(id),
  answer_id uuid NOT NULL REFERENCES interview_answers(id),
  criterion text NOT NULL CHECK (criterion IN ('EXPERTISE','LOGIC','SPECIFICITY','DELIVERY')),
  score smallint NOT NULL CHECK (score BETWEEN 1 AND 5),
  evidence_status text NOT NULL CHECK (evidence_status IN ('OK','INSUFFICIENT','NO_ANSWER')),
  insufficient_reason text CHECK (insufficient_reason IN ('TOO_SHORT','QUOTE_MISMATCH','NO_EVIDENCE')),
  quote text,                                   -- source substring of eval_text, never the LLM string (ADR-0008)
  quote_start int,
  quote_end int,
  comment text,
  llm_attempts smallint NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, answer_id, criterion),
  CHECK ((evidence_status = 'INSUFFICIENT') = (insufficient_reason IS NOT NULL)),
  CHECK (evidence_status = 'OK' OR score = 1),                 -- 6.4
  CHECK (evidence_status <> 'OK' OR quote IS NOT NULL)         -- AC-16: every OK score carries a verified quote
);
CREATE TABLE answer_pre_evaluations (           -- EVL-05 cache, reused by the final run when the manifest matches
  answer_id uuid NOT NULL REFERENCES interview_answers(id),
  manifest_hash text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (answer_id, manifest_hash)
);
CREATE TABLE recruiter_reports (
  application_id uuid PRIMARY KEY,              -- one report per application: double-writes impossible (R-14)
  company_id uuid NOT NULL,
  run_id uuid NOT NULL UNIQUE,
  score_expertise numeric(5,2) NOT NULL,
  score_logic numeric(5,2) NOT NULL,
  score_specificity numeric(5,2) NOT NULL,
  score_delivery numeric(5,2) NOT NULL,
  score_coding numeric(5,2),
  total_score numeric(5,2) NOT NULL CHECK (total_score BETWEEN 0 AND 100),
  insufficient_count int NOT NULL,
  no_answer_count int NOT NULL,
  insufficient_by_criterion jsonb NOT NULL,
  answered_main_count smallint NOT NULL,        -- "10문항 중 N문항 응답" (7.1)
  question_scores jsonb NOT NULL,
  weights jsonb NOT NULL,
  summary text NOT NULL,
  strengths jsonb NOT NULL,
  improvements jsonb NOT NULL,
  code_review jsonb,                            -- COD-07, not scored
  proctor_summary jsonb,                        -- INT-16
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (run_id, application_id) REFERENCES evaluation_runs(id, application_id),
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id),
  CHECK (jsonb_array_length(strengths) BETWEEN 1 AND 3 AND jsonb_array_length(improvements) BETWEEN 1 AND 3)
);
CREATE TABLE candidate_feedback (               -- separate data for the candidate (RPT-03); no total/quote/weight columns
  application_id uuid PRIMARY KEY,
  company_id uuid NOT NULL,
  run_id uuid NOT NULL UNIQUE,
  score_expertise numeric(5,2) NOT NULL,
  score_logic numeric(5,2) NOT NULL,
  score_specificity numeric(5,2) NOT NULL,
  score_delivery numeric(5,2) NOT NULL,
  strengths jsonb NOT NULL,
  improvements jsonb NOT NULL,
  coding_all_passed boolean,                    -- OQ-24
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (run_id, application_id) REFERENCES evaluation_runs(id, application_id),
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id)
);
CREATE TABLE decision_history (                 -- append-only (ADM-04)
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  application_id uuid NOT NULL,
  company_id uuid NOT NULL,
  from_status text NOT NULL,
  to_status text NOT NULL CHECK (to_status IN ('PASS','FAIL')),
  decided_by uuid NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (decided_by, company_id) REFERENCES users(id, company_id),
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id),
  CHECK (from_status <> to_status)
);
CREATE TABLE recruiter_notes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL,
  company_id uuid NOT NULL,
  author_id uuid NOT NULL,
  body text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz,
  deleted_at timestamptz,
  FOREIGN KEY (author_id, company_id) REFERENCES users(id, company_id),
  FOREIGN KEY (application_id, company_id) REFERENCES applications(id, company_id)
);

-- ── audit, queue, periodic tasks, file deletion outbox ─────────────────────
CREATE TABLE audit_logs (                       -- append-only, IDs and states only (SEC-08)
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  actor_id uuid,
  actor_role text NOT NULL,
  company_id uuid,
  action text NOT NULL,
  target_type text NOT NULL,
  target_id text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}',
  request_id text
);
CREATE INDEX ix_audit_company ON audit_logs(company_id, occurred_at DESC);

CREATE SEQUENCE job_lease_token_seq;
CREATE TABLE jobs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind text NOT NULL,
  payload jsonb NOT NULL,                       -- IDs only, never personal data
  application_id uuid,
  dedupe_key text,
  priority smallint NOT NULL DEFAULT 50,
  resource text NOT NULL CHECK (resource IN ('LLM','RUNNER','CPU','NONE')),
  state text NOT NULL DEFAULT 'QUEUED' CHECK (state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
  run_after timestamptz NOT NULL DEFAULT now(),
  attempts smallint NOT NULL DEFAULT 0,
  max_attempts smallint NOT NULL,
  lost_leases smallint NOT NULL DEFAULT 0,
  infra_deferrals int NOT NULL DEFAULT 0,
  depends_on bigint REFERENCES jobs(id),
  lease_owner text,
  lease_token bigint,
  lease_expires_at timestamptz,
  attempt_deadline_at timestamptz,
  last_error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  CHECK ((state = 'RUNNING') = (lease_token IS NOT NULL)),
  CHECK (attempts BETWEEN 0 AND max_attempts)
);
CREATE UNIQUE INDEX ux_jobs_dedupe ON jobs(dedupe_key) WHERE state IN ('QUEUED','RUNNING');
CREATE INDEX ix_jobs_claim ON jobs(resource, priority, run_after) WHERE state = 'QUEUED';
CREATE INDEX ix_jobs_lease ON jobs(lease_expires_at) WHERE state = 'RUNNING';
CREATE INDEX ix_jobs_app ON jobs(application_id);

CREATE TABLE periodic_tasks (
  name text PRIMARY KEY,
  interval_sec int NOT NULL,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  last_ok_at timestamptz,
  last_error_code text
);
CREATE TABLE file_deletions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  storage_key text NOT NULL,
  reason text NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  done_at timestamptz
);

-- ── triggers: immutability and state-transition guard ─────────────────────
CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Retention/withdrawal purges set app.purge='on' inside their own transaction.
  IF current_setting('app.purge', true) = 'on' THEN RETURN COALESCE(NEW, OLD); END IF;
  RAISE EXCEPTION '% is immutable', TG_TABLE_NAME USING ERRCODE = 'AP002';
END $$;

-- Records of what happened (consent, decisions, audit) are never purge-able (consolidation T14).
CREATE FUNCTION forbid_mutation_strict() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'AP002';
END $$;

CREATE TRIGGER t_iq_immutable BEFORE UPDATE OR DELETE ON interview_questions
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER t_answers_immutable BEFORE UPDATE OR DELETE ON interview_answers
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER t_consent_immutable BEFORE UPDATE OR DELETE ON consent_records
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation_strict();
CREATE TRIGGER t_settings_immutable BEFORE UPDATE OR DELETE ON posting_eval_settings
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER t_decisions_immutable BEFORE UPDATE OR DELETE ON decision_history
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation_strict();
CREATE TRIGGER t_audit_immutable BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation_strict();
CREATE TRIGGER t_answer_eval_immutable BEFORE UPDATE OR DELETE ON answer_evaluations
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE FUNCTION guard_application() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.attempt_status IS DISTINCT FROM OLD.attempt_status THEN
    IF NOT EXISTS (SELECT 1 FROM state_transitions WHERE machine = 'ATTEMPT'
                   AND from_state = OLD.attempt_status AND to_state = NEW.attempt_status) THEN
      RAISE EXCEPTION 'attempt % -> %', OLD.attempt_status, NEW.attempt_status USING ERRCODE = 'AP001';
    END IF;
    INSERT INTO application_state_events(application_id, machine, from_state, to_state)
      VALUES (NEW.id, 'ATTEMPT', OLD.attempt_status, NEW.attempt_status);
  END IF;
  IF NEW.eval_status IS DISTINCT FROM OLD.eval_status THEN
    IF NOT EXISTS (SELECT 1 FROM state_transitions WHERE machine = 'EVAL'
                   AND from_state = COALESCE(OLD.eval_status, '-') AND to_state = COALESCE(NEW.eval_status, '-')) THEN
      RAISE EXCEPTION 'eval % -> %', OLD.eval_status, NEW.eval_status USING ERRCODE = 'AP001';
    END IF;
    INSERT INTO application_state_events(application_id, machine, from_state, to_state)
      VALUES (NEW.id, 'EVAL', OLD.eval_status, COALESCE(NEW.eval_status, '-'));
  END IF;
  IF NEW.decision_status IS DISTINCT FROM OLD.decision_status THEN
    IF NOT EXISTS (SELECT 1 FROM state_transitions WHERE machine = 'DECISION'
                   AND from_state = OLD.decision_status AND to_state = NEW.decision_status) THEN
      RAISE EXCEPTION 'decision % -> %', OLD.decision_status, NEW.decision_status USING ERRCODE = 'AP001';
    END IF;
    INSERT INTO application_state_events(application_id, machine, from_state, to_state)
      VALUES (NEW.id, 'DECISION', OLD.decision_status, NEW.decision_status);
  END IF;
  IF NEW.resume_snapshot IS DISTINCT FROM OLD.resume_snapshot AND current_setting('app.purge', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'resume snapshot is immutable' USING ERRCODE = 'AP002';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_guard_application BEFORE UPDATE ON applications
  FOR EACH ROW EXECUTE FUNCTION guard_application();

CREATE FUNCTION guard_locked_settings() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM job_postings WHERE id = NEW.posting_id AND settings_locked_at IS NOT NULL) THEN
    RAISE EXCEPTION 'evaluation settings are locked' USING ERRCODE = 'AP003';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_settings_locked BEFORE INSERT ON posting_eval_settings
  FOR EACH ROW EXECUTE FUNCTION guard_locked_settings();

CREATE FUNCTION guard_posting_coding_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.settings_locked_at IS NOT NULL AND (
       NEW.coding_enabled IS DISTINCT FROM OLD.coding_enabled
    OR NEW.coding_problem_id IS DISTINCT FROM OLD.coding_problem_id
    OR NEW.coding_language IS DISTINCT FROM OLD.coding_language
    OR NEW.coding_time_limit_min IS DISTINCT FROM OLD.coding_time_limit_min
    OR NEW.current_settings_id IS DISTINCT FROM OLD.current_settings_id) THEN
    RAISE EXCEPTION 'coding settings and weights are locked' USING ERRCODE = 'AP003';   -- JOB-06
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_posting_coding_lock BEFORE UPDATE ON job_postings
  FOR EACH ROW EXECUTE FUNCTION guard_posting_coding_lock();
"""

# Allowed transitions (docs/trd/03-data-model.md 4절). '-' = NULL. B1/B2 extensions are flagged in open-questions.
TRANSITIONS = {
    "ATTEMPT": [
        ("READY", "PREPARING"), ("PREPARING", "READY"), ("PREPARING", "INTERVIEWING"),
        ("INTERVIEWING", "CODING"), ("INTERVIEWING", "SUBMITTED"), ("INTERVIEWING", "INTERRUPTED"),
        ("CODING", "SUBMITTED"), ("CODING", "INTERRUPTED"),
        ("INTERRUPTED", "INTERVIEWING"), ("INTERRUPTED", "CODING"), ("INTERRUPTED", "EXPIRED"),
        ("INTERRUPTED", "SUBMITTED"),              # OQ-25: coding deadline passed while disconnected (COD-05)
        ("READY", "EXPIRED"), ("PREPARING", "EXPIRED"),   # OQ-26: posting closed before the attempt finished
    ],
    "EVAL": [
        ("-", "PENDING"), ("PENDING", "RUNNING"), ("RUNNING", "DONE"), ("RUNNING", "ON_HOLD"),
        ("RUNNING", "PENDING"),                    # infra deferral returns the job to the queue (OQ-23)
        ("ON_HOLD", "PENDING"),
    ],
    "DECISION": [("UNDECIDED", "PASS"), ("UNDECIDED", "FAIL"), ("PASS", "FAIL"), ("FAIL", "PASS")],
}

PERIODIC = [
    ("job_reaper", 15), ("attempt_sweeper", 30), ("coding_autosubmit", 15), ("posting_autoclose", 60),
    ("retention_purge", 3600), ("file_gc", 3600), ("session_gc", 3600),
]


def split_sql(script: str) -> list[str]:
    """Split on ';' outside $$-quoted bodies; asyncpg runs one statement per call."""
    out, buf, in_body = [], [], False
    for line in script.splitlines():
        if line.lstrip().startswith("--") and not in_body:
            continue
        buf.append(line)
        if line.count("$$") % 2:
            in_body = not in_body
        if not in_body and line.split("--")[0].rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt.rstrip(";").strip():
                out.append(stmt)
            buf = []
    if "\n".join(buf).strip():
        out.append("\n".join(buf).strip())
    return out


def upgrade() -> None:
    for stmt in split_sql(SCHEMA):
        op.execute(stmt)
    rows = ",".join(f"('{m}','{f}','{t}')" for m, pairs in TRANSITIONS.items() for f, t in pairs)
    op.execute(f"INSERT INTO state_transitions(machine, from_state, to_state) VALUES {rows}")
    rows = ",".join(f"('{n}', {s})" for n, s in PERIODIC)
    op.execute(f"INSERT INTO periodic_tasks(name, interval_sec) VALUES {rows}")


def downgrade() -> None:
    op.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
