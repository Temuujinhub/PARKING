import os
from pathlib import Path
import shutil
import subprocess

import pytest

BASH=os.environ.get('PARKING_TEST_BASH') or (shutil.which('bash') if os.name!='nt' else None)
pytestmark=pytest.mark.skipif(not BASH,reason='Bash required')


@pytest.mark.parametrize('role,approved,target,success',[
    ('production',None,None,False),
    ('production','not-a-commit',None,False),
    ('production','a'*40,'b'*40,False),
    ('production','a'*40,None,True),
    ('production','a'*40,'a'*40,True),
    ('staging',None,None,True),
    ('invalid',None,None,False),
])
def test_only_explicit_approved_sha_reaches_production(tmp_path,role,approved,target,success):
    script=Path(__file__).resolve().parents[3]/'deploy/approved-release.sh'
    approval=tmp_path/'approved'
    if approved is not None: approval.write_text(approved+'\n')
    env=dict(os.environ,PARKING_DEPLOY_ROLE=role,PARKING_APPROVED_RELEASE_FILE=approval.as_posix())
    env.pop('PARKING_DEPLOY_TARGET',None)
    if target: env['PARKING_DEPLOY_TARGET']=target
    result=subprocess.run([BASH,script.as_posix()],env=env,capture_output=True,text=True,timeout=10)
    assert (result.returncode==0)==success,result.stderr


@pytest.mark.parametrize('role,confirmation,database',[
    ('production','DELETE_TEST_TRANSACTIONS','parking'),
    ('staging','','parking_test'),
    ('staging','DELETE_TEST_TRANSACTIONS','parking'),
])
def test_reset_refuses_production_or_unconfirmed_targets(tmp_path,role,confirmation,database):
    original=Path(__file__).resolve().parents[3]/'tools/reset_test_data.sh'
    script=tmp_path/'reset.sh'
    # Windows checkout line endings do not change what the Linux script means.
    script.write_text(original.read_text(),encoding='utf-8',newline='\n')
    env=dict(os.environ,PARKING_DEPLOY_ROLE=role,PARKING_RESET_TEST_DATA=confirmation,
             PARKING_TEST_DATABASE_NAME=database)
    result=subprocess.run([BASH,script.as_posix()],env=env,capture_output=True,
                          text=True,encoding='utf-8',timeout=10)
    assert result.returncode!=0
    assert 'Refusing reset' in result.stderr or 'production' in result.stderr
    assert 'DB backup' not in result.stdout
