"""Actual 0.7.0 -> 0.7.1 installer upgrade with a live isolated application."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

p=argparse.ArgumentParser()
p.add_argument('--repo', required=True)
p.add_argument('--old-installer', required=True)
p.add_argument('--new-installer', required=True)
p.add_argument('--out', required=True)
a=p.parse_args()
repo,out=Path(a.repo).resolve(),Path(a.out).resolve()
out.mkdir(parents=True,exist_ok=False)
sys.path[:0]=[str(repo),str(repo/'scripts')]
import check_windows_installer_roundtrip as install
assert not install._registered(), 'existing install registry must be preserved'
shortcuts=install._shortcut_dir()
if shortcuts.exists(): shutil.copytree(shortcuts,out/'shortcuts-before')
target=out/'应用 安装 Apps'
report={'started_at':time.time(),'scope':'actual 0.7.0 -> 0.7.1; native frozen flows; isolated tasks'}
child=None
stub=None
try:
    install._install(Path(a.old_installer).resolve(),target)
    env=dict(os.environ,QT_QPA_PLATFORM='windows',FOLITH_QT_NONINTERACTIVE='1',
             FOLITH_EXIT_FORCE_PROMPT='0',FOLITH_QT_SETTINGS_DIR=str(out/'old-settings'))
    old_smoke=out/'old-smoke.json'
    r=subprocess.run([str(target/'Folith.exe'),'--smoke','--data-dir',str(out/'old-data'),
                      '--log-dir',str(out/'old-logs'),'--result',str(old_smoke)],env=env,timeout=90)
    report['old_smoke']=json.loads(old_smoke.read_text(encoding='utf-8'))
    assert r.returncode==0 and report['old_smoke']['ok']
    from qt_desktop_probe import StubEndpoint
    stub=StubEndpoint(out,delay=.8)
    env.update(FOLITH_QT_SELFTEST_BASE_URL=stub.start(),FOLITH_QT_SELFTEST_PROVIDER='自定义中转站',
               FOLITH_QT_SELFTEST_MODEL='stub-model',FOLITH_QT_SELFTEST_API_KEY='stub-key')
    child=subprocess.Popen([str(target/'Folith.exe'),'--selftest','--selftest-fixture','docx',
                            '--data-dir',str(out/'old-flow-data'),'--log-dir',str(out/'old-flow-logs'),
                            '--result',str(out/'old-flow.json')],env=env)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        if child.poll() is not None: raise AssertionError('old app exited before lock test')
        if list((out/'old-flow-data').glob('*/runtime_state.json')): break
        time.sleep(.2)
    assert child.poll() is None
    r=subprocess.run(f'"{Path(a.new_installer).resolve()}" /S /D={target}',timeout=120)
    report['upgrade_while_old_running_exit']=r.returncode
    assert report['upgrade_while_old_running_exit']==2
    report['old_flow_exit']=child.wait(timeout=300)
    # The old build's selftest result is diagnostic; the upgrade is accepted
    # only through a fresh current-build selftest, never by an old flow pass.
    user_file=target/'用户文件.txt'
    user_file.write_text('preserve across upgrade and uninstall',encoding='utf-8')
    stale=target/'_internal/stale-0.7.0.dll'
    stale.write_bytes(b'old')
    install._install(Path(a.new_installer).resolve(),target)
    assert not stale.exists() and user_file.is_file()
    report['stale_removed']=True
    result=subprocess.Popen([sys.executable,str(repo/'scripts/qt_desktop_probe.py'),
                           '--fixture','both','--window','--frozen-app',str(target/'Folith.exe'),
                           '--data-dir',str(out/'new-flow/data'),'--out',str(out/'new-flow/report.json')],
                          env=dict(os.environ,PYTHONUTF8='1'))
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        if list((out/'new-flow/data').glob('*/runtime_state.json')): break
        if result.poll() is not None: raise AssertionError('new flow exited before the live-uninstall check')
        time.sleep(.1)
    r=subprocess.run(f'"{target / "Uninstall.exe"}" /S _?={target}',timeout=60)
    report['new_uninstall_while_running_exit']=r.returncode
    assert r.returncode==2
    flow_exit=result.wait(timeout=600)
    flow=json.loads((out/'new-flow/report.json').read_text(encoding='utf-8'))
    assert flow_exit==0 and flow['ok']
    report['new_identity']=flow['identity']
    report['pdf_sources']=flow['pdf_sources']
    report['new_flow_ok']=True
    install._uninstall(target)
    assert user_file.read_text(encoding='utf-8')=='preserve across upgrade and uninstall'
    report.update(ok=True,user_file_preserved=True)
except BaseException as exc:
    import traceback
    report.update(ok=False,error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
finally:
    if child is not None and child.poll() is None:
        child.terminate(); child.wait(timeout=30)
    if stub: stub.stop()
    if (target/'Uninstall.exe').exists():
        try: install._uninstall(target)
        except Exception as exc: report['cleanup_error']=str(exc)
    if (out/'shortcuts-before').exists():
        shortcuts.mkdir(parents=True,exist_ok=True)
        for f in (out/'shortcuts-before').iterdir(): shutil.copy2(f,shortcuts/f.name)
    report['duration_s']=round(time.time()-report['started_at'],2)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if report['ok'] else 1)
