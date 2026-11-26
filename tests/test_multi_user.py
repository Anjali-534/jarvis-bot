"""Any user can install Jarvis: data folders, settings.json, secrets in the Credential Manager (a fake keyring
here), the .env migration, gender-neutral talk, and no personal data left in the shipped code."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import config
from jarvis import grammar, persona, settings_store, vault

ROOT = Path(config.REPO_DIR)


# ---------------------------------------------------------------- a fake Credential Manager
class MemoryKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        if (service, name) not in self.store:
            raise KeyError(name)
        del self.store[(service, name)]


@pytest.fixture
def keyring(monkeypatch):
    import keyring as real

    fake = MemoryKeyring()
    monkeypatch.setenv("JARVIS_NO_VAULT", "0")
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(real, name, getattr(fake, name))
    return fake


# ---------------------------------------------------------------- where data lives
def config_in(env):
    """Import config in a fresh Python with these environment variables; return its paths."""
    code = ("import json, config; print(json.dumps({'dev': config.DEV, 'data': str(config.DATA_DIR), "
            "'settings': str(config.SETTINGS_FILE), 'notes': str(config.NOTES_FILE), 'logs': str(config.LOG_DIR), "
            "'memory': str(config.MEMORY_FILE), 'base': str(config.BASE_DIR), 'version': config.VERSION}))")
    clean = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**clean, "JARVIS_NO_VAULT": "1", **env})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_installed_app_keeps_everything_in_appdata(tmp_path):
    paths = config_in({"JARVIS_DEV": "0", "APPDATA": str(tmp_path)})
    home = tmp_path / "Jarvis"
    assert paths["dev"] is False and paths["data"] == str(home)
    for key in ("settings", "notes", "logs", "memory"):
        assert paths[key].startswith(str(home)), key
    assert paths["base"] == str(ROOT)  # bundled files (COMMANDS.md, examples) stay with the program


def test_jarvis_home_overrides_the_data_folder(tmp_path):
    paths = config_in({"JARVIS_HOME": str(tmp_path / "portable")})
    assert paths["data"] == str(tmp_path / "portable") and paths["notes"].startswith(str(tmp_path / "portable"))


def test_dev_mode_keeps_the_repo_data_folder():
    paths = config_in({"JARVIS_DEV": "1", "JARVIS_TESTING": "1"})
    assert paths["dev"] is True and paths["data"] == str(ROOT / "data")


def test_one_version_number():
    from jarvis import __version__

    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__) and config.VERSION == __version__


def test_relative_paths_mean_the_data_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOTES_FILE", "data/my notes.txt")
    assert config._path("NOTES_FILE", "data/notes.txt") == tmp_path / "my notes.txt"
    monkeypatch.setenv("NOTES_FILE", str(tmp_path / "abs.txt"))
    assert config._path("NOTES_FILE", "data/notes.txt") == tmp_path / "abs.txt"


# ---------------------------------------------------------------- settings.json and secrets
def test_settings_json_never_holds_secrets(tmp_path, keyring):
    settings_store.update({"USER_NAME": "Sam", "AI_API_KEY": "gsk_secret_value_123456", "WAKE_WORD_ENABLED": True},
                          tmp_path)
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"USER_NAME": "Sam", "WAKE_WORD_ENABLED": True}
    assert "gsk_secret" not in (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert vault.get("AI_API_KEY") == "gsk_secret_value_123456"
    assert keyring.store == {("Jarvis (dev)" if config.DEV else "Jarvis", "AI_API_KEY"): "gsk_secret_value_123456"}


def test_vault_delete_and_mask(keyring):
    vault.set("TAVILY_API_KEY", "tvly-abcdefghijklmnop")
    assert vault.mask(vault.get("TAVILY_API_KEY")) == "tvly...mnop"
    vault.set("TAVILY_API_KEY", "")  # an empty value deletes it
    assert vault.get("TAVILY_API_KEY") is None and vault.all_secrets() == {}


def test_vault_errors_never_stop_jarvis(monkeypatch):
    import keyring as real

    def broken(*args):
        raise RuntimeError("no keyring backend")

    monkeypatch.setenv("JARVIS_NO_VAULT", "0")
    monkeypatch.setattr(real, "get_password", broken)
    assert vault.get("AI_API_KEY") is None


def test_env_migration_moves_secrets_to_the_vault_and_blanks_them(tmp_path, keyring):
    env = tmp_path / ".env"
    env.write_text("# my setup\nUSER_NAME=Sam\nAI_API_KEY=gsk_live_key_123456\nTTS_VOICE=en-US-GuyNeural\nWAQI_TOKEN=\n",
                   encoding="utf-8")
    moved = config.migrate_env(env, tmp_path / "data")
    assert moved["secrets"] == ["AI_API_KEY"]
    settings = settings_store.load(tmp_path / "data")
    assert settings["USER_NAME"] == "Sam" and settings["TTS_VOICE"] == "en-US-GuyNeural"
    assert "AI_API_KEY" not in settings and vault.get("AI_API_KEY") == "gsk_live_key_123456"
    text = env.read_text(encoding="utf-8")
    assert "gsk_live_key_123456" not in text and "AI_API_KEY=  # moved to Windows Credential Manager" in text
    assert "# my setup" in text and "USER_NAME=Sam" in text  # the rest of .env is untouched
    assert config.migrate_env(env, tmp_path / "data") is None  # only once


def test_settings_beat_env_but_real_environment_wins(tmp_path):
    """Precedence: the real environment > settings.json > .env (a fresh process, a JARVIS_HOME data folder)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"USER_NAME": "FromSettings", "NICKNAME": "Nick"}), encoding="utf-8")
    (home / ".env").write_text("USER_NAME=FromEnvFile\nASSISTANT_NAME=Friday\n", encoding="utf-8")
    code = "import config; print(config.USER_NAME, config.NICKNAME, config.ASSISTANT_NAME, config.PERSONALITY)"
    clean = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**clean, "JARVIS_HOME": str(home), "JARVIS_NO_VAULT": "1", "PERSONALITY": "professional"})
    assert out.stdout.split() == ["FromSettings", "Nick", "Friday", "professional"], out.stderr


