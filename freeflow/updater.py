"""Self-update from the GitHub release: fetch the small code-only update zip, stage it, restart.

The packaged FreeFlow.exe starts through freeflow_main.py, which prefers the code in
%APPDATA%/FreeFlow/update when its CODE_REVISION is newer than the bundled code.  This
module fills that folder from the release asset "FreeFlow-update-latest.zip".
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from typing import Optional

from . import APP_VERSION, CODE_REVISION, signing
from .config import DATA_DIR

log = logging.getLogger(__name__)

DEFAULT_UPDATE_URL = "https://github.com/luizrodrigues987-source/FreeFlow/releases/latest/download/FreeFlow-update-latest.zip"
DEFAULT_RELEASE_PAGE = "https://github.com/luizrodrigues987-source/FreeFlow/releases/latest"
UPDATE_DIR = os.path.join(DATA_DIR, "update")
# the publisher's Ed25519 public key: an update is installed only with a valid signature for it (see
# signing.py; the private key is on the publisher's PC, build_update.py signs every zip)
UPDATE_PUBLIC_KEY = "6ca1fcd1288f24667a4c26fede257e930107250d192345c8155834a28e36c4d1"


@dataclass
class UpdateResult:
    status: str            # updated | current | full_package | source | error
    message: str
    revision: int = 0
    version: str = ""
    page_url: str = DEFAULT_RELEASE_PAGE

    @property
    def restart_needed(self) -> bool:
        return self.status == "updated"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _revision_of(init_path: str) -> int:
    try:
        with open(init_path, encoding="utf-8") as f:
            m = re.search(r"CODE_REVISION\s*=\s*(\d+)", f.read())
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def staged_revision(update_dir: str = None) -> int:
    return _revision_of(os.path.join(update_dir or UPDATE_DIR, "freeflow", "__init__.py"))


def broken_revisions(update_dir: str = None) -> set:
    """Revisions the loader put aside because they failed to start (folders update.broken-*)."""
    base = update_dir or UPDATE_DIR
    parent, name = os.path.dirname(base), os.path.basename(base)
    out = set()
    try:
        for d in os.listdir(parent):
            if d.startswith(name + ".broken-"):
                rev = _revision_of(os.path.join(parent, d, "freeflow", "__init__.py"))
                if rev:
                    out.add(rev)
    except OSError:
        pass
    return out


def local_revision(update_dir: str = None) -> int:
    """Newest code available on this PC: what is running, or what is already staged for the next start."""
    return max(int(CODE_REVISION or 0), staged_revision(update_dir))


def preflight(z: "zipfile.ZipFile") -> Optional[str]:
    """Why the code in the zip must not be installed (None = fine): a module that does not compile."""
    for name in z.namelist():
        if name.startswith("freeflow/") and name.endswith(".py"):
            try:
                compile(z.read(name).decode("utf-8"), name, "exec")
            except Exception as e:
                return f"{name} does not compile ({e})"
    return None


def inspect_zip(data: bytes) -> tuple[int, str]:
    """(CODE_REVISION, APP_VERSION) of the code inside an update zip."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        src = z.read("freeflow/__init__.py").decode("utf-8", "replace")
    rev = re.search(r"CODE_REVISION\s*=\s*(\d+)", src)
    ver = re.search(r"APP_VERSION\s*=\s*\"([^\"]+)\"", src)
    return (int(rev.group(1)) if rev else 0), (ver.group(1) if ver else "")


def download(url: str, timeout: float = 60.0) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": f"FreeFlow/{APP_VERSION} updater"})
    if r.status_code != 200:
        raise RuntimeError(f"download failed with HTTP {r.status_code}")
    if not r.content or r.content[:2] != b"PK":
        raise RuntimeError("the downloaded file is not a zip archive")
    return r.content


