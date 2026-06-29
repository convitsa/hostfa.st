import logging
import json
from pathlib import Path
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse

from cachetools import TTLCache
from app.services.local_storage import get_site_file
from app.models.site import Site

logger = logging.getLogger(__name__)

domain_cache: TTLCache = TTLCache(maxsize=1000, ttl=60)
meta_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)

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
  host = (
    request.headers.get("x-forwarded-host") or
    request.headers.get("host", "")
  ).lower()
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

  # Step 5: Fetch from local storage
  content = await get_site_file(site_id, blob_path)

  if content is None:
    # Step 1: Get _hostfast_meta.json from cache or local storage
    if f"meta:{site_id}" in meta_cache:
      meta = meta_cache[f"meta:{site_id}"]
    else:
      meta_bytes = await get_site_file(site_id, "_hostfast_meta.json")
      if meta_bytes:
        try:
          meta = json.loads(meta_bytes)
        except Exception:
          meta = {"custom_404_path": None}
      else:
        meta = {"custom_404_path": None}
      meta_cache[f"meta:{site_id}"] = meta

    # Step 2: Try to serve custom 404 page
    custom_404_path = meta.get("custom_404_path")
    if custom_404_path:
      content_404 = await get_site_file(site_id, custom_404_path)
      if content_404:
        return Response(
          content=content_404,
          media_type="text/html; charset=utf-8",
          status_code=404,
          headers={"Cache-Control": "public, max-age=300",
                   "X-Served-By": "hostfa.st"}
        )

    # Step 3: If no custom 404, return generic hostfa.st 404 HTML.
    return HTMLResponse(content=NOT_FOUND_HTML, status_code=404)

  # Step 6: Return response
  path_lower = blob_path.lower()
  is_404_path = any(p in path_lower for p in [
    "/404/", "404/", "not-found", "notfound", "page-not-found"
  ])
  if is_404_path and content:
    status_code = 404
  else:
    status_code = 200

  return Response(
    content=content,
    media_type=get_content_type(blob_path),
    status_code=status_code,
    headers={
      "Cache-Control": get_cache_control(blob_path),
      "X-Served-By": "hostfa.st",
    }
  )