# ---------------------------------------------------------------- no personal defaults
def test_no_personal_defaults(monkeypatch):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_") and k not in (
        "USER_NAME", "NICKNAME", "USER_GENDER", "DEFAULT_LOCATION", "PROJECT_ROOTS", "LANGUAGE")}
    code = ("import config; print(repr(config.USER_NAME), repr(config.USER_GENDER), repr(config.DEFAULT_LOCATION), "
            "repr(config.LANGUAGE), [str(p) for p in config.PROJECT_ROOTS])")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**clean, "JARVIS_DEV": "0", "APPDATA": str(Path(os.environ.get("TEMP", ".")) /
                                                                         "jarvis-fresh-user-test"),
                              "JARVIS_NO_VAULT": "1"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("'' 'neutral' '' 'english'")
    assert "Desktop" in out.stdout and "Documents" in out.stdout  # project folders: the user's own


# Shipped code (not tests, docs or examples) must not contain one user's personal details
PERSONAL = re.compile(r"anjali|bogie|delhi", re.IGNORECASE)
SHIPPED = ["config.py", "main.py", "run_jarvis.pyw", "install_startup.py", "uninstall_startup.py",
           "routines.example.json", "projects.example.json", "contacts.example.json"]


def shipped_files():
    files = [ROOT / name for name in SHIPPED if (ROOT / name).exists()]
    files += [p for p in (ROOT / "jarvis").rglob("*") if p.suffix in (".py", ".json", ".txt", ".html", ".css", ".js")
              and "__pycache__" not in p.parts]
    files += [p for p in (ROOT / "scripts").rglob("*.py")] + [p for p in (ROOT / "packaging").rglob("*")
                                                              if p.suffix in (".py", ".spec", ".iss", ".ps1")]
    return files