def apply_from_zip(data: bytes, update_dir: str = None, force: bool = False,
                   page_url: str = DEFAULT_RELEASE_PAGE) -> UpdateResult:
    update_dir = update_dir or UPDATE_DIR
    rev, ver = inspect_zip(data)
    if not rev:
        return UpdateResult("error", "The update file has no revision stamp", page_url=page_url)
    if ver != APP_VERSION:
        return UpdateResult("full_package",
                            f"Version {ver} needs the full package; you have {APP_VERSION}. Download it from {page_url}",
                            rev, ver, page_url)
    if rev <= local_revision(update_dir) and not force:
        return UpdateResult("current", f"FreeFlow is up to date (revision {local_revision(update_dir)})",
                            local_revision(update_dir), ver, page_url)
    if rev in broken_revisions(update_dir) and not force:
        # it failed to start here before; installing it again would restart FreeFlow every half minute
        return UpdateResult("current", f"Update r{rev} did not start on this PC before; waiting for a newer one",
                            local_revision(update_dir), ver, page_url)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {n: z.read(n) for n in signing.installable(z.namelist())}
        try:
            sig = bytes.fromhex(z.read("SIGNATURE").decode("ascii").strip())
        except (KeyError, ValueError, UnicodeDecodeError):
            sig = b""
        problem = preflight(z)
    if not signing.verify(bytes.fromhex(UPDATE_PUBLIC_KEY), signing.manifest(files), sig):
        log.error("Update r%d rejected: %s", rev, "no valid signature" if sig else "not signed")
        return UpdateResult("error", f"Update r{rev} is not signed by FreeFlow's publisher; not installed",
                            page_url=page_url)
    if problem:
        log.error("Update r%d rejected: %s", rev, problem)
        return UpdateResult("error", f"Update r{rev} is damaged ({problem}); not installed", page_url=page_url)
    if not is_frozen():
        return UpdateResult("source", f"Update r{rev} is available, but this copy runs from source code: use git pull",
                            rev, ver, page_url)
    tmp = update_dir + ".tmp"
    old = update_dir + ".old"
    for d in (tmp, old):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "freeflow"))
    for name, content in files.items():               # exactly the files the signature covers
        with open(os.path.join(tmp, name.replace("/", os.sep)), "wb") as f:
            f.write(content)
    if os.path.isdir(update_dir):
        os.rename(update_dir, old)
    os.rename(tmp, update_dir)
    shutil.rmtree(old, ignore_errors=True)
    log.info("Update r%d staged in %s", rev, update_dir)
    return UpdateResult("updated", f"Update r{rev} installed; FreeFlow restarts to use it", rev, ver, page_url)


def check_and_apply(url: str = None, page_url: str = None, update_dir: str = None, force: bool = False) -> UpdateResult:
    url = url or DEFAULT_UPDATE_URL
    page_url = page_url or DEFAULT_RELEASE_PAGE
    try:
        data = download(url)
    except Exception as e:
        log.warning("Update check failed: %s", e)
        return UpdateResult("error", f"Could not check for updates ({e})", page_url=page_url)
    try:
        return apply_from_zip(data, update_dir, force, page_url)
    except Exception as e:
        log.exception("Applying the update failed")
        return UpdateResult("error", f"Could not apply the update ({e})", page_url=page_url)


def relaunch(extra_args: Optional[list] = None):
    """Start a fresh FreeFlow (which asks the running one to quit) so the staged code takes effect.
    The new copy is told which process it replaces: it is a child of this one, and a FreeFlow never
    closes its own parents (its launcher) otherwise - before, the new copy just met the single-instance
    lock ("FreeFlow is already running") and the update waited for the next sign-in."""
    args = list(extra_args or ["--restart"])
    if "--restart" in args and "--replace" not in args:
        args += ["--replace", str(os.getpid())]
    try:
        if is_frozen():
            subprocess.Popen([sys.executable] + args, cwd=os.path.dirname(sys.executable), close_fds=True)
        else:
            from .autostart import launcher_path, pythonw_path
            subprocess.Popen([pythonw_path(), launcher_path()] + args, close_fds=True)
    except Exception as e:
        log.error("Could not relaunch FreeFlow: %s", e)
