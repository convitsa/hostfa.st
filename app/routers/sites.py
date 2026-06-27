from datetime import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from beanie import PydanticObjectId

from app.routers.auth import get_current_user
from app.models.site import Site
from app.models.deployment import Deployment
from app.models.user import User
from app.config import get_settings
from app.schemas.site import CreateSiteRequest, SiteResponse, SiteDetailResponse
from app.schemas.deployment import DeploymentResponse
from app.services.cloudflare import create_cf_project, add_custom_domain, verify_domain, delete_project
from app.tasks.crawl_tasks import crawl_and_deploy
from app.services.crawler import is_valid_framer_url
import dns.resolver
import redis.asyncio as aioredis
from pydantic import BaseModel
from fastapi import Request

router = APIRouter()
logger = logging.getLogger(__name__)


def serialize_site(site: Site) -> SiteResponse:
    return SiteResponse(
        id=str(site.id),
        user_id=str(site.user_id),
        framer_url=site.framer_url,  # type: ignore[arg-type]
        custom_domain=site.custom_domain,
        cf_project_name=site.cf_project_name,
        cf_project_id=site.cf_project_id,
        status=site.status,
        last_etag=site.last_etag,
        last_modified=site.last_modified,
        last_polled_at=site.last_polled_at,
        last_deployed_at=site.last_deployed_at,
        created_at=site.created_at,
        updated_at=site.updated_at,
    )


def serialize_deployment(deployment: Deployment) -> DeploymentResponse:
    return DeploymentResponse(
        id=str(deployment.id),
        site_id=str(deployment.site_id),
        trigger=deployment.trigger,
        status=deployment.status,
        cf_deployment_id=deployment.cf_deployment_id,
        blob_container=deployment.blob_container,
        pages_crawled=deployment.pages_crawled,
        assets_count=deployment.assets_count,
        error_message=deployment.error_message,
        started_at=deployment.started_at,
        completed_at=deployment.completed_at,
        created_at=deployment.created_at,
    )


@router.get("", response_model=list[SiteResponse])
async def list_sites(user: User = Depends(get_current_user)):
    sites = await Site.find(Site.user_id == user.id).to_list()
    return [serialize_site(site) for site in sites]


@router.post("", response_model=SiteResponse, status_code=status.HTTP_201_CREATED)
async def create_site(request: CreateSiteRequest, user: User = Depends(get_current_user)):
    # 1. Validate Framer URL format
    framer_url_str = str(request.framer_url).rstrip("/")
    if not is_valid_framer_url(framer_url_str):
        raise HTTPException(
            status_code=400,
            detail="Framer URL must be a valid .framer.website, .framer.app, or .framer.media domain"
        )

    # 2. Enforce active plan limits
    user_sites = await Site.find(Site.user_id == user.id).to_list()
    limits = {"starter": 1, "pro": 3, "scale": 10}
    limit = limits.get(user.plan, 1)
    if len(user_sites) >= limit:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Site limit reached",
                "current_plan": user.plan,
                "limit": limit,
                "upgrade_url": "/sites/new",
            }
        )

    # 3. Create initial pending site document
    site = Site(
        user_id=user.id,
        framer_url=str(request.framer_url),
        custom_domain=request.custom_domain,
        status="pending",
    )
    await site.insert()

    # 4. Integrate Cloudflare project configurations
    cf_project_name = f"hostfast-{str(site.id)}"
    try:
        cf_project = await create_cf_project(cf_project_name)
        site.cf_project_name = cf_project_name
        site.cf_project_id = cf_project["id"]

        # Bind custom domain on Cloudflare if set
        if request.custom_domain:
            await add_custom_domain(cf_project_name, request.custom_domain)

        await site.save()
    except Exception as e:
        logger.error(f"Cloudflare project creation failed for site {site.id}: {e}")
        # Revert site record insertion
        await site.delete()
        raise HTTPException(
            status_code=500,
            detail=f"Cloudflare project creation failed: {str(e)}"
        )

    # 5. Enqueue initial site crawl Celery task
    try:
        crawl_and_deploy.delay(str(site.id), "initial")
    except Exception as e:
        logger.error(f"Celery initial crawl task enqueuing failed for site {site.id}: {e}")

    return serialize_site(site)


@router.get("/{site_id}", response_model=SiteDetailResponse)
async def get_site(site_id: str, user: User = Depends(get_current_user)):
    site = await Site.get(PydanticObjectId(site_id))
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    # Fetch last 5 deployments
    deployments = await Deployment.find(Deployment.site_id == site.id).sort("-created_at").limit(5).to_list()
    
    serialized_site = serialize_site(site)
    return SiteDetailResponse(
        **serialized_site.model_dump(),
        deployments=[serialize_deployment(depl) for depl in deployments]
    )


@router.get("/{site_id}/deployments", response_model=list[DeploymentResponse])
async def list_site_deployments(site_id: str, user: User = Depends(get_current_user)):
    site = await Site.get(PydanticObjectId(site_id))
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    # Fetch last 20 deployments
    deployments = await Deployment.find(Deployment.site_id == site.id).sort("-created_at").limit(20).to_list()
    return [serialize_deployment(depl) for depl in deployments]