def test_no_personal_data_left_in_the_code():
    hits = []
    for path in shipped_files():
        for number, text in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if PERSONAL.search(text) and "personal-data-check: ignore" not in text:
                hits.append(f"{path.relative_to(ROOT)}:{number}: {text.strip()[:80]}")
    assert hits == []


def test_no_hard_coded_user_paths():
    pattern = re.compile(r"C:\\\\?Users\\\\?(?!Public)[A-Za-z]+\\\\?(?:OneDrive|Desktop|Documents)", re.IGNORECASE)
    hits = [str(p.relative_to(ROOT)) for p in shipped_files()
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []


# ---------------------------------------------------------------- gender-neutral and nameless talk
@pytest.fixture
def neutral(monkeypatch):
    monkeypatch.setattr(config, "USER_GENDER", "neutral")
    monkeypatch.setattr(config, "PERSONALITY", "friend")


def test_neutral_users_never_get_gendered_phrases(neutral):
    for key, entry in persona.PHRASES.items():
        for which in entry:
            for template in persona.variants(key, which):
                assert "{u_a}" not in template and "{u_ta}" not in template, (key, template)
    assert persona.line("greet_friend") if "greet_friend" in persona.PHRASES else True


def test_every_phrase_still_has_something_to_say_when_neutral(neutral):
    for key in persona.PHRASES:
        assert persona.variants(key), key


def test_neutral_grammar_leaves_the_user_alone_but_fixes_jarvis(neutral):
    assert grammar.fix("tu kaisa hai? main kar dungi") == "tu kaisa hai? main kar dunga"
    rule = persona.grammar_rule()
    assert "gender-neutral" in rule and "kaisi hai" not in rule


def test_male_and_female_users_still_get_their_forms(monkeypatch):
    monkeypatch.setattr(config, "USER_GENDER", "male")
    assert grammar.fix("tu kaisi hai?") == "tu kaisa hai?"
    monkeypatch.setattr(config, "USER_GENDER", "female")
    assert grammar.fix("tu kaisa hai?") == "tu kaisi hai?"


def test_a_missing_name_reads_naturally(monkeypatch):
    monkeypatch.setattr(config, "USER_NAME", "")
    monkeypatch.setattr(config, "NICKNAME", "")
    monkeypatch.setattr(config, "PERSONALITY", "professional")
    assert persona.line("online_morning") == "Good morning. Jarvis is online."


def test_english_friends_get_english_lines(monkeypatch):
    monkeypatch.setattr(config, "PERSONALITY", "friend")
    monkeypatch.setattr(config, "LANGUAGE", "english")
    assert persona.mode() == persona.PROFESSIONAL and persona.casual()
    assert "close friend" in persona.ai_style() and persona.line("done") in persona.PHRASES["done"]["professional"]
    monkeypatch.setattr(config, "LANGUAGE", "hinglish")
    assert persona.mode() == persona.FRIEND


def test_no_city_asks_for_one(monkeypatch):
    from jarvis import geo
    from jarvis.router import SpokenError

    monkeypatch.setattr(config, "DEFAULT_LOCATION", "")
    with pytest.raises(SpokenError, match="I don't know where you are yet"):
        geo.default_place()


def test_saved_home_coordinates_skip_geocoding(monkeypatch):
    from jarvis import geo

    monkeypatch.setattr(config, "DEFAULT_LOCATION", "Pune, Maharashtra")
    monkeypatch.setattr(config, "HOME_LAT", 18.52)
    monkeypatch.setattr(config, "HOME_LON", 73.85)
    place = geo.default_place()
    assert (place.name, place.lat, place.lon) == ("Pune", 18.52, 73.85)


def test_red_zones_circle_the_users_city():
    from jarvis.geo import Place
    from jarvis.skills import aqi

    points = aqi.area_points(Place("Pune", 18.52, 73.85))
    assert len(points) == 17 and points[0].name == "Central Pune"
    assert points[1].name == "North of Pune (about 10 km)" and points[1].lat > 18.6
