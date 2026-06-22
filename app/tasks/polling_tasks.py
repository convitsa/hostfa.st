import asyncio
from datetime import datetime
import aiohttp

from app.tasks.celery_app import celery
from app.config import get_settings
from app.models.site import Site
from app.models.user import User
from app.database import init_db
from beanie.operators import In


async def _poll_all_sites_async() -> None:
    settings = get_settings()
    await init_db(settings)
    
    sites = await Site.find(Site.status == "live").to_list()
    if not sites:
        return

    user_ids = list({site.user_id for site in sites})
    users = await User.find(In(User.id, user_ids)).to_list()
    user_map = {u.id: u for u in users}

    plan_intervals = {"starter": 600, "pro": 300, "scale": 60}
    now = datetime.utcnow()

    sites_to_poll = []
    for site in sites:
        user = user_map.get(site.user_id)
        if not user:
            continue
            
        interval = plan_intervals.get(user.plan, 600)
        
        if not site.last_polled_at or (now - site.last_polled_at).total_seconds() >= interval:
            sites_to_poll.append((site, user.plan))

    semaphore = asyncio.Semaphore(50)
    
    async with aiohttp.ClientSession() as session:
        async def poll_site(site: Site, plan: str):
            async with semaphore:
                try:
                    async with session.head(str(site.framer_url), allow_redirects=True, timeout=10) as resp:
                        etag = resp.headers.get("ETag")
                        last_modified = resp.headers.get("Last-Modified")
                        
                        site.last_polled_at = datetime.utcnow()
                        
                        changed = False
                        if etag and etag != site.last_etag:
                            changed = True
                        elif last_modified and last_modified != site.last_modified:
                            changed = True
                            
                        site.last_etag = etag
                        site.last_modified = last_modified
                        await site.save()
                        
                        if changed:
                            from app.tasks.crawl_tasks import crawl_and_deploy
                            queue_name = plan if plan in ["scale", "pro", "starter"] else "starter"
                            crawl_and_deploy.apply_async(args=[str(site.id), "auto_poll"], queue=queue_name)
                except Exception:
                    pass

        await asyncio.gather(*(poll_site(site, plan) for site, plan in sites_to_poll))


@celery.task(name="app.tasks.polling_tasks.poll_all_sites")
def poll_all_sites() -> None:
    asyncio.run(_poll_all_sites_async())
