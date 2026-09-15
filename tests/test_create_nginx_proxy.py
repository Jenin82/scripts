"""Isolated regression coverage; never downloads files or controls host services."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/create_nginx_proxy.sh"
SOURCE = (
    "https://raw.githubusercontent.com/certbot/certbot/"
    "485649333422392901e7ef891630f0129985df8e/certbot/src/certbot"
)
URLS = {
    "options-ssl-nginx.conf": SOURCE
    + "/_internal/plugins/nginx/tls_configs/options-ssl-nginx.conf",
    "ssl-dhparams.pem": SOURCE + "/ssl-dhparams.pem",
}

# These executables are first in PATH. The sudo shim never elevates privileges
# and only allows the commands and temporary paths this script needs.
SHIM = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path(os.environ["TEST_ROOT"])
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps([name, *args]) + "\n")
if name == "sudo":
    allowed = {"mkdir", "mktemp", "wget", "test", "chmod", "mv", "rm", "bash", "ln", "nginx", "systemctl"}
    assert args[0] in allowed, args
    for arg in args[1:]:
        if arg.startswith("/"):
            assert Path(arg).is_relative_to(root), args
    if args[0] == "bash":
        assert args[1] == "-c" and args[2].startswith("cat > "), args
        target = args[2][len("cat > "):]
        assert target == str(root / "etc/nginx/sites-available/example.test"), args
    os.execvp(args[0], args)
elif name == "wget":
    assert len(args) == 3 and args[1] == "-O", args
    target = Path(args[2])
    assert target.is_relative_to(root), args
    urls = json.loads(os.environ["TEST_URLS"])
    filename = next((key for key in urls if target.name.startswith(key + ".")), None)
    assert filename is not None and args[0] == urls[filename], args
    mode = os.environ.get("TEST_DOWNLOAD_MODE", "success")
    if filename == os.environ.get("TEST_FAIL_FILE"):
        if mode == "partial_failure":
            target.write_text("incomplete download\n")
            sys.exit(8)
        if mode == "empty_success":
            target.write_text("")
            sys.exit(0)
    target.write_text("downloaded " + filename + "\n")
elif name == "nginx":
    assert args == ["-t"], args
elif name == "systemctl":
    assert args == ["reload", "nginx"], args
else:
    raise AssertionError(name)
'''


class CreateNginxProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nginx-proxy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ssl_dir = self.root / "etc/letsencrypt"
        self.config = self.root / "etc/nginx/sites-available/example.test"
        self.enabled = self.root / "etc/nginx/sites-enabled/example.test"
        self.config.parent.mkdir(parents=True)
        self.enabled.parent.mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for command in ("sudo", "wget", "nginx", "systemctl"):
            executable = self.bin / command
            executable.write_text(f"#!{sys.executable}\n" + SHIM)
            executable.chmod(0o755)

        source = SCRIPT.read_text().replace("/etc/", str(self.root / "etc") + "/")
        # A new hard-coded host path must be addressed before this suite runs it.
        self.assertNotIn("/etc/", source.replace(str(self.root / "etc") + "/", "SANDBOX/"))
        self.script = self.root / "create_nginx_proxy.sh"
        self.script.write_text(source)
        self.env = dict(os.environ)
        self.env.update(
            PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin",
            TEST_ROOT=str(self.root),
            TEST_URLS=json.dumps(URLS),
        )

    def run_script(self, *args, fail_file=None, mode="success"):
        env = dict(self.env, TEST_DOWNLOAD_MODE=mode, TEST_FAIL_FILE=fail_file or "")
        return subprocess.run(
            ["/bin/bash", str(self.script), *args],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def calls(self, command):
        log = self.root / "calls.jsonl"
        entries = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [entry[1:] for entry in entries if entry[0] == command]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.enabled.is_symlink())
        self.assertEqual(self.enabled.resolve(), self.config)
        config = self.config.read_text()
        self.assertIn("server_name example.test;", config)
        self.assertIn("proxy_pass http://localhost:8000;", config)
        self.assertIn("return 301 https://$host$request_uri;", config)
        self.assertIn(f"ssl_dhparam {self.ssl_dir}/ssl-dhparams.pem;", config)
        self.assertEqual(self.calls("nginx"), [["-t"]])
        self.assertEqual(self.calls("systemctl"), [["reload", "nginx"]])
        self.assert_no_temp_files()

    def assert_no_temp_files(self):
        if self.ssl_dir.exists():
            self.assertLessEqual({path.name for path in self.ssl_dir.iterdir()}, set(URLS))

    def test_missing_directory_and_files_download_then_create_site(self):
        self.assertFalse(self.ssl_dir.exists())
        result = self.run_script("example.test", "http://localhost:8000")
        self.assert_success(result)
        self.assertEqual(len(self.calls("wget")), 2)
        for filename in URLS:
            destination = self.ssl_dir / filename
            self.assertEqual(destination.read_text(), f"downloaded {filename}\n")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o644)

    def test_empty_files_from_previous_failure_are_replaced(self):
        self.ssl_dir.mkdir()
        for filename in URLS:
            (self.ssl_dir / filename).touch()
        result = self.run_script("example.test", "http://localhost:8000")
        self.assert_success(result)
        self.assertEqual(len(self.calls("wget")), 2)
        for filename in URLS:
            self.assertEqual((self.ssl_dir / filename).read_text(), f"downloaded {filename}\n")

    def test_existing_nonempty_files_keep_content_and_permissions(self):
        self.ssl_dir.mkdir()
        for filename in URLS:
            destination = self.ssl_dir / filename
            destination.write_text("existing local customization\n")
            destination.chmod(0o600)
        result = self.run_script("example.test", "http://localhost:8000")
        self.assert_success(result)
        self.assertEqual(self.calls("wget"), [])
        for filename in URLS:
            destination = self.ssl_dir / filename
            self.assertEqual(destination.read_text(), "existing local customization\n")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_failed_or_empty_download_never_installs_or_configures(self):
        for filename in URLS:
            for mode in ("partial_failure", "empty_success"):
                for existing_empty in (False, True):
                    with self.subTest(filename=filename, mode=mode, existing_empty=existing_empty):
                        # Each scenario gets its own paths and command log.
                        with self.__class__("runTest") as case:
                            case.ssl_dir.mkdir()
                            other = next(name for name in URLS if name != filename)
                            (case.ssl_dir / other).write_text("preserved\n")
                            destination = case.ssl_dir / filename
                            if existing_empty:
                                destination.touch()
                            result = case.run_script(
                                "example.test", "http://localhost:8000", fail_file=filename, mode=mode
                            )
                            self.assertNotEqual(result.returncode, 0)
                            self.assertEqual(len(case.calls("wget")), 1)
                            if existing_empty:
                                self.assertEqual(destination.read_bytes(), b"")
                            else:
                                self.assertFalse(destination.exists())
                            self.assertEqual((case.ssl_dir / other).read_text(), "preserved\n")
                            self.assertFalse(case.config.exists())
                            self.assertFalse(case.enabled.is_symlink())
                            self.assertEqual(case.calls("nginx"), [])
                            self.assertEqual(case.calls("systemctl"), [])
                            case.assert_no_temp_files()

    def test_usage_exits_before_any_side_effects(self):
        for args in ((), ("example.test",)):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Usage:", result.stdout)
                self.assertFalse((self.root / "calls.jsonl").exists())
                self.assertFalse(self.ssl_dir.exists())
                self.assertFalse(self.config.exists())

    def __enter__(self):
        self.setUp()
        return self

    def __exit__(self, *exc):
        self.doCleanups()


if __name__ == "__main__":
    unittest.main()
