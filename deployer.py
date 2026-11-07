from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import psutil

from config import ALLOW_UNSANDBOXED_DEPLOYMENTS, BASE_DIR
from database import add_bot, get_bot, set_bot_username, update_status

logger = logging.getLogger("hostify.deployer")

ENTRY_CANDIDATES = ("main.py", "bot.py", "app.py")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")
ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")

# These names can alter the Python runtime, dynamic linker, or virtualenv and
# must not be controlled through a customer bot's environment form.
RESERVED_ENV_KEYS = {
    "PATH", "HOME", "PWD", "OLDPWD", "VIRTUAL_ENV", "PYTHONHOME",
    "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONSTARTUP", "PYTHONUSERBASE",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
    "SHELL", "BASH_ENV", "ENV", "IFS",
    "PYTHONINSPECT", "PYTHONWARNINGS", "PYTHONFAULTHANDLER",
}

CLONE_TIMEOUT_SECONDS = 180
VENV_TIMEOUT_SECONDS = 180
PIP_TIMEOUT_SECONDS = 900
STARTUP_GRACE_SECONDS = 1.0
LOG_TAIL_BYTES = 12_000

# Per-name locks prevent two deployment requests from racing on one directory.
_deployment_locks: dict[str, asyncio.Lock] = {}


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError("Bot name must contain 1-32 letters, numbers, or underscores.")
    return name


