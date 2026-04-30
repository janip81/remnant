from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bearer_token: str = "change-me"
    anthropic_api_key: str = ""
    database_url: str = "postgresql://mem0:mem0@localhost:5432/mem0"
    mem0_user_id: str = "jani"
    mem0_agent_id: str = "default"   # fallback when no X-Agent-ID header is sent
    mem0_infer: bool = True           # enable LLM extraction and deduplication
    app_port: int = 8080
    ollama_base_url: str = "http://192.168.81.20:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embed_model: str = "nomic-embed-text"


settings = Settings()
