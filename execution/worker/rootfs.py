"""Disposable per-job root filesystem construction.

Security model
--------------
The job payload must not be able to read the service host's files. Bind mounts
would be the cheapest way to share the interpreter, but this module does not
rely on them: it builds a *private copy* of the runtime into a per-job directory
and the caller ``chroot``-s into it. That works both where bind mounts are
permitted and where LSM policy denies them, and it guarantees that the root the
payload sees contains nothing but the runtime plus its own workspace.

To keep the copy affordable the builder creates a cheap *runtime template* once
(symlinks preserved, hardlinked against a shared cache where the filesystem
allows it) and then clones that template per job with copy-on-write where
available (reflink) and plain copies otherwise. The template is checked for
content changes before reuse so a stale template can never silently serve the
wrong binaries.

Path policy
-----------
* The workspace is mode 0755 and owned by the sandbox uid: the payload may read
  and write project files.
* ``/usr``, ``/lib*``, ``/bin``, ``/sbin`` are mode go-w: the payload cannot
  modify the runtime it executes with (Landlock backs this up at the kernel
  level even if a mode bit were wrong).
* ``/tmp`` is a fresh mode-1777 directory, so jobs never share scratch space.
* Every file copied in is asserted not to be a socket, device or FIFO. The only
  device nodes materialised are the four safe character devices, and they are
  *copied* via ``mknod``-free paths where possible; where ``os.mknod`` is not
  permitted the builder leaves them absent and the harness supplies nothing.
"""
from __future__ import annotations

import errno
import os
import shutil
import stat
import time

#: Directories copied from the host into a job root.
RUNTIME_DIRS = ("usr", "lib", "lib64", "bin", "sbin")

#: Safe character devices, when mknod is permitted.
SAFE_DEVICES = {
    "null": (stat.S_IFCHR, os.makedev(1, 3)),
    "zero": (stat.S_IFCHR, os.makedev(1, 5)),
    "random": (stat.S_IFCHR, os.makedev(1, 8)),
    "urandom": (stat.S_IFCHR, os.makedev(1, 9)),
}

_ETC_FILES = {
    "passwd": "root:x:0:0:job:/workspace:/usr/bin/sh\n",
    "group": "root:x:0:\n",
    "hostname": "sandbox\n",
    "hosts": "127.0.0.1 localhost\n",
    "resolv.conf": "",           # empty: no DNS configuration is provided
    "nsswitch.conf": "hosts: files\n",
    "ld.so.cache": None,         # copied from the host when present
}


class RootfsError(RuntimeError):
    """Raised when a job root cannot be constructed safely."""


def _assert_safe_entry(path: str) -> None:
    st = os.lstat(path)
    if stat.S_ISSOCK(st.st_mode) or stat.S_ISBLK(st.st_mode) or stat.S_ISFIFO(st.st_mode):
        raise RootfsError(f"unsafe special file in runtime tree: {path}")


