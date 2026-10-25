"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    environment: str = "development"  # development | production | test
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    storage_dir: Path = BASE_DIR / "data" / "files"
    frontend_dist: Path = BASE_DIR.parent / "frontend" / "dist"
    # Fernet key for encrypting stored files and extracted text at rest.
    # Generated and persisted automatically in development if empty.
    encryption_key: str = ""
    secret_key: str = ""
    cookie_secure: bool = False
    allowed_origins: list[str] = Field(default_factory=list)

    # Jobs
    embedded_worker: bool = True  # run worker threads inside the API process
    worker_threads: int = 2
    job_heartbeat_timeout_s: int = 300
    max_job_attempts: int = 2

    # Limits
    max_upload_mb: int = 20
    max_document_words: int = 60_000
    max_pdf_pages: int = 300
    extraction_timeout_s: int = 60
    extraction_memory_mb: int = 1024
    max_queries_per_document: int = 24
    max_sources_per_document: int = 40
    max_source_chars: int = 400_000
    default_retention_days: int = 90

    # External search providers (all optional; coverage is reported honestly)
    contact_email: str = ""  # sent to OpenAlex/Crossref "polite pool" as required by their etiquette
    enable_openalex: bool = True
    enable_crossref: bool = True
    enable_semantic_scholar: bool = True
    semantic_scholar_api_key: str = ""
    enable_arxiv: bool = True
    enable_wikipedia: bool = True
    wikipedia_language: str = "en"
    brave_api_key: str = ""
    google_cse_key: str = ""
    google_cse_cx: str = ""
    fetch_full_text: bool = True
    http_timeout_s: float = 20.0
    search_cache_ttl_hours: int = 168
    user_agent: str = "OriginalityChecker/2.0 (academic integrity research tool)"

    # AI providers
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    embedding_provider: str = "auto"  # auto | sbert | lexical
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    languagetool_url: str = ""  # e.g. http://languagetool:8010 (optional grammar engine)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
