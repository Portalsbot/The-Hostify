from __future__ import annotations

import base64
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import jwt

from config import (
    GITHUB_APP_ID,
    GITHUB_APP_PRIVATE_KEY,
    GITHUB_APP_PRIVATE_KEY_B64,
)

logger = logging.getLogger("hostify.github")

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
_TIMEOUT = aiohttp.ClientTimeout(total=25)


class GitHubAppError(RuntimeError):
    """A safe, user-facing GitHub App integration error."""


class GitHubAppNotConfigured(GitHubAppError):
    """Raised when the private key or App ID has not been configured."""


def is_configured() -> bool:
    return bool(GITHUB_APP_ID and (GITHUB_APP_PRIVATE_KEY_B64 or GITHUB_APP_PRIVATE_KEY))


def _load_private_key() -> str:
    if GITHUB_APP_PRIVATE_KEY_B64:
        try:
            raw = base64.b64decode(GITHUB_APP_PRIVATE_KEY_B64, validate=True)
            key = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubAppNotConfigured(
                "GITHUB_APP_PRIVATE_KEY_B64 is not valid Base64-encoded PEM data."
            ) from exc
    elif GITHUB_APP_PRIVATE_KEY:
        key = GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")
    else:
        raise GitHubAppNotConfigured(
            "Add GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_B64 to the Hostify service environment."
        )

    if "-----BEGIN" not in key or "PRIVATE KEY-----" not in key:
        raise GitHubAppNotConfigured(
            "The configured GitHub private key is not a valid PEM private key."
        )
    return key


def _app_jwt() -> str:
    if not GITHUB_APP_ID:
        raise GitHubAppNotConfigured("GITHUB_APP_ID is missing from the Hostify environment.")

    now = int(time.time())
    claims = {
        "iat": now - 60,
        "exp": now + 8 * 60,
        "iss": str(GITHUB_APP_ID),
    }
    try:
        value = jwt.encode(claims, _load_private_key(), algorithm="RS256")
    except GitHubAppError:
        raise
    except Exception as exc:
        logger.exception("Could not sign a GitHub App authentication token")
        raise GitHubAppNotConfigured(
            "GitHub App private-key authentication failed. Check the App ID and PEM key."
        ) from exc
    return value.decode("utf-8") if isinstance(value, bytes) else value


async def _request(
    method: str,
    path: str,
    token: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "The-Hostify-GitHub-App",
    }
    url = f"{API_ROOT}{path}"

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.request(method, url, headers=headers, json=payload) as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = {}

                if response.status < 200 or response.status >= 300:
                    message = body.get("message") if isinstance(body, dict) else None
                    safe_message = str(message or "GitHub API request failed")[:300]
                    raise GitHubAppError(
                        f"GitHub returned HTTP {response.status}: {safe_message}"
                    )
                if not isinstance(body, (dict, list)):
                    raise GitHubAppError("GitHub returned an unexpected response.")
                return body
    except GitHubAppError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.warning("GitHub API request failed: %s %s (%s)", method, path, type(exc).__name__)
        raise GitHubAppError("Could not reach GitHub. Check the connection and try again.") from exc


async def list_installations() -> list[dict[str, Any]]:
    """Return installations of this GitHub App without exposing credentials."""
    app_token = _app_jwt()
    installations: list[dict[str, Any]] = []

    for page in range(1, 21):
        result = await _request(
            "GET",
            f"/app/installations?per_page=100&page={page}",
            app_token,
        )
        if not isinstance(result, list):
            raise GitHubAppError("GitHub returned an invalid installations response.")
        installations.extend(item for item in result if isinstance(item, dict))
        if len(result) < 100:
            break

    return installations


async def _create_installation_token(installation_id: int) -> str:
    app_token = _app_jwt()
    result = await _request(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        app_token,
        payload={},
    )
    if not isinstance(result, dict) or not result.get("token"):
        raise GitHubAppError("GitHub did not return an installation access token.")
    return str(result["token"])


async def _list_installation_repositories(token: str) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []

    for page in range(1, 51):
        result = await _request(
            "GET",
            f"/installation/repositories?per_page=100&page={page}",
            token,
        )
        if not isinstance(result, dict):
            raise GitHubAppError("GitHub returned an invalid repository response.")
        items = result.get("repositories")
        if not isinstance(items, list):
            raise GitHubAppError("GitHub did not return an installation repository list.")
        repositories.extend(item for item in items if isinstance(item, dict))
        if len(items) < 100:
            break

    return repositories


async def get_accessible_repositories() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return safe installation summaries and all repositories accessible to the app."""
    installations = await list_installations()
    summaries: list[dict[str, Any]] = []
    all_repositories: list[dict[str, Any]] = []

    for installation in installations:
        installation_id = installation.get("id")
        if not isinstance(installation_id, int):
            continue
        account = installation.get("account") or {}
        account_name = str(account.get("login") or account.get("name") or "unknown")
        repositories = await _list_installation_repositories(
            await _create_installation_token(installation_id)
        )
        summaries.append({
            "id": installation_id,
            "account": account_name,
            "target_type": str(installation.get("target_type") or "account"),
            "repository_count": len(repositories),
        })
        for repository in repositories:
            all_repositories.append({
                "full_name": str(repository.get("full_name") or ""),
                "private": bool(repository.get("private")),
                "default_branch": str(repository.get("default_branch") or "unknown"),
                "html_url": str(repository.get("html_url") or ""),
                "installation_id": installation_id,
            })

    return summaries, all_repositories


async def get_installation_token_for_repository(repo_url: str) -> str | None:
    """Return a short-lived token only when this app installation can access the repo.

    Public repositories not selected in the app installation remain deployable
    without an installation token. Private repositories must be selected in the
    GitHub App installation.
    """
    parsed = urlsplit(repo_url.strip())
    if parsed.hostname != "github.com":
        raise GitHubAppError("Only github.com repository URLs are supported.")

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise GitHubAppError("Send a URL pointing directly to one GitHub repository.")

    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    wanted = f"{owner}/{repository}".casefold()

    if not is_configured():
        return None

    for installation in await list_installations():
        installation_id = installation.get("id")
        if not isinstance(installation_id, int):
            continue
        token = await _create_installation_token(installation_id)
        repositories = await _list_installation_repositories(token)
        for item in repositories:
            if str(item.get("full_name") or "").casefold() == wanted:
                return token

    # No token is returned for an unselected repository. Git can still clone
    # a public repository anonymously; a private repository will fail safely.
    return None
