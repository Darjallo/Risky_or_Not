from io import BytesIO

from aiobotocore.client import AioBaseClient
from aiobotocore.session import get_session
from botocore.client import Config

from ethelflow.settings.s3_settings import s3_settings


class S3Manager:
    def __init__(self):
        self.session = get_session()
        self.endpoint_url = s3_settings.endpoint_url
        self.access_key = s3_settings.access_key
        self.secret_key = s3_settings.secret_key
        self.bucket_name = s3_settings.bucket_name
        self.config = Config(signature_version="s3v4")
        self.s3_client: AioBaseClient | None = None
        self._initialized = False

    async def init(self):
        """Initialize the async S3 client and ensure the bucket exists once."""
        if self._initialized:
            return  # already initialized

        self.s3_client = await self.session.create_client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self.config,
        ).__aenter__()

        await self._ensure_bucket_exists()
        self._initialized = True

    async def close(self):
        """Close the S3 client when the app shuts down."""
        if self.s3_client:
            await self.s3_client.__aexit__(None, None, None)
            self.s3_client = None
            self._initialized = False

    async def _ensure_bucket_exists(self):
        try:
            await self.s3_client.head_bucket(Bucket=self.bucket_name)
        except self.s3_client.exceptions.ClientError as e:
            error_code = int(e.response["Error"]["Code"])
            if error_code == 404:
                await self.s3_client.create_bucket(Bucket=self.bucket_name)

    async def upload_file(self, file_object, object_name):
        await self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=object_name,
            Body=file_object.read(),
        )
        return f"s3://{self.bucket_name}/{object_name}"

    async def delete_file(self, object_name):
        await self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_name)

    async def download_file(self, object_name, file_object: BytesIO):
        response = await self.s3_client.get_object(
            Bucket=self.bucket_name,
            Key=object_name,
        )
        async with response["Body"] as stream:
            data = await stream.read()
            file_object.write(data)


s3_manager = S3Manager()
