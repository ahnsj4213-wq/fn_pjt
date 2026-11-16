# ADR-0032: Keep the resume's blocks word for word, and connect job and motivation questions to the candidate's experience

- Status: Accepted (2026-11-16, user instruction)
- Related: ADR-0020 (company-document RAG), ADR-0025 (level), ADR-0027 (quality loop and code fallback), ADR-0030 (hybrid search), ADR-0031 (question bank retired)
- Impact:
  - `resumes/sections.py`, `resumes/schemas.py` (`details`)
  - new `questions/resume_match.py`
  - `ai/tasks/questions.py` (T2 1.10.0)
  - `questions/rag_graph.py`, `questions/compose.py`, `questions/quality.py`, `questions/level.py`
  - frontend type `ResumeContent.details`
  - new tool `tools/eval_personalization.py`
  - new API `GET /api/candidate/resume-files/{id}/content` and `ResumeFileOut.media_type` (original file panel, section 6)
  - frontend: `screens/candidate/resume/OriginalFile.tsx`, My Page resume and the apply resume step; `next.config.ts` headers no longer on `/api`
  - no migration (the snapshot is JSON)

## Background

A diagnosis of one uploaded resume showed two problems.

1. **Most of the resume never reached the snapshot.** The extracted text was 4,803 characters, the snapshot text 517.
   - The file's pages are titled like "이름 · 대표 프로젝트 / ARCHITECTURE", "문제 해결 / TECHNICAL DEPTH" and "프로젝트 한눈에 보기".
   - The parser knew none of these headings. Only `기술 스택` and `자기소개` matched.
   - So the career and project sections were empty, and each row kept only the model's two-line description (T1 copies at most 150 characters).
2. **T2 (job and motivation questions) never saw the resume.** It got the posting, the company introduction and the company's documents. Two candidates with different resumes got the same kind of questions.

While fixing (1), a second fault surfaced in the old code. A description ran from a row's name to the next row's name **across headings**. With the new headings recognised, project B took project A's pages. That is a misclassification, so the cross-heading fill was removed.

## Decision

### 1. Parsing (`resumes/sections.py`)

**Headings**
- A wider vocabulary.
- Page titles of the form `[이름 ·] 한국어 제목 / ENGLISH`: the Korean name is matched, and the English name is used when the Korean one is unknown.
- New kinds: `skills`, `summary`, `facts` (education, certificates, military, languages) and `contact` (basic and personal information).

**Page headers**
- A line repeated word for word on three or more pages is dropped, however long it is.
- Before, only lines of 40 characters or fewer were dropped. A long line that differs only in a number is not dropped.

**`details`**
- Every block under a heading of kind career, projects, skills or summary is kept word for word. Each entry records:
  - its heading;
  - `lines`: the first and last line number in the extracted text, so the place in the PDF text can be checked;
  - `row`: the career or project row it belongs to;
  - `how`: how it was linked to that row.
- A block whose text names a row (the name within the first 30 characters of a line) is cut there, and that piece belongs to that row (`how="name"`).
- A block that names no row belongs to one only when it **clearly** holds that row's words (`how="similar"`). All of these must be true:
  - at least 40% of the row's words, and at least 3 of them;
  - the row has at least 4 words;
  - at least twice the share of any other row.
- Otherwise the block belongs to no row.
- A long block is **split** into entries of at most 4,000 characters, never cut.
- Contact blocks are not kept. An e-mail address or phone number inside a kept block is masked.

**Row descriptions**
- A description is the cleaned text of the pieces that belong to the row, joined into one line.
- It stops at a line boundary at the form's 3,000 characters. The full text stays in `details`.
- A row that no piece belongs to keeps the model's description.

**Nothing is invented**
- Values that are not in the file are dropped, as before: T1 checks every value against the source (`_verify`).
- `details` is only the file's own lines.

### 2. Evidence trail

- The snapshot (`applications.resume_snapshot`) carries `details` with `lines`, `row` and `how`.
- The original text stays in `resume_files.extracted_text` (unchanged).
- `flatten` (the quote source for resume questions) includes the details, so a quote from them can be verified.

### 3. Job and motivation questions (T2)

**Excerpts** (`questions/resume_match.relevant`)
- Code picks at most 3 resume sentences, word for word.
- They are the ones sharing the most words with the posting and the documents found.
- At most one is taken per row.
- Name, contact details and dates are never included.

