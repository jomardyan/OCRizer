from __future__ import annotations

import json
import logging

import pytest

from ocrizer.config import (
    AppConfig,
    OcrSettings,
    config_path,
    load_config,
    normalize_language,
    save_config,
    split_language,
)
from ocrizer.errors import ConfigError
from ocrizer.logging_setup import JsonFormatter, configure_logging, get_logger, log_event


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("auto", "auto"), ("AUTO", "auto"), ("pol", "pol"), ("pl", "pol"), ("Polish", "pol"),
        ("eng", "eng"), ("en", "eng"), ("english", "eng"), ("pol+eng", "pol+eng"),
        ("eng+pol", "eng+pol"), ("pl,en", "pol+eng"), (" pol + eng ", "pol+eng"), ("pol+pol", "pol"),
    ],
)
def test_normalize_language(value, expected):
    assert normalize_language(value) == expected


@pytest.mark.parametrize("value", ["", "+,+", None, 3, [], "deu", "pol+deu", "auto+pol", "klingon"])
def test_normalize_language_rejects(value):
    with pytest.raises(ConfigError):
        normalize_language(value)


def test_split_language():
    assert split_language("auto") == ()
    assert split_language("pol+eng") == ("pol", "eng")


def test_ocr_settings_validation():
    assert OcrSettings(language="pl").validated().language == "pol"
    with pytest.raises(ConfigError):
        OcrSettings(optimize=9).validated()
    with pytest.raises(ConfigError):
        OcrSettings(detect_pages=0).validated()


@pytest.mark.parametrize("settings", [
    {"optimize": 1.5}, {"detect_pages": True}, {"ocr_jobs": "2"},
    {"max_ocr_megapixels": 0}, {"max_ocr_megapixels": float("nan")},
    {"tesseract_timeout": float("inf")}, {"tesseract_timeout": -1},
    {"existing_text": "unknown"}, {"export_text": "false"},
    {"existing_text": "redo", "deskew": True},
])
def test_ocr_settings_reject_invalid_library_values(settings):
    with pytest.raises(ConfigError):
        OcrSettings(**settings).validated()


def test_config_defaults_roundtrip_and_env(monkeypatch):
    assert not config_path().exists()
    cfg = load_config()
    assert cfg.language == "auto" and cfg.workers >= 1
    cfg.workers = 3
    cfg.gui_language = "pol"
    save_config(cfg)
    again = load_config()
    assert again.workers == 3 and again.gui_language == "pol"

    monkeypatch.setenv("OCRIZER_WORKERS", "2")
    monkeypatch.setenv("OCRIZER_LANGUAGE", "eng")
    env_cfg = load_config()
    assert env_cfg.workers == 2 and env_cfg.language == "eng"


def test_config_rejects_bad_values(monkeypatch):
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"workers": 0}))
    with pytest.raises(ConfigError):
        load_config()
    path.write_text("not json")
    with pytest.raises(ConfigError):
        load_config()
    path.write_text(json.dumps({"unknown_key": 1, "workers": 2}))
    assert load_config().workers == 2  # unknown keys are ignored
    monkeypatch.setenv("OCRIZER_WORKERS", "many")
    with pytest.raises(ConfigError):
        load_config()


def test_suffix_validation():
    with pytest.raises(ConfigError):
        AppConfig(suffix="a/b").validate()
    with pytest.raises(ConfigError):
        AppConfig(suffix="").validate()


@pytest.mark.parametrize("values", [
    {"language": None}, {"suffix": []}, {"suffix": "\x00"},
    {"tesseract_path": 42}, {"gui_output_dir": None}, {"gui_export_text": "false"},
])
def test_invalid_config_types_raise_readable_config_error(values):
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config()


def test_failed_config_save_cleans_temporary_file(tmp_path, monkeypatch):
    from ocrizer import config

    path = tmp_path / "config.json"
    path.write_text("original", encoding="utf-8")

    def cannot_replace(*args):
        raise PermissionError("read only")

    monkeypatch.setattr(config.os, "replace", cannot_replace)
    with pytest.raises(ConfigError):
        save_config(AppConfig(), path)
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_json_log_file_is_structured_and_free_of_content(tmp_path):
    log_file = tmp_path / "logs" / "run.jsonl"
    configure_logging(verbosity=1, log_file=log_file)
    log_event(get_logger("test"), logging.INFO, "job_finished", file="zażółć.pdf", pages=3)
    for handler in logging.getLogger("ocrizer").handlers:
        handler.flush()
    record = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "job_finished"
    assert record["file"] == "zażółć.pdf" and record["pages"] == 3
    assert record["level"] == "INFO" and "ts" in record
    configure_logging()  # release the file handle


def test_json_formatter_handles_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, "f", 1, "failed", (), sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError" in payload["exception"]
