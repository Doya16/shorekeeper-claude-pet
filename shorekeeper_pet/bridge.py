"""Read local Claude Code events. Never read credentials or invoke inference."""
from __future__ import annotations
import json
import os
import pathlib
import shutil
import time
from datetime import datetime
from .paths import ROOT, CLAUDE_HOME
from .telemetry import store_path, phase
from .quota import quota_from_status


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def discover_claude(explicit=''):
    if explicit:
        return str(pathlib.Path(explicit)) if pathlib.Path(explicit).is_file() else None
    exe = shutil.which('claude')
    if exe:
        return exe
    candidates = [pathlib.Path.home()/'.local/bin/claude.exe']
    roots = [pathlib.Path(os.environ.get('APPDATA', ''))/'Claude/claude-code']
    roots += list((pathlib.Path(os.environ.get('LOCALAPPDATA', ''))/'Packages').glob(
        'Claude_*/LocalCache/Roaming/Claude/claude-code'))
    for root in roots:
        candidates.extend(root.glob('*/*/claude.exe'))
    candidates = [p for p in candidates if p.is_file()]
    return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None


class RateClient:
    def __init__(self, executable='', claude_home='', store=None):
        self.executable = executable
        self.claude_home = claude_home
        self.store = pathlib.Path(store) if store else store_path()

    def read(self):
        latest = {}
        for path in self.store.glob('*.status.json'):
            try:
                if path.stat().st_size > 100_000:
                    continue
                item = json.loads(path.read_text('utf8'))
                if item.get('kind') == 'status' and item.get('at', 0) > latest.get('at', 0):
                    latest = item
            except (OSError, ValueError, TypeError):
                continue
        # An absent field in a newer payload clears the old value.
        return quota_from_status(latest)

    def close(self):
        pass


