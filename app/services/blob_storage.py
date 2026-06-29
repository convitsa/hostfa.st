import os
import mimetypes

from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings


class AzureBlobStorage:
    def __init__(self, connection_string: str, container_name: str = "hostfast-crawls") -> None:
        self.connection_string = connection_string
        self.container_name = container_name
        self.client = BlobServiceClient.from_connection_string(self.connection_string)

    async def _ensure_container(self):
        container_client = self.client.get_container_client(self.container_name)
        if not await container_client.exists():
            await container_client.create_container()

    async def upload_crawl_files(self, deployment_id: str, local_dir: str) -> str:
        await self._ensure_container()
        container_client = self.client.get_container_client(self.container_name)

        prefix_path = f"{deployment_id}/"

        for root, _, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                rel_path = os.path.relpath(local_path, local_dir)
                blob_path = f"{deployment_id}/{rel_path}"

                content_type, _ = mimetypes.guess_type(local_path)
                if not content_type:
                    content_type = "application/octet-stream"

                with open(local_path, "rb") as data:
                    blob_client = container_client.get_blob_client(blob_path)
                    await blob_client.upload_blob(
                        data,
                        overwrite=True,
                        content_settings=ContentSettings(content_type=content_type),
                    )

        return prefix_path

    async def download_crawl_files(self, deployment_id: str, local_dir: str) -> None:
        await self._ensure_container()
        container_client = self.client.get_container_client(self.container_name)

        prefix = f"{deployment_id}/"
        async for blob in container_client.list_blobs(name_starts_with=prefix):
            blob_client = container_client.get_blob_client(blob.name)
            rel_path = blob.name[len(prefix):]
            local_path = os.path.join(local_dir, rel_path)

            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            with open(local_path, "wb") as f:
                download_stream = await blob_client.download_blob()
                f.write(await download_stream.readall())

    async def delete_crawl_files(self, deployment_id: str) -> None:
        await self._ensure_container()
        container_client = self.client.get_container_client(self.container_name)

        prefix = f"{deployment_id}/"
        async for blob in container_client.list_blobs(name_starts_with=prefix):
            blob_client = container_client.get_blob_client(blob.name)
            await blob_client.delete_blob()

    async def upload_file(self, blob_name: str, local_file_path: str) -> None:
        await self._ensure_container()
        container_client = self.client.get_container_client(self.container_name)
        blob_client = container_client.get_blob_client(blob_name)

        content_type, _ = mimetypes.guess_type(local_file_path)
        if not content_type:
            content_type = "application/zip"

        with open(local_file_path, "rb") as data:
            await blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )

    def generate_sas_url(self, blob_name: str, expiry_hours: int = 1) -> str:
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        from datetime import datetime, timedelta, timezone

        account_name = None
        account_key = None
        for part in self.connection_string.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip().lower() == "accountname":
                    account_name = v.strip()
                elif k.strip().lower() == "accountkey":
                    account_key = v.strip()

        if not account_name or not account_key:
            raise ValueError("Invalid Azure connection string")

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=self.container_name,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
        )

        endpoint_suffix = "core.windows.net"
        for part in self.connection_string.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip().lower() == "endpointsuffix":
                    endpoint_suffix = v.strip()

        return f"https://{account_name}.blob.{endpoint_suffix}/{self.container_name}/{blob_name}?{sas_token}"


async def upload_file_to_blob(local_path: str, blob_name: str, expiry_hours: int = 1) -> str:
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.environ.get("AZURE_STORAGE_CONTAINER")
    
    if not connection_string:
        from app.config import get_settings
        settings = get_settings()
        connection_string = settings.azure_storage_connection_string
        container_name = settings.azure_storage_container
    
    if not container_name:
        container_name = "hostfast-assets"

    account_name = None
    account_key = None
    for part in connection_string.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().lower() == "accountname":
                account_name = v.strip()
            elif k.strip().lower() == "accountkey":
                account_key = v.strip()

    if not account_name or not account_key:
        raise ValueError("Invalid Azure connection string")

    async with BlobServiceClient.from_connection_string(connection_string) as service_client:
        container_client = service_client.get_container_client(container_name)
        if not await container_client.exists():
            await container_client.create_container()
            
        blob_client = container_client.get_blob_client(blob_name)
        content_type, _ = mimetypes.guess_type(local_path)
        if not content_type:
            content_type = "application/zip"

        with open(local_path, "rb") as data:
            await blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )

    from azure.storage.blob import generate_blob_sas, BlobSasPermissions
    from datetime import datetime, timedelta, timezone

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
    )

    endpoint_suffix = "core.windows.net"
    for part in connection_string.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().lower() == "endpointsuffix":
                endpoint_suffix = v.strip()

    return f"https://{account_name}.blob.{endpoint_suffix}/{container_name}/{blob_name}?{sas_token}"
