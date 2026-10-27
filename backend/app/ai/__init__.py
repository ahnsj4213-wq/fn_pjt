"""AI contracts shared by the `ai` service (app.aisvc) and its callers (app, worker).

- textrules: eval_text (rd-1), short answers (nc-1), pass/fail recommendation filter (p8-1), injection fences (inj-1)
- tasks: T1~T8 prompts, output schemas and validators (TRD 05 §6)
- client / fake: async client used by app and worker, deterministic fake for tests
"""