class SessionReader:
    def __init__(self, path=None):
        self.path = pathlib.Path(path) if path else None
        self.offset = 0
        self.pending = b''
        self.state = 'idle'
        self.public_note = ''
        self.last_event = self.last_hook = self.started = self.ended = 0.0
        self.turn_id = None
        self.hook_prompt_id = None
        self.active = False
        self.rate = None
        self.revision = 0
        self.events = []
        self.terminal_seen = set()
        self.transcript_seen = set()
        self.tools = {}
        self.title = 'Claude Code'
        self.recent_tool_state = None
        self.tool_visible_until = 0

    def start(self, when, turn_id=None):
        if not self.active or (turn_id and turn_id != self.turn_id):
            self.started = when
            self.turn_id = turn_id or str(when)
            self.tools.clear()
        self.active = True
        self.ended = 0
        self.state = 'thinking'

    def terminal(self, state, when):
        if not self.started or not self.active:
            return
        key = self.turn_id or self.started
        if key not in self.terminal_seen:
            self.terminal_seen.add(key)
            self.events.append(dict(state=state, turn_id=self.turn_id, started=self.started, ended=when))
        self.active = False
        self.ended = when
        self.state = state
        self.tools.clear()

    def drain_events(self):
        result = self.events
        self.events = []
        return result

    def tool_started(self, tool_id, name):
        self.tools[tool_id] = name
        self.state = phase(name)
        self.recent_tool_state = self.state
        self.tool_visible_until = 0

    def tool_finished(self, when):
        if self.state in ('reading', 'writing'):
            self.tool_visible_until = when+1.5
        if self.active:
            self.state = 'working' if self.tools else 'thinking'

    def accept_hook(self, item):
        when = item.get('at', 0)
        if when < self.last_hook or when < self.last_event:
            return
        self.last_hook = when
        self.last_event = max(self.last_event, when)
        self.revision += 1
        self.title = item.get('project') or self.title
        event = item.get('event')
        tool = item.get('tool_name', '')
        if event == 'UserPromptSubmit':
            self.hook_prompt_id = item.get('prompt_id') or None
            self.start(when, item.get('prompt_id') or str(when))
        elif event == 'PreToolUse':
            if not self.active:
                self.start(when)
            self.tool_started(item.get('tool_use_id') or tool, tool)
        elif event == 'PermissionRequest' or (event == 'Notification' and item.get('notification_type') in ('permission_prompt', 'elicitation_dialog')):
            if self.active:
                self.state = 'waiting'
        elif event in ('PostToolUse', 'PostToolUseFailure'):
            self.tools.pop(item.get('tool_use_id') or tool, None)
            if event == 'PostToolUse' and tool.endswith('__wrap_up'):
                self.terminal('done', when)
            else:
                self.tool_finished(when)
        elif event in ('Stop', 'StopFailure'):
            if item.get('prompt_id') and self.hook_prompt_id and item['prompt_id'] != self.hook_prompt_id:
                return
            self.terminal('done' if event == 'Stop' else 'error', when)
        elif event == 'SessionEnd':
            self.terminal('paused', when)

    def accept(self, obj):
        if not isinstance(obj, dict) or obj.get('isSidechain'):
            return
        when = timestamp(obj.get('timestamp'))
        if not when or when <= self.last_hook:
            return
        ident = obj.get('uuid')
        if ident:
            if ident in self.transcript_seen:
                return
            self.transcript_seen.add(ident)
        message = obj.get('message') or {}
        if not isinstance(message, dict):
            return
        content = message.get('content')
        blocks = content if isinstance(content, list) else []
        kind = obj.get('type')
        if kind == 'user':
            results = [b for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_result']
            if results:
                for result in results:
                    name = self.tools.pop(result.get('tool_use_id'), '')
                    if name.endswith('__wrap_up') and not result.get('is_error'):
                        self.terminal('done', when)
                self.tool_finished(when)
            elif not obj.get('isMeta') and not obj.get('toolUseResult'):
                self.start(when, obj.get('promptId') or ident)
            else:
                return
        elif kind == 'assistant':
            if not self.active:
                return
            tools = [b for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_use']
            for tool in tools:
                self.tool_started(tool.get('id'), tool.get('name', ''))
            if message.get('stop_reason') in ('end_turn', 'stop_sequence'):
                self.terminal('done', when)
            elif not tools:
                self.state = 'thinking'
        elif kind == 'system' and obj.get('subtype') == 'turn_duration':
            self.terminal('done', when)
        else:
            return
        self.last_event = max(self.last_event, when)
        self.revision += 1

    def display_state(self, now):
        if self.active and self.state == 'thinking' and now < self.tool_visible_until and self.recent_tool_state in ('reading', 'writing'):
            return self.recent_tool_state
        if not self.active and self.ended and now-self.ended > 12:
            return 'idle'
        return self.state

    def poll(self):
        if not self.path:
            return
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.pending = b''
                self.transcript_seen.clear()
            with self.path.open('rb') as file:
                file.seek(self.offset)
                if not self.offset and size > 2_000_000:
                    file.seek(size-2_000_000)
                    file.readline()
                chunk = file.read(4_000_000)
                self.offset = file.tell()
            lines = (self.pending+chunk).split(b'\n')
            self.pending = lines.pop()[-2_000_000:]
            for line in lines:
                try:
                    self.accept(json.loads(line))
                except (ValueError, TypeError, AttributeError):
                    continue
        except OSError:
            pass


class Monitor:
    def __init__(self, claude_home=CLAUDE_HOME, store=None):
        self.home = pathlib.Path(claude_home)
        self.store = pathlib.Path(store) if store else store_path()
        self.readers = {}
        self.threads = []
        self.last_discovery = 0
        self.selected = 'auto'
        self.error = None
        self.observe_since = time.time()
        self.seen_files = set()

    def discover(self):
        paths = sorted(self.home.glob('projects/*/*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)[:64]
        for path in paths:
            if path.stem not in self.readers:
                self.readers[path.stem] = SessionReader(path)
            else:
                self.readers[path.stem].path = path
        self.error = None if paths or self.readers else '等待 Claude Code 会话；请在设置中连接 Claude Code。'

    def poll(self):
        now = time.time()
        if now-self.last_discovery > 3:
            self.discover()
            self.last_discovery = now
        for reader in self.readers.values():
            reader.poll()
        paths = sorted(self.store.glob('*.event.json'))[-4000:]
        pending_events = []
        for path in paths:
            if path.name in self.seen_files:
                continue
            self.seen_files.add(path.name)
            try:
                if path.stat().st_size > 20_000:
                    continue
                item = json.loads(path.read_text('utf8'))
                if isinstance(item,dict) and isinstance(item.get('session_id'),str):
                    pending_events.append(item)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for item in sorted(pending_events,key=lambda item:(item.get('at',0),item.get('order',0))):
            self.readers.setdefault(item['session_id'], SessionReader()).accept_hook(item)
        self.seen_files.intersection_update(p.name for p in paths)
        events = []
        for sid, reader in self.readers.items():
            for event in reader.drain_events():
                if event['ended'] >= self.observe_since:
                    events.append(dict(event, thread_id=sid, title=reader.title))
        events.sort(key=lambda event: event['ended'])
        recent = sorted(self.readers, key=lambda sid: self.readers[sid].last_event, reverse=True)
        keep = set(recent[:64]) | {sid for sid, reader in self.readers.items() if reader.active} | {self.selected}
        self.readers = {sid: reader for sid, reader in self.readers.items() if sid in keep}
        self.threads = [dict(id=sid, title=f'{reader.title} · {sid[:6]}') for sid, reader in self.readers.items()]
        chosen = self.selected if self.selected in self.readers else None
        if self.selected == 'auto' and self.readers:
            active = [sid for sid, reader in self.readers.items() if reader.active and now-reader.last_event < 600]
            chosen = max(active or self.readers, key=lambda sid: self.readers[sid].last_event)
        if not chosen:
            return dict(state='idle', message=self.error or '', title='', thread_id=None, stale=False, revision=0, events=events)
        reader = self.readers[chosen]
        age = now-reader.last_event
        stale = reader.active and age > 180
        return dict(state='unknown' if stale else reader.display_state(now), message='', title=reader.title,
                    thread_id=chosen, turn_id=reader.turn_id, stale=stale, revision=reader.revision,
                    last_event=reader.last_event, active=reader.active, age=age, started=reader.started,
                    ended=reader.ended, events=events)

    def cached_rate(self):
        return None
