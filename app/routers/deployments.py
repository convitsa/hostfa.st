from fastapi import APIRouter, Depends, HTTPException, status
from app.routers.auth import get_current_user
from app.models.user import User
from app.models.site import Site
from app.services.local_storage import get_site_history, rollback_deployment
from beanie import PydanticObjectId

router = APIRouter()

@router.get("/{site_id}/history")
async def list_site_deployments_history(
    site_id: str,
    user: User = Depends(get_current_user)
):
    try:
        site = await Site.get(PydanticObjectId(site_id))
    except Exception:
        site = None
        
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    history = await get_site_history(site_id)
    return history

@router.post("/{site_id}/rollback/{deployment_id}")
async def trigger_rollback(
    site_id: str,
    deployment_id: str,
    user: User = Depends(get_current_user)
):
    try:
        site = await Site.get(PydanticObjectId(site_id))
    except Exception:
        site = None
        
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    success = await rollback_deployment(site_id, deployment_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rollback failed. Deployment not found.")

    return {"success": True, "deployment_id": deployment_id}
