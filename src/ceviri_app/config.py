from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path


APP_DIR = Path(os.getenv("APPDATA", str(Path.home() / ".config"))) / "CaptionBridge"
CONFIG_PATH = APP_DIR / "config.json"
ENV_PATH = Path.cwd() / ".env"


@dataclass
class AppConfig:
    speech_key: str = ""
    speech_region: str = ""
    gemini_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    ai_profile: str = ""
    source_locale: str = "de-DE"
    target_language: str = "tr"
    ui_language: str = ""  # Initialized in __post_init__ if empty
    capture_mode: str = "system"
    input_device_name: str = ""
    topmost: bool = True
    font_size: int = 18
    auto_answer: bool = False

    def __post_init__(self) -> None:
        if not self.ui_language:
            self.ui_language = self._get_system_ui_language()

    @staticmethod
    def _get_system_ui_language() -> str:
        """Detect system language on Windows. Defaults to 'en'."""
        try:
            import ctypes
            # GetUserDefaultUILanguage returns the LANGID for the user's UI language
            # Turkish: 1055 (0x041f)
            # German: 1031 (0x0407)
            # English (US): 1033 (0x0409)
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if langid == 1055:
                return "tr"
            if langid == 1031:
                return "de"
        except Exception:
            pass
        return "en"


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config() -> AppConfig:
    config = AppConfig()

    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stored = {}
        for field_name in asdict(config):
            if field_name in stored:
                setattr(config, field_name, stored[field_name])

    env_values = _load_env_file(ENV_PATH)
    config.speech_key = env_values.get("AZURE_SPEECH_KEY", config.speech_key)
    config.speech_region = env_values.get("AZURE_SPEECH_REGION", config.speech_region)
    config.gemini_key = env_values.get("GEMINI_API_KEY", config.gemini_key)
    config.gemini_model = env_values.get("GEMINI_MODEL", config.gemini_model)
    config.source_locale = env_values.get("CEVIRI_SOURCE_LOCALE", config.source_locale)
    config.target_language = env_values.get("CEVIRI_TARGET_LANGUAGE", config.target_language)
    config.ui_language = env_values.get("CEVIRI_UI_LANGUAGE", config.ui_language)
    config.input_device_name = env_values.get("CEVIRI_INPUT_DEVICE", config.input_device_name)
    return config


def save_config(config: AppConfig) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
