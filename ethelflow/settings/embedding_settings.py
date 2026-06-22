from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseSettings):
    """
    Settings for the embedding service.
    """

    api_key: str
    api_version: str = "2025-04-01-preview"
    azure_endpoint: str

    model_config = SettingsConfigDict(
        env_prefix="ETHELFLOW_EMBEDDING_",
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = EmbeddingSettings()
