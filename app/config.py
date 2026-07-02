"""Application configuration, loaded from environment / .env file."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Which extraction engine to use for the "AI" step:
    #   "ollama"    -> local vision LLM, free & offline (recommended free option)
    #   "rules"     -> pure offline regex/heuristic parser, zero extra install
    #   "anthropic" -> Claude cloud API (paid, needs ANTHROPIC_API_KEY)
    extraction_backend: str = "ollama"

    # If an LLM backend (ollama/anthropic) fails on a page, fall back to the
    # offline rules parser so you still get output instead of an empty result.
    llm_fallback_to_rules: bool = True
    # After this many consecutive LLM failures, stop calling the LLM for the
    # rest of the document and use the rules parser (fast) for the remainder.
    llm_fallback_max_failures: int = 2

    # Anthropic (only used when extraction_backend = "anthropic").
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Ollama (only used when extraction_backend = "ollama").
    # Install Ollama from https://ollama.com then: ollama pull llama3.2-vision
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2-vision"
    ollama_timeout: int = 180          # seconds per page
    ollama_load_timeout: int = 300     # seconds allowed to load the model once
    ollama_keep_alive: str = "30m"     # keep model resident between pages (speed!)
    ollama_image_max_width: int = 1024  # smaller image = faster & less memory
    ollama_num_predict: int = 700      # cap output tokens (JSON is short)

    # OCR / rendering.
    render_dpi: int = 220
    ai_image_max_width: int = 1600

    # Optional explicit paths for Windows users.
    tesseract_cmd: str = ""
    poppler_path: str = ""


settings = Settings()