from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    bearer_token: str = ""
    anthropic_api_key: str = ""
    database_url: str = "postgresql://mem0:mem0@localhost:5432/mem0"
    # REMNANT_* is the documented name; MEM0_* kept for existing deployments (Helm chart).
    mem0_user_id: str = Field("default", validation_alias=AliasChoices("REMNANT_USER_ID", "MEM0_USER_ID"))
    mem0_agent_id: str = Field("default", validation_alias=AliasChoices("REMNANT_AGENT_ID", "MEM0_AGENT_ID"))  # fallback agent_id
    # Default for add_memory when a client omits infer. False = store as written
    # (agents decide what to save); True = mem0 LLM extraction first.
    mem0_infer: bool = False
    app_port: int = 8080
    app_host: str = "0.0.0.0"   # the benchmark harness binds 127.0.0.1
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
