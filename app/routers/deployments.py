from fastapi import APIRouter, Depends, HTTPException, status
from beanie import PydanticObjectId
from beanie.operators import In

from app.routers.auth import get_current_user
from app.models.site import Site
from app.models.deployment import Deployment
from app.models.user import User
from app.schemas.deployment import DeploymentResponse
from app.tasks.crawl_tasks import crawl_and_deploy

router = APIRouter()


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


@router.get("", response_model=list[DeploymentResponse])
async def list_deployments(user: User = Depends(get_current_user)):
    sites = await Site.find(Site.user_id == user.id).to_list()
    site_ids = [site.id for site in sites]
    deployments = await Deployment.find(In(Deployment.site_id, site_ids)).to_list()
    return [serialize_deployment(deployment) for deployment in deployments]


@router.post("/{site_id}/trigger")
async def trigger_deployment(site_id: str, user: User = Depends(get_current_user)):
    site = await Site.get(PydanticObjectId(site_id))
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    task = crawl_and_deploy.delay(site_id, "manual")
    return {"task_id": task.id, "status": "queued"}