def _validate_repo_url(repo_url: str) -> str:
    """Accept only credential-free HTTPS GitHub repository URLs.

    Private-repository tokens must never be embedded in a URL. A dedicated
    credential mechanism is required before private repositories are enabled.
    """
    if not isinstance(repo_url, str) or len(repo_url) > 300:
        raise ValueError("Repository URL is missing or too long.")

    parsed = urlsplit(repo_url.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Use a credential-free https://github.com/OWNER/REPOSITORY URL.")

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("URL must point directly to one GitHub repository.")

    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    segment_pattern = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
    if not segment_pattern.fullmatch(owner) or not segment_pattern.fullmatch(repository):
        raise ValueError("GitHub owner or repository name contains invalid characters.")
    if owner in {".", ".."} or repository in {".", ".."}:
        raise ValueError("Invalid repository path.")

    return f"https://github.com/{owner}/{repository}.git"


def _validate_env_vars(env_vars: dict[str, str] | None) -> dict[str, str]:
    if env_vars is None:
        return {}
    if not isinstance(env_vars, dict) or len(env_vars) > 40:
        raise ValueError("Environment variables must be a dictionary with at most 40 entries.")

    validated: dict[str, str] = {}
    total_bytes = 0
    for key, value in env_vars.items():
        if not isinstance(key, str) or not ENV_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Invalid environment variable name: {key!r}.")
        if (
            key in RESERVED_ENV_KEYS
            or key.startswith(("PYTHON", "LD_", "DYLD_", "PIP_", "GIT_"))
        ):
            raise ValueError(f"Environment variable {key} is reserved by the host.")
        if not isinstance(value, str) or len(value) > 4_096:
            raise ValueError(f"Environment variable {key} must be text of at most 4096 characters.")
        if "\x00" in value:
            raise ValueError(f"Environment variable {key} contains a null byte.")
        total_bytes += len(key.encode()) + len(value.encode())
        validated[key] = value

    if total_bytes > 32_768:
        raise ValueError("Combined environment variables exceed 32 KiB.")
    return validated


def _bot_directory(name: str) -> Path:
    safe_name = _validate_name(name)
    base = Path(BASE_DIR).expanduser().resolve()
    target = (base / safe_name).resolve()
    if target.parent != base:
        raise ValueError("Resolved bot directory escapes BASE_DIR.")
    return target


def _child_environment(bot_dir: Path, venv_bin: Path, env_vars: dict[str, str]) -> dict[str, str]:
    """Build a clean child environment; never inherit controller secrets."""
    env = {
        "PATH": f"{venv_bin}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(bot_dir / ".home"),
        "TMPDIR": str(bot_dir / ".tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }
    env.update(env_vars)
    return env


def _open_append_log(path: Path):
    """Open an append-only log without following a symbolic-link target."""
    if path.is_symlink():
        raise ValueError(f"Refusing to write through symbolic link: {path.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "ab")


def _tail_file(path: Path, limit: int = LOG_TAIL_BYTES) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read(limit).decode("utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Terminate the process group, including children of an exited launcher."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.returncode is None:
            process.terminate()
    except ProcessLookupError:
        pass

    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def _run_logged(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    timeout: int,
) -> tuple[int, str]:
    """Run one build command without blocking the Telegram event loop."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _open_append_log(log_path) as log_file:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )
        try:
            return_code = await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await _terminate_process(process)
            raise TimeoutError(f"Command timed out after {timeout} seconds.")
        except asyncio.CancelledError:
            await _terminate_process(process)
            raise

    return return_code, _tail_file(log_path)


async def _fetch_bot_username(token: str) -> str | None:
    """Resolve a Telegram bot username without logging or exposing its token."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                payload = await response.json(content_type=None)
                if not payload.get("ok"):
                    return None
                return payload.get("result", {}).get("username")
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


def _find_entry(bot_dir: Path) -> str | None:
    for candidate in ENTRY_CANDIDATES:
        path = bot_dir / candidate
        if path.is_file() and not path.is_symlink():
            return candidate
    return None


async def _launch(bot_dir: Path, env_vars: dict[str, str]) -> asyncio.subprocess.Process:
    entry = _find_entry(bot_dir)
    if entry is None:
        raise FileNotFoundError("No entry point found (main.py, bot.py, or app.py).")

    venv_bin = bot_dir / "venv" / "bin"
    venv_python = venv_bin / "python"
    if not venv_python.is_file():
        raise FileNotFoundError("Virtual environment is missing; deployment is incomplete.")

    for runtime_dir in (bot_dir / ".home", bot_dir / ".tmp"):
        if runtime_dir.is_symlink():
            raise ValueError(f"Refusing symbolic-link runtime directory: {runtime_dir.name}")
        runtime_dir.mkdir(mode=0o700, exist_ok=True)
        if not runtime_dir.is_dir():
            raise ValueError(f"Runtime path is not a directory: {runtime_dir.name}")

    log_path = bot_dir / "bot.log"
    child_env = _child_environment(bot_dir, venv_bin, env_vars)

    with _open_append_log(log_path) as log_file:
        process = await asyncio.create_subprocess_exec(
            str(venv_python),
            entry,
            cwd=str(bot_dir),
            env=child_env,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )

    try:
        await asyncio.sleep(STARTUP_GRACE_SECONDS)
        if process.returncode is not None:
            raise RuntimeError(
                f"Bot exited during startup (exit code {process.returncode}); inspect bot.log."
            )
        return process
    except BaseException:
        await _terminate_process(process)
        raise


async def deploy_bot(name: str, repo_url: str, env_vars: dict[str, str] | None = None):
    """Clone, build and launch one bot. Returns (success, pid_or_reason).

    IMPORTANT: This improves process handling and secret separation, but does
    not sandbox untrusted code. Deploy only repositories you trust until each
    bot runs inside an OS/container isolation boundary with resource limits.
    """
    if not ALLOW_UNSANDBOXED_DEPLOYMENTS:
        return False, (
            "Deployment is disabled because customer code is not sandboxed. "
            "Set ALLOW_UNSANDBOXED_DEPLOYMENTS=true only for repositories you personally trust."
        )

    try:
        name = _validate_name(name)
        repo_url = _validate_repo_url(repo_url)
        safe_env = _validate_env_vars(env_vars)
    except ValueError as exc:
        return False, str(exc)

    lock = _deployment_locks.setdefault(name, asyncio.Lock())
    async with lock:
        bot_dir = _bot_directory(name)
        if await get_bot(name):
            return False, "A bot with this name is already registered."
        if bot_dir.exists():
            return False, "A bot directory with this name already exists. Inspect or remove it first."

        try:
            bot_dir.parent.mkdir(parents=True, exist_ok=True)
            clone_code, clone_log = await _run_logged(
                ["git", "clone", "--depth", "1", "--", repo_url, str(bot_dir)],
                cwd=bot_dir.parent,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(bot_dir.parent),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                },
                log_path=bot_dir.parent / f".{name}.deploy.log",
                timeout=CLONE_TIMEOUT_SECONDS,
            )
            if clone_code != 0:
                logger.error("Git clone failed for %s: %s", name, clone_log[-3_000:])
                return False, "Git clone failed; inspect the deployment log on the server."

            venv_path = bot_dir / "venv"
            if venv_path.exists() or venv_path.is_symlink():
                return False, "Repository contains a reserved venv path; remove it and redeploy."

            venv_code, venv_log = await _run_logged(
                [sys.executable, "-m", "venv", str(venv_path)],
                cwd=bot_dir,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(bot_dir / ".home"),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                },
                log_path=bot_dir / "deploy.log",
                timeout=VENV_TIMEOUT_SECONDS,
            )
            if venv_code != 0:
                logger.error("Virtual environment creation failed for %s: %s", name, venv_log[-3_000:])
                return False, "Virtual environment creation failed; inspect deploy.log on the server."

            requirements = bot_dir / "requirements.txt"
            if requirements.exists():
                if requirements.is_symlink() or not requirements.is_file():
                    return False, "requirements.txt must be a regular file, not a symlink."
                pip_code, pip_log = await _run_logged(
                    [str(venv_path / "bin" / "python"), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", str(requirements)],
                    cwd=bot_dir,
                    env={
                        "PATH": f"{venv_path / 'bin'}:/usr/local/bin:/usr/bin:/bin",
                        "HOME": str(bot_dir / ".home"),
                        "TMPDIR": str(bot_dir / ".tmp"),
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    },
                    log_path=bot_dir / "deploy.log",
                    timeout=PIP_TIMEOUT_SECONDS,
                )
                if pip_code != 0:
                    logger.error("Dependency installation failed for %s: %s", name, pip_log[-3_000:])
                    return False, "Dependency installation failed; inspect deploy.log on the server."

            if _find_entry(bot_dir) is None:
                return False, "No entry point (main.py, bot.py, or app.py) found in repository root."

            process = await _launch(bot_dir, safe_env)
            try:
                process_created_at = await asyncio.to_thread(
                    psutil.Process(process.pid).create_time
                )
            except psutil.Error:
                await _terminate_process(process)
                return False, "Could not verify the launched process identity."

            notify_channel = safe_env.get("NOTIFY_CHANNEL")
            try:
                await add_bot(name, repo_url, safe_env, notify_channel)
                await update_status(name, "running", process.pid, process_created_at)
            except Exception:
                await _terminate_process(process)
                logger.exception("Database registration failed for bot %s", name)
                return False, "The bot started but database registration failed; process was stopped."

            token = safe_env.get("BOT_TOKEN")
            if token:
                username = await _fetch_bot_username(token)
                if username:
                    try:
                        await set_bot_username(name, username)
                    except Exception:
                        # Username metadata is non-critical; deployment itself
                        # is already running and must not be reported as failed.
                        logger.exception("Could not save Telegram username for bot %s", name)

            logger.info("Bot %s deployed successfully (pid=%s)", name, process.pid)
            return True, str(process.pid)

        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.exception("Deployment failed for bot %s", name)
            return False, str(exc)[:2_000]
        except Exception:
            logger.exception("Unexpected deployment failure for bot %s", name)
            return False, "Unexpected deployment error. Check controller logs."


async def launch_existing(name: str, env_vars: dict[str, str] | None = None):
    """Restart an existing bot without re-cloning or reinstalling dependencies."""
    if not ALLOW_UNSANDBOXED_DEPLOYMENTS:
        return False, "Restart is blocked while unsandboxed deployments are disabled."
    try:
        name = _validate_name(name)
        safe_env = _validate_env_vars(env_vars)
        bot_dir = _bot_directory(name)
        if not bot_dir.is_dir():
            return False, "Bot folder missing."

        process = await _launch(bot_dir, safe_env)
        try:
            process_created_at = await asyncio.to_thread(
                psutil.Process(process.pid).create_time
            )
            await update_status(name, "running", process.pid, process_created_at)
        except Exception:
            await _terminate_process(process)
            raise
        logger.info("Bot %s restarted (pid=%s)", name, process.pid)
        return True, str(process.pid)

    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception("Restart failed for bot %s", name)
        return False, str(exc)[:2_000]
    except Exception:
        logger.exception("Unexpected restart failure for bot %s", name)
        return False, "Unexpected restart error. Check controller logs."
