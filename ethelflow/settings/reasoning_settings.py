from pydantic_settings import BaseSettings, SettingsConfigDict


class ReasoningSettings(BaseSettings):
    api_key: str
    api_version: str = "2025-04-01-preview"
    azure_endpoint: str

    model_config = SettingsConfigDict(
        env_prefix="ETHELFLOW_REASONING_",
        env_file="secret/reasoning.env",
        env_file_encoding="utf-8",
    )


settings = ReasoningSettings()
