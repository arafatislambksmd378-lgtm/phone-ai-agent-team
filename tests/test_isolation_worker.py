"""Adversarial tests for the isolated execution worker (Phase 5.1).

These tests execute real payloads inside the real sandbox and assert on observed
behaviour — not on configuration. They are split in two layers:

* **Structural tests** always run: they verify the policy objects, the fail-closed
  gate, request authentication, cleanup bookkeeping and quota enforcement, using a
  fake executor where no kernel boundary is needed.
* **Boundary tests** require a verified worker. When the host cannot provide one
  (no Landlock, no user namespaces, restricted CI sandbox) they *skip with an
  explicit reason* rather than silently passing, and
  ``tests/test_isolation_gate.py`` asserts that a failing self-check never
  enables execution.

The boundary layer is the evidence for: filesystem escape, symlink escape, secret
exposure, network egress, cross-process signalling, resource exhaustion, timeout,
cancellation, cleanup, and unauthorized submission.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

import pytest

from execution.worker import seccomp
from execution.worker.limits import apply_limits, read_limits

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux kernel boundaries only")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _worker_enabled() -> bool:
    return os.environ.get("ISOLATED_WORKER_ENABLED", "").lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="module")
def supervisor():
    """A prepared supervisor, or skip with a precise reason."""
    if not _worker_enabled():
        pytest.skip("ISOLATED_WORKER_ENABLED is not set (fail-closed default)")
    from execution.worker.supervisor import Supervisor, WorkerUnavailable

    sup = Supervisor()
    try:
        report = sup.prepare()
    except WorkerUnavailable as exc:  # pragma: no cover - host dependent
        pytest.skip(f"no verified isolated worker on this host: {exc}")
    if not report.get("boundary", {}).get("verified"):
        result = report.get("boundary", {}).get("result", {})
        pytest.skip(
            "sandbox self-check failed on this host: "
            f"{(result.get('stdout') or result.get('stderr') or '')[-400:]}"
        )
    yield sup
    sup.stop()


def _job_dir(supervisor, name: str, files: dict[str, str]) -> str:
    workspace = os.path.join(supervisor.job_root, name)
    shutil.rmtree(workspace, ignore_errors=True)
    os.makedirs(workspace, exist_ok=True)
    for rel, content in files.items():
        path = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(path) or workspace, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return workspace


def _run(supervisor, name: str, script: str, *, timeout=120, memory_mb=768) -> dict:
    workspace = _job_dir(supervisor, name, {"job.py": script})
    return supervisor.run_job(
        job_id=name, project_id="test-project", workspace=workspace,
        argv=["/usr/bin/python3", "/workspace/job.py"],
        timeout_seconds=timeout, memory_mb=memory_mb,
    )


# --------------------------------------------------------------------------- #
# always-on structural tests
# --------------------------------------------------------------------------- #
def test_gate_is_fail_closed_without_opt_in(monkeypatch):
    from execution import isolation

    monkeypatch.delenv("ISOLATED_WORKER_ENABLED", raising=False)
    isolation.reset_cache_for_tests()
    assert isolation.isolated_worker_available() is False
    assert isolation.executor() is None
    assert "blocked" in isolation.BLOCKED_MESSAGE.lower()
    assert isolation.status()["verified"] is False


def test_planned_backend_cannot_be_enabled(monkeypatch):
    from execution import isolation

    monkeypatch.setenv("ISOLATED_WORKER_ENABLED", "true")
    monkeypatch.setenv("ISOLATED_WORKER_BACKEND", "firecracker")
    isolation.reset_cache_for_tests()
    assert isolation.isolated_worker_available() is False
    status = isolation.status()
    assert status["verified"] is False
    assert "not implemented" in status["error"] or "planned" in status["error"]
    monkeypatch.delenv("ISOLATED_WORKER_BACKEND")
    isolation.reset_cache_for_tests()


def test_unknown_backend_is_rejected(monkeypatch):
    from execution import isolation

    monkeypatch.setenv("ISOLATED_WORKER_ENABLED", "true")
    monkeypatch.setenv("ISOLATED_WORKER_BACKEND", "totally-made-up")
    isolation.reset_cache_for_tests()
    assert isolation.isolated_worker_available() is False
    monkeypatch.delenv("ISOLATED_WORKER_BACKEND")
    isolation.reset_cache_for_tests()


def test_sandbox_uid_cannot_be_root(monkeypatch):
    from execution.worker.supervisor import WorkerUnavailable, sandbox_uid

    monkeypatch.setenv("SANDBOX_UID", "0")
    with pytest.raises(WorkerUnavailable):
        sandbox_uid()
    monkeypatch.delenv("SANDBOX_UID")


def test_seccomp_policy_covers_dangerous_syscalls():
    table = seccomp.syscall_table("x86_64")
    for name in ("mount", "ptrace", "unshare", "setns", "bpf", "init_module", "kexec_load"):
        assert name in table, f"{name} must be denied"
    assert "kill" not in table, "kill is only blocked when a PID namespace is unavailable"
    restricted = seccomp.syscall_table("x86_64", block_signals=True)
    assert "kill" in restricted and "tgkill" in restricted


def test_seccomp_program_is_well_formed():
    import struct

    for block in (False, True):
        raw = seccomp.bpf_program(block_signals=block, self_pid=4242)
        assert len(raw) % 8 == 0
        count = len(raw) // 8
        for index in range(count):
            code, jt, jf, k = struct.unpack_from("=HBBI", raw, index * 8)
            if (code & 0x07) == 0x05:  # BPF_JMP
                assert index + 1 + jt < count, "forward jt out of range"
                assert index + 1 + jf < count, "forward jf out of range"


def test_limits_are_clamped_not_disabled():
    """A caller asking for 0 must get a real limit, never 'unlimited'.

    ``apply_limits`` is irreversible and affects the calling process, so it is
    exercised in a forked child; running it in the pytest process would corrupt
    every later test (an RLIMIT_FSIZE of 16 MB breaks rootfs construction).
    """
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            data = apply_limits(cpu_seconds=0, address_space_mb=0,
                                process_count=0, file_size_mb=0)
            limits = read_limits()
            ok = (data.get("cpu", (0,))[0] >= 1
                  and data.get("as", (0,))[0] >= 64 * 1024 * 1024
                  and data.get("nproc", (0,))[0] >= 8
                  and limits["fsize"][0] >= 1024 * 1024)
            os._exit(0 if ok else 1)
        except BaseException:
            os._exit(2)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0


def test_worker_request_requires_token_and_nonce():
    """Direct socket clients without the token must be rejected."""
    from execution.worker.jobserver import JobServer

    tmp = os.path.join("/tmp", f"phone-agent-auth-{os.getpid()}")
    os.makedirs(tmp, exist_ok=True)
    server = JobServer(
        socket_path=os.path.join(tmp, "s.sock"), runtime_template=tmp,
        state_dir=tmp, sandbox_uid=20000, sandbox_gid=20000, token="secret-token",
    )
    try:
        assert server._dispatch({"action": "ping"})["ok"] is False
        assert server._dispatch({"action": "ping", "token": "wrong", "nonce": "x" * 32})["ok"] is False
        assert server._dispatch({"action": "run", "token": "", "nonce": "y" * 32,
                                 "job": {"job_id": "x"}})["ok"] is False
        good = {"action": "ping", "token": "secret-token", "nonce": "a" * 32}
        assert server._dispatch(dict(good))["ok"] is True
        # Replay of the same nonce must fail.
        assert server._dispatch(dict(good))["ok"] is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_jobserver_rejects_duplicate_and_malformed_job_ids():
    from execution.worker.jobserver import JobServer

    tmp = os.path.join("/tmp", f"phone-agent-ids-{os.getpid()}")
    os.makedirs(tmp, exist_ok=True)
    server = JobServer(socket_path=os.path.join(tmp, "s.sock"), runtime_template=tmp,
                       state_dir=tmp, sandbox_uid=20000, sandbox_gid=20000, token="t")
    try:
        assert server._run_job({"job_id": "../../etc"})["ok"] is False
        assert server._run_job({"job_id": ""})["ok"] is False
        assert server._run_job({"job_id": "ok-1", "workspace_src": "/nonexistent"})["ok"] is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rootfs_builder_refuses_special_files(tmp_path):
    from execution.worker.rootfs import RootfsError, _assert_safe_entry

    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(RootfsError):
        _assert_safe_entry(str(fifo))
    normal = tmp_path / "file.txt"
    normal.write_text("ok")
    _assert_safe_entry(str(normal))  # must not raise


def test_stage_workspace_skips_vcs_and_caches(tmp_path):
    from execution.worker.client import _stage_workspace

    src = tmp_path / "proj"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "config").write_text("secret")
    (src / "node_modules").mkdir()
    (src / "app.py").write_text("print(1)")
    staged = _stage_workspace(src, "job-x")
    try:
        names = sorted(p.name for p in staged.iterdir())
        assert names == ["app.py"]
    finally:
        shutil.rmtree(staged, ignore_errors=True)


# --------------------------------------------------------------------------- #
# boundary tests (require a verified worker)
# --------------------------------------------------------------------------- #
def test_fixture_worker_is_verified(supervisor):
    health = supervisor.health()
    assert health["ok"] and health["verified"], health
    report = supervisor.selfcheck()
    checks = report["boundary"]["result"].get("handshake") or {}
    assert checks.get("landlock", {}).get("abi", 0) >= 1
    assert checks.get("no_new_privs") is True


def test_host_filesystem_is_not_readable(supervisor):
    """Host paths, host /etc content and traversal must all be unreachable.

    ``/etc`` inside the sandbox is a *synthetic* directory containing exactly
    seven generated files; the host's passwd/shadow/keys must not be present.
    """
    secret = os.path.abspath("tests/conftest.py")
    result = _run(supervisor, "fs-escape", f"""
