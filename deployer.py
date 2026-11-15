from __future__ import annotations

import asyncio
import logging
import hashlib
import os
import re
import signal
import shutil
import sys
import tempfile
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


def _docker_names(name: str) -> tuple[str, str]:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:32].rstrip("-") or "bot"
    suffix = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"hostify-{slug}-{suffix}", f"hostify/{slug}-{suffix}:latest"


async def _docker_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
) -> tuple[int, str]:
    """Execute Docker CLI without a shell and return combined output."""
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", *args,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
            env={
                "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": os.getenv("HOME", "/tmp"),
                **({
                    key: os.environ[key]
                    for key in ("DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_CONTEXT")
                    if os.getenv(key)
                }),
            },
        )
    except FileNotFoundError:
        return 127, "Docker deployment is unavailable: the Docker CLI is not installed. A working Docker Engine is also required. Native Render Python services do not provide one."
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate_process(process)
        return 124, f"Docker command timed out after {timeout} seconds."
    return process.returncode or 0, output.decode("utf-8", errors="replace")[-LOG_TAIL_BYTES:]


async def _docker_engine_status() -> tuple[bool, str]:
    code, output = await _docker_command(["info", "--format", "{{.ServerVersion}}"], timeout=12)
    if code == 0:
        return True, output.strip() or "Docker Engine is available."
    detail = output.strip() or "Docker Engine is not responding."
    if "Cannot connect" in detail or "permission denied" in detail.lower():
        detail += (
            "\nA Docker CLI alone is insufficient; this host must expose a running Docker Engine "
            "with permission for the Hostify process. Native Render Python services do not provide one."
        )
    return False, detail[:1200]


def _write_docker_env(bot_dir: Path, env_vars: dict[str, str]) -> Path:
    path = bot_dir / ".hostify.env"
    if path.is_symlink():
        raise ValueError("Refusing to write Docker environment values through a symbolic link.")
    lines = []
    for key, value in env_vars.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"Docker environment variable {key} cannot contain newline characters.")
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    path.chmod(0o600)
    return path


async def _docker_container_running(container_name: str) -> bool:
    code, output = await _docker_command(
        ["inspect", "--format", "{{.State.Running}}", container_name],
        timeout=10,
    )
    return code == 0 and output.strip().lower() == "true"


async def _launch_docker_container(
    bot_dir: Path,
    name: str,
    env_vars: dict[str, str],
    *,
    build: bool,
    replace: bool = True,
) -> tuple[bool, str]:
    container_name, image_name = _docker_names(name)
    available, detail = await _docker_engine_status()
    if not available:
        return False, detail

    dockerfile = bot_dir / "Dockerfile"
    if dockerfile.is_symlink() or not dockerfile.is_file():
        return False, "Docker deployment requires a regular Dockerfile in the repository root."

    if build:
        # Never let Docker COPY instructions capture Hostify's private runtime
        # environment file from a previous run.
        env_secret_file = bot_dir / ".hostify.env"
        parked_env_file = bot_dir.parent / f".{container_name}.hostify-env.tmp"
        parked = False
        if env_secret_file.exists():
            if env_secret_file.is_symlink() or parked_env_file.exists():
                return False, "Could not safely exclude the private Docker environment file from the build context."
            os.replace(env_secret_file, parked_env_file)
            parked = True
        try:
            code, output = await _docker_command(
                ["build", "--tag", image_name, "--file", "Dockerfile", "."],
                cwd=bot_dir,
                timeout=PIP_TIMEOUT_SECONDS,
            )
        finally:
            if parked and parked_env_file.exists():
                os.replace(parked_env_file, env_secret_file)
        if code != 0:
            return False, f"Docker image build failed (exit {code}):\n{output[-2600:]}"

    if replace:
        await _docker_command(["rm", "--force", container_name], timeout=30)

    try:
        env_file = _write_docker_env(bot_dir, env_vars)
    except (OSError, ValueError) as exc:
        return False, str(exc)

    code, output = await _docker_command(
        [
            "run", "--detach",
            "--name", container_name,
            "--restart", "unless-stopped",
            "--init",
            "--env-file", str(env_file),
            image_name,
        ],
        cwd=bot_dir,
        timeout=60,
    )
    if code != 0:
        return False, f"Docker container start failed (exit {code}):\n{output[-2600:]}"

    container_id = output.strip().splitlines()[-1] if output.strip() else container_name
    await asyncio.sleep(3)
    if not await _docker_container_running(container_name):
        _, logs = await _docker_command(["logs", "--tail", "60", container_name], timeout=15)
        await _docker_command(["rm", "--force", container_name], timeout=30)
        return False, "Docker container exited during startup.\n" + (logs[-2600:] or output[-1200:])
    return True, container_id