**T2 1.10.0**
- The excerpts go into the prompt in their own fence: "지원자가 직접 적은 문장, 이 밖의 경험은 가정하지 않는다".
- The output has an optional `resume_quote`.
- The question's basis must still be a posting or document sentence.

**`resume_link`** (code). A question that tells something as the candidate's past work ("…하셨는데", "운영해 보신") is dropped as `ASSUMED_EXPERIENCE` when any of these is true:
- no excerpts were given;
- a technology named in that part is not in the resume;
- its quote is not in the excerpts, and it shares fewer than 2 words with them.

When the link is verified, the level rule `PRESUMES_EXPERIENCE` is not applied (`from_resume`).

**Personalisation by code (`compose.personalise`)**
- The measurement showed the model writes **no** accepted question that joins the resume to the posting:
  - when it uses the resume, it drops the basis or asks yes/no;
  - so 0 AI questions were linked.
- Like T3 (the model chooses, code writes the sentence around a verified quote), a job question the model made from the posting or the documents gets the row it is about in front: "<가상데이터랩 데이터 엔지니어 업무에서> 쌓은 경험을 살려, <질문>".
- It is applied only when all of these hold:
  - the row's sentence shares at least 2 words with the question and its basis;
  - the row is not already linked in this set (one row, one question);
  - the whole sentence fits the level's length limit;
  - it passes every quality rule (`quality.judge`).
- Otherwise the model's sentence is asked as it was.
- Nothing is said about what the candidate did; the row is named as they wrote it.

**Motivation**
- The fixed sentence names the row most related to the document: "이 회사의 ‘<자료>’ 업무에서 어떤 점에 관심이 있고, ‘<이력서 행>’ 경험을 어떻게 살릴 수 있다고 보십니까?"
- Without a related row it stays as before.

**Grounding**
- Each question records `grounding.resume` = `{given: [refs], link: {ref, scene, quote, how}}`.
- `how` is one of `quote` (T2), `words` (T2), `code` (personalise) or `template` (fallback).

### 4. The code-made question (`posting_fallback`)

- The posting line is no longer copied in quotes ("공고에 적힌 ‘…’ 업무를 맡게 된다면").
- `job_topic` takes away the requirement wording ("경험이 있으신 분", "우대", "가능하신 분" …). What is left is the work ("Kafka 기반 이벤트 처리"), phrased as a piece of work (`job_work`: "…하는 일" / "…와 관련된 일").
- With a related resume row, the question becomes: "<row> 업무에서 <what the resume says>…셨는데, 그 경험을 <work>에 어떻게 살리시겠습니까?" The short form is "<row>에서 쌓은 경험을 <work>에 어떻게 살리시겠습니까?", chosen to fit the level's length.
- Without one: "이 직무에서 <work>을 맡는다면 …".

### 5. The reject analysis (no rule was relaxed)

Every `QUOTE_MISMATCH` from two real-model runs was checked against the documents in the database.

| Case | Count | Verdict |
|---|---|---|
| The question copied the **example in the system prompt** ("맞힐 확률이 60~80%…" → "학습자에게 너무 쉽거나…"); the quote is the prompt's, in no source | 6 of 10 | Right to drop. Worse: one copy **passed** in an earlier run with another quote, for a backend posting. Now blocked: `PROMPT_EXAMPLES` in `copies_example` |
| Two sentences stitched and the ending changed ("…됐습니다" → "…됐어요") | 3 | Right to drop |
| A word changed ("늘었습니다" → "늘렸습니다") | 1 | Right to drop |
| Empty quote | 1 | Right to drop |
| A quote that is in the source but was rejected | 0 | — |

- `QUOTE_MISMATCH` stays as it is.
- `SAME_BASIS` did not occur in these runs (0).
- `ASKS_COMPANY` cases were yes/no questions ("…확보하시겠습니까?") or questions about the company's past. Right to drop.
- `MANY_TOPICS` had a **false positive**:
  - one product name was counted as two topics ("Spark Connect" → spark + connect; "GPU" with "GPU-aware").
  - `level.techs` now counts Latin words side by side as one name and drops a name inside a longer one.
  - Three different technologies are still three.

### 6. The original file beside the form (user request, 2026-11-16)