import os, json
report = {{}}
report['abs'] = os.path.exists({secret!r})
report['rel'] = os.path.exists({os.path.relpath(secret)!r})
report['host_home'] = os.path.exists('/home')
report['host_repo'] = os.path.exists('/home/user/phone-ai-agent-team')
report['dotdot_escape'] = os.path.exists('../../../../../../home/user/phone-ai-agent-team/README.md')
report['host_shadow'] = os.path.exists('/etc/shadow')
report['host_ssh'] = os.path.exists('/home/user/.ssh')
report['synthetic_etc'] = sorted(os.listdir('/etc')) if os.path.isdir('/etc') else []
print(json.dumps(report))
""")
    assert result["ok"], result
    payload = json.loads(result["stdout"].strip().splitlines()[-1])
    assert payload["abs"] is False
    assert payload["rel"] is False
    assert payload["host_home"] is False
    assert payload["host_repo"] is False
    assert payload["dotdot_escape"] is False
    assert payload["host_shadow"] is False
    assert payload["host_ssh"] is False
    assert set(payload["synthetic_etc"]) <= {
        "group", "hostname", "hosts", "ld.so.cache", "nsswitch.conf", "passwd", "resolv.conf"
    }, payload["synthetic_etc"]


def test_workspace_is_writable_and_isolated(supervisor):
    result = _run(supervisor, "ws-write", """