@router.post("/{site_id}/deploy")
async def trigger_site_deploy(site_id: str, user: User = Depends(get_current_user)):
    site = await Site.get(PydanticObjectId(site_id))
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    settings = get_settings()
    
    # Create deployment document immediately
    deployment = Deployment(
        site_id=site.id,
        trigger="manual",
        status="pending",
        blob_container=settings.azure_storage_container,
        started_at=datetime.utcnow(),
    )
    await deployment.insert()

    # Trigger async task and pass deployment record id
    crawl_and_deploy.delay(str(site.id), "manual", str(deployment.id))

    return {"deployment_id": str(deployment.id)}


@router.post("/{site_id}/verify-dns")
async def verify_site_dns(site_id: str, user: User = Depends(get_current_user)):
    site = await Site.get(PydanticObjectId(site_id))
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    if not site.custom_domain:
        raise HTTPException(
            status_code=400,
            detail="No custom domain configured for this site"
        )

    try:
        res = _check_cname_single(site.custom_domain)
        if not res["verified"] and not site.custom_domain.startswith("www."):
            res = _check_cname_single(f"www.{site.custom_domain}")
            
        if res["verified"]:
            # Change status to live when DNS verifies successfully
            site.status = "live"
            await site.save()
            return {"verified": True, "status": "live"}
        else:
            return {"verified": False, "status": site.status}
    except Exception as e:
        logger.error(f"DNS verification failed for site {site.id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"DNS verification failed: {str(e)}"
        )


@router.get("/{site_id}/dns")
async def get_site_dns_info(site_id: str, user: User = Depends(get_current_user)):
    site = await Site.get(PydanticObjectId(site_id))
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    cname_value = "cname.hostfa.st"
    return {
        "type": "CNAME",
        "name": "@" if not site.custom_domain else site.custom_domain,
        "value": cname_value
    }


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(site_id: str, user: User = Depends(get_current_user)):
    site = await Site.get(PydanticObjectId(site_id))
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    # 1. Cascade delete deployments from MongoDB
    await Deployment.find(Deployment.site_id == site.id).delete()

    # 2. Delete site document from MongoDB
    await site.delete()

    # 3. Delete blob files from Azure
    from app.services.blob_storage import AzureBlobStorage
    settings = get_settings()
    sites_storage = AzureBlobStorage(
        connection_string=settings.azure_storage_connection_string,
        container_name=settings.azure_storage_sites_container
    )
    try:
        await sites_storage.delete_site_files(str(site_id))
    except Exception as e:
        logger.error(f"Azure site files deletion failed for site {site_id}: {e}")
    finally:
        await sites_storage.client.close()

    # 4. Invalidate cache
    from app.routers.proxy import domain_cache
    if site.custom_domain and site.custom_domain in domain_cache:
        del domain_cache[site.custom_domain]

    return None


class VerifyDNSRequest(BaseModel):
    domain: str


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def _check_cname_single(domain: str) -> dict:
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = ['8.8.8.8', '1.1.1.1']
        resolver.timeout = 5.0
        resolver.lifetime = 5.0
        answers = resolver.resolve(domain, 'CNAME')
        for rdata in answers:
            target = str(rdata.target).rstrip('.').lower()
            if target in ("cname.hostfa.st", "cname.hostfa.st.", "hostfa.st", "hostfa.st."):
                return {"verified": True}
        return {"verified": False, "reason": "CNAME points to wrong value"}
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return {"verified": False, "reason": "No CNAME record found"}
    except Exception:
        return {"verified": False, "reason": "Could not resolve domain"}



@router.post("/verify-dns")
async def verify_dns(request: Request, body: VerifyDNSRequest):
    # 1. Validate
    domain = body.domain.strip() if body.domain else ""
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain must be a non-empty string"
        )

    # 2. Rate limit: 10 requests per IP per minute using Redis
    ip = get_client_ip(request)
    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url)
    try:
        limit_key = f"dns_verify_ratelimit:{ip}"
        current = await redis_client.get(limit_key)
        if current and int(current) >= 10:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again in a minute."
            )

        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.incr(limit_key)
            if not current:
                pipe.expire(limit_key, 60)
            await pipe.execute()
    finally:
        await redis_client.close()

    # 3. DNS check
    res = _check_cname_single(domain)
    if res["verified"]:
        return res

    # Fallback to www.{domain} if the root domain fails
    if not domain.startswith("www."):
        fallback_res = _check_cname_single(f"www.{domain}")
        if fallback_res["verified"]:
            return fallback_res

    # Return original verification failure if fallback also failed
    return res


caddy_router = APIRouter()


@caddy_router.get("/check-domain")
async def check_domain(domain: str):
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain must be a non-empty string"
        )

    domain_stripped = domain.lower()
    if domain_stripped.startswith("www."):
        domain_stripped = domain_stripped[4:]

    site = await Site.find_one(
        Site.custom_domain == domain_stripped,
        Site.status == "active"
    )
    if not site:
        site = await Site.find_one(
            Site.custom_domain == domain_stripped,
            Site.status == "live"
        )

    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found or not active"
        )

    return {"status": "ok"}

