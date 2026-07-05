"""identity-service settings. DB_SCHEMA is fixed to `identity`."""
from shared.config import Settings as BaseSettings


class Settings(BaseSettings):
    DB_SCHEMA: str = "identity"


settings = Settings()  # type: ignore[call-arg]