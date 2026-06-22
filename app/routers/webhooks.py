from fastapi import APIRouter

router = APIRouter()

@router.post("/cloudflare")
async def cloudflare_webhook(payload: dict):
    return {"received": True, "payload": payload}
