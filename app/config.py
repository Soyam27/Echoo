from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    debug: bool = False

    database_url: str

    secret_key: str
    access_token_expire_days: int = 7

    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_embedding_deployment: str = "text-embedding-3-small"
    azure_chat_deployment: str = "gpt-4o-mini"

    instagram_client_id: str
    instagram_client_secret: str
    instagram_redirect_uri: str

    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