- The My Page resume form and the apply step's "파일에서 읽은 정보" boxes show the uploaded file on the right, so each box can be checked against it.
  - PDF: the browser's own viewer in a frame.
  - PNG/JPEG: an image.
  - DOCX: no preview; a download link.
  - Narrow screens: the panel goes under the form.
- `GET /api/candidate/resume-files/{id}/content` serves only the owner's file (`get_for_owner`, otherwise 404). The rules are the recruiter view's (T26: the media type comes from the stored extension, `nosniff`, `no-store`), with two changes:
  - our own pages may frame it: `X-Frame-Options: SAMEORIGIN`, `frame-ancestors 'self'`;
  - a DOCX is sent as an attachment.
- The frontend server set `X-Frame-Options: DENY` on every path, `/api` included, which would block the frame. It now sets it on pages only. The API's middleware still puts `DENY` and `frame-ancestors 'none'` on every other API answer (`setdefault`), so nothing else became frameable.

## Results

Measured with `tools/eval_personalization.py` on the local model (llama.cpp, the live stack, read-only). The setup:
- 3 postings: platform, backend, AI/ML;
- 3 JOB slots and 1 MOTIVATION slot per set;
- no resume, plus 3 synthetic resumes (backend_cache, data_ml, agent_newgrad);
- before: 1 round; after: 2 rounds.

The model is not deterministic, so small differences are noise.

**Parsing** (the diagnosed file)

| | Before | After |
|---|---|---|
| Snapshot text | 517 characters | 6,499 characters (source 4,803; details overlap the row descriptions) |
| Details | — | 12 blocks: 3 linked by name, 2 by resemblance, 7 kept with `row = null` |

**Questions**

| Measure | No resume | Before, with resume | After, with resume |
|---|---|---|---|
| Sets | 6 | 9 | 18 |
| AI-written share | 0.83 | 0.64 | 0.69 |
| Personalised (linked to a resume row) | 0 | 0.19 (template only) | **0.36** |
| Basis found in the posting or documents | 1.0 | 1.0 | 1.0 |
| Passes the quality rules again | 1.0 | 1.0 | 1.0 |
| Duplicates inside a set | 0 | 0 | 0 |
| Same question for two different resumes | — | 0.14 | **0.07** |
| p50 / p95 generation time per set | 30.3 s / 36.9 s | 38.9 s / 42.0 s | 30.3 s / 38.4 s |

**Where the personalisation comes from** (after; 26 linked questions)
- 13 motivation sentences, linked by code;
- 12 code-made questions;
- 1 AI job question with a verified resume quote.

`personalise` linked **no** AI job question in these runs. The accepted AI job questions are about the companies' documents (GPU clusters, sandboxes, Milvus sizing). They shared 0–1 words with any of the three resumes. Linking them would assert a connection the resume does not show, so the rule (2 shared words) was left as it is.

**Found and fixed after the run**
- One accepted question said a newcomer's experience as a fact: "…지연 원인을 찾아 본 경험을 어떻게 살리시겠습니까?" The resume did not have it. `ASSUMES_EXPERIENCE` now covers "…본 경험을", "쌓으신" and "해 오신". "…본 경험이 있다면" is a supposition and stays allowed.
- A code-made question cut the resume sentence and kept only its end ("120ms로 줄이셨는데"). The sentence is now repeated only when it fits whole (≤ 40 characters). Otherwise the question says "<row>에서 쌓은 경험을".

**Not measured**
- More than 3 resumes or postings.
- Human ratings of naturalness. The quality rules stand in for them, and the examples above were read by hand.
- GPU contention with other workloads.

## Consequences

- The snapshot grows with `details` (up to 80 entries × 4,000 characters; the diagnosed resume went from 517 to about 6,500 characters of snapshot text).
- The retention policy deletes it with the snapshot, as before (`retention/service.py` empties `resume_snapshot`).
- `to_ai_resume` does not pass `details` to T3 (unchanged T3 input). T2 gets at most 3 sentences of 200 characters.
- The candidate's form does not show `details`. They are kept as they are when the form is saved, and replaced when a new file is read.
- Existing snapshots have no `details`; nothing is migrated. They keep working (the field defaults to empty).
- The similarity link is a heuristic. When it is unsure it links nothing, and the block is still kept, with `row = null`.
