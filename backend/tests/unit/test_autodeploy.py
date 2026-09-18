"""Execute a sandboxed copy of the timer script: no network or real server writes."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

BASH = os.environ.get('PARKING_TEST_BASH') or (shutil.which('bash') if os.name != 'nt' else None)
pytestmark = pytest.mark.skipif(not BASH, reason='POSIX bash required (or PARKING_TEST_BASH)')


@pytest.mark.parametrize('pending,remote,dirty,locked,expected', [
    (False,'same',False,False,False),
    (True,'same',False,False,True),
    (False,'new',False,False,True),
    (True,'new',True,False,False),
    (True,'new',False,True,False),
])
def test_timer_retries_pending_release_and_respects_guards(tmp_path,pending,remote,dirty,locked,expected):
    root = Path(__file__).resolve().parents[3]
    (tmp_path/'.git').mkdir()
    (tmp_path/'deploy').mkdir()
    (tmp_path/'deploy/update.sh').write_text('printf "%s" "$PARKING_DEPLOY_TARGET" > invoked\n')
    if pending:
        (tmp_path/'.git/parking-deploy-pending').write_text('old')
    # Shell functions replace external commands. The child script above only
    # records its target; production update.sh is never executed by this test.
    prelude = '''
git() {
  case "$*" in
    "rev-parse --abbrev-ref HEAD") echo main;;
    "rev-parse HEAD") echo same;;
    "rev-parse origin/main") echo "$TEST_REMOTE";;
    "diff --quiet"|"diff --cached --quiet") return "$TEST_DIRTY";;
    "fetch origin main --quiet") return 0;;
    *) return 99;;
  esac
}
flock() { return "$TEST_LOCKED"; }
timeout() { shift; "$@"; }
'''
    script = (root/'deploy/autodeploy.sh').read_text(encoding='utf-8')
    script = script.replace('cd /root/PARKING', 'cd "$TEST_REPO"')
    script = script.replace('/run/lock/parking-deploy.lock', '"$TEST_REPO/deploy.lock"')
    run = tmp_path/'timer-test.sh'
    run.write_text(prelude+script, encoding='utf-8', newline='\n')
    env = dict(os.environ, TEST_REPO=tmp_path.as_posix(), TEST_REMOTE=remote,
               TEST_DIRTY=str(int(dirty)), TEST_LOCKED=str(int(locked)))
    result = subprocess.run([BASH, run.as_posix()], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (tmp_path/'invoked').exists() == expected
    if expected:
        assert (tmp_path/'invoked').read_text() == remote
