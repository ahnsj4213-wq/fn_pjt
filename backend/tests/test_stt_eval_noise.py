"""Conservative STT-noise filtering of the evaluation text only."""
from app.ai.textrules import make_eval_text, remove_stt_noise


def test_isolated_hallucination_fragments_do_not_affect_voice_evaluation():
    raw = "쿼리 실행 계획에서 병목을 찾고 인덱스를 조정했습니다.\n안녕\n아멘\n계란\n고춧가루"
    clean = make_eval_text(raw, question="데이터베이스 쿼리 성능을 어떻게 개선했나요?", voice=True)
    assert clean == "쿼리 실행 계획에서 병목을 찾고 인덱스를 조정했습니다."
    assert "계란" in raw  # The transcript itself is never modified.


def test_text_mode_does_not_apply_stt_noise_filter():
    raw = "쿼리 실행 계획에서 병목을 찾고 인덱스를 조정했습니다.\n안녕\n아멘\n계란\n고춧가루"
    assert "계란" in make_eval_text(raw, question="DB 성능 개선", voice=False)


def test_single_word_answer_is_not_silently_deleted():
    assert remove_stt_noise("계란", "무슨 재료를 사용했나요?") == "계란"
    assert make_eval_text("아멘", question="어떤 표현을 썼나요?", voice=True) == "아멘"


def test_relevant_question_terms_and_long_statements_are_preserved():
    raw = "계란과 채소를 활용해 메뉴를 개선했습니다.\n안녕\n아멘\n계란"
    cleaned = make_eval_text(raw, question="계란을 활용한 메뉴를 설명해 주세요.", voice=True)
    assert "계란" in cleaned
    assert "메뉴를 개선했습니다" in cleaned


def test_no_aggressive_removal_of_unrelated_looking_complete_sentences():
    # Without a reliable semantic validator, a complete sentence must not be
    # discarded only because its words do not overlap the interview question.
    raw = "쿼리 병목을 분석했습니다. 재고 관리 프로젝트도 담당했습니다."
    assert make_eval_text(raw, question="쿼리 성능에 대해 설명해 주세요.", voice=True) == raw


def test_name_redaction_remains_after_voice_cleanup():
    raw = "김철수는 SQL 실행 계획을 조사하고 성능 문제를 해결했습니다.\n안녕"
    clean = make_eval_text(raw, ["김철수"], question="SQL 성능을 어떻게 개선했나요?", voice=True)
    assert "김철수" not in clean
    assert "[지원자]" in clean
    assert "안녕" not in clean


def test_a_voice_answer_without_artifacts_keeps_its_line_breaks():
    raw = "첫째로 실행 계획을 확인했습니다.
둘째로 인덱스를 추가했습니다.
결과적으로 응답이 빨라졌습니다."
    assert remove_stt_noise(raw, "쿼리 성능") == raw
    assert make_eval_text(raw, question="쿼리 성능", voice=True) == make_eval_text(raw, question="쿼리 성능", voice=False)
