import os
import shutil
import asyncio
from datetime import datetime

from app.tasks.celery_app import celery
from app.config import get_settings
from app.services.crawler import crawl_framer_site
from app.services.blob_storage import AzureBlobStorage
from app.services.cloudflare import upload_deployment
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

    cf_deployment_id = await upload_deployment(site.cf_project_name or str(site.id), temp_dir)

    deployment.status = "live"
    deployment.cf_deployment_id = cf_deployment_id
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
        key = f"framer_export:job:{job_id}"
        client.set(key, json.dumps(status_data), ex=86400) # 24 hour expiry
    finally:
        client.close()

async def _export_site_async(framer_url: str, job_id: str) -> None:
    import zipfile
    settings = get_settings()
    
    # Update status to crawling
    set_job_status_sync(job_id, {"status": "crawling"})

    temp_dir = f"/tmp/hostfast/export/{job_id}"
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Crawl framer site in export mode (relative assets paths)
        result = await crawl_framer_site(framer_url, temp_dir, export_mode=True)
        
        if not result.success:
            set_job_status_sync(job_id, {"status": "failed", "error": result.error or "Failed to crawl Framer site"})
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
        set_job_status_sync(job_id, {
            "status": "done",
            "download_url": download_url,
            "pages_count": result.pages_crawled,
            "assets_count": result.assets_count
        })
        
        # Cleanup zip file
        if os.path.exists(zip_path):
            os.remove(zip_path)
            
    except Exception as e:
        logger.error(f"Error during export of {framer_url}: {e}", exc_info=True)
        set_job_status_sync(job_id, {"status": "failed", "error": str(e)})
    finally:
        # Cleanup crawl temp dir
        shutil.rmtree(temp_dir, ignore_errors=True)

@celery.task(name="app.tasks.crawl_tasks.export_site")
def export_site(framer_url: str, job_id: str) -> None:
    try:
        asyncio.run(_export_site_async(framer_url, job_id))
    except Exception as e:
        logger.error(f"Celery export site task failed: {e}", exc_info=True)
        set_job_status_sync(job_id, {"status": "failed", "error": str(e)})
