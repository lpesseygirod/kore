"""The box's install and night-update scripts, checked on the Mac.

No real systemd, network, GPIO or uv download: update.sh runs in a temporary folder with stand-ins for systemctl,
uv and the release's Python, so the update / rollback logic itself is exercised.
"""

import configparser
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
PI = DEPLOY / "pi"
SCRIPTS = sorted(DEPLOY.rglob("*.sh"))
UNITS = ["kore-home.service", "kore-web.service", "kore-update.service", "kore-update.timer"]


# --- syntax ------------------------------------------------------------------------------------------------------

def test_the_expected_scripts_exist():
    names = {p.name for p in SCRIPTS}
    assert {"install.sh", "update.sh", "send_release.sh"} <= names


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_and_are_strict(script):
    subprocess.run(["bash", "-n", str(script)], check=True)
    text = script.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck isn't installed here")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shellcheck_clean(script):
    subprocess.run(["shellcheck", "-x", str(script)], check=True)


def _unit(name):
    cp = configparser.ConfigParser(strict=True, interpolation=None)
    cp.optionxform = str  # systemd keys are case-sensitive
    cp.read_string((PI / name).read_text())
    return cp


@pytest.mark.parametrize("name", ["kore-home.service", "kore-web.service"])
def test_long_running_services(name):
    u = _unit(name)
    s = u["Service"]
    assert s["Restart"] == "always" and 1 <= int(s["RestartSec"]) <= 30
    assert u["Unit"]["StartLimitIntervalSec"] == "0"        # never stop restarting
    assert s["User"] == "kore" and s["Group"] == "kore"
    assert s["EnvironmentFile"] == "/etc/kore/kore.env"
    assert s["WorkingDirectory"] == "/var/lib/kore"
    assert s["ExecStart"].startswith("/opt/kore/current/.venv/bin/python -m kore.")
    assert "--db /var/lib/kore/kore.db" in s["ExecStart"]
    assert s["ProtectSystem"] == "strict" and s["ReadWritePaths"] == "/var/lib/kore"
    assert s["StandardOutput"] == "journal"
    assert u["Install"]["WantedBy"] == "multi-user.target"


def test_pepe_starts_without_waiting_for_the_internet():
    assert "network-online" not in _unit("kore-home.service")["Unit"].get("After", "")


def test_the_web_service_names_the_box():
    cmd = _unit("kore-web.service")["Service"]["ExecStart"]
    assert " serve " in cmd and "--name %H" in cmd and "--dev" not in cmd


def test_update_service_and_night_timer():
    s = _unit("kore-update.service")["Service"]
    assert s["Type"] == "oneshot" and s["ExecStart"] == "/usr/local/sbin/kore-update"
    t = _unit("kore-update.timer")
    assert t["Timer"]["OnCalendar"] == "*-*-* 03:30:00"
    assert t["Timer"]["Persistent"] == "false"               # never catch up in the daytime
    assert t["Install"]["WantedBy"] == "timers.target"


def test_install_sets_up_every_unit_and_a_private_keys_file():
    text = (PI / "install.sh").read_text()
    for name in UNITS:
        assert name in text
    assert 'chmod 600 "$ETC/kore.env"' in text
    assert "read -r -s" in text                              # keys are typed hidden
    assert "/var/lib/kore" in text and "/opt/kore/releases" in text



