"""Service state regressions; never run a real ACME client or service command."""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[3]
GIT_BASH=Path(r'C:\Program Files\Git\bin\bash.exe')
BASH=str(GIT_BASH) if os.name=='nt' and GIT_BASH.is_file() else shutil.which('bash')
pytestmark=pytest.mark.skipif(not BASH,reason='Bash is required for deployment script regressions')

def shell_path(path):
    value=str(path).replace('\\','/')
    if os.name=='nt' and value[1:2]==':':return '/'+value[0].lower()+value[2:]
    return value

def run_script(tmp_path,code,**env):
    path=tmp_path/'test.sh';path.write_text(code,newline='\n')
    return subprocess.run([BASH,str(path)],capture_output=True,text=True,timeout=15,
        env={**os.environ,'FIXTURE':shell_path(tmp_path),**env})

@pytest.mark.parametrize('hook,code,state',[
    ('systemctl reload nginx',1,'inactive'),
    ('nginx -t && systemctl reload-or-restart nginx',0,'active'),
])
def test_reload_before_post_hook(tmp_path,hook,code,state):
    (tmp_path/'state').write_text('active')
    setup=(ROOT/'deploy/setup_domain_alpn.sh').read_text()
    configured=re.search(r'--reloadcmd "([^"]+)"',setup)[1]
    if code==0:assert hook==configured
    result=run_script(tmp_path,r'''
set -euo pipefail
nginx() { return 0; }
systemctl() {
  case "$*" in
    'stop nginx') echo inactive > "$FIXTURE/state";;
    'reload nginx') [ "$(cat "$FIXTURE/state")" = active ];;
    'reload-or-restart nginx'|'start nginx') echo active > "$FIXTURE/state";;
    *) return 99;;
  esac
}
renewal() {
  systemctl stop nginx
  if ! eval "$RELOAD"; then return 1; fi
  systemctl start nginx
}
renewal
''',RELOAD=hook)
    assert result.returncode==code,result.stderr
    assert (tmp_path/'state').read_text().strip()==state

@pytest.mark.parametrize('scenario,code,start_expected',[
    ('normal_active',0,False),('success_stopped',0,True),('failed_stopped',7,True),
    ('timeout_stopped',124,True),('start_failure',1,True),
    ('initial_inactive',75,False),('bad_config',2,False),('locked',75,False),
])
def test_guard_recovers_only_a_service_that_was_running(tmp_path,scenario,code,start_expected):
    (tmp_path/'state').write_text('inactive' if scenario=='initial_inactive' else 'active')
    (tmp_path/'trace').write_text('')
    guard=(ROOT/'deploy/parking-acme-renewal.sh').read_text()
    guard=guard.replace('[ "$EUID" = 0 ]','[ 0 = 0 ]')
    guard=guard.replace('[ -x /root/.acme.sh/acme.sh ]','[ 1 = 1 ]')
    guard=guard.replace('/run/lock/parking-deploy.lock',shell_path(tmp_path/'lock'))
    guard=guard.replace('/var/log/parking-acme-renewal.log',shell_path(tmp_path/'private.log'))
    stubs=r'''
flock() { [ "$SCENARIO" != locked ]; }
logger() { printf '%s\n' "$*" >> "$FIXTURE/trace"; }
nginx() { [ "$SCENARIO" != bad_config ]; }
touch() { :; }
chmod() { :; }
systemctl() {
  case "$*" in
    'is-active --quiet nginx.service') [ "$(cat "$FIXTURE/state")" = active ];;
    'start nginx.service')
      echo start_nginx >> "$FIXTURE/trace"
      [ "$SCENARIO" != start_failure ] || return 1
      echo active > "$FIXTURE/state";;
    *) echo UNEXPECTED_MUTATION >> "$FIXTURE/trace"; return 99;;
  esac
}
timeout() {
  echo acme_invoked >> "$FIXTURE/trace"
  case "$SCENARIO" in
    normal_active) return 0;;
    success_stopped|start_failure) echo inactive > "$FIXTURE/state"; return 0;;
    failed_stopped) echo inactive > "$FIXTURE/state"; return 7;;
    timeout_stopped) echo inactive > "$FIXTURE/state"; return 124;;
    *) return 98;;
  esac
}
'''
    guard=guard.replace('for command_name in ',stubs+'\nfor command_name in ',1)
    result=run_script(tmp_path,guard,SCENARIO=scenario)
    assert result.returncode==code,result.stderr
    trace=(tmp_path/'trace').read_text()
    assert 'UNEXPECTED_MUTATION' not in trace
    assert ('start_nginx' in trace)==start_expected
    if scenario in ('initial_inactive','bad_config','locked'):assert 'acme_invoked' not in trace
    elif scenario!='start_failure':assert (tmp_path/'state').read_text().strip()=='active'
