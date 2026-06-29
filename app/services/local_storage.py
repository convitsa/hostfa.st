import os
import json
import shutil
import logging
from typing import Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

async def save_deployment(
    site_id: str,
    deployment_id: str,
    source_dir: str,
    metadata: dict
) -> int:
    settings = get_settings()
    site_dir = os.path.join(settings.local_sites_dir, site_id)
    deployment_dir = os.path.join(site_dir, "deployments", deployment_id)
    files_dir = os.path.join(deployment_dir, "files")

    # 1. Create directory structure
    os.makedirs(files_dir, exist_ok=True)

    # 2. Copy all files from source_dir to files/
    shutil.copytree(source_dir, files_dir, dirs_exist_ok=True)

    # 3. Write metadata to meta.json
    meta_path = os.path.join(deployment_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # 4. Update current symlink
    symlink_path = os.path.join(site_dir, "current")
    target_path = os.path.join("deployments", deployment_id, "files")

    if os.path.islink(symlink_path) or os.path.exists(symlink_path):
        try:
            if os.path.islink(symlink_path):
                os.unlink(symlink_path)
            else:
                shutil.rmtree(symlink_path)
        except Exception as e:
            logger.error(f"Failed to remove old symlink {symlink_path}: {e}")

    try:
        os.symlink(target_path, symlink_path)
    except Exception as e:
        logger.error(f"Failed to create new symlink from {symlink_path} to {target_path}: {e}")

    # 5. Update history.json (prepend new, keep last 10)
    history_path = os.path.join(site_dir, "history.json")
    history = []
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    # Prepend new deployment entry
    history.insert(0, {
        "deployment_id": deployment_id,
        "created_at": metadata.get("created_at"),
        "status": "live"
    })

    # Keep last 10
    history = history[:10]

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    # 6. Delete old deployment directories beyond 10
    deployments_base_dir = os.path.join(site_dir, "deployments")
    active_ids = {h["deployment_id"] for h in history}

    if os.path.exists(deployments_base_dir):
        try:
            for dep_id in os.listdir(deployments_base_dir):
                if dep_id not in active_ids:
                    dep_path = os.path.join(deployments_base_dir, dep_id)
                    if os.path.isdir(dep_path):
                        shutil.rmtree(dep_path)
        except Exception as e:
            logger.error(f"Failed cleaning old deployments in {deployments_base_dir}: {e}")

    # 7. Return count of copied files
    copied_files = 0
    for root, _, files in os.walk(files_dir):
        copied_files += len(files)

    return copied_files

async def get_site_file(site_id: str, file_path: str) -> Optional[bytes]:
    settings = get_settings()
    clean_path = file_path.lstrip("/")
    if ".." in clean_path:
        return None

    full_path = os.path.join(settings.local_sites_dir, site_id, "current", clean_path)

    try:
        if os.path.isfile(full_path):
            with open(full_path, "rb") as f:
                return f.read()
    except Exception:
        pass
    return None

async def delete_site(site_id: str):
    settings = get_settings()
    site_dir = os.path.join(settings.local_sites_dir, site_id)
    try:
        if os.path.exists(site_dir):
            shutil.rmtree(site_dir)
    except Exception as e:
        logger.error(f"Failed to delete site {site_id}: {e}")

async def get_site_history(site_id: str) -> list:
    settings = get_settings()
    history_path = os.path.join(settings.local_sites_dir, site_id, "history.json")
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

async def rollback_deployment(site_id: str, deployment_id: str) -> bool:
    settings = get_settings()
    site_dir = os.path.join(settings.local_sites_dir, site_id)
    deployment_files_dir = os.path.join(site_dir, "deployments", deployment_id, "files")

    if not os.path.exists(deployment_files_dir):
        return False

    symlink_path = os.path.join(site_dir, "current")
    target_path = os.path.join("deployments", deployment_id, "files")

    if os.path.islink(symlink_path) or os.path.exists(symlink_path):
        try:
            if os.path.islink(symlink_path):
                os.unlink(symlink_path)
            else:
                shutil.rmtree(symlink_path)
        except Exception as e:
            logger.error(f"Failed to remove old symlink {symlink_path} during rollback: {e}")

    try:
        os.symlink(target_path, symlink_path)
    except Exception as e:
        logger.error(f"Failed to symlink during rollback: {e}")
        return False

    # Update history.json
    history_path = os.path.join(site_dir, "history.json")
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)

            for entry in history:
                if entry.get("deployment_id") == deployment_id:
                    entry["status"] = "live"
                else:
                    entry["status"] = "inactive"

            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to update history.json during rollback: {e}")
            return False

    return True

async def get_current_deployment_id(site_id: str) -> Optional[str]:
    settings = get_settings()
    symlink_path = os.path.join(settings.local_sites_dir, site_id, "current")
    if os.path.islink(symlink_path):
        try:
            target = os.readlink(symlink_path)
            parts = target.replace("\\", "/").split("/")
            if len(parts) >= 3 and parts[0] == "deployments" and parts[2] == "files":
                return parts[1]
        except Exception:
            pass
    return None
