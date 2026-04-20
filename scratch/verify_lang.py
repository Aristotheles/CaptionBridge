from ceviri_app.config import AppConfig
import os
import json
from pathlib import Path

# Mock APP_DIR for test
os.environ["APPDATA"] = str(Path.cwd() / "test_appdata")
test_dir = Path(os.environ["APPDATA"]) / "CaptionBridge"
test_dir.mkdir(parents=True, exist_ok=True)
config_path = test_dir / "config.json"

if config_path.exists():
    config_path.unlink()

# Test 1: First run (no config file)
config = AppConfig()
print(f"Default detected language: {config.ui_language}")

# Test 2: After loading from non-existent config
from ceviri_app.config import load_config
# We need to monkeypatch the path inside config.py or just rely on the fact that if it doesn't exist, it uses defaults.
# The current config.py uses a hardcoded APP_DIR. 
# Let's just test the AppConfig initialization.
print(f"AppConfig default: {AppConfig().ui_language}")