async def deploy_bot(
    name: str,
    repo_url: str,
    env_vars: dict[str, str] | None = None,
    github_token: str | None = None,
    deployed_by: int | None = None,
    runtime_type: str = "python",
):
    """Clone, build and launch one bot. Returns (success, pid_or_reason).

    IMPORTANT: This improves process handling and secret separation, but does
    not sandbox untrusted code. Deploy only repositories you trust until each
    bot runs inside an OS/container isolation boundary with resource limits.
    """
    if not ALLOW_UNSANDBOXED_DEPLOYMENTS:
        return False, (
            "Deployment is disabled. Set ALLOW_UNSANDBOXED_DEPLOYMENTS=true only for repositories you trust."
        )
    if runtime_type not in {"python", "docker"}:
        return False, "Choose either Python or Docker deployment."
    if runtime_type == "docker":
        available, detail = await _docker_engine_status()
        if not available:
            return False, detail

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
            clone_env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(bot_dir.parent),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            }
            askpass_dir = None
            try:
                if github_token:
                    # Keep the short-lived installation token out of the clone URL,
                    # command arguments, logs, database and deployed bot environment.
                    askpass_dir = Path(tempfile.mkdtemp(
                        prefix=f".{name}.git-auth-",
                        dir=str(bot_dir.parent),
                    ))
                    askpass = askpass_dir / "askpass.sh"
                    askpass.write_text(
                        "#!/bin/sh\n"
                        "case \"$1\" in\n"
                        "  *Username*) printf '%s\n' 'x-access-token' ;;\n"
                        "  *Password*) printf '%s\n' \"$HOSTIFY_GITHUB_TOKEN\" ;;\n"
                        "  *) exit 1 ;;\n"
                        "esac\n",
                        encoding="utf-8",
                    )
                    askpass.chmod(0o700)
                    clone_env.update({
                        "HOME": str(askpass_dir),
                        "GIT_ASKPASS": str(askpass),
                        "GIT_ASKPASS_REQUIRE": "force",
                        "HOSTIFY_GITHUB_TOKEN": github_token,
                    })

                clone_code, clone_log = await _run_logged(
                    ["git", "clone", "--depth", "1", "--", repo_url, str(bot_dir)],
                    cwd=bot_dir.parent,
                    env=clone_env,
                    log_path=bot_dir.parent / f".{name}.deploy.log",
                    timeout=CLONE_TIMEOUT_SECONDS,
                )
            finally:
                clone_env.pop("HOSTIFY_GITHUB_TOKEN", None)
                if askpass_dir is not None:
                    shutil.rmtree(askpass_dir, ignore_errors=True)
            if clone_code != 0:
                logger.error("Git clone failed for %s: %s", name, clone_log[-3_000:])
                shutil.rmtree(bot_dir, ignore_errors=True)
                return False, f"Git clone failed: {clone_log[-1800:] or 'inspect the controller deployment log.'"

            if runtime_type == "docker":
                dockerfile = bot_dir / "Dockerfile"
                if dockerfile.is_symlink() or not dockerfile.is_file():
                    shutil.rmtree(bot_dir, ignore_errors=True)
                    return False, "Docker deployment requires a Dockerfile in the repository root."
                launched, container_id = await _launch_docker_container(
                    bot_dir, name, safe_env, build=True, replace=True
                )
                if not launched:
                    logger.error("Docker deployment failed for %s: %s", name, container_id[-2500:])
                    shutil.rmtree(bot_dir, ignore_errors=True)
                    return False, container_id
                notify_channel = safe_env.get("NOTIFY_CHANNEL")
                try:
                    await add_bot(
                        name, repo_url, safe_env, notify_channel,
                        deployed_by=deployed_by, runtime_type="docker", container_id=container_id,
                    )
                    await update_status(name, "running", None, None)
                except Exception:
                    await _docker_command(["rm", "--force", _docker_names(name)[0]], timeout=30)
                    logger.exception("Database registration failed for Docker bot %s", name)
                    shutil.rmtree(bot_dir, ignore_errors=True)
                    return False, "Docker container started but database registration failed; container was removed."
                token = safe_env.get("BOT_TOKEN")
                if token:
                    username = await _fetch_bot_username(token)
                    if username:
                        try:
                            await set_bot_username(name, username)
                        except Exception:
                            logger.exception("Could not save Telegram username for Docker bot %s", name)
                logger.info("Docker bot %s deployed successfully (container=%s)", name, container_id[:12])
                return True, f"container {container_id[:12]}"

            venv_path = bot_dir / "venv"
            if venv_path.exists() or venv_path.is_symlink():
                shutil.rmtree(bot_dir, ignore_errors=True)
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
                shutil.rmtree(bot_dir, ignore_errors=True)
                return False, f"Virtual environment creation failed: {venv_log[-1800:] or 'unknown error.'"

            requirements = bot_dir / "requirements.txt"
            if requirements.exists():
                if requirements.is_symlink() or not requirements.is_file():
                    shutil.rmtree(bot_dir, ignore_errors=True)
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
                    shutil.rmtree(bot_dir, ignore_errors=True)
                    return False, f"Dependency installation failed: {pip_log[-1800:] or 'unknown error.'"

            if _find_entry(bot_dir) is None:
                shutil.rmtree(bot_dir, ignore_errors=True)
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
                await add_bot(name, repo_url, safe_env, notify_channel, deployed_by=deployed_by)
                await update_status(name, "running", process.pid, process_created_at)
            except Exception:
                await _terminate_process(process)
                logger.exception("Database registration failed for bot %s", name)
                shutil.rmtree(bot_dir, ignore_errors=True)
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


