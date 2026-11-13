from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlsplit

import aiohttp

from config import RENDER_API_KEY, RENDER_WORKSPACE_ID, RENDER_REGION
from database import add_render_bot, update_render_deployment

logger = logging.getLogger("hostify.render_deployer")
_API = "https://api.render.com/v1"
_GITHUB_API = "https://api.github.com"
_TERMINAL_FAILURES = {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"}
_POLL_INTERVAL = 5
_DEPLOY_TIMEOUT = 30 * 60


class RenderDeploymentError(RuntimeError):
    pass


def is_render_configured() -> bool:
    return bool(RENDER_API_KEY and RENDER_WORKSPACE_ID)


def _repo_parts(repo_url: str) -> tuple[str, str]:
    parsed = urlsplit(repo_url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise RenderDeploymentError("Use a GitHub HTTPS repository URL.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise RenderDeploymentError("Repository URL must point directly to OWNER/REPOSITORY.")
    return parts[0], parts[1].removesuffix(".git")


def _service_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    value = re.sub(r"-+", "-", value)
    if not value:
        raise RenderDeploymentError("The deployment name cannot be converted to a valid Render service name.")
    return value[:50].rstrip("-")


async def _json_request(session, method: str, url: str, *, headers=None, payload=None):
    async with session.request(method, url, headers=headers, json=payload) as response:
        raw = await response.text()
        try:
            data = await response.json(content_type=None) if raw else {}
        except ValueError:
            data = {"message": raw[:1000]}
        if response.status >= 400:
            detail = data.get("message") or data.get("error") or raw or f"HTTP {response.status}"
            raise RenderDeploymentError(f"Render API returned HTTP {response.status}: {str(detail)[:1200]}")
        return data


async def _inspect_repository(repo_url: str, github_token: str, runtime_type: str) -> tuple[str, set[str]]:
    owner, repo = _repo_parts(repo_url)
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        metadata = await _json_request(session, "GET", f"{_GITHUB_API}/repos/{owner}/{repo}", headers=headers)
        branch = str(metadata.get("default_branch") or "main")
        contents = await _json_request(
            session, "GET", f"{_GITHUB_API}/repos/{owner}/{repo}/contents?ref={branch}", headers=headers
        )
    if not isinstance(contents, list):
        raise RenderDeploymentError("Could not inspect the repository root. Check GitHub App access.")
    files = {str(item.get("name")) for item in contents if isinstance(item, dict) and item.get("type") == "file"}
    if runtime_type in {"docker", "java"} and "Dockerfile" not in files:
        if runtime_type == "docker":
            raise RenderDeploymentError("Docker deployment requires a file named Dockerfile in the repository root. Add it to GitHub and try again.")
        raise RenderDeploymentError("Render does not provide a native Java runtime. Add a Java Dockerfile to the repository root, then deploy using Java.")
    if runtime_type == "python" and not ({"main.py", "bot.py", "app.py"} & files):
        raise RenderDeploymentError("Python repository needs main.py, bot.py, or app.py in its root directory.")
    return branch, files


async def _create_render_service(name: str, repo_url: str, runtime_type: str, env_vars: dict[str, str], branch: str, files: set[str]):
    api_headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    env = [{"key": key, "value": value} for key, value in env_vars.items()]
    details = {
        "runtime": "python" if runtime_type == "python" else "docker",
        "plan": "free",
        "region": RENDER_REGION,
    }
    if runtime_type == "python":
        entry = next(item for item in ("main.py", "bot.py", "app.py") if item in files)
        details["envSpecificDetails"] = {
            "buildCommand": "pip install -r requirements.txt" if "requirements.txt" in files else "pip install --disable-pip-version-check -U pip",
            "startCommand": f"python {entry}",
        }
    else:
        details["envSpecificDetails"] = {"dockerfilePath": "./Dockerfile"}

    payload = {
        "type": "web_service",
        "name": _service_name(name),
        "ownerId": RENDER_WORKSPACE_ID,
        "repo": repo_url.removesuffix(".git"),
        "branch": branch,
        "autoDeploy": "yes",
        "autoDeployTrigger": "commit",
        "envVars": env,
        "serviceDetails": details,
    }
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return await _json_request(session, "POST", f"{_API}/services", headers=api_headers, payload=payload)


async def deploy_to_render(
    name: str,
    repo_url: str,
    runtime_type: str,
    env_vars: dict[str, str],
    *,
    github_token: str,
    deployed_by: int,
) -> tuple[bool, str]:
    if not is_render_configured():
        return False, "Render API is not configured. Add RENDER_API_KEY and RENDER_WORKSPACE_ID to Hostify's Render environment."
    if runtime_type not in {"python", "java", "docker"}:
        return False, "Unsupported deployment type."

    try:
        branch, files = await _inspect_repository(repo_url, github_token, runtime_type)
        global _current_files
        _current_files = files
        created = await _create_render_service(name, repo_url, runtime_type, env_vars, branch)
        service = created.get("service") or {}
        deploy = created.get("deploy") or {}
        service_id = service.get("id")
        deploy_id = deploy.get("id")
        if not service_id or not deploy_id:
            raise RenderDeploymentError("Render accepted the request but did not return a service and deployment ID.")

        service_url = service.get("serviceDetails", {}).get("url") or service.get("url")
        await add_render_bot(
            name=name,
            repo_url=repo_url,
            env_vars=env_vars,
            deployed_by=deployed_by,
            runtime_type=runtime_type,
            service_id=service_id,
            deploy_id=deploy_id,
            service_url=service_url,
            status="deploying",
        )

        headers = {"Authorization": f"Bearer {RENDER_API_KEY}", "Accept": "application/json"}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            deadline = asyncio.get_running_loop().time() + _DEPLOY_TIMEOUT
            last_status = str(deploy.get("status") or "created")
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(_POLL_INTERVAL)
                state = await _json_request(
                    session, "GET", f"{_API}/services/{service_id}/deploys/{deploy_id}", headers=headers
                )
                last_status = str(state.get("status") or "unknown").lower()
                if last_status == "live":
                    await update_render_deployment(name, status="running", deploy_status=last_status)
                    dashboard_url = f"https://dashboard.render.com/web/{service_id}"
                    return True, f"{service_id}|{dashboard_url}"
                if last_status in _TERMINAL_FAILURES:
                    await update_render_deployment(name, status="failed", deploy_status=last_status)
                    return False, f"Render deployment status: {last_status}. Open the Render dashboard to inspect the build and runtime logs: https://dashboard.render.com/web/{service_id}"
        await update_render_deployment(name, status="deploying", deploy_status=last_status)
        return False, f"Render deployment did not finish within {_DEPLOY_TIMEOUT // 60} minutes. Check its progress and logs: https://dashboard.render.com/web/{service_id}"

    except (RenderDeploymentError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Render deployment failed for %s: %s", name, exc)
        return False, str(exc)[:1800]
    except Exception:
        logger.exception("Unexpected Render deployment failure for %s", name)
        return False, "Unexpected Render deployment error. Check Hostify logs and the Render service dashboard."


_current_files: set[str] = set()