import os
open('/workspace/created.txt','w').write('hello')
os.makedirs('/workspace/sub/dir', exist_ok=True)
open('/workspace/sub/dir/deep.txt','w').write('deep')
print(len(open('/workspace/created.txt').read()))
""")
    assert result["ok"], result
    assert result["stdout"].strip() == "5"
    assert result["workspace_bytes"] > 0


def test_writes_inside_sandbox_do_not_touch_host_workspace(supervisor):
    workspace = _job_dir(supervisor, "host-write", {"job.py": """
open('/workspace/mutated.txt','w').write('sandbox only')
print('done')
"""})
    result = supervisor.run_job(job_id="host-write", project_id="test-project",
                                workspace=workspace,
                                argv=["/usr/bin/python3", "/workspace/job.py"],
                                timeout_seconds=120)
    assert result["ok"], result
    assert not os.path.exists(os.path.join(workspace, "mutated.txt")), \
        "payload must not write the host workspace (it runs on a staged copy)"


@pytest.mark.parametrize("target", ["/etc/passwd", "/usr/bin/python3", "/tmp/../etc"])
def test_cannot_write_outside_workspace(supervisor, target):
    """Writes outside the workspace must fail for every one of these targets."""
    result = _run(supervisor, f"write-out-{abs(hash(target)) % 1000}", f"""
outcomes = []
for target in ({target!r}, '/etc/passwd', '/usr/bin/python3', '/workspace/../escape.txt'):
    try:
        open(target, 'a').write('x')
        outcomes.append(('WROTE', target))
    except OSError as exc:
        outcomes.append((type(exc).__name__, target))