def test_kore_home_accepts_the_pi_flag():
    p = subprocess.run([sys.executable, "-m", "kore.home", "--pi", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "--pi" in p.stdout


# --- update.sh in a sandbox --------------------------------------------------------------------------------------

FAKE_UV = """#!/usr/bin/env bash
set -euo pipefail
echo "uv $*" >> "$KORE_TEST_LOG"
[ "$1" = sync ] || exit 0
[ -e FAIL_BUILD ] && exit 1
mkdir -p .venv/bin
cat > .venv/bin/python <<'PY'
#!/usr/bin/env bash
rel="$(cd "$(dirname "$0")/../.." && pwd)"
echo "python $(basename "$rel") $*" >> "$KORE_TEST_LOG"
[ "$1" = -c ] && exit 0
[ -e "$rel/FAIL_SELFTEST" ] && exit 1
case " $* " in *" --require-web "*) [ -e "$rel/FAIL_AFTER_SWITCH" ] && exit 1 ;; esac
exit 0
PY
chmod +x .venv/bin/python
"""

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$KORE_TEST_LOG"
exit 0
"""


@pytest.fixture
def box(tmp_path):
    root = tmp_path / "box"
    for d in ("opt/releases", "data/incoming", "etc", "bin"):
        (root / d).mkdir(parents=True)
    log = root / "log.txt"
    log.touch()
    for name, body in (("uv", FAKE_UV), ("systemctl", FAKE_SYSTEMCTL)):
        p = root / "bin" / name
        p.write_text(body)
        p.chmod(0o755)
    env = {**os.environ, "KORE_OPT": str(root / "opt"), "KORE_DATA": str(root / "data"),
           "KORE_ETC": str(root / "etc"), "KORE_UV": str(root / "bin" / "uv"),
           "KORE_SYSTEMCTL": str(root / "bin" / "systemctl"), "KORE_SETTLE_S": "0", "KORE_RUN_DIRECT": "1",
           "KORE_PI_PACKAGES": "", "KORE_TEST_LOG": str(log)}
    return root, env, log


def _release(box, version, *markers, checksum=True):
    root, _, _ = box
    src = root / f"src-{version}"
    (src / "kore").mkdir(parents=True, exist_ok=True)
    (src / "pyproject.toml").write_text("[project]\nname='kore'\n")
    (src / "kore" / "selftest.py").write_text("")
    for m in markers:
        (src / m).write_text("")
    tgz = root / "data" / "incoming" / f"kore-{version}.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        for p in sorted(src.rglob("*")):
            tf.add(p, arcname=str(p.relative_to(src)), recursive=False)
    digest = hashlib.sha256(tgz.read_bytes()).hexdigest() if checksum else "0" * 64
    Path(str(tgz) + ".sha256").write_text(f"{digest}  {tgz.name}\n")
    return tgz


def _update(box, *args, hhmm=None):
    _, env, _ = box
    if hhmm:
        env = {**env, "KORE_CLOCK_HHMM": hhmm}
    return subprocess.run(["bash", str(PI / "update.sh"), *args], env=env, capture_output=True, text=True,
                          timeout=60)


def _current(box):
    link = box[0] / "opt" / "current"
    return Path(os.readlink(link)).name if link.is_symlink() else None


def _install_first(box):
    _release(box, "20261101-0100-aaa")
    r = _update(box, "--now")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _current(box) == "20261101-0100-aaa"


def test_first_install(box):
    _install_first(box)
    log = box[2].read_text()
    assert "systemctl restart" not in log  # services start only once the keys are typed
    assert (box[0] / "data" / "incoming" / "done" / "kore-20261101-0100-aaa.tar.gz").exists()


def test_does_nothing_by_day(box):
    _install_first(box)
    _release(box, "20261102-0100-bbb")
    r = _update(box, hhmm="1500")
    assert r.returncode == 0 and "outside the night window" in r.stdout
    assert _current(box) == "20261101-0100-aaa"


def test_nothing_waiting(box):
    r = _update(box, hhmm="0345")
    assert r.returncode == 0 and "no new version" in r.stdout


def test_good_update_at_night(box):
    _install_first(box)
    _release(box, "20261102-0100-bbb")
    r = _update(box, hhmm="0340")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _current(box) == "20261102-0100-bbb"
    log = box[2].read_text()
    assert "python 20261102-0100-bbb -m kore.selftest" in log            # before switching
    assert "--require-web" in log and "systemctl restart kore-home.service kore-web.service" in log
    assert "python 20261102-0100-bbb -c" not in log                      # no database yet: nothing to snapshot


def test_newest_waiting_version_wins(box):
    _install_first(box)
    _release(box, "20261102-0100-bbb")
    _release(box, "20261103-0100-ccc")
    assert _update(box, "--now").returncode == 0
    assert _current(box) == "20261103-0100-ccc"


def test_failing_selftest_before_switch_changes_nothing(box):
    _install_first(box)
    _release(box, "20261102-0100-bbb", "FAIL_SELFTEST")
    box[2].write_text("")
    r = _update(box, "--now")
    assert r.returncode == 1 and "before switching" in r.stdout
    assert _current(box) == "20261101-0100-aaa"
    assert "systemctl restart" not in box[2].read_text()                # Pepe was never interrupted
    assert not (box[0] / "opt" / "releases" / "20261102-0100-bbb").exists()
    assert (box[0] / "data" / "incoming" / "rejected" / "kore-20261102-0100-bbb.tar.gz").exists()


def test_failing_after_switch_rolls_back(box):
    _install_first(box)
    _release(box, "20261102-0100-bbb", "FAIL_AFTER_SWITCH")
    box[2].write_text("")
    r = _update(box, "--now")
    assert r.returncode == 1 and "going back" in r.stdout and "healthy again" in r.stdout
    assert _current(box) == "20261101-0100-aaa"
    log = box[2].read_text()
    assert log.count("systemctl restart") == 2                           # once forward, once back
    assert "python 20261101-0100-aaa -m kore.selftest --db" in log        # the old one checked again
    # the same version is never tried twice
    _release(box, "20261102-0100-bbb")
    r = _update(box, "--now")
    assert r.returncode == 1 and "taken back" in r.stdout and _current(box) == "20261101-0100-aaa"


def test_a_half_copied_file_is_refused(box):
    _install_first(box)
    _release(box, "20261102-0100-bbb", checksum=False)
    r = _update(box, "--now")
    assert r.returncode == 1 and "checksum" in r.stdout
    assert _current(box) == "20261101-0100-aaa"


def test_a_failed_build_changes_nothing(box):
    _install_first(box)
    _release(box, "20261102-0100-bbb", "FAIL_BUILD")
    r = _update(box, "--now")
    assert r.returncode == 1 and "Python environment failed" in r.stdout
    assert _current(box) == "20261101-0100-aaa"
    assert not (box[0] / "opt" / "releases" / "20261102-0100-bbb").exists()


def test_the_database_is_snapshotted_before_switching(box):
    _install_first(box)
    (box[0] / "data" / "kore.db").write_text("")
    _release(box, "20261102-0100-bbb")
    assert _update(box, "--now").returncode == 0
    assert "python 20261102-0100-bbb -c" in box[2].read_text()


# --- send_release.sh ---------------------------------------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "deploy").mkdir(parents=True)
    shutil.copy(DEPLOY / "send_release.sh", r / "deploy" / "send_release.sh")
    (r / "pyproject.toml").write_text("[project]\n")
    (r / ".gitignore").write_text(".env\n")
    (r / ".env").write_text("NOT_A_REAL_KEY=1\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "x")
    return r


def _send(repo, tmp_path):
    env = {**os.environ, "KORE_DRY_RUN": "1", "TMPDIR": str(tmp_path)}
    return subprocess.run(["bash", str(repo / "deploy" / "send_release.sh")], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=60)


def test_send_release_builds_a_clean_archive(repo, tmp_path):
    (repo / "uncommitted.txt").write_text("x")
    r = _send(repo, tmp_path)
    assert r.returncode == 0, r.stderr
    tgz = Path(r.stdout.strip())
    assert tgz.name.startswith("kore-") and tgz.name.endswith(".tar.gz")
    with tarfile.open(tgz) as tf:
        names = tf.getnames()
    assert "pyproject.toml" in names and ".env" not in names and "uncommitted.txt" not in names
    digest = Path(str(tgz) + ".sha256").read_text().split()[0]
    assert digest == hashlib.sha256(tgz.read_bytes()).hexdigest()


def test_send_release_refuses_a_committed_secret(repo, tmp_path):
    _git(repo, "add", "-f", ".env")
    _git(repo, "commit", "-q", "-m", "oops")
    r = _send(repo, tmp_path)
    assert r.returncode == 1 and "Refused" in r.stderr
