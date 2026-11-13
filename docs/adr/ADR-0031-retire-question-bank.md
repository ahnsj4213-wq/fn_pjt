# ADR-0031: Retire the question bank and keep it as a backup table

- Status: Accepted (2026-11-13, user decision)
- Related: ADR-0005 (question bank), ADR-0020 (company-document RAG), ADR-0027 (quality loop and code fallback), ADR-0029 (tech blog postings). Raised by the PR #5 review (P1).
- Impact: migration 0036, `questions/compose.py`, `interview/router.py`, `models.py`, PRD QST-01·QST-07·6.1·8.3

## Background

- The PR #5 review asked: "rename the old question bank table to back it up, and make sure the old way cannot come back."
- When the user was asked whether to keep the bank as a fallback, they chose **"rename it and stop using it."**
- The code had already moved most of the way:
  - Personality and collaboration questions are made by code from the resume and the posting (`common_fallback`).
  - Questions generation cannot make are made by code from the posting or the resume (`TEMPLATE`, 2026-11-10).
  - `_take_from_bank`, which used to pick bank questions, no longer had any caller.
- Three paths were still in use:
  1. Generated questions grounded in the posting were saved into the company's bank (`_add_to_bank`).
  2. The usage count of bank questions was bumped when an interview started (`interview/router.py`).
  3. Code that adapts bank questions to the resume (T12). Since no bank questions come out any more, it never runs.

## Decision

- **Migration 0036**:
  - `question_bank` → `question_bank_backup_20261113`.
  - The rows (54 seed questions in the live DB at decision time), indexes and comment are kept as they are.
  - `interview_questions.bank_question_id` keeps pointing at the renamed table. PostgreSQL moves the foreign key with it, so the origin of earlier question sets stays visible.
  - Downgrade renames it back.
- **Code**:
  - Removed the `QuestionBank` model and the bank read, write, near-duplicate and relevance-ranking functions (`_bank_candidates`, `_take_from_bank`, `_add_to_bank`, `_insert_bank`, `_near_duplicate`, `_rank_by_relevance`).
  - Removed the usage-count update.
  - Generated questions are not saved for reuse.
  - The fixed T12 checks that read `BANK`/`BANK_FALLBACK` remain so old question sets can still be read. No new question gets these sources.
- **Guard against revival**: `tests/test_question_bank_retired.py`
  - Checks that the table exists only under its new name and that the foreign key points at the renamed table.
  - Checks that question composition and interview start leave the backup table unchanged.
  - Checks that no module under `app/` names `question_bank` or `QuestionBank`.
- **Not changed**:
  - The `question_examples` reference examples (RAG examples shown to T2/T3, never asked as written).
  - The coding problem bank (`coding_problems`).
  - The evaluation rubric.

## Conflict with the PRD and how it was handled

- PRD v2.0 QST-07 says "after one retry, fall back to a question-bank question," and QST-01 describes a question bank as a requirement.
- By user decision, the fallback becomes **a question made by code from the posting or resume (`TEMPLATE`)**.
- QST-01 is now "the question bank is retired (kept as a backup)."
- Updated in PRD v2.1:
  - 6.1 question-composition table
  - 8.3 failure fallback
  - The QST-01 and QST-07 rows
  - Chapter 13 change history
- The traceability table was updated to match.
- Extension point: to bring back a reusable question pool, design a new table and a new ADR. Do not revive this backup table.

## Risks

- Code-made questions (`TEMPLATE`) are less varied than bank questions. Their rate and quality need watching (`tools/eval_question_rag.py`).
- Old reporting queries that read `question_bank` directly will fail. `tools/similar_questions.*` now point at the new name.
