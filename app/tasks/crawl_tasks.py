import os
import shutil
import asyncio
from datetime import datetime

from app.tasks.celery_app import celery
from app.config import get_settings
from app.services.crawler import crawl_framer_site
from app.services.blob_storage import AzureBlobStorage
from app.services.email_service import EmailService
from app.models.site import Site
from app.models.user import User
from app.models.deployment import Deployment
from beanie import PydanticObjectId
from app.database import init_db


from typing import Optional

async def _crawl_and_deploy_async(site_id: str, trigger: str, deployment_id: Optional[str] = None) -> None:
    settings = get_settings()
    await init_db(settings)
    site = await Site.get(PydanticObjectId(site_id))
    if site is None:
        return
        
    site.status = "deploying"
    await site.save()

    if deployment_id:
        deployment = await Deployment.get(PydanticObjectId(deployment_id))
        if deployment:
            deployment.status = "crawling"
            deployment.started_at = datetime.utcnow()
            await deployment.save()
        else:
            deployment = Deployment(
                site_id=site.id,
                trigger=trigger,
                status="crawling",
                blob_container=settings.azure_storage_container,
                started_at=datetime.utcnow(),
            )
            await deployment.insert()
    else:
        deployment = Deployment(
            site_id=site.id,
            trigger=trigger,
            status="crawling",
            blob_container=settings.azure_storage_container,
            started_at=datetime.utcnow(),
        )
        await deployment.insert()

    temp_dir = f"/tmp/hostfast/{deployment.id}/"
    os.makedirs(temp_dir, exist_ok=True)

    result = await crawl_framer_site(str(site.framer_url), temp_dir, mode='full')

    user = await User.get(site.user_id)
    email_service = EmailService(settings)

    if not result.success:
        deployment.status = "failed"
        deployment.error_message = result.error
        deployment.completed_at = datetime.utcnow()
        await deployment.save()
        
        site.status = "error"
        await site.save()

        if user and user.plan in ("pro", "scale"):
            await email_service.send_deployment_error(user.email, site.cf_project_name or str(site.id), result.error or "Unknown error")
        return

    storage = AzureBlobStorage(settings.azure_storage_connection_string, settings.azure_storage_container)
    try:
        await storage.upload_crawl_files(str(deployment.id), temp_dir)
    finally:
        await storage.client.close()

    sites_storage = AzureBlobStorage(
        connection_string=settings.azure_storage_connection_string,
        container_name=settings.azure_storage_sites_container
    )
    try:
        await sites_storage.upload_site_files(str(site.id), temp_dir)
    finally:
        await sites_storage.client.close()

    deployment.status = "live"
    deployment.pages_crawled = result.pages_crawled
    deployment.assets_count = result.assets_count
    deployment.completed_at = datetime.utcnow()
    await deployment.save()

    site.status = "live"
    site.last_deployed_at = deployment.completed_at
    await site.save()

    if user and user.plan in ("pro", "scale"):
        await email_service.send_deployment_success(user.email, site.cf_project_name or str(site.id))

    try:
        await storage.delete_crawl_files(str(deployment.id))
    finally:
        await storage.client.close()
        
    shutil.rmtree(temp_dir, ignore_errors=True)


@celery.task(bind=True, max_retries=2, default_retry_delay=60, name="app.tasks.crawl_tasks.crawl_and_deploy")
def crawl_and_deploy(self, site_id: str, trigger: str, deployment_id: Optional[str] = None) -> None:
    try:
        asyncio.run(_crawl_and_deploy_async(site_id, trigger, deployment_id))
    except Exception as e:
        try:
            self.retry(exc=e)
        except self.MaxRetriesExceededError:
            async def _mark_failed():
                settings = get_settings()
                await init_db(settings)
                site = await Site.get(PydanticObjectId(site_id))
                if site:
                    site.status = "error"
                    await site.save()
            asyncio.run(_mark_failed())
            raise