async def _restore_python_bot_folder(name: str, bot_dir: Path, repo_url: str) -> tuple[bool, str]:
    """Re-clone tracked source and rebuild the Python environment after ephemeral storage loss."""
    if bot_dir.exists():
        return True, "Project folder is present."
    github_token = None
    try:
        from github_app import get_installation_token_for_repository
        github_token = await get_installation_token_for_repository(repo_url)
    except Exception as exc:
        logger.warning("GitHub App token unavailable while restoring %s: %s", name, exc)

    bot_dir.parent.mkdir(parents=True, exist_ok=True)
    clone_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(bot_dir.parent),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    askpass_dir = None
    try:
        if github_token:
            askpass_dir = Path(tempfile.mkdtemp(prefix=f".{name}.restore-auth-", dir=str(bot_dir.parent)))
            askpass = askpass_dir / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\\n"
                "case \\"$1\\" in\\n"
                "  *Username*) printf '%s\\n' 'x-access-token' ;;\\n"
                "  *Password*) printf '%s\\n' \\"$HOSTIFY_GITHUB_TOKEN\\" ;;\\n"
                "  *) exit 1 ;;\\n"
                "esac\\n",
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            clone_env.update({
                "HOME": str(askpass_dir),
                "GIT_ASKPASS": str(askpass),
                "GIT_ASKPASS_REQUIRE": "force",
                "HOSTIFY_GITHUB_TOKEN": github_token,
            })
        clone_code, clone_log = await _run_logged(
            ["git", "clone", "--depth", "1", "--", repo_url, str(bot_dir)],
            cwd=bot_dir.parent,
            env=clone_env,
            log_path=bot_dir.parent / f".{name}.restore.log",
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except (OSError, RuntimeError, TimeoutError) as exc:
        shutil.rmtree(bot_dir, ignore_errors=True)
        return False, f"Could not restore repository source: {exc}"
    finally:
        clone_env.pop("HOSTIFY_GITHUB_TOKEN", None)
        if askpass_dir is not None:
            shutil.rmtree(askpass_dir, ignore_errors=True)

    if clone_code != 0:
        shutil.rmtree(bot_dir, ignore_errors=True)
        return False, f"Git clone failed while restoring source: {clone_log[-1800:]}"

    entry = _find_entry(bot_dir)
    if entry is None:
        shutil.rmtree(bot_dir, ignore_errors=True)
        return False, "Restored repository has no main.py, bot.py, or app.py entry point."

    venv_path = bot_dir / "venv"
    venv_code, venv_log = await _run_logged(
        [sys.executable, "-m", "venv", str(venv_path)],
        cwd=bot_dir,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(bot_dir / ".home")},
        log_path=bot_dir / "deploy.log",
        timeout=VENV_TIMEOUT_SECONDS,
    )
    if venv_code != 0:
        shutil.rmtree(bot_dir, ignore_errors=True)
        return False, f"Could not recreate Python virtual environment: {venv_log[-1800:]}"

    requirements = bot_dir / "requirements.txt"
    if requirements.exists():
        if requirements.is_symlink() or not requirements.is_file():
            shutil.rmtree(bot_dir, ignore_errors=True)
            return False, "Restored requirements.txt is not a regular file."
        pip_code, pip_log = await _run_logged(
            [
                str(venv_path / "bin" / "python"), "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input", "-r", str(requirements),
            ],
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
            shutil.rmtree(bot_dir, ignore_errors=True)
            return False, f"Dependency installation failed while restoring source: {pip_log[-1800:]}"

    logger.info("Restored local project folder for %s from %s", name, repo_url)
    return True, "Repository source and dependencies restored."




async def launch_existing(name: str, env_vars: dict[str, str] | None = None):
    """Restart an existing bot without re-cloning or reinstalling dependencies."""
    if not ALLOW_UNSANDBOXED_DEPLOYMENTS:
        return False, "Restart is blocked while unsandboxed deployments are disabled."
    try:
        name = _validate_name(name)
        safe_env = _validate_env_vars(env_vars)
        bot_dir = _bot_directory(name)
        bot_record = await get_bot(name) or {}
        if bot_record.get("runtime_type", "python") == "docker":
            container_name, image_name = _docker_names(name)
            available, detail = await _docker_engine_status()
            if not available:
                return False, detail
            state_code, state = await _docker_command(
                ["inspect", "--format", "{{.State.Status}}", container_name], timeout=10
            )
            if state_code == 0:
                if state.strip().lower() == "running":
                    await update_status(name, "running", None, None)
                    return True, container_name
                if bot_dir.is_dir():
                    launched, detail = await _launch_docker_container(
                        bot_dir, name, safe_env, build=False, replace=True
                    )
                    if not launched:
                        return False, detail
                    await update_status(name, "running", None, None)
                    return True, detail
                code, output = await _docker_command(["start", container_name], timeout=30)
                if code != 0:
                    return False, output or "Could not start the existing Docker container."
                await update_status(name, "running", None, None)
                return True, container_name
            launched, detail = await _launch_docker_container(
                bot_dir, name, safe_env, build=True, replace=False
            )
            if launched:
                await update_status(name, "running", None, None)
                return True, detail
            return False, detail

        if not bot_dir.is_dir():
            repo_url = bot_record.get("repo_url")
            if not isinstance(repo_url, str) or not repo_url:
                return False, "Bot folder missing and no repository URL is saved for recovery."
            restored, detail = await _restore_python_bot_folder(name, bot_dir, repo_url)
            if not restored:
                return False, detail

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