print(outcomes)
""")
    assert result["ok"], result
    assert "WROTE" not in result["stdout"], result["stdout"]


def test_symlink_escape_is_denied(supervisor):
    """A symlink out of the workspace must not grant write access to /etc.

    Reading ``/etc/hostname`` through the link is legitimate (that is the
    sandbox's *own* synthetic /etc); what must fail is writing through it.
    """
    result = _run(supervisor, "symlink-escape", """
import os
os.symlink('/etc', '/workspace/etc_link')
try:
    open('/workspace/etc_link/passwd', 'w').write('x')
    print('WROTE_PASSWD')
except OSError as exc:
    print('DENIED_WRITE', type(exc).__name__)
try:
    open('/workspace/etc_link/new_file', 'w').write('x')
    print('WROTE_NEW')
except OSError as exc:
    print('DENIED_NEW', type(exc).__name__)
try:
    print('PASSWD', open('/workspace/etc_link/passwd').read()[:40].strip())
except OSError as exc:
    print('PASSWD_DENIED', type(exc).__name__)
""")
    assert result["ok"], result
    assert "WROTE_PASSWD" not in result["stdout"]
    assert "WROTE_NEW" not in result["stdout"]
    # The sandbox's synthetic passwd is readable; the host's is not.
    assert "PASSWD root:x:0:0:job:/workspace" in result["stdout"] or "PASSWD_DENIED" in result["stdout"]


def test_network_egress_is_blocked(supervisor):
    result = _run(supervisor, "network", """
import socket
codes = {}
for label, addr in (('dns', ('1.1.1.1', 53)), ('http', ('93.184.216.34', 80)),
                    ('https', ('140.82.121.4', 443)), ('local', ('127.0.0.1', 8000))):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect(addr); codes[label] = 'CONNECTED'
    except OSError as exc:
        codes[label] = type(exc).__name__
    finally:
        s.close()
print(codes)
""")
    assert result["ok"], result
    assert "CONNECTED" not in result["stdout"], result["stdout"]


def test_signal_to_other_process_is_denied(supervisor):
    """Without a PID namespace the payload shares host PIDs; signalling is blocked."""
    handshake = (supervisor.selfcheck().get("boundary") or {}).get("result", {}).get("handshake") or {}
    result = _run(supervisor, "signals", """
import os
try:
    os.kill(os.getppid(), 0)
    print('PARENT_OK')
except OSError as exc:
    print('PARENT_DENIED', exc.errno)
try:
    os.kill(os.getpid(), 0)
    print('SELF_OK')
except OSError:
    print('SELF_DENIED')
try:
    os.kill(1, 0)
    print('INIT_OK')
except OSError:
    print('INIT_DENIED')
""")
    assert result["ok"], result
    assert "SELF_OK" in result["stdout"]
    if handshake.get("pidns") is False:
        assert "PARENT_OK" not in result["stdout"]
        assert "INIT_OK" not in result["stdout"]


def test_cannot_unshare_nested_namespaces(supervisor):
    result = _run(supervisor, "nested-ns", """
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
print('unshare_rc', libc.unshare(0x10000000))
print('mount_rc', libc.mount(b'/tmp', b'/mnt', None, 0, None))
print('chroot_rc', libc.chroot(b'/tmp'))
""")
    assert result["ok"], result
    assert "unshare_rc -1" in result["stdout"]
    assert "mount_rc -1" in result["stdout"]
    assert "chroot_rc -1" in result["stdout"]


def test_memory_exhaustion_is_contained(supervisor):
    result = _run(supervisor, "oom", """
blocks = []
try:
    for _ in range(200):
        blocks.append(b'x' * (4 * 1024 * 1024))
    print('ALLOCATED', len(blocks))
except MemoryError:
    print('MEMORY_ERROR')
""", memory_mb=128)
    assert result["stdout"].strip().startswith(("MEMORY_ERROR", "ALLOCATED"))
    if result["stdout"].strip().startswith("ALLOCATED"):
        allocated = int(result["stdout"].split()[-1])
        assert allocated < 200, "address-space limit was not enforced"
    assert not result["timed_out"]


def test_file_size_limit_is_enforced(supervisor):
    result = _run(supervisor, "fsize", """
try:
    with open('/workspace/big.bin','wb') as fh:
        for _ in range(64):
            fh.write(b'x' * (1024 * 1024))
    print('WROTE_BIG')
except OSError as exc:
    print('DENIED', type(exc).__name__)
""")
    assert "WROTE_BIG" not in result["stdout"]


def test_timeout_kills_job_and_reports_it(supervisor):
    started = time.time()
    result = supervisor.run_job(
        job_id="timeout", project_id="test-project",
        workspace=_job_dir(supervisor, "timeout", {"job.py": "import time\nwhile True: time.sleep(0.2)\n"}),
        argv=["/usr/bin/python3", "/workspace/job.py"], timeout_seconds=6,
    )
    elapsed = time.time() - started
    assert result["timed_out"] is True
    assert result["ok"] is False
    assert result["exit_code"] not in (0, None)
    assert elapsed < 90, f"timeout not enforced promptly ({elapsed:.1f}s)"
    assert result.get("cleaned_up") is True


def test_cleanup_removes_job_directory(supervisor):
    result = _run(supervisor, "cleanup", "print('bye')")
    assert result["ok"], result
    assert result.get("cleaned_up") is True
    assert not os.path.exists(os.path.join("/tmp/phone-agent-jobs/jobs", "cleanup"))


def test_cancellation_stops_a_running_job(supervisor):
    import threading

    workspace = _job_dir(supervisor, "cancel", {"job.py": "import time\nwhile True: time.sleep(0.2)\n"})
    outcome: dict = {}

    def _dispatch():
        outcome.update(supervisor.run_job(job_id="cancel", project_id="test-project",
                                          workspace=workspace,
                                          argv=["/usr/bin/python3", "/workspace/job.py"],
                                          timeout_seconds=120))

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()
    time.sleep(3)
    cancel = supervisor.cancel_job("cancel")
    thread.join(timeout=60)
    assert cancel.get("ok") is True, cancel
    assert outcome.get("ok") is False
    assert outcome.get("exit_code") != 0


def test_unauthorized_dispatch_cannot_reach_the_socket(supervisor):
    """A raw client without the supervisor token is refused."""
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect(supervisor.socket_path)
        sock.sendall(b'{"action":"run","job":{"job_id":"evil"}}\n')
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        response = json.loads(data.split(b"\n")[0])
        assert response["ok"] is False
        assert "unauthor" in response.get("error", "").lower()
    finally:
        sock.close()


def test_stale_token_replay_is_rejected(supervisor):
    """Even with the real token, a replayed nonce must fail."""
    token = supervisor._token
    first = supervisor._request({"action": "ping"})
    assert first.get("ok") is True
    # Re-send the identical frame by monkeypatching a fixed nonce.
    import execution.worker.supervisor as sup_mod

    original = sup_mod.uuid.uuid4

    class _Fixed:
        hex = "f" * 32

    sup_mod.uuid.uuid4 = lambda: _Fixed()
    try:
        supervisor._request({"action": "ping"})
        second = supervisor._request({"action": "ping"})
    finally:
        sup_mod.uuid.uuid4 = original
    assert second.get("ok") is False
    assert token  # never logged, only asserted non-empty


def test_cross_project_isolation(supervisor):
    """One project's job cannot read another project's staged workspace."""
    other = _job_dir(supervisor, "project-a-secret", {"secret.txt": "PROJECT_A_SECRET"})
    result = _run(supervisor, "project-b", f"""
import os
print('visible', os.path.exists({other!r}))
print('parent', os.path.exists({os.path.dirname(other)!r}))
""")
    assert result["ok"], result
    assert "visible False" in result["stdout"]
    assert "parent False" in result["stdout"]


def test_worker_crash_is_detected_and_recovered(supervisor):
    pid_before = supervisor._proc.pid
    os.killpg(os.getpgid(pid_before), 9)
    deadline = time.time() + 20
    while time.time() < deadline and supervisor._proc.poll() is None:
        time.sleep(0.2)
    assert supervisor._proc.poll() is not None, "worker crash not observed"
    result = _run(supervisor, "after-crash", "print('recovered')")
    assert result["ok"], result
    assert result["stdout"].strip() == "recovered"
    assert supervisor._proc.pid != pid_before


def _assert_pytest_available(result):
    """Skip (never pass) when the interpreter is a venv without bootstrapped packages."""
    text = f"{result.get('stdout') or ''}{result.get('stderr') or ''}"
    if "No module named pytest" in text or "No module named 'pytest'" in text:
        pytest.skip(
            "pytest is not importable by the sandbox interpreter on this host "
            "(venv-only installation); verify on a deployment image with pytest "
            "installed for the system interpreter"
        )


def test_pytest_suite_runs_inside_sandbox(supervisor):
    from execution.worker.client import WorkerClient

    workspace = _job_dir(supervisor, "pytest-project", {
        "calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    })
    client = WorkerClient(supervisor)
    result = client.run_pytest(project_id="pytest-project", workspace=workspace, timeout_seconds=180)
    _assert_pytest_available(result)
    assert result["ok"], result
    assert "1 passed" in result["stdout"], result["stdout"][-800:]
    assert result["isolation"]["verified"] is True


def test_failing_tests_are_reported_as_failures(supervisor):
    from execution.worker.client import WorkerClient

    workspace = _job_dir(supervisor, "pytest-fail", {
        "calc.py": "def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    })
    client = WorkerClient(supervisor)
    result = client.run_pytest(project_id="pytest-fail", workspace=workspace, timeout_seconds=180)
    _assert_pytest_available(result)
    assert result["ok"] is False
    assert result["exit_code"] != 0
    assert "1 failed" in result["stdout"], result["stdout"][-800:]
