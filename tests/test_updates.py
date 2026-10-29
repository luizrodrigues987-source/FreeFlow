"""Code updates: only signed, compiling, not-known-broken updates are installed."""
import io
import os
import tempfile
import zipfile

from _util import case, run
from freeflow import APP_VERSION, signing, updater

TEST_KEY = bytes(range(32))                        # a throwaway key; the real one never leaves the publisher's PC
REV = 999912312359


def make_zip(files=None, key=TEST_KEY, rev=REV, version=APP_VERSION, signed=True, tamper=False):
    files = dict(files or {"freeflow/app.py": b"print('hello')\n"})
    files["freeflow/__init__.py"] = f'APP_VERSION = "{version}"\nCODE_REVISION = {rev}\n'.encode()
    sig = signing.sign(key, signing.manifest(files))
    if tamper:
        files["freeflow/app.py"] = b"print('something else')\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
        if signed:
            z.writestr("SIGNATURE", sig.hex())
    return buf.getvalue()


def apply(data):
    saved = updater.UPDATE_PUBLIC_KEY, updater.is_frozen
    updater.UPDATE_PUBLIC_KEY, updater.is_frozen = signing.public_key(TEST_KEY).hex(), lambda: True
    target = os.path.join(tempfile.mkdtemp(prefix="ff-upd-"), "update")
    try:
        return updater.apply_from_zip(data, target), target
    finally:
        updater.UPDATE_PUBLIC_KEY, updater.is_frozen = saved


@case
def ed25519_matches_rfc8032():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    assert signing.public_key(sk).hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = signing.sign(sk, b"")
    assert sig.hex().startswith("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb882")
    assert signing.verify(signing.public_key(sk), b"", sig)
    assert not signing.verify(signing.public_key(sk), b"x", sig)


@case
def a_signed_update_is_installed():
    res, target = apply(make_zip())
    assert res.status == "updated", res
    assert open(os.path.join(target, "freeflow", "app.py"), encoding="utf-8").read() == "print('hello')\n"


@case
def unsigned_tampered_or_foreign_updates_are_refused():
    for data in (make_zip(signed=False), make_zip(tamper=True), make_zip(key=bytes(32))):
        res, target = apply(data)
        assert res.status == "error" and "not signed" in res.message, res
        assert not os.path.exists(target)


@case
def code_that_does_not_compile_is_refused():
    res, target = apply(make_zip({"freeflow/app.py": b"def broken(:\n"}))
    assert res.status == "error" and "damaged" in res.message, res
    assert not os.path.exists(target)


@case
def a_revision_that_failed_to_start_is_not_installed_again():
    data = make_zip()
    saved = updater.UPDATE_PUBLIC_KEY, updater.is_frozen
    updater.UPDATE_PUBLIC_KEY, updater.is_frozen = signing.public_key(TEST_KEY).hex(), lambda: True
    base = tempfile.mkdtemp(prefix="ff-upd-")
    target = os.path.join(base, "update")
    broken = os.path.join(base, "update.broken-20261029-120000", "freeflow")
    os.makedirs(broken)
    open(os.path.join(broken, "__init__.py"), "w").write(f"CODE_REVISION = {REV}\n")
    try:
        res = updater.apply_from_zip(data, target)
    finally:
        updater.UPDATE_PUBLIC_KEY, updater.is_frozen = saved
    assert res.status == "current" and "did not start" in res.message, res


@case
def another_package_version_needs_the_full_package():
    res, _ = apply(make_zip(version="9.9.9"))
    assert res.status == "full_package", res


@case
def the_real_public_key_is_built_in():
    assert len(bytes.fromhex(updater.UPDATE_PUBLIC_KEY)) == 32


if __name__ == "__main__":
    run()
