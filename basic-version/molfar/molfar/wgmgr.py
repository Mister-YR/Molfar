"""Керування WireGuard-профілями через root-помічник /usr/local/sbin/molfar-wg (sudo -n)."""
import asyncio
import json
import os
import shutil

HELPER = os.environ.get("MOLFAR_WG_HELPER", "/usr/local/sbin/molfar-wg")
USE_SUDO = os.environ.get("MOLFAR_WG_SUDO", "1") != "0" and os.geteuid() != 0
WG_BIN = os.environ.get("MOLFAR_WG_BIN") or shutil.which("wg") or "/usr/bin/wg"
ROLLBACK_SEC = 60

_lock = asyncio.Lock()


class WgError(Exception):
    def __init__(self, code, **kw):
        super().__init__(code)
        self.code, self.kw = code, kw


def available() -> bool:
    return os.path.isfile(HELPER)


async def _run(args, stdin: str | None = None, timeout=90):
    cmd = (["sudo", "-n", HELPER] if USE_SUDO else [HELPER]) + args
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise WgError("unavailable")
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin is not None else None), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise WgError("helper_error", err="timeout")
    try:
        data = json.loads(out.decode() or "{}")
    except ValueError:
        data = {}
    if not data.get("ok"):
        if "code" in data:
            raise WgError(data.pop("code"), **{k: v for k, v in data.items() if k != "ok"})
        msg = err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
        if "password is required" in msg or "not allowed" in msg:
            raise WgError("unavailable")
        raise WgError("helper_error", err=msg[:300] or f"rc={proc.returncode}")
    return data


async def call(*args, stdin=None):
    async with _lock:  # одна операція з WireGuard за раз
        return await _run(list(args), stdin)


async def list_profiles():
    return (await call("list"))["profiles"]


async def get_profile(name):
    return await call("get", name)


async def create(name, config):
    return await call("put", name, "--create", stdin=config)


async def save(name, config):
    return await call("put", name, "--rollback", str(ROLLBACK_SEC), stdin=config)


async def action(name, act):
    if act == "up":
        return await call("up", name, "--rollback", str(ROLLBACK_SEC))
    return await call(act, name)


async def delete(name):
    return await call("delete", name)


async def _wg(args, stdin=None):
    proc = await asyncio.create_subprocess_exec(
        WG_BIN, *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin else None), 10)
    if proc.returncode != 0:
        raise WgError("helper_error", err=err.decode(errors="replace").strip()[:200] or "wg failed")
    return out.decode().strip()


async def keygen():
    """Пара ключів. `wg genkey` не потребує root."""
    priv = await _wg(["genkey"])
    return {"private_key": priv, "public_key": await _wg(["pubkey"], priv + "\n")}


async def pubkey(priv: str):
    return await _wg(["pubkey"], priv.strip() + "\n")
