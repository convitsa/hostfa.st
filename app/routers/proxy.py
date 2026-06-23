import logging
from pathlib import Path
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse

from cachetools import TTLCache
from app.config import Settings
from app.services.blob_storage import AzureBlobStorage
from app.models.site import Site

logger = logging.getLogger(__name__)

domain_cache: TTLCache = TTLCache(maxsize=1000, ttl=60)

NOT_FOUND_HTML = """<!DOCTYPE html>
<html>
<head><title>404 - Site Not Found</title>
<style>
  body { background: #0A0A0A; color: #888; font-family: sans-serif;
         display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; flex-direction: column; gap: 8px; }
  h1 { color: #EDEDED; font-size: 24px; margin: 0; }
  p { font-size: 14px; margin: 0; }
  small { font-size: 11px; color: #444; margin-top: 24px; }
</style>
</head>
<body>
  <h1>404 — Site not found</h1>
  <p>If you just set this up, please wait a few minutes and try again.</p>
  <small>Powered by hostfa.st</small>
</body>
</html>"""

CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css",
  ".js": "application/javascript",
  ".mjs": "application/javascript",
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".avif": "image/avif",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".ico": "image/x-icon",
  ".gif": "image/gif",
}

def get_content_type(path: str) -> str:
  ext = Path(path).suffix.lower()
  return CONTENT_TYPES.get(ext, "application/octet-stream")

def get_cache_control(path: str) -> str:
  ext = Path(path).suffix.lower()
  if ext == ".html" or not ext:
    return "public, max-age=300"
  return "public, max-age=31536000, immutable"

router = APIRouter()

@router.api_route(
  "/{path:path}",
  methods=["GET", "HEAD"],
  include_in_schema=False
)
async def proxy_site(request: Request, path: str = ""):
  
  # Step 1: Extract host
  host = request.headers.get("host", "").lower()
  host = host.split(":")[0]
  if host.startswith("www."):
    host = host[4:]

  # Step 2: Skip internal domains
  if host == "hostfa.st" or host.endswith(".hostfa.st"):
    raise HTTPException(status_code=404)

  # Step 3: Look up site
  if host in domain_cache:
    site_id = domain_cache[host]
  else:
    site = await Site.find_one(
      Site.custom_domain == host,
      Site.status == "live"
    )
    if site is None:
      return HTMLResponse(content=NOT_FOUND_HTML, status_code=404)
    site_id = str(site.id)
    domain_cache[host] = site_id

  # Step 4: Resolve blob path
  path = path.strip("/")
  last_segment = path.split("/")[-1] if path else ""
  if not path or path == "index.html":
    blob_path = "index.html"
  elif "." not in last_segment:
    blob_path = (path + "/index.html").lstrip("/")
  else:
    blob_path = path

  # Step 5: Fetch from blob
  settings = Settings()
  storage = AzureBlobStorage(
    connection_string=settings.azure_storage_connection_string,
    container_name=settings.azure_storage_sites_container
  )
  content = await storage.get_site_file(site_id, blob_path)

  if content is None and blob_path != "index.html":
    content = await storage.get_site_file(site_id, "index.html")

  if content is None:
    return HTMLResponse(content=NOT_FOUND_HTML, status_code=404)

  # Step 6: Return response
  return Response(
    content=content,
    media_type=get_content_type(blob_path),
    headers={
      "Cache-Control": get_cache_control(blob_path),
      "X-Served-By": "hostfa.st",
    }
  )
