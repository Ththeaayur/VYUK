"""Wizard chrome strings, by home language (round-3 wizard expansion).

The first-run wizard opens on the language page, and the rest of the
flow follows the pick. The pick already drives her voice and her
answers (core/languages.set_preferred_language); this module carries
the small set of wizard-chrome strings that follow it too: the
navigation buttons, the stage-rail titles, the Done-page hello, and
the voice sample sentence.

Scope, honestly: ONLY this chrome is localized. Every wizard page's
title and body copy stays English — full page-copy localization is
an owed follow-up, recorded in the wizard-expansion report, not
claimed here. Every lookup falls back to English for a missing
entry, so a gap reads English, never blank.

The non-English strings are first-pass copy (short, standard UI
words), not native-verified — the report says so.

Qt-free, like core/first_run_asks.py, so the suite can read it
without building Qt.
"""

from __future__ import annotations

#: Language codes with chrome strings. The two Englishes share the
#: English table; every other code is one of core/languages.LANGUAGES.
_EN = {
    "continue": "Continue",
    "back": "Back",
    "skip": "Skip setup",
    "finish": "Finish",
    "not_now": "Not now",
    "advanced": "Advanced setup",
    "stage_interaction": "Interaction",
    "stage_brain": "Brain",
    "stage_permissions": "Permissions",
    "stage_first_mission": "First mission",
    "hello": "Hello",
    "sample": ("Hello, I'm VYUK — your voice assistant. I can help "
               "with your day, your files, and your computer."),
}

NAV = {
    "en-us": dict(_EN),
    "en-gb": dict(_EN),
    "hi": {
        "continue": "जारी रखें",
        "back": "वापस",
        "skip": "सेटअप छोड़ें",
        "finish": "समाप्त करें",
        "not_now": "अभी नहीं",
        "advanced": "उन्नत सेटअप",
        "stage_interaction": "बातचीत",
        "stage_brain": "दिमाग",
        "stage_permissions": "अनुमतियाँ",
        "stage_first_mission": "पहला काम",
        "hello": "नमस्ते",
        "sample": ("नमस्ते, मैं VYUK हूँ — आपकी आवाज़ सहायक। मैं आपके "
                   "दिन, आपकी फ़ाइलों और आपके कंप्यूटर में मदद कर सकती हूँ।"),
    },
    "ja": {
        "continue": "次へ",
        "back": "戻る",
        "skip": "セットアップをスキップ",
        "finish": "完了",
        "not_now": "今はしない",
        "advanced": "詳細設定",
        "stage_interaction": "対話",
        "stage_brain": "頭脳",
        "stage_permissions": "権限",
        "stage_first_mission": "最初のミッション",
        "hello": "こんにちは",
        "sample": ("こんにちは、私はVYUKです。あなたの1日やファイル、"
                   "パソコンのお手伝いができます。"),
    },
    "cmn": {
        "continue": "继续",
        "back": "返回",
        "skip": "跳过设置",
        "finish": "完成",
        "not_now": "以后再说",
        "advanced": "高级设置",
        "stage_interaction": "互动",
        "stage_brain": "大脑",
        "stage_permissions": "权限",
        "stage_first_mission": "首个任务",
        "hello": "你好",
        "sample": ("你好，我是 VYUK——你的语音助手。我可以帮你处理日常"
                   "事务、文件和电脑。"),
    },
    "es": {
        "continue": "Continuar",
        "back": "Atrás",
        "skip": "Omitir configuración",
        "finish": "Terminar",
        "not_now": "Ahora no",
        "advanced": "Configuración avanzada",
        "stage_interaction": "Interacción",
        "stage_brain": "Cerebro",
        "stage_permissions": "Permisos",
        "stage_first_mission": "Primera misión",
        "hello": "Hola",
        "sample": ("Hola, soy VYUK, tu asistente de voz. Puedo ayudarte "
                   "con tu día, tus archivos y tu ordenador."),
    },
    "fr-fr": {
        "continue": "Continuer",
        "back": "Retour",
        "skip": "Ignorer la configuration",
        "finish": "Terminer",
        "not_now": "Pas maintenant",
        "advanced": "Configuration avancée",
        "stage_interaction": "Interaction",
        "stage_brain": "Cerveau",
        "stage_permissions": "Autorisations",
        "stage_first_mission": "Première mission",
        "hello": "Bonjour",
        "sample": ("Bonjour, je suis VYUK, votre assistante vocale. Je "
                   "peux vous aider avec votre journée, vos fichiers et "
                   "votre ordinateur."),
    },
    "it": {
        "continue": "Continua",
        "back": "Indietro",
        "skip": "Salta la configurazione",
        "finish": "Fine",
        "not_now": "Non ora",
        "advanced": "Configurazione avanzata",
        "stage_interaction": "Interazione",
        "stage_brain": "Cervello",
        "stage_permissions": "Autorizzazioni",
        "stage_first_mission": "Prima missione",
        "hello": "Ciao",
        "sample": ("Ciao, sono VYUK, la tua assistente vocale. Posso "
                   "aiutarti con la tua giornata, i tuoi file e il tuo "
                   "computer."),
    },
    "pt-br": {
        "continue": "Continuar",
        "back": "Voltar",
        "skip": "Pular configuração",
        "finish": "Concluir",
        "not_now": "Agora não",
        "advanced": "Configuração avançada",
        "stage_interaction": "Interação",
        "stage_brain": "Cérebro",
        "stage_permissions": "Permissões",
        "stage_first_mission": "Primeira missão",
        "hello": "Olá",
        "sample": ("Olá, eu sou a VYUK, sua assistente de voz. Posso "
                   "ajudar com o seu dia, seus arquivos e seu computador."),
    },
}

#: Back-compat names the wizard reads: NAV is the whole table;
#: SAMPLE_SENTENCE is the per-language sample sentence alone.
SAMPLE_SENTENCE = {code: table["sample"] for code, table in NAV.items()}

#: Loophole batch 7 (#17): the scope note the language page shows
#: under the language radios, in English (the note itself is page
#: copy, so it stays English — that is the point it states). The
#: page uses this constant, never a retyped copy, so the suite can
#: pin the exact sentence the user reads.
LANGUAGE_PAGE_SCOPE_NOTE = (
    "Buttons and stage names follow your pick; page titles and "
    "instructions stay in English for now.")

#: What is NOT translated, in the same words the scope note uses.
NOT_TRANSLATED = ("page titles", "page bodies", "help text")


def localisation_scope() -> dict:
    """The wizard's localisation scope, honestly: {"translated":
    the sorted chrome keys (the only strings that follow the
    language pick), "not_translated": the page copy that stays
    English}. The language page states this scope to the user;
    the suite pins it so the promise can never drift ahead of
    the delivery again. Full page-copy localisation is still an
    owed follow-up — recorded here, not claimed."""
    return {
        "translated": sorted(_EN),
        "not_translated": list(NOT_TRANSLATED),
    }


def chrome(code: str | None, key: str) -> str:
    """One chrome string for a language code, English on any miss.
    A missing key reads as the key itself — never blank, never a
    raise (the wizard chrome must never break on a gap)."""
    table = NAV.get(str(code or "").strip().lower())
    if table is not None and key in table:
        return table[key]
    return _EN.get(key, key)


def hello(code: str | None) -> str:
    """The Done-page hello for a language code, English on any miss."""
    return chrome(code, "hello")


def sample_sentence(code: str | None) -> str:
    """The voice sample sentence for a language code."""
    return chrome(code, "sample")
