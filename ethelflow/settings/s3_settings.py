from pydantic_settings import BaseSettings, SettingsConfigDict


class S3Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ETHELFLOW_S3_", case_sensitive=False)
    endpoint_url: str = "http://minio:9000"
    access_key: str = "ethel"
    secret_key: str = "ethel_secret"
    bucket_name: str = "ethel-documents"


s3_settings = S3Settings()
