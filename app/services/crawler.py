import os
import logging
import asyncio
from typing import Optional, Set
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import aiohttp
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from playwright.async_api import async_playwright

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)


def is_valid_framer_url(url: str) -> bool:
    if not url:
        return False
    clean = url.strip().lower()
    if not clean.startswith("http://") and not clean.startswith("https://"):
        clean = "https://" + clean
    try:
        parsed = urlparse(clean)
        host = parsed.netloc
        valid_domains = (".framer.website", ".framer.app", ".framer.media")
        return any(host == domain.lstrip(".") or host.endswith(domain) for domain in valid_domains)
    except Exception:
        return False


@dataclass
class CrawlResult:
    pages_crawled: int
    assets_count: int
    output_dir: str
    success: bool
    error: Optional[str]
    partial: bool = False
    zip_path: str = ""
    total_sitemap_pages: Optional[int] = None


async def crawl_framer_site(
    framer_url: str,
    output_dir: str,
    export_mode: bool = False,
    mode: str = 'full',
    progress_callback = None
) -> CrawlResult:
    parsed_base_url = urlparse(framer_url)
    base_domain = parsed_base_url.netloc
    scheme = parsed_base_url.scheme or "https"
    base_url = f"{scheme}://{base_domain}"
    sitemap_url = f"{base_url}/sitemap.xml"

    sitemap_found = False
    sitemap_urls = []
    total_sitemap_pages = None

    if mode == 'fast':
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(sitemap_url, timeout=10) as resp:
                    if resp.status == 200:
                        xml_content = await resp.text()
                        sitemap_soup = BeautifulSoup(xml_content, "html.parser")
                        loc_tags = sitemap_soup.find_all("loc")
                        for tag in loc_tags:
                            loc_url = tag.get_text().strip()
                            parsed_loc = urlparse(loc_url)
                            if parsed_loc.hostname == parsed_base_url.hostname:
                                sitemap_urls.append(loc_url.split("#")[0])
                        # Preserve order, deduplicate
                        seen = set()
                        deduped_urls = []
                        for u in sitemap_urls:
                            if u not in seen:
                                seen.add(u)
                                deduped_urls.append(u)
                        total_sitemap_pages = len(deduped_urls)
                        sitemap_urls = deduped_urls[:5]
                        if sitemap_urls:
                            sitemap_found = True
        except Exception as e:
            logger.error(f"Failed to fetch or parse sitemap.xml: {e}")

    if sitemap_found:
        pages_to_crawl = set(sitemap_urls)
        url_depths = {u: 0 for u in sitemap_urls}
        logger.info(f"Discovered sitemap.xml, crawling 5 URLs: {sitemap_urls}")
    else:
        pages_to_crawl = {framer_url}
        url_depths = {framer_url: 0}
        if mode == 'fast':
            logger.info("sitemap.xml not found, falling back to homepage link extraction")

    crawled_pages: Set[str] = set()
    assets_to_download: Set[str] = set()

    # Track original URLs of files (HTML pages and assets) to resolve relative links in second pass
    original_urls = {}

    pages_crawled = 0
    assets_count = 0
    partial = False

    max_pages = 5 if mode == 'fast' else 200
    sem_limit = 5 if mode == 'fast' else 3

    try:
        os.makedirs(output_dir, exist_ok=True)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()

            # Set up context level response interceptor to automatically capture dynamic imports
            def handle_response(response):
                try:
                    url = response.url
                    if not url.startswith(("http://", "https://")):
                        return
                    # We only care about assets, not page HTMLs
                    if url not in crawled_pages and url not in pages_to_crawl:
                        assets_to_download.add(url)
                        
                        # Populate original_urls mapping
                        parsed_asset = urlparse(url)
                        asset_filename = os.path.basename(parsed_asset.path)
                        if not asset_filename:
                            asset_filename = "asset_" + str(abs(hash(url)))
                        original_urls[f"assets/{asset_filename}"] = url
                except Exception as e:
                    logger.error(f"Error in response interceptor: {e}")

            context.on("response", handle_response)

            semaphore = asyncio.Semaphore(sem_limit)

            async def process_page(url: str):
                nonlocal pages_crawled
                async with semaphore:
                    if len(crawled_pages) >= max_pages:
                        return

                    crawled_pages.add(url)

                    page = await context.new_page()
                    page_timeout = 15000  # Page render timeout
                    try:
                        try:
                            await page.goto(url, wait_until="networkidle", timeout=page_timeout)
                        except Exception:
                            try:
                                await page.goto(url, wait_until="load", timeout=10000)
                            except Exception:
                                pass
                        
                        # Scroll page to bottom in 3 steps with 500ms delay each to trigger lazy-loaded assets
                        try:
                            for step_i in range(1, 4):
                                await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {step_i} / 3);")
                                await asyncio.sleep(0.5)
                            # Wait 2000ms for dynamic imports to load
                            await asyncio.sleep(2.0)
                        except Exception as scroll_err:
                            logger.error(f"Failed to scroll page {url}: {scroll_err}")

                        html_content = await page.content()
                    except Exception as e:
                        logger.error(f"Failed to crawl {url}: {e}")
                        await page.close()
                        return

                    await page.close()

                    soup = BeautifulSoup(html_content, "html.parser")

                    # Remove Framer's injected badge/editorbar elements
                    removed_badges = 0
                    for badge_id in ["__framer-editorbar-container", "__framer-badge-container"]:
                        for elem in soup.find_all(id=badge_id):
                            elem.decompose()
                            removed_badges += 1
                    logger.info(f"Removed {removed_badges} Framer injected badge elements from page {url}")

                    current_depth = url_depths.get(url, 0)

                    # Extract internal links
                    # In fast mode, only extract links if depth < 1 and sitemap was not found
                    if mode != 'fast' or (current_depth < 1 and not sitemap_found):
                        for a_tag in soup.find_all("a", attrs={"href": True}):
                            href = a_tag.get("href")
                            if isinstance(href, list):
                                href = href[0] if href else ""
                            if not isinstance(href, str) or not href:
                                continue
                                
                            full_url = urljoin(url, href)
                            parsed_url = urlparse(full_url)
                            if parsed_url.hostname == parsed_base_url.hostname:
                                normalized_url = full_url.split("#")[0]
                                if mode == 'fast':
                                    skip_patterns = [
                                        "google-analytics", "googletagmanager", "gtag", "hotjar", 
                                        "intercom", "crisp", "hubspot", "fbq", "clarity", "segment", 
                                        "mixpanel", "amplitude", "fullstory", "logrocket", 
                                        "facebook.net", "doubleclick"
                                    ]
                                    if any(pattern in normalized_url for pattern in skip_patterns):
                                        continue
                                
                                if normalized_url not in crawled_pages:
                                    pages_to_crawl.add(normalized_url)
                                    if normalized_url not in url_depths:
                                        url_depths[normalized_url] = current_depth + 1

                    # Extract external assets
                    for tag, attr in [("link", "href"), ("script", "src"), ("img", "src"), ("source", "src")]:
                        for node in soup.find_all(tag, attrs={attr: True}):
                            asset_url = node.get(attr)
                            if isinstance(asset_url, list):
                                asset_url = asset_url[0] if asset_url else ""
                            if not isinstance(asset_url, str) or not asset_url:
                                continue
                                
                            if asset_url.startswith("data:"):
                                continue

                            full_asset_url = urljoin(url, asset_url)
                            
                            # In fast mode, check for tracking script patterns
                            if mode == 'fast':
                                skip_patterns = [
                                    "google-analytics", "googletagmanager", "gtag", "hotjar", 
                                    "intercom", "crisp", "hubspot", "fbq", "clarity", "segment", 
                                    "mixpanel", "amplitude", "fullstory", "logrocket", 
                                    "facebook.net", "doubleclick"
                                ]
                                if any(pattern in full_asset_url for pattern in skip_patterns):
                                    continue

                            assets_to_download.add(full_asset_url)

                            # Rewrite absolute URLs to relative
                            parsed_asset = urlparse(full_asset_url)
                            asset_filename = os.path.basename(parsed_asset.path)
                            if not asset_filename:
                                asset_filename = "asset_" + str(abs(hash(full_asset_url)))

                            original_urls[f"assets/{asset_filename}"] = full_asset_url

                            parsed_url = urlparse(url)
                            page_path = parsed_url.path.strip("/")
                            if not page_path:
                                depth = 0
                            elif page_path.endswith(".html"):
                                depth = len(page_path.split("/")) - 1
                            else:
                                depth = len(page_path.split("/"))

                            rel_prefix = "../" * depth if (depth > 0 and export_mode) else ("" if export_mode else "/")
                            node[attr] = f"{rel_prefix}assets/{asset_filename}"

                    # Save HTML and map original url
                    parsed_url = urlparse(url)
                    path = parsed_url.path.strip("/")

                    if not path:
                        file_path = os.path.join(output_dir, "index.html")
                        rel_file_path = "index.html"
                    elif path.endswith(".html"):
                        file_path = os.path.join(output_dir, path)
                        rel_file_path = path
                    else:
                        file_path = os.path.join(output_dir, path, "index.html")
                        rel_file_path = os.path.join(path, "index.html")

                    original_urls[rel_file_path] = url

                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(str(soup))

                    pages_crawled += 1
                    
                    if progress_callback:
                        try:
                            progress_callback(
                                status='crawling',
                                pages_done=pages_crawled,
                                current_url=url,
                                assets_count=len(assets_to_download)
                            )
                        except Exception as e:
                            logger.error(f"Error in progress_callback: {e}")

            # Main crawling loop
            start_time = asyncio.get_event_loop().time()
            while pages_to_crawl and len(crawled_pages) < max_pages:
                if mode == 'fast':
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= 60.0:
                        partial = True
                        break
                
                batch = list(pages_to_crawl - crawled_pages)[:20]
                if not batch:
                    break

                if mode == 'fast':
                    remaining = 60.0 - elapsed
                    if remaining <= 0:
                        partial = True
                        break
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*(process_page(u) for u in batch)),
                            timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        partial = True
                        break
                else:
                    await asyncio.gather(*(process_page(u) for u in batch))

            await browser.close()

        # Download assets
        assets_dir = os.path.join(output_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)

        async with aiohttp.ClientSession() as session:
            async def download_asset(asset_url: str):
                nonlocal assets_count
                parsed_asset = urlparse(asset_url)
                
                # Skip mp4/webm in fast mode
                if mode == 'fast':
                    path_lower = parsed_asset.path.lower()
                    if path_lower.endswith(".mp4") or path_lower.endswith(".webm"):
                        return

                asset_filename = os.path.basename(parsed_asset.path)
                if not asset_filename:
                    asset_filename = "asset_" + str(abs(hash(asset_url)))

                asset_path = os.path.join(assets_dir, asset_filename)

                if os.path.exists(asset_path):
                    return

                try:
                    async with session.get(asset_url) as resp:
                        if resp.status == 200:
                            content = await resp.read()
                            with open(asset_path, "wb") as f:
                                f.write(content)
                            assets_count += 1
                except Exception as e:
                    logger.error(f"Failed to download asset {asset_url}: {e}")

            # Download in batches to limit concurrent connections
            asset_batch_size = 10
            assets_list = list(assets_to_download)
            for i in range(0, len(assets_list), asset_batch_size):
                batch = assets_list[i : i + asset_batch_size]
                await asyncio.gather(*(download_asset(u) for u in batch))

        # SECOND PASS: Scan and download dynamic imports in HTML and JS/MJS files
        logger.info("Starting second pass for dynamic imports...")
        import re
        import_regex = re.compile(r'import\s*\(\s*["\']([^"\']+)["\']\s*\)')
        from_regex = re.compile(r'from\s*["\']([^"\']+)["\']')

        all_files_to_scan = []
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.endswith((".html", ".js", ".mjs")):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, output_dir)
                    all_files_to_scan.append(rel_path)

        async with aiohttp.ClientSession() as session:
            file_idx = 0
            while file_idx < len(all_files_to_scan):
                rel_file_path = all_files_to_scan[file_idx]
                file_idx += 1

                full_file_path = os.path.join(output_dir, rel_file_path)
                original_file_url = original_urls.get(rel_file_path)
                if not original_file_url:
                    continue

                try:
                    with open(full_file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except Exception as e:
                    logger.error(f"Failed to read file {rel_file_path} for scan: {e}")
                    continue

                found_urls = []
                for match in import_regex.finditer(content):
                    found_urls.append(match.group(1))
                for match in from_regex.finditer(content):
                    found_urls.append(match.group(1))

                modified = False
                for raw_url in found_urls:
                    is_absolute = raw_url.startswith(("http://", "https://", "//"))
                    
                    target_absolute_url = ""
                    if is_absolute:
                        target_absolute_url = raw_url if not raw_url.startswith("//") else "https:" + raw_url
                    else:
                        # Resolve relative path if original file was from Framer CDN
                        parsed_original = urlparse(original_file_url)
                        if "framerusercontent.com" in parsed_original.netloc or "framer" in parsed_original.netloc:
                            target_absolute_url = urljoin(original_file_url, raw_url)

                    if target_absolute_url:
                        parsed_target = urlparse(target_absolute_url)
                        asset_filename = os.path.basename(parsed_target.path)
                        if not asset_filename or "." not in asset_filename:
                            asset_filename = "chunk_" + str(abs(hash(target_absolute_url))) + ".mjs"
                        
                        asset_path = os.path.join(output_dir, "assets", asset_filename)
                        
                        # Download if not already downloaded
                        download_success = False
                        if os.path.exists(asset_path):
                            download_success = True
                        else:
                            try:
                                async with session.get(target_absolute_url) as resp:
                                    if resp.status == 200:
                                        file_content = await resp.read()
                                        os.makedirs(os.path.dirname(asset_path), exist_ok=True)
                                        with open(asset_path, "wb") as out_f:
                                            out_f.write(file_content)
                                        download_success = True
                                        assets_count += 1
                                        
                                        # Record new asset in original_urls and scan queue
                                        new_rel_path = f"assets/{asset_filename}"
                                        original_urls[new_rel_path] = target_absolute_url
                                        all_files_to_scan.append(new_rel_path)
                            except Exception as e:
                                logger.error(f"Failed to download dynamic import {target_absolute_url}: {e}")

                        if download_success:
                            # Rewrite URL in code
                            parts = rel_file_path.split("/")
                            depth = len(parts) - 1
                            rel_prefix = "../" * depth if (depth > 0 and export_mode) else ("" if export_mode else "/")
                            rewritten_url = f"{rel_prefix}assets/{asset_filename}"
                            
                            content = content.replace(f'"{raw_url}"', f'"{rewritten_url}"')
                            content = content.replace(f"'{raw_url}'", f"'{rewritten_url}'")
                            modified = True

                if modified:
                    try:
                        with open(full_file_path, "w", encoding="utf-8") as out_f:
                            out_f.write(content)
                    except Exception as e:
                        logger.error(f"Failed to write modified file {rel_file_path}: {e}")

        # Create SPA routing files (Part 5)
        try:
            redirects_path = os.path.join(output_dir, "_redirects")
            with open(redirects_path, "w", encoding="utf-8") as f:
                f.write("/* /index.html 200\n")
                
            headers_path = os.path.join(output_dir, "_headers")
            with open(headers_path, "w", encoding="utf-8") as f:
                f.write("/*\n  X-Frame-Options: DENY\n  X-Content-Type-Options: nosniff\n  Cache-Control: public, max-age=3600\n")
        except Exception as e:
            logger.error(f"Failed to create SPA redirect/header files: {e}")

        # ZIP packaging for fast mode (Part 2)
        zip_path = ""
        if mode == 'fast':
            zip_path = output_dir.rstrip("/") + ".zip"
            import zipfile
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(output_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, output_dir)
                        zipf.write(file_path, arcname)

        if progress_callback:
            try:
                progress_callback(
                    status='done',
                    pages_done=pages_crawled,
                    current_url=framer_url,
                    assets_count=assets_count
                )
            except Exception as e:
                logger.error(f"Error in final progress_callback: {e}")

        return CrawlResult(
            pages_crawled=pages_crawled,
            assets_count=assets_count,
            output_dir=output_dir,
            success=True,
            error=None,
            partial=partial,
            zip_path=zip_path,
            total_sitemap_pages=total_sitemap_pages
        )

    except Exception as e:
        logger.error(f"Crawl failed: {e}", exc_info=True)
        return CrawlResult(
            pages_crawled=pages_crawled,
            assets_count=assets_count,
            output_dir=output_dir,
            success=False,
            error=str(e),
            partial=True,
            zip_path="",
            total_sitemap_pages=total_sitemap_pages
        )
