from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bearer_token: str = ""
    anthropic_api_key: str = ""
    database_url: str = "postgresql://mem0:mem0@localhost:5432/mem0"
    mem0_user_id: str = "default"
    mem0_agent_id: str = "default"   # fallback when no X-Agent-ID header is sent
    mem0_infer: bool = True           # enable LLM extraction and deduplication
    app_port: int = 8080
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embed_model: str = "nomic-embed-text"
    redis_url: str = ""  # optional: enable async add_memory queue

    def require_safe_token(self) -> None:
        """Refuse to start with an empty, placeholder or trivially short bearer token."""
        token = self.bearer_token.strip()
        if token in ("", "change-me", "changeme") or len(token) < 16:
            raise SystemExit(
                "BEARER_TOKEN is empty, a placeholder or shorter than 16 characters. "
                "Generate one with: openssl rand -hex 32"
            )


settings = Settings()