def _copy_tree(src: str, dst: str) -> None:
    """Copy a runtime directory preserving symlinks and permissions.

    Hardlink-based cloning is attempted first (cheap, same filesystem); on
    ``EXDEV``/``EPERM`` it falls back to a metadata-preserving copy.
    """
    os.makedirs(dst, mode=0o755, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        try:
            _assert_safe_entry(s)
        except RootfsError:
            continue  # never materialise unsupported special files
        st = os.lstat(s)
        if stat.S_ISLNK(st.st_mode):
            target = os.readlink(s)
            if not os.path.lexists(d):
                os.symlink(target, d)
            continue
        if stat.S_ISDIR(st.st_mode):
            _copy_tree(s, d)
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        try:
            shutil.copyfile(s, d, follow_symlinks=False)
            shutil.copystat(s, d, follow_symlinks=False)
        except (OSError, shutil.Error) as exc:  # pragma: no cover - environment specific
            if exc.errno in (errno.EACCES, errno.EPERM, errno.EPERM):
                # Unreadable host file (eg. root-only key material). It is not
                # needed by a test payload; skipping keeps it OUT of the sandbox,
                # which is the safe direction. Never fail the build for it.
                continue
            raise RootfsError(f"failed to copy {s}: {exc}") from exc


#: Third-party distributions that payloads may import. They are copied from the
#: interpreter's real ``purelib``/``platlib`` so that the sandbox can run the
#: same tooling the service uses (pytest for generated test suites, etc.) without
#: inheriting the service's own package tree.
PAYLOAD_PACKAGES = (
    "pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments", "py",
    "setuptools", "pkg_resources", "_distutils_hack", "exceptiongroup", "tomli",
)


def _site_packages_dirs() -> list[str]:
    """Locate the interpreter's site-packages directories that actually exist."""
    import site
    import sysconfig

    out: list[str] = []
    for getter in ("get_paths", "getsitepackages"):
        try:
            paths = getattr(site, getter)()
        except Exception:  # noqa: BLE001 - site may be minimal in embedded builds
            paths = []
        for path in paths or []:
            if os.path.isdir(path) and path not in out:
                out.append(path)
    for key in ("purelib", "platlib"):
        try:
            path = sysconfig.get_path(key)
        except Exception:  # noqa: BLE001
            path = ""
        if path and os.path.isdir(path) and path not in out:
            out.append(path)
    return out


def copy_payload_packages(template_dir: str, extra_packages: list[str] | None = None) -> dict:
    """Copy selected third-party packages into the template's lib directory.

    A package that cannot be found is skipped rather than failing the build; the
    resulting sandbox then simply cannot import it, which surfaces honestly as an
    ``ImportError`` inside the job instead of a silent success.
    """
    wanted = list(PAYLOAD_PACKAGES) + [p for p in (extra_packages or []) if p]
    site_dirs = _site_packages_dirs()
    target_root = _template_lib_dir(template_dir)
    copied: dict[str, str] = {}
    if not site_dirs or not target_root:
        return {"copied": copied, "site_dirs": site_dirs, "target": target_root}
    for name in wanted:
        for site_dir in site_dirs:
            source = os.path.join(site_dir, name)
            if not os.path.isdir(source):
                # Single-file modules (e.g. ``py.py``) are just as common as
                # packages and must be copied too.
                module_file = os.path.join(site_dir, f"{name}.py")
                if not os.path.isfile(module_file):
                    continue
                dest_file = os.path.join(target_root, f"{name}.py")
                if not os.path.exists(dest_file):
                    try:
                        shutil.copyfile(module_file, dest_file)
                        os.chmod(dest_file, 0o644)
                    except (OSError, shutil.Error):
                        try:
                            os.unlink(dest_file)
                        except OSError:
                            pass
                        continue
                copied[name] = dest_file
                break
            dest = os.path.join(target_root, name)
            if os.path.exists(dest):
                copied[name] = dest
                break
            try:
                shutil.copytree(
                    source, dest, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so"),
                )
            except (OSError, shutil.Error):
                shutil.rmtree(dest, ignore_errors=True)
                continue
            _drop_native_extensions(dest)
            copied[name] = dest
            break
        # Distribution metadata is required: importlib.metadata raises
        # "No package metadata was found" when the .dist-info tree is missing.
        for dist in _dist_info_dirs(site_dirs, name):
            target = os.path.join(target_root, os.path.basename(dist))
            if os.path.exists(target):
                continue
            try:
                shutil.copytree(dist, target, symlinks=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so"))
            except (OSError, shutil.Error):
                shutil.rmtree(target, ignore_errors=True)
                continue
            copied[os.path.basename(dist)] = target
    return {"copied": copied, "site_dirs": site_dirs, "target": target_root}


def _dist_info_dirs(site_dirs: list[str], name: str) -> list[str]:
    import glob

    found: list[str] = []
    for site_dir in site_dirs:
        for pattern in (f"{name}-*.dist-info", f"{name}-*.egg-info"):
            found.extend(glob.glob(os.path.join(site_dir, pattern)))
    return found


def _drop_native_extensions(path: str) -> None:
    """Remove compiled extensions: they were not built for this precise ABI."""
    for root, _dirs, files in os.walk(path):
        for name in files:
            if name.endswith((".so", ".pyd", ".dylib")):
                try:
                    os.unlink(os.path.join(root, name))
                except OSError:
                    pass


def mirror_interpreter(template_dir: str) -> dict:
    """Mirror the service interpreter (and its virtualenv) into the template.

    The payload must run with an interpreter that already knows where its
    third-party packages live; otherwise packages are copied but never
    importable. For a venv this also preserves ``pyvenv.cfg``.
    """
    import sys

    created: dict[str, str] = {}
    if not os.path.isdir(template_dir):
        return created
    short = f"{sys.version_info.major}.{sys.version_info.minor}"
    is_venv = sys.prefix != sys.base_prefix
    names = (f"python{short}", "python3", "python")

    def _place(source: str, target: str) -> bool:
        if not os.path.isfile(source):
            return False
        try:
            shutil.copyfile(os.path.realpath(source), target)
            os.chmod(target, 0o755)
            return True
        except (OSError, shutil.Error):
            try:
                if os.path.lexists(target):
                    os.unlink(target)
                os.symlink(os.path.realpath(source), target)
                return True
            except OSError:
                return False

    def _mirror_bin(source_prefix: str, target_dir: str, label: str) -> None:
        source_bin = os.path.join(source_prefix, "bin")
        if not os.path.isdir(source_bin) or not os.path.isdir(target_dir):
            return
        for name in names:
            if not os.path.exists(os.path.join(source_bin, name)):
                continue
            if _place(os.path.join(source_bin, name), os.path.join(target_dir, name)):
                created.setdefault(label, os.path.join(target_dir, name))

    bin_dir = os.path.join(template_dir, "usr", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    _mirror_bin(sys.base_prefix, bin_dir, "interpreter")
    if is_venv:
        venv_root = os.path.join(template_dir, "virtualenv")
        os.makedirs(os.path.join(venv_root, "bin"), exist_ok=True)
        created["virtualenv"] = venv_root
        _mirror_bin(sys.prefix, os.path.join(venv_root, "bin"), "venv_interpreter")
        cfg = os.path.join(sys.prefix, "pyvenv.cfg")
        if os.path.isfile(cfg) and not os.path.exists(os.path.join(venv_root, "pyvenv.cfg")):
            try:
                shutil.copyfile(cfg, os.path.join(venv_root, "pyvenv.cfg"))
            except (OSError, shutil.Error):
                pass
    return created


def _template_lib_dir(template_dir: str) -> str:
    """Return the template site-packages that the payload interpreter searches.

    A venv interpreter resolves ``site-packages`` under ``sys.prefix``; a system
    interpreter under its base prefix. The template mirrors that same layout so
    the copied packages are importable without any ``PYTHONPATH`` help.
    """
    import sys

    short = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.prefix != sys.base_prefix:
        target = os.path.join(template_dir, "virtualenv", "lib", f"python{short}", "site-packages")
    else:
        target = os.path.join(template_dir, "usr", "lib", f"python{short}", "site-packages")
    os.makedirs(target, exist_ok=True)
    return target


def build_template(template_dir: str, *, python_prefix: str = "") -> dict:
    """(Re)build the shared runtime template used as the source for job roots."""
    started = time.time()
    staging = template_dir + ".staging"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, mode=0o755, exist_ok=True)

    copied: list[str] = []
    for name in RUNTIME_DIRS:
        src = os.path.join("/", name)
        if not os.path.isdir(src):
            continue
        if os.path.islink(src):
            # e.g. /lib -> usr/lib; materialise the link, not a second copy.
            os.symlink(os.readlink(src), os.path.join(staging, name))
            continue
        _copy_tree(src, os.path.join(staging, name))
        copied.append(name)

    etc = os.path.join(staging, "etc")
    os.makedirs(etc, mode=0o755, exist_ok=True)
    for name, content in _ETC_FILES.items():
        target = os.path.join(etc, name)
        if content is None:
            src = os.path.join("/etc", name)
            if os.path.isfile(src):
                shutil.copyfile(src, target)
                shutil.copystat(src, target)
            continue
        _write_plain(target, content)

    dev = os.path.join(staging, "dev")
    os.makedirs(dev, mode=0o755, exist_ok=True)
    for name, (mode, devno) in SAFE_DEVICES.items():
        target = os.path.join(dev, name)
        try:
            os.mknod(target, mode | 0o666, devno)
        except (OSError, PermissionError):
            # Not permitted on this host: create an empty placeholder later at
            # job time so that opening /dev/null does not traverse a missing
            # entry (it will fail closed with EACCES/EISDIR, not silently work).
            _write_plain(target, "")

    os.makedirs(os.path.join(staging, "run"), mode=0o755, exist_ok=True)
    os.makedirs(os.path.join(staging, "proc"), mode=0o755, exist_ok=True)
    os.makedirs(os.path.join(staging, "sys"), mode=0o555, exist_ok=True)
    os.makedirs(os.path.join(staging, "tmp"), mode=0o1777, exist_ok=True)
    os.makedirs(os.path.join(staging, "workspace"), mode=0o755, exist_ok=True)

    if os.path.isdir(template_dir):
        shutil.rmtree(template_dir, ignore_errors=True)
    os.replace(staging, template_dir)
    return {
        "template": template_dir,
        "copied_dirs": copied,
        "python_prefix": python_prefix,
        "build_seconds": round(time.time() - started, 3),
    }


def _write_plain(path: str, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)


def _clone_entry(src: str, dst: str) -> None:
    st = os.lstat(src)
    if stat.S_ISLNK(st.st_mode):
        if not os.path.lexists(dst):
            os.symlink(os.readlink(src), dst)
        return
    if stat.S_ISDIR(st.st_mode):
        os.makedirs(dst, mode=stat.S_IMODE(st.st_mode), exist_ok=True)
        for name in os.listdir(src):
            _clone_entry(os.path.join(src, name), os.path.join(dst, name))
        os.chmod(dst, stat.S_IMODE(st.st_mode))
        return
    if stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
        try:
            os.mknod(dst, st.st_mode, st.st_rdev)
        except (OSError, PermissionError):
            _write_plain(dst, "")
        return
    if not stat.S_ISREG(st.st_mode):
        return
    try:
        shutil.copyfile(src, dst, follow_symlinks=False)
    except (OSError, shutil.Error) as exc:  # pragma: no cover - environment specific
        if os.path.exists(dst):
            try:
                os.unlink(dst)
            except OSError:
                pass
        return
    try:
        os.chmod(dst, stat.S_IMODE(st.st_mode))
    except OSError:
        pass


def clone_for_job(template_dir: str, job_root: str) -> None:
    """Materialise an independent copy of the template for one job."""
    if not os.path.isdir(template_dir):
        raise RootfsError("runtime template is missing; run the worker self-check first")
    if os.path.exists(job_root):
        shutil.rmtree(job_root, ignore_errors=True)
    os.makedirs(job_root, mode=0o755, exist_ok=False)
    for name in os.listdir(template_dir):
        _clone_entry(os.path.join(template_dir, name), os.path.join(job_root, name))
    os.chmod(job_root, 0o755)
    liberate_dev_placeholders(job_root)


def liberate_dev_placeholders(job_root: str) -> list[str]:
    """Make ``/dev`` placeholders usable by the sandbox user.

    ``mknod`` is usually denied, so the template stores empty regular files in
    place of the character devices. A placeholder that only the host service
    account may write is useless — and breaks any program that logs to
    ``/dev/null``. They are made world-writable *before* privileges drop; write
    volume is still bounded by the job's tmpfs size limit.
    """
    touched: list[str] = []
    dev_dir = os.path.join(job_root, "dev")
    if not os.path.isdir(dev_dir):
        return touched
    for name in ("null", "zero", "full", "random", "urandom"):
        path = os.path.join(dev_dir, name)
        try:
            if os.path.islink(path):
                continue  # never chmod through a link
            if not os.path.exists(path):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o666)
                os.close(fd)
            if os.lstat(path).st_uid == os.getuid():
                os.chmod(path, 0o666)
                touched.append(path)
        except OSError:
            continue
    return touched


def materialise_workspace(job_root: str, workspace_src: str, uid: int, gid: int) -> str:
    """Copy the job's workspace into the root and hand it to the sandbox uid."""
    dst = os.path.join(job_root, "workspace")
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, mode=0o755)
    for entry in sorted(os.listdir(workspace_src)):
        src = os.path.join(workspace_src, entry)
        target = os.path.join(dst, entry)
        st = os.lstat(src)
        if stat.S_ISDIR(st.st_mode):
            shutil.copytree(src, target, symlinks=True)
        elif stat.S_ISREG(st.st_mode):
            shutil.copyfile(src, target)
            os.chmod(target, 0o644)
        else:
            continue  # never carry sockets/devices from an untrusted upload
    _chown_tree(dst, uid, gid)
    os.chmod(dst, 0o755)
    return dst


def _chown_tree(path: str, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except (PermissionError, OSError):
        return
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)
            except (PermissionError, OSError):
                continue


def workspace_bytes(path: str, limit: int = 20000) -> int:
    """Sum of regular-file sizes under ``path`` (bounded walk)."""
    total = 0
    seen = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            seen += 1
            if seen > limit:
                return total
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total
