from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_env: str = Field(default='dev', alias='APP_ENV')
    app_port: int = Field(default=8000, alias='APP_PORT')

    database_url: str = Field(default='postgresql://postgres:postgres@localhost:5432/fipro1', alias='DATABASE_URL')
    redis_url: str = Field(default='redis://localhost:6379/0', alias='REDIS_URL')

    neo4j_uri: str = Field(default='bolt://localhost:7687', alias='NEO4J_URI')
    neo4j_user: str = Field(default='neo4j', alias='NEO4J_USER')
    neo4j_password: str = Field(default='neo4jpass', alias='NEO4J_PASSWORD')

    llm_provider: str = Field(default='mock', alias='LLM_PROVIDER')
    llm_api_key: str = Field(default='', alias='LLM_API_KEY')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
