"""Local, allowlisted Claude hook/status-line data. Never stores prompt bodies."""
import ctypes, hashlib, json, math, os, pathlib, sys, time, uuid

EVENTS=('SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','PostToolUseFailure','PermissionRequest','Notification','Stop','StopFailure','SessionEnd')

def store_path():
    return pathlib.Path.home()/'.shorekeeper-claude-pet'/'telemetry'

def text(value, limit=160):
    return ''.join(c for c in value if c.isprintable())[:limit] if isinstance(value,str) else ''

def number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None

def phase(name):
    name=name.lower()
    if any(s in name for s in ('askuser','ask_user','permission','elicitation')):return 'waiting'
    if any(s in name for s in ('write','edit','patch','create_file')):return 'writing'
    if any(s in name for s in ('read','grep','glob','search','fetch','list','browse')):return 'reading'
    return 'working'

def sanitize(data, status=False, now=None):
    if not isinstance(data,dict):return None
    sid=text(data.get('session_id'))
    if not sid:return None
    item=dict(session_id=sid,at=time.time() if now is None else now,order=time.monotonic_ns())
    if status:
        item['kind']='status'
        rate=data.get('rate_limits');rate=rate if isinstance(rate,dict) else {}
        item['rate_limits']={}
        for key,window in list(rate.items())[:20]:
            if not isinstance(key,str) or not isinstance(window,dict):continue
            safe={k:number(window.get(k)) for k in ('used_percentage','resets_at','used_usd','limit_usd')}
            safe={k:v for k,v in safe.items() if v is not None}
            if key=='spend_limit':safe['period']=text(window.get('period'),30)
            if safe:item['rate_limits'][text(key,80)]=safe
        for field in ('context_window','cost'):
            value=data.get(field);value=value if isinstance(value,dict) else {}
            keys=('remaining_percentage','used_percentage') if field=='context_window' else ('total_cost_usd',)
            item[field]={key:number(value.get(key)) for key in keys if number(value.get(key)) is not None}
        model=data.get('model');item['model']=text(model.get('display_name') or model.get('id')) if isinstance(model,dict) else ''
        return item
    event=data.get('hook_event_name')
    if event not in EVENTS:return None
    # Child agent tools and stops must never complete the parent's task.
    if data.get('agent_id'):return None
    item.update(kind='event',event=event,tool_name=text(data.get('tool_name')),notification_type=text(data.get('notification_type')),
                error=text(data.get('error')),tool_use_id=text(data.get('tool_use_id')),prompt_id=text(data.get('prompt_id')))
    cwd=data.get('cwd')
    item['project']=text(pathlib.PureWindowsPath(cwd).name if isinstance(cwd,str) else '',80)
    return item

def receive(data,status=False,store=None,now=None):
    item=sanitize(data,status,now)
    if item is None:return None
    root=pathlib.Path(store) if store else store_path();root.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(item['session_id'].encode()).hexdigest()
    name=identity+'.status.json' if status else f'{time.time_ns()}-{item["order"]:020d}-{uuid.uuid4().hex}.event.json'
    target=root/name;temp=root/(uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(item,ensure_ascii=False),'utf8');temp.replace(target)
    finally:temp.unlink(missing_ok=True)
    # SessionStart is infrequent; bound disk history without a background service.
    if not status and item['event']=='SessionStart':
        cutoff=time.time()-7*86400
        for old in root.glob('*.json'):
            try:
                if old.stat().st_mtime<cutoff:old.unlink()
            except OSError:pass
    return item

def _stdin():
    if sys.stdin is not None:return sys.stdin.buffer.read(2_000_001)
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetStdHandle.restype=ctypes.c_void_p
    handle=kernel.GetStdHandle(-10)
    if not handle or handle==ctypes.c_void_p(-1).value:return b''
    fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    with os.fdopen(fd,'rb') as stream:return stream.read(2_000_001)

def _stdout(value):
    raw=(value+'\n').encode('utf8')
    if sys.stdout is not None:sys.stdout.buffer.write(raw);sys.stdout.flush();return
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetStdHandle.restype=ctypes.c_void_p
    handle=kernel.GetStdHandle(-11);written=ctypes.c_ulong()
    kernel.WriteFile.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.c_void_p]
    if handle:kernel.WriteFile(handle,raw,len(raw),ctypes.byref(written),None)

def main(status=False):
    # Always observational: no permission decisions, prompt injection, or model call.
    try:
        raw=_stdin()
        if len(raw)>2_000_000:return 0
        item=receive(json.loads(raw.decode('utf-8-sig')),status)
        if status and item:
            parts=[]
            for key,label in (('five_hour','5h'),('seven_day','7d')):
                used=item['rate_limits'].get(key,{}).get('used_percentage')
                if used is not None:parts.append(f'{label} remaining {max(0,100-used):g}%')
            _stdout('Shorekeeper | '+(' | '.join(parts) or 'Claude connected'))
    except Exception:pass
    return 0
