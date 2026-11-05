"""
Configuration and settings management for Patchwork.
"""

from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    APP_NAME: str = "Patchwork Self-Healing CI"
    ALLOW_REGISTRATION: bool = False
    PUBLIC_WEBHOOK_URL: str = Field(
        default="",
        description="Full public webhook URL shown in Settings (tunnel or smee.io channel).",
    )

    # GitHub App credentials
    GITHUB_APP_ID: str = Field(default="", description="GitHub App ID")
    GITHUB_PRIVATE_KEY: str = Field(default="", description="RSA private key string or path")
    GITHUB_WEBHOOK_SECRET: str = Field(default="patchwork_secret_token", description="Webhook HMAC secret")
    GITHUB_TOKEN: str = Field(default="", description="Optional fallback PAT for development/testing")

    # LLM Settings
    LLM_PROVIDER: str = Field(default="gemini", description="'gemini', 'anthropic', 'openai', or 'mock'")
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key")
    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic Claude API key")
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")
    LLM_MODEL: str = Field(default="gemini-2.5-flash", description="Model name")

    # Queue & Persistence
    REDIS_URL: str = "memory"
    DATABASE_URL: str = "sqlite+aiosqlite:///./patchwork.db"

    # Ephemeral Sandbox Settings (Component D)
    SANDBOX_TIMEOUT_SECONDS: int = 120  # Strict 2-minute timeout
    SANDBOX_MEMORY_LIMIT: str = "512m"  # 512MB RAM limit
    SANDBOX_CPU_LIMIT: float = 0.5      # 0.5 vCPU quota
    SANDBOX_NETWORK_DISABLED: bool = True  # Restricted network access
    USE_DOCKER_SANDBOX: bool = True      # Falls back to isolated subprocess if Docker daemon inactive

    # Industry-Grade Guardrails (Section 4)
    MAX_RETRY_ATTEMPTS: int = 3
    MAX_DIFF_LINES: int = 30            # Diff size limiter
    LOOP_PREVENTION_PREFIX: str = "ai-fix/"
    PROTECTED_PATHS: List[str] = [
        "tests/",
        "/tests",
        ".github/workflows",
        "test_",
        "_test.py"
    ]


settings = Settings()
