import pytest

from app.core.config import Settings, validate_required_settings


def test_validate_required_settings_rejects_blank_values() -> None:
    settings = Settings(
        DATABASE_URL='',
        REDIS_URL='redis://localhost:6379/0',
        NEO4J_URI='bolt://localhost:7687',
        NEO4J_USER='neo4j',
        NEO4J_PASSWORD='neo4j',
    )

    with pytest.raises(ValueError, match='Missing required settings'):
        validate_required_settings(settings)
