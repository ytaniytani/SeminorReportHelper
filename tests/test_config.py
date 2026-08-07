"""設定読み込みの回帰テスト。

.env.example をそのままコピーすると `KEY=`(値なし)の行が並ぶ。これが
そのまま int フィールドに渡ると起動時に ValidationError で落ちるため、
空文字は未指定として扱われることを保証する。
"""

from __future__ import annotations

from seminar_report.config import Settings


def _settings(**env: str) -> Settings:
    # _env_file=None で実際の .env を無視し、渡した値だけで組み立てる。
    return Settings(_env_file=None, **env)


def test_empty_int_fields_fall_back_to_none() -> None:
    """`.env.example` の「空なら自動」を成立させる(空文字で落ちない)。"""
    settings = _settings(
        SRH_WHISPER_BEAM_SIZE="",
        SRH_WHISPER_CPU_THREADS="",
    )

    assert settings.whisper_beam_size is None
    assert settings.whisper_cpu_threads is None


def test_empty_optional_strings_become_none() -> None:
    """空文字のまま通すと「設定済み」と誤判定されるため None に潰す。"""
    settings = _settings(
        SRH_WHISPER_LANGUAGE="",
        ANTHROPIC_API_KEY="",
        CONFLUENCE_BASE_URL="",
    )

    assert settings.whisper_language is None
    assert settings.anthropic_api_key is None
    assert settings.confluence_base_url is None


def test_whitespace_only_is_also_treated_as_unset() -> None:
    settings = _settings(SRH_WHISPER_BEAM_SIZE="   ")

    assert settings.whisper_beam_size is None


def test_actual_values_are_preserved() -> None:
    """空文字の処理が、正しく設定された値を壊していないこと。"""
    settings = _settings(
        SRH_WHISPER_BEAM_SIZE="5",
        SRH_WHISPER_CPU_THREADS="16",
        SRH_WHISPER_LANGUAGE="ja",
    )

    assert settings.whisper_beam_size == 5
    assert settings.whisper_cpu_threads == 16
    assert settings.whisper_language == "ja"


def test_empty_credentials_leave_confluence_unconfigured() -> None:
    """空文字が残ると confluence_configured() が真になり、投稿で失敗する。"""
    settings = _settings(
        CONFLUENCE_BASE_URL="",
        CONFLUENCE_EMAIL="",
        CONFLUENCE_API_TOKEN="",
    )

    assert settings.confluence_configured() is False
