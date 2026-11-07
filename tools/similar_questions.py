"""Find the questions closest (cosine) to any sentence, in question_examples and question_bank.

    cd deploy
    docker compose exec -T worker python - "찾을 문장" [개수] < ../tools/similar_questions.py

The sentence is embedded by the same model as the stored rows (the ai service), so it does not have to exist in the DB.
"""
import asyncio
import sys

from sqlalchemy import text

from app.ai.client import get_ai_client
from app.core.config import get_settings
from app.core.db import session_scope
from app.questions.knowledge import vec_literal


async def main(sentence: str, n: int) -> None:
    ai = get_ai_client()
    vec = (await ai.embed([sentence]))[0]
    model = get_settings().embed_model
    params = {"v": vec_literal(vec), "m": model, "n": n}
    async with session_scope() as db:
        for title, sql in (
            ("question_examples (엑셀 참고 질문)",
             "SELECT kind, origin AS src, text, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM question_examples "
             "WHERE embedding IS NOT NULL AND embedding_model = :m ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
            ("question_bank (질문은행)",
             "SELECT kind, source AS src, text, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM question_bank "
             "WHERE embedding IS NOT NULL AND embedding_model = :m ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
        ):
            print(f"\n== {title}")
            for r in (await db.execute(text(sql), params)).all():
                print(f"{r.sim:.4f}  [{r.kind}/{r.src}]  {r.text}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5))
