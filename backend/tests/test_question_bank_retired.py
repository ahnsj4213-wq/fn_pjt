"""The question bank is retired (ADR-0031): its table is kept as a backup under a new name and nothing reads or writes it."""
import pathlib

from tests.test_handlers import fake_ai, prepare_and_compose, q, setup_app  # noqa: F401  fixture + helpers
from tests.test_interview import mark_prechecked, url

BACKUP = "question_bank_backup_20261113"
APP = pathlib.Path(__file__).resolve().parents[1] / "app"


async def test_the_table_is_kept_as_a_backup_under_its_new_name():
    assert (await q("SELECT to_regclass('question_bank') IS NULL, to_regclass(:b) IS NOT NULL", b=BACKUP)) == [(True, True)]
    assert (await q(f"SELECT count(*) FROM {BACKUP} WHERE source = 'SEED'"))[0][0] > 0  # the seed questions are still there
    # old question sets keep their link: the foreign key moved with the table
    fk = await q("SELECT confrelid::regclass::text FROM pg_constraint WHERE conrelid = 'interview_questions'::regclass "
                 "AND contype = 'f' AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'interview_questions'::regclass "
                 "AND attname = 'bank_question_id')]::smallint[]")
    assert fk == [(BACKUP,)]


async def test_composing_and_starting_an_interview_leave_the_backup_untouched(client, make_client, fake_ai):
    before = await q(f"SELECT count(*), coalesce(sum(usage_count), 0), max(last_used_at) FROM {BACKUP}")
    _, app_id, cand = await setup_app(client, make_client)
    qs = await prepare_and_compose(cand, app_id)
    assert qs and all(x.bank_question_id is None and x.source not in ("BANK", "BANK_FALLBACK") for x in qs)
    await mark_prechecked(app_id)
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 200, r.text
    assert await q(f"SELECT count(*), coalesce(sum(usage_count), 0), max(last_used_at) FROM {BACKUP}") == before


def test_no_application_code_names_the_bank():
    """The old path must not come back by accident: no module reads, writes or maps either table name."""
    hits = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
            if "question_bank" in p.read_text(encoding="utf-8") or "QuestionBank" in p.read_text(encoding="utf-8")]
    assert hits == []
