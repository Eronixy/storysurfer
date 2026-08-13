from __future__ import annotations

from pathlib import Path

import pytest

from redditsurfer.config import load_config
from redditsurfer.errors import ConfigurationError


def test_environment_supplies_secrets_without_exposing_them(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "reddit:\n  user_agent: test-agent\nselection:\n  target_duration_seconds: 60\n",
        encoding="utf-8",
    )
    config = load_config(
        config_path,
        environ={
            "REDDIT_CLIENT_ID": "secret-id",
            "REDDIT_CLIENT_SECRET": "secret-value",
        },
    )

    assert config.reddit.credentials_configured
    assert config.selection.target_duration_seconds == 60
    public = config.public_dict()
    assert "secret-id" not in str(public)
    assert "secret-value" not in str(public)


def test_yaml_secrets_are_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("reddit:\n  client_secret: do-not-store-this\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="environment-only"):
        load_config(config_path, environ={})


def test_obsolete_elevenlabs_config_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "speech:\n  provider: elevenlabs\n  api_key: obsolete\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="Obsolete ElevenLabs"):
        load_config(config_path, environ={})


def test_edge_speech_modifiers_are_validated(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("speech:\n  rate: fast\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Edge TTS syntax"):
        load_config(config_path, environ={})


def test_invalid_fraction_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "selection:\n  comment_budget_fraction: 1.5\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="between 0 and 1"):
        load_config(config_path, environ={})


def test_caption_word_limits_must_be_ordered(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "captions:\n  min_words: 5\n  max_words: 2\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="cannot exceed"):
        load_config(config_path, environ={})
