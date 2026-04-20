from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageOption:
    label: str
    speech_locale: str
    translation_code: str


LANGUAGES: list[LanguageOption] = [
    LanguageOption("Deutsch (German)", "de-DE", "de"),
    LanguageOption("English", "en-US", "en"),
    LanguageOption("Turkce (Turkish)", "tr-TR", "tr"),
    LanguageOption("Francais (French)", "fr-FR", "fr"),
    LanguageOption("Espanol (Spanish)", "es-ES", "es"),
    LanguageOption("Italiano (Italian)", "it-IT", "it"),
    LanguageOption("Portugues (Portuguese)", "pt-BR", "pt"),
    LanguageOption("Nederlands (Dutch)", "nl-NL", "nl"),
    LanguageOption("Russkiy (Russian)", "ru-RU", "ru"),
    LanguageOption("Arabic", "ar-EG", "ar"),
    LanguageOption("Japanese", "ja-JP", "ja"),
    LanguageOption("Korean", "ko-KR", "ko"),
    LanguageOption("Chinese Simplified", "zh-CN", "zh-Hans"),
]

LANGUAGE_BY_LABEL = {item.label: item for item in LANGUAGES}
LANGUAGE_BY_SPEECH = {item.speech_locale: item for item in LANGUAGES}
LANGUAGE_BY_TARGET = {item.translation_code: item for item in LANGUAGES}


def source_label_for_locale(locale: str) -> str:
    option = LANGUAGE_BY_SPEECH.get(locale)
    return option.label if option else LANGUAGES[0].label


def target_label_for_code(code: str) -> str:
    option = LANGUAGE_BY_TARGET.get(code)
    return option.label if option else next(
        (item.label for item in LANGUAGES if item.translation_code == code.split("-")[0]),
        LANGUAGES[2].label,
    )
