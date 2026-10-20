"""Install only companion-owned hooks; preserve unrelated Claude settings."""
import base64,copy,json,pathlib,subprocess,sys,time
from .paths import ROOT,CLAUDE_HOME,EXECUTABLE_NAME
from .telemetry import EVENTS

def command(root=ROOT):
    root=pathlib.Path(root)
    if (root/EXECUTABLE_NAME).is_file():return [str(root/EXECUTABLE_NAME)]
    python=pathlib.Path(sys.executable);hidden=python.with_name('pythonw.exe')
    return [str(hidden if hidden.exists() else python),str(root/'tools/run_pet.py')]

def own(entry):
    if not isinstance(entry,dict):return False
    command=str(entry.get('command',''))
    if '--shorekeeper-event' in entry.get('args',[]) or '--shorekeeper-status' in command:return True
    if command.startswith('powershell.exe -NoProfile -NonInteractive -EncodedCommand '):
        try:return '# SHOREKEEPER_CLAUDE_STATUS_V1' in base64.b64decode(command.rsplit(' ',1)[1],validate=True).decode('utf-16le')
        except (ValueError,UnicodeError):pass
    return False

def status_command(args):
    # A single ASCII shell command works in both Git Bash and PowerShell.
    # Literal paths live inside the encoded script, never shell interpolation.
    def quote(value):return "'"+value.replace("'","''")+"'"
    script=(
        "# SHOREKEEPER_CLAUDE_STATUS_V1\n"
        "$s=New-Object System.Diagnostics.ProcessStartInfo\n"
        f"$s.FileName={quote(args[0])}\n"
        f"$s.Arguments={quote(subprocess.list2cmdline(args[1:]+['--shorekeeper-status']))}\n"
        "$s.UseShellExecute=$false;$s.CreateNoWindow=$true\n"
        "$s.RedirectStandardInput=$true;$s.RedirectStandardOutput=$true\n"
        "$s.StandardInputEncoding=New-Object System.Text.UTF8Encoding($false)\n"
        "$p=[System.Diagnostics.Process]::Start($s)\n"
        "$p.StandardInput.Write([Console]::In.ReadToEnd());$p.StandardInput.Close()\n"
        "[Console]::Out.Write($p.StandardOutput.ReadToEnd());$p.WaitForExit();$p.Dispose()\n")
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand '+base64.b64encode(script.encode('utf-16le')).decode('ascii')

def configure(enabled=True,home=CLAUDE_HOME,root=ROOT):
    home=pathlib.Path(home);home.mkdir(parents=True,exist_ok=True);path=home/'settings.json'
    raw=path.read_bytes() if path.exists() else None
    data=json.loads(raw.decode('utf-8-sig')) if raw else {}
    if not isinstance(data,dict):raise ValueError('Claude 设置文件格式不是对象，未修改。')
    updated=copy.deepcopy(data);hooks=updated.setdefault('hooks',{})
    if not isinstance(hooks,dict):raise ValueError('Claude hooks 设置格式无效，未修改。')
    for event,groups in list(hooks.items()):
        if not isinstance(groups,list):continue
        keep=[]
        for group in groups:
            if not isinstance(group,dict):keep.append(group);continue
            entries=group.get('hooks',[])
            if not isinstance(entries,list) or not any(own(e) for e in entries):keep.append(group);continue
            cleaned=[e for e in entries if not own(e)]
            if cleaned:keep.append(dict(group,hooks=cleaned))
        if keep:hooks[event]=keep
        else:hooks.pop(event,None)
    current=updated.get('statusLine');foreign=current is not None and not own(current)
    if current and own(current):updated.pop('statusLine',None)
    if enabled:
        args=command(root)
        for event in EVENTS:
            handler=dict(type='command',command=args[0],args=args[1:]+['--shorekeeper-event'],timeout=3,onFailure='continue')
            hooks.setdefault(event,[]).append(dict(hooks=[handler]))
        if not foreign:
            updated['statusLine']=dict(type='command',command=status_command(args),padding=0)
    if not hooks:updated.pop('hooks',None)
    backup=None
    if updated!=data:
        if raw is not None:
            folder=home/'shorekeeper-backups';folder.mkdir(exist_ok=True)
            backup=folder/f'settings-{time.time_ns()}.json';backup.write_bytes(raw)
        temporary=path.with_name('settings.shorekeeper.tmp')
        try:
            temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2),'utf8')
            if (path.read_bytes() if path.exists() else None)!=raw:raise RuntimeError('Claude 设置同时发生变化，请重试。')
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)
    return dict(enabled=enabled,status_line=enabled and not foreign,preserved_custom_statusline=foreign,backup=str(backup) if backup else None)
