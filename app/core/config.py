from functools import lru_cache
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_env: str = Field(default='dev', alias='APP_ENV')
    app_port: int = Field(default=8000, alias='APP_PORT')

    database_url: str = Field(alias='DATABASE_URL')
    redis_url: str = Field(alias='REDIS_URL')

    neo4j_uri: str = Field(alias='NEO4J_URI')
    neo4j_user: str = Field(alias='NEO4J_USER')
    neo4j_password: str = Field(alias='NEO4J_PASSWORD')

    llm_provider: str = Field(default='mock', alias='LLM_PROVIDER')
    llm_api_key: str = Field(default='', alias='LLM_API_KEY')

    tushare_token: str = Field(default='', alias='TUSHARE_TOKEN')
    news_data_api_key: str = Field(default='', alias='NEWS_DATA_API_KEY')
    tushare_base_url: str = Field(default='https://api.tushare.pro', alias='TUSHARE_BASE_URL')
    datasource_timeout_seconds: float = Field(default=6.0, alias='DATASOURCE_TIMEOUT_SECONDS')


REQUIRED_SETTINGS: Final[tuple[str, ...]] = (
    'database_url',
    'redis_url',
    'neo4j_uri',
    'neo4j_user',
    'neo4j_password',
)


def validate_required_settings(settings: Settings) -> None:
    missing = [name for name in REQUIRED_SETTINGS if not str(getattr(settings, name, '')).strip()]
    if missing:
        missing_display = ', '.join(missing)
        raise ValueError(f'Missing required settings: {missing_display}')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    validate_required_settings(settings)
    return settings
