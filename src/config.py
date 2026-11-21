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
    # MCP DNS-rebinding protection, on by default. MCP_ALLOWED_HOSTS: comma-separated Host
    # header values that may reach /mcp besides loopback (exact, or "host:*" for any port).
    # Unset = loopback only. MCP_DNS_REBINDING_PROTECTION=false is the explicit opt-out.
    mcp_dns_rebinding_protection: bool = True
    mcp_allowed_hosts: str = ""
    mcp_allowed_origins: str = ""   # browser Origins allowed on /mcp (non-browser clients send none)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embed_model: str = "nomic-embed-text"
    # Optional separate Ollama for embeddings (writes and search). With MEM0_INFER=false, the
    # default, writes and search need only this one, so an always-on CPU Ollama can serve them
    # while the LLM (infer=True, dedup) stays on a GPU host that may sleep. Empty = OLLAMA_BASE_URL.
    # Must serve the same model as before, or stored vectors stop matching new queries.
    ollama_embed_base_url: str = ""
    redis_url: str = ""  # optional: enable async add_memory queue

    @property
    def embed_base_url(self) -> str:
        return self.ollama_embed_base_url or self.ollama_base_url

    def require_safe_token(self) -> None:
        """Refuse to start with an empty, placeholder or trivially short bearer token."""
        token = self.bearer_token.strip()
        if token in ("", "change-me", "changeme") or len(token) < 16:
            raise SystemExit(
                "BEARER_TOKEN is empty, a placeholder or shorter than 16 characters. "
                "Generate one with: openssl rand -hex 32"
            )


settings = Settings()


LOOPBACK_HOSTS = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"]


def mcp_transport_security(cfg: Settings):
    """DNS-rebinding protection for /mcp, on unless MCP_DNS_REBINDING_PROTECTION=false.

    Loopback is always allowed (probes, local tools, the benchmark harness); MCP_ALLOWED_HOSTS
    adds hosts that match exactly or as "host:*" (any port). Unset = loopback only, so a
    server reached under another name rejects /mcp requests until that name is listed."""
    import logging
    from mcp.server.transport_security import TransportSecuritySettings
    log = logging.getLogger(__name__)
    if not cfg.mcp_dns_rebinding_protection:
        log.warning("MCP_DNS_REBINDING_PROTECTION=false: DNS rebinding protection on /mcp is OFF")
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h.strip() for h in cfg.mcp_allowed_hosts.split(",") if h.strip()]
    if not hosts:
        log.warning("MCP_ALLOWED_HOSTS is not set: /mcp accepts loopback Host headers only "
                    "(set it to the names clients use to reach this server)")
    origins = [o.strip() for o in cfg.mcp_allowed_origins.split(",") if o.strip()]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=hosts + LOOPBACK_HOSTS,
                                     allowed_origins=origins)