import logging
logger = logging.getLogger(__name__)

def set_job_status_sync(job_id: str, status_data: dict) -> None:
    import redis
    import json
    settings = get_settings()
    client = redis.from_url(settings.redis_url)
    try:
        key = f"export_job:{job_id}"
        client.set(key, json.dumps(status_data), ex=3600)
    finally:
        client.close()

async def _export_site_async(job_id: str, framer_url: str) -> None:
    import zipfile
    import redis.asyncio as aioredis
    import json
    settings = get_settings()
    
    # 1. Update status to crawling in Redis
    redis_client = aioredis.from_url(settings.redis_url)
    try:
        await redis_client.set(f"export_job:{job_id}", json.dumps({
            "status": "crawling",
            "pages_done": 0,
        }), ex=3600)
    finally:
        await redis_client.close()

    temp_dir = f"/tmp/hostfast/export/{job_id}"
    os.makedirs(temp_dir, exist_ok=True)
    
    # 2. Define progress callback to update Redis
    def progress_cb(status, pages_done, current_url, assets_count):
        if status == 'crawling':
            async def update_redis():
                r_client = aioredis.from_url(settings.redis_url)
                try:
                    await r_client.set(f"export_job:{job_id}", json.dumps({
                        "status": "crawling",
                        "pages_done": pages_done,
                    }), ex=3600)
                finally:
                    await r_client.close()
            asyncio.create_task(update_redis())
            
    try:
        # Crawl framer site in export mode (relative assets paths)
        result = await crawl_framer_site(
            framer_url,
            temp_dir,
            export_mode=True,
            mode='fast',
            progress_callback=progress_cb
        )
        
        if not result.success:
            redis_client = aioredis.from_url(settings.redis_url)
            try:
                await redis_client.set(f"export_job:{job_id}", json.dumps({
                    "status": "failed",
                    "error": result.error or "Failed to crawl Framer site"
                }), ex=3600)
            finally:
                await redis_client.close()
            return

        # Create ZIP archive
        zip_path = f"/tmp/hostfast/export/{job_id}.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zipf.write(file_path, arcname)

        # Upload ZIP to Azure Blob storage
        blob_name = f"exports/{job_id}.zip"
        storage = AzureBlobStorage(settings.azure_storage_connection_string, settings.azure_storage_container)
        try:
            await storage.upload_file(blob_name, zip_path)
            download_url = storage.generate_sas_url(blob_name, expiry_hours=1)
        finally:
            await storage.client.close()

        # Update status to done
        redis_client = aioredis.from_url(settings.redis_url)
        try:
            await redis_client.set(f"export_job:{job_id}", json.dumps({
                "status": "done",
                "download_url": download_url,
                "pages_crawled": result.pages_crawled,
                "assets_count": result.assets_count,
                "total_sitemap_pages": result.total_sitemap_pages,
                "partial": result.partial,
            }), ex=3600)
        finally:
            await redis_client.close()
        
        # Cleanup zip file
        if os.path.exists(zip_path):
            os.remove(zip_path)
            
    except Exception as e:
        logger.error(f"Error during export of {framer_url}: {e}", exc_info=True)
        redis_client = aioredis.from_url(settings.redis_url)
        try:
            await redis_client.set(f"export_job:{job_id}", json.dumps({
                "status": "failed",
                "error": str(e),
            }), ex=3600)
        finally:
            await redis_client.close()
    finally:
        # Cleanup crawl temp dir
        shutil.rmtree(temp_dir, ignore_errors=True)

@celery.task(name="app.tasks.crawl_tasks.export_site")
def export_site(job_id: str, framer_url: str) -> None:
    try:
        asyncio.run(_export_site_async(job_id, framer_url))
    except Exception as e:
        logger.error(f"Celery export site task failed: {e}", exc_info=True)
        set_job_status_sync(job_id, {"status": "failed", "error": str(e)})
