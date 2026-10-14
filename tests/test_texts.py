"""Похожесть текстов и правила первого сообщения."""

import pytest

from bot.config import Settings
from bot.safety import (
    GROUP_FOOTER,
    OUTREACH_FOOTER,
    last_line_is,
    outreach_text_problems,
    similarity,
)
from tests.helpers import TEXT_A, TEXT_A_TEMPLATE, TEXT_B, TEXT_C, fake_outreach_sent, outreach, ready_for_outreach

S = Settings()

# ---------------------------------------------------------------- похожесть


def test_template_with_another_name_is_too_similar():
    assert similarity(TEXT_A, TEXT_A_TEMPLATE) > 0.7


@pytest.mark.parametrize("other", [TEXT_B, TEXT_C])
def test_rewritten_texts_are_different(other):
    assert similarity(TEXT_A, other) < 0.7


def test_mandatory_footer_does_not_count_as_similarity():
    a = "Здравствуйте, Анна. Это ассистент Real Yoga. " + OUTREACH_FOOTER
    b = "Игорь, добрый вечер, напоминаем про кичари. " + OUTREACH_FOOTER
    assert similarity(a, b) < 0.4


def test_similar_text_is_rejected_and_rewrite_passes(safety, clock):
    ready_for_outreach(safety, clock)
    safety.record_sent(outreach(1, TEXT_A))
    clock.advance(minutes=13)

    decision = safety.check(outreach(2, TEXT_A_TEMPLATE))
    assert not decision.allowed and "похож на отправленный ранее" in decision.reason
    assert any("похож" in p for p in safety.outreach_text_issues(TEXT_A_TEMPLATE))

    assert safety.check(outreach(2, TEXT_B)).allowed


def test_only_last_n_texts_are_compared(make_safety, clock):
    safety = make_safety(outreach_similarity_window=3)
    ready_for_outreach(safety, clock)
    safety.record_sent(outreach(1, TEXT_A))
    clock.advance(minutes=13)
    fake_outreach_sent(safety, clock, 2)
    assert not safety.check(outreach(2, TEXT_A_TEMPLATE)).allowed  # TEXT_A ещё среди последних 3
    fake_outreach_sent(safety, clock, 1, start_id=2000)
    assert safety.check(outreach(2, TEXT_A_TEMPLATE)).allowed


# ---------------------------------------------------------------- первое сообщение


@pytest.mark.parametrize("text", [TEXT_A, TEXT_B, TEXT_C])
def test_good_first_messages(text):
    assert outreach_text_problems(text, S) == []


@pytest.mark.parametrize("link", [
    "https://realyoga.example", "www.realyoga.ru", "t.me/realyoga_bot", "@realyoga_bot", "realyoga.com",
    "реалйога.рф",
])
def test_no_links_in_first_message(link):
    text = TEXT_A.replace("у моря.", f"у моря: {link}.")
    assert any("ссылок" in p for p in outreach_text_problems(text, S))


def test_times_and_dates_are_not_links():
    text = TEXT_A.replace("в ноябре", "15.11 в 10.30")
    assert outreach_text_problems(text, S) == []


def test_max_300_chars():
    long = TEXT_A.replace("у моря.", "у моря. " + "Очень подробно рассказываем о программе. " * 3)
    assert len(long) > 300
    assert any("длиннее 300" in p for p in outreach_text_problems(long, S))


@pytest.mark.parametrize("text, problem", [
    (TEXT_A.replace("в ноябре", "ВНИМАНИЕ, в ноябре"), "капс"),
    (TEXT_A.replace("у моря.", "у моря 🌊🧘✨🙏"), "эмодзи"),
    (TEXT_A.replace("Я ассистент Real Yoga", "Я из Real Yoga"), "ассистентом"),
    (TEXT_A.replace("Я ассистент Real Yoga", "Я доктор, ассистент Real Yoga"), "доктором"),
    (TEXT_A.replace("Я ассистент Real Yoga", "Меня зовут Анна, я ассистент Real Yoga"), "живым человеком"),
    (TEXT_A.replace(", больше не напишу", ""), "в конце должна быть фраза"),
])
def test_first_message_rules(text, problem):
    assert any(problem in p for p in outreach_text_problems(text, S))


def test_one_or_two_emoji_are_fine():
    assert outreach_text_problems(TEXT_A.replace("у моря.", "у моря 🌊"), S) == []


def test_footer_variants_are_accepted():
    for footer in (
        "Если не актуально — просто ответьте «стоп», больше не напишу.",
        'Если не актуально - просто ответьте "стоп", больше не напишу!',
    ):
        text = TEXT_A.replace(OUTREACH_FOOTER, footer)
        assert outreach_text_problems(text, S) == []


def test_group_footer_is_last_line():
    assert last_line_is("Хатха по утрам.\nПодробнее — в личку", GROUP_FOOTER)
    assert last_line_is("Хатха по утрам.\nподробнее - в личку\n\n", GROUP_FOOTER)
    assert not last_line_is("Подробнее — в личку\nХатха по утрам.", GROUP_FOOTER)
