import contextlib
from concurrent.futures import ThreadPoolExecutor
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "codex-quota-resumer"
loader = importlib.machinery.SourceFileLoader("quota_resumer", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
resumer = importlib.util.module_from_spec(spec)
loader.exec_module(resumer)


def event(kind, turn_id, **fields):
    return {"type": "event_msg", "payload": {"type": kind, "turn_id": turn_id, **fields}}


def quota_failure(turn_id="failed"):
    return event("task_complete", turn_id, error={"codex_error_info": "usage_limit_exceeded"})


class QuotaResumerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.work = self.home / "quota-resumer"
        self.state = self.work / "state.json"
        self.config = self.work / "config.json"
        self.rollout = self.home / "rollout.jsonl"
        self.cfg = dict(resumer.DEFAULT_CONFIG, executor="exec", transport="stdio",
                        codex_bin=str(self.home / "missing-codex"))
        self.item = {"thread_id": "thread", "turn_id": "failed", "cwd": str(self.home),
                     "rollout_path": str(self.rollout)}
        paths = mock.patch.multiple(
            resumer, WORK_DIR=str(self.work), STATE_PATH=str(self.state),
            CONFIG_PATH=str(self.config), LOCK_PATH=str(self.work / "resumer.lock"),
            LOG_PATH=str(self.work / "resumer.log"), RUNS_DIR=str(self.work / "runs"),
        )
        paths.start()
        self.addCleanup(paths.stop)
        log = mock.patch.object(resumer, "log")
        log.start()
        self.addCleanup(log.stop)

    def write_rollout(self, *events):
        self.rollout.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              env={**os.environ, "CODEX_HOME": str(self.home)},
                              capture_output=True, text=True, timeout=10)

    def write_indexes(self, *thread_ids):
        with contextlib.closing(sqlite3.connect(self.home / "state_5.sqlite")) as state:
            with state:
                state.execute("""CREATE TABLE threads (
                    id, cwd, title, source, originator, archived, updated_at, rollout_path, tokens_used
                )""")
                for tid in thread_ids:
                    state.execute("INSERT INTO threads VALUES (?, ?, '', 'cli', '', 0, ?, ?, 0)",
                                  (tid, str(self.home), resumer.now_ts(), str(self.rollout)))
        with contextlib.closing(sqlite3.connect(self.home / "thread_history_1.sqlite")) as history:
            with history:
                history.execute("CREATE TABLE thread_turns (thread_id, turn_id, status, error_json, started_at, completed_at)")
                for tid in thread_ids:
                    history.execute("INSERT INTO thread_turns VALUES (?, 'failed', 'failed', ?, 1, 2)",
                                    (tid, '{"codex_error_info":"usage_limit_exceeded"}'))

    def test_only_explicit_boolean_allowance_schedules_a_resume(self):
        self.write_rollout(quota_failure())
        for source, allowed, expected in (
            ("rollout", None, False), ("rpc", None, False), ("rpc", "true", False),
            ("rpc", False, False), ("rpc", True, True),
        ):
            with self.subTest(source=source, allowed=allowed):
                snap = {"source": source, "ordinary_usage_allowed": allowed, "primary": {"usedPercent": 60}}
                with mock.patch.object(resumer, "snapshot", return_value=snap), \
                     mock.patch.object(resumer, "find_paused_threads", return_value=[self.item.copy()]), \
                     mock.patch.object(resumer, "spawn_resume") as spawn:
                    resumer.run_loop(self.cfg, once=True, only_thread=None, dry_run=False)
                self.assertEqual(spawn.called, expected)

    def test_rpc_transport_fallback_keeps_the_permission_field(self):
        reader = resumer.RateLimitReader(dict(self.cfg, transport="ws"))
        with mock.patch.object(reader, "_call", side_effect=[None, {"ordinaryUsageAllowed": True}]) as call:
            self.assertIs(reader.read()["ordinary_usage_allowed"], True)
        self.assertEqual(call.call_args_list, [mock.call("ws"), mock.call("stdio")])
        with mock.patch.object(reader, "_call", return_value={"rateLimits": {"primary": {"usedPercent": 35}}}) as call:
            self.assertTrue(resumer.usage_blocked(reader.read()))
            call.assert_called_once_with("ws")

    def test_stdio_initializes_and_reads_a_real_json_rpc_exchange(self):
        fake = self.home / "fake-codex"
        fake.write_text(f"""#!{sys.executable}
import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    method = message["method"]
    if method == "initialize":
        print(json.dumps({{"id": message["id"], "result": {{"userAgent": "fake"}}}}), flush=True)
    elif method == "account/rateLimits/read":
        print(json.dumps({{"id": message["id"], "result": {{"ordinaryUsageAllowed": True}}}}), flush=True)
""", encoding="utf-8")
        fake.chmod(0o700)
        snap = resumer.RateLimitReader(dict(self.cfg, codex_bin=str(fake), rpc_timeout_seconds=2)).read()
        self.assertIs(snap["ordinary_usage_allowed"], True)

    def test_force_allowed_requires_dry_run_and_creates_no_runtime_files(self):
        self.assertEqual(self.cli("--once", "--force-allowed").returncode, 2)
        result = self.cli("--once", "--dry-run", "--force-allowed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[simulation] allowed=True", result.stdout)
        self.assertFalse(self.work.exists())

    def test_dry_run_leaves_existing_runtime_state_unchanged(self):
        self.write_rollout(quota_failure())
        self.write_indexes("thread", "parked")
        self.work.mkdir()
        self.config.write_text(json.dumps(self.cfg), encoding="utf-8")
        saved_state = json.dumps({"threads": {
            "parked": {"status": "finished", "last_failed_turn_id": "failed", "attempts": 3},
            "running": {"status": "running", "pid": 999999999, "resumed_at": 1},
        }}).encode()
        self.state.write_bytes(saved_state)
        result = self.cli("--once", "--dry-run", "--force-allowed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[dry-run][exec] 续跑 thread", result.stdout)
        self.assertIn("parked", result.stdout)
        self.assertEqual(self.state.read_bytes(), saved_state)
        self.assertEqual(set(self.work.iterdir()), {self.state, self.config})

    def test_reconcile_only_signals_tracked_process_groups(self):
        self.work.mkdir()
        record = {"status": "running", "pid": 12345, "resumed_at": 1,
                  "pid_start_ticks": 100, "pid_boot_id": "this-boot"}
        cfg = dict(self.cfg, resume_timeout_seconds=1)
        for entry, pid_ticks, dry_run, signals in (
            (record, 100, True, []),
            (record, 999, False, []),
            (dict(record, pid_boot_id="previous-boot"), 100, False, []),
            (dict(record, pid_start_ticks=None, pid_boot_id=None), 100, False, []),
            (record, 100, False, [signal.SIGTERM, signal.SIGKILL]),
        ):
            with self.subTest(entry=entry, pid_ticks=pid_ticks, dry_run=dry_run):
                with mock.patch.object(resumer, "boot_id", return_value="this-boot"), \
                     mock.patch.object(resumer, "read_process", return_value=("alive", pid_ticks)), \
                     mock.patch.object(resumer.os, "getpgid", return_value=12345), \
                     mock.patch.object(resumer.os, "killpg") as kill, \
                     mock.patch.object(resumer.time, "sleep"):
                    resumer.reconcile_running({"threads": {"thread": entry.copy()}}, cfg, dry_run=dry_run)
                self.assertEqual(kill.call_args_list, [mock.call(12345, sig) for sig in signals])

    def test_rollout_must_confirm_the_same_failed_turn(self):
        self.assertFalse(resumer.rollout_still_paused(str(self.rollout), "failed")[0])
        self.write_rollout(quota_failure(), {"type": "response_item", "payload": "x" * 300_000})
        self.assertTrue(resumer.rollout_still_paused(str(self.rollout), "failed")[0])
        self.assertFalse(resumer.rollout_still_paused(str(self.rollout), "old")[0])
        for later in (event("task_started", "manual"), event("turn_aborted", "manual"),
                      quota_failure("new"), event("task_complete", "manual")):
            with self.subTest(later=later):
                self.write_rollout(quota_failure(), later)
                self.assertFalse(resumer.rollout_still_paused(str(self.rollout), "failed")[0])
        with self.rollout.open("ab") as fh:
            fh.write(b'{"type":"event_msg","payload":')
        self.assertFalse(resumer.rollout_still_paused(str(self.rollout), "failed")[0])

    def test_thread_already_monitored_cannot_take_a_second_slot(self):
        self.write_rollout(quota_failure())
        state = {"threads": {"thread": {"status": "running", "last_failed_turn_id": "old", "pid": 42}}}
        with mock.patch.object(resumer, "find_paused_threads", return_value=[self.item]):
            _, ready, skipped = resumer.collect_targets(self.cfg, state, {"ordinary_usage_allowed": True})
        self.assertFalse(ready)
        self.assertIn("仍在运行", skipped[0][1])

    def test_remote_rechecks_rollout_after_thread_resume(self):
        self.write_rollout(quota_failure())
        client = mock.Mock()

        def rpc(method, params, timeout):
            self.assertEqual(method, "thread/resume")
            self.write_rollout(quota_failure(), event("turn_aborted", "manual"))
            return True, {}

        client.call.side_effect = rpc
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(resumer, "AppServerClient", return_value=client):
            code = resumer.run_remote_turn(self.cfg, "thread", "failed", str(self.rollout), "继续", str(self.home))
        self.assertEqual(code, 4)
        self.assertEqual(client.call.call_count, 1)
        client.stop.assert_called_once()

    def test_remote_waits_for_its_own_completion_and_reports_status(self):
        self.write_rollout(quota_failure())
        for status, code in (("completed", 0), ("failed", 5), ("interrupted", 5)):
            with self.subTest(status=status):
                client = mock.Mock()
                client.call.side_effect = [(True, {}), (True, {"turn": {"id": "started"}})]
                client.next_message.side_effect = [
                    {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "other", "status": "failed"}}},
                    {"method": "error", "params": {"threadId": "thread", "turnId": "other", "willRetry": False}},
                    {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "started", "status": status}}},
                ]
                with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(resumer, "AppServerClient", return_value=client):
                    result = resumer.run_remote_turn(self.cfg, "thread", "failed", str(self.rollout), "继续", str(self.home))
                self.assertEqual(result, code)
                self.assertEqual(client.next_message.call_count, 3)

    def test_once_spawns_a_worker_that_resumes_through_the_daemon(self):
        self.write_rollout(quota_failure())
        self.write_indexes("thread")
        self.work.mkdir()
        sock_path = self.home / "daemon.sock"
        self.config.write_text(json.dumps(dict(self.cfg, executor="daemon", transport="ws",
                                               daemon_sock=str(sock_path), rpc_timeout_seconds=2)),
                               encoding="utf-8")

        def receive(ws, expected_method):
            message = json.loads(ws.recv_text(timeout=3))
            self.assertEqual(message["method"], expected_method)
            return message

        def send(conn, message):
            payload = json.dumps(message).encode()
            length = bytes([len(payload)]) if len(payload) < 126 else b"\x7e" + struct.pack(">H", len(payload))
            conn.sendall(b"\x81" + length + payload)

        def serve(listener):
            for session in ("quota", "worker"):
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(3)
                    header = b""
                    while not header.endswith(b"\r\n\r\n"):
                        byte = conn.recv(1)
                        self.assertTrue(byte, "client closed during handshake")
                        header += byte
                    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
                    ws = resumer.WsUnix("")
                    ws.sock = conn
                    request = receive(ws, "initialize")
                    send(conn, {"id": request["id"], "result": {"userAgent": "fake"}})
                    receive(ws, "initialized")
                    if session == "quota":
                        request = receive(ws, "account/rateLimits/read")
                        send(conn, {"id": request["id"], "result": {"ordinaryUsageAllowed": True}})
                    else:
                        request = receive(ws, "thread/resume")
                        self.assertEqual(request["params"]["threadId"], "thread")
                        send(conn, {"id": request["id"], "result": {}})
                        request = receive(ws, "turn/start")
                        self.assertEqual(request["params"]["input"][0]["text"], "继续")
                        for turn_id, status in (("other", "failed"), ("started", "completed")):
                            send(conn, {"method": "turn/completed", "params": {
                                "threadId": "thread", "turn": {"id": turn_id, "status": status},
                            }})
                        send(conn, {"id": request["id"], "result": {"turn": {"id": "started"}}})
                    self.assertEqual(conn.recv(1), b"")

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener, ThreadPoolExecutor(max_workers=1) as pool:
            listener.bind(str(sock_path))
            listener.listen(2)
            listener.settimeout(3)
            exchange = pool.submit(serve, listener)
            result = self.cli("--once")
            self.assertEqual(result.returncode, 0, result.stderr)
            exchange.result(timeout=7)
        entry = json.loads(self.state.read_bytes())["threads"]["thread"]
        logfile = Path(entry["log"])
        self.assertIsNotNone(entry["pid_start_ticks"])
        self.assertEqual(stat.S_IMODE(logfile.stat().st_mode), 0o600)
        self.assertIn("turn/completed status=completed", logfile.read_text(encoding="utf-8"))
        self.assertEqual(list(logfile.parent.glob("*.prompt")), [])

    def test_invalid_config_stops_before_creating_state(self):
        self.work.mkdir()
        for content in ('{"dry_run": true,', '{"dry_run": "true"}', '{"poll_seconds_near_reset": 0}'):
            with self.subTest(content=content):
                self.config.write_text(content, encoding="utf-8")
                result = self.cli("--once")
                self.assertEqual(result.returncode, 2)
                self.assertIn("无法读取配置", result.stderr)
                self.assertEqual(set(self.work.iterdir()), {self.config})

    def test_websocket_stop_releases_reader_and_peer(self):
        local, peer = socket.socketpair()
        self.addCleanup(peer.close)
        self.addCleanup(local.close)
        client = resumer.AppServerClient(self.cfg, mode="ws")
        client.ws = resumer.WsUnix("")
        client.ws.sock = local
        entered = threading.Event()
        read_exact = client.ws._read_exact

        def observe_read(size):
            entered.set()
            return read_exact(size)

        with mock.patch.object(client.ws, "_read_exact", side_effect=observe_read):
            client.reader = threading.Thread(target=client._ws_reader, daemon=True)
            client.reader.start()
            self.assertTrue(entered.wait(1))
            client.reader.join(timeout=0.05)
            client.stop()
        peer.settimeout(1)
        self.assertEqual(peer.recv(1), b"")
        self.assertFalse(client.reader.is_alive())

    def test_private_permissions_apply_to_new_and_existing_runtime_files(self):
        prior_umask = os.umask(0o002)
        self.addCleanup(os.umask, prior_umask)
        resumer.save_state({"threads": {}})
        self.assertEqual(stat.S_IMODE(self.work.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        runs = self.work / "runs"
        runs.mkdir(mode=0o775)
        logfile = runs / "older.log"
        logfile.write_text("fixture", encoding="utf-8")
        self.state.chmod(0o664)
        with resumer.acquire_lock():
            resumer.secure_runtime_files()
        for path in (self.state, self.work / "resumer.lock", logfile):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(runs.stat().st_mode), 0o700)

    def test_near_reset_uses_configured_poll_interval(self):
        cfg = dict(self.cfg, poll_seconds_near_reset=2)
        snap = {"ordinary_usage_allowed": False, "primary": {"usedPercent": 100, "resetsAt": 100}}
        with mock.patch.object(resumer, "snapshot", return_value=snap), \
             mock.patch.object(resumer, "now_ts", return_value=120), \
             mock.patch.object(resumer.time, "sleep", side_effect=InterruptedError) as sleep:
            with self.assertRaises(InterruptedError):
                resumer.run_loop(cfg, once=False, only_thread=None, dry_run=True)
        sleep.assert_called_once_with(2)

    def test_install_stops_when_enabling_service_fails(self):
        repo = self.home / "repo"
        for rel in ("install.sh", "bin/codex-quota-resumer", "systemd/codex-quota-resumer.service"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, repo / rel)
        stub = self.home / "systemctl"
        stub.write_text("#!/bin/sh\ncase \"$2\" in daemon-reload) exit 0 ;; enable) exit 7 ;; *) exit 70 ;; esac\n",
                        encoding="utf-8")
        stub.chmod(0o700)
        result = subprocess.run(["bash", str(repo / "install.sh")],
                                env={**os.environ, "HOME": str(self.home),
                                     "PATH": str(self.home) + os.pathsep + os.environ["PATH"]},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("未能启用", result.stderr)
        self.assertNotIn("完成", result.stdout)


if __name__ == "__main__":
    unittest.main()
