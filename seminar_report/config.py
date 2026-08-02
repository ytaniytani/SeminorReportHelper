"""環境変数 / .env からの設定読み込み。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- LLM ----
    llm_provider: str = Field(default="claude", alias="SRH_LLM_PROVIDER")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-5", alias="SRH_CLAUDE_MODEL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="SRH_OPENAI_MODEL")
    ollama_host: str = Field(default="http://localhost:11434", alias="SRH_OLLAMA_HOST")
    ollama_model: str = Field(default="qwen2.5:14b", alias="SRH_OLLAMA_MODEL")

    # ---- 文字起こし ----
    whisper_model: str = Field(default="medium", alias="SRH_WHISPER_MODEL")
    whisper_device: str = Field(default="auto", alias="SRH_WHISPER_DEVICE")
    whisper_language: str | None = Field(default=None, alias="SRH_WHISPER_LANGUAGE")
    # 未指定なら device に応じて決める(GPU: 5 / CPU: 1)。CPU では beam_size を
    # 下げると精度をほぼ落とさずに約 2 倍速くなる。
    whisper_beam_size: int | None = Field(default=None, alias="SRH_WHISPER_BEAM_SIZE")
    # 未指定なら CPU コア数。CTranslate2 の既定は 4 で、多コア機では遊んでしまう。
    whisper_cpu_threads: int | None = Field(default=None, alias="SRH_WHISPER_CPU_THREADS")

    # ---- レポート ----
    report_language: str = Field(default="日本語", alias="SRH_REPORT_LANGUAGE")
    # LLM を同時に何本走らせるか。上げすぎるとレート制限に当たる。
    llm_concurrency: int = Field(default=4, alias="SRH_LLM_CONCURRENCY")

    # ---- Confluence ----
    confluence_base_url: str | None = Field(default=None, alias="CONFLUENCE_BASE_URL")
    confluence_email: str | None = Field(default=None, alias="CONFLUENCE_EMAIL")
    confluence_api_token: str | None = Field(default=None, alias="CONFLUENCE_API_TOKEN")
    confluence_space_key: str | None = Field(default=None, alias="CONFLUENCE_SPACE_KEY")

    # ---- パス ----
    cache_dir: Path = Field(default=PROJECT_ROOT / ".cache", alias="SRH_CACHE_DIR")
    output_dir: Path = Field(default=PROJECT_ROOT / "output", alias="SRH_OUTPUT_DIR")
    jobs_dir: Path = Field(default=PROJECT_ROOT / "jobs", alias="SRH_JOBS_DIR")

    def confluence_configured(self) -> bool:
        return all(
            [
                self.confluence_base_url,
                self.confluence_email,
                self.confluence_api_token,
            ]
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """プロセス内で使い回す設定インスタンス。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
