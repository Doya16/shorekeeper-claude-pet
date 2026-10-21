"""Read local Claude Code events. Never read credentials or invoke inference."""
from __future__ import annotations
import json
import os
import pathlib
import re
import shutil
import time
from datetime import datetime
from .paths import ROOT, CLAUDE_HOME
from .telemetry import store_path, phase, background_prompt
from .quota import quota_from_status, quota_from_desktop

# How turns Claude starts by itself (task results, schedules, side sessions)
# are presented: not at all, animation only, or like a typed request.
BACKGROUND_MODES = ('silent', 'animate', 'full')
# A finished turn that left background commands running has not finished the
# request; Claude resumes when their results arrive. Servers and watchers
# never finish, so they are not waited for.
SERVICE = re.compile(r'(?i)http\.server|\bserve[rs]?\b|\bdev\b|--watch|\bwatch\b|tail\s+-f|uvicorn|gunicorn|'
                     r'flask\s+run|vite|webpack|nodemon|live-server|\bpreview\b|\blisten|port\s+\d+')
NOTIFICATION = re.compile(r'<task-id>([^<\s]+)</task-id>.*?<status>(\w+)</status>', re.S)
# With no activity for this long, a waiting request is treated as finished.
WAIT_LIMIT = 1800


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def turn_is_background(obj):
    """Only a prompt the person typed counts as a request.

    Claude desktop marks each turn: ``human`` for typed prompts and
    ``task_notification`` / ``sdk`` / ... for background task results,
    scheduled runs, side-session events and sessions started by another
    session. Older transcripts lack the marker; fall back to known wrappers.
    """
    origin = obj.get('turnOrigin')
    if isinstance(origin, str) and origin:
        return origin != 'human'
    origin = obj.get('origin')
    if isinstance(origin, dict) and isinstance(origin.get('kind'), str):
        return origin['kind'] != 'human'
    content = (obj.get('message') or {}).get('content')
    if isinstance(content, list):
        content = next((b.get('text') for b in content if isinstance(b, dict) and b.get('type') == 'text'), '')
    return background_prompt(content)


HUMAN_MARK = b'"turnOrigin":"human"'


def last_request_line(file, size, limit=64_000_000, block=1_000_000):
    """Offset of the line holding the latest typed prompt, searching backward.

    A long session can push the person's request far before the tail read on
    startup; without it the session would look like background work only.
    """
    end = size
    floor = max(0, size-limit)
    while end > floor:
        start = max(floor, end-block)
        file.seek(start)
        data = file.read(end-start+len(HUMAN_MARK))
        hit = data.rfind(HUMAN_MARK)
        if hit >= 0:
            position = start+hit
            # Walk back to the beginning of that line; prompts may hold images.
            while position > floor:
                low = max(floor, position-65_536)
                file.seek(low)
                newline = file.read(position-low).rfind(b'\n')
                if newline >= 0:
                    return low+newline+1
                position = low
            return floor if floor == 0 else None
        end = start
    return None


def desktop_usage_paths():
    roots = [pathlib.Path(os.environ.get('APPDATA', ''))/'Claude']
    roots += list((pathlib.Path(os.environ.get('LOCALAPPDATA', ''))/'Packages').glob(
        'Claude_*/LocalCache/Roaming/Claude'))
    return [root/'plan-usage-history.json' for root in roots]


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
    def __init__(self, executable='', claude_home='', store=None, desktop=None):
        self.executable = executable
        self.claude_home = claude_home
        self.store = pathlib.Path(store) if store else store_path()
        self.desktop = [pathlib.Path(p) for p in desktop] if desktop is not None else desktop_usage_paths()

    def read(self):
        """Newest of the two local sources; neither reads credentials."""
        sources = [self.read_statusline(), self.read_desktop()]
        usable = [item for item in sources if item.get('windows')]
        best = dict(max(usable or sources, key=lambda item: item.get('updated_at', 0)))
        best['sources'] = {item['source']: dict(updated_at=item.get('updated_at', 0), available=bool(item.get('windows')),
                                                 found=item.get('found', False)) for item in sources}
        return best

    def read_desktop(self):
        """Claude desktop samples plan usage about every 15 minutes."""
        latest = {}
        found = False
        for path in self.desktop:
            try:
                if not path.is_file() or path.stat().st_size > 2_000_000:
                    continue
                found = True
                data = json.loads(path.read_text('utf8'))
                for sample in data.get('samples') or []:
                    if isinstance(sample, dict) and isinstance(sample.get('t'), (int, float)) and sample['t'] > latest.get('t', 0):
                        latest = sample
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return dict(quota_from_desktop(latest), found=found)

    def read_statusline(self):
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
        return dict(quota_from_status(latest), found=bool(latest))

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
        self.project = ''
        self.custom_title = ''
        self.recent_tool_state = None
        self.tool_visible_until = 0
        # The current turn was started by Claude itself, not typed by the person.
        self.background = False
        # The person's most recent request and when it last made progress.
        self.request = None
        self.request_event = 0.0
        # Background commands started for the request: task id -> start time.
        self.waiting_on = {}
        self.launches = {}
        # A background turn resuming a request that is waiting on results.
        self.continuation = False

    @property
    def title(self):
        return self.custom_title or self.project or 'Claude Code'

    def awaiting(self):
        return bool(self.request and self.request.get('state') == 'awaiting')

    def in_progress(self):
        return bool(self.request and not self.request.get('ended'))

    def start(self, when, turn_id=None, background=False):
        # A background result delivered while the person's own request is
        # still running belongs to that request; never relabel it.
        if self.active and not self.background and background:
            return
        if not self.active or (turn_id and turn_id != self.turn_id):
            self.started = when
            self.turn_id = turn_id or str(when)
            self.tools.clear()
        self.active = True
        self.background = background
        self.continuation = background and self.awaiting()
        self.ended = 0
        self.state = 'thinking'
        if not background:
            # A new request supersedes one still waiting on old commands.
            self.waiting_on.clear()
            self.request = dict(state='thinking', turn_id=self.turn_id, started=self.started, ended=0)
            self.request_event = max(self.request_event, when)

    def finish_request(self, state, when, announce=True):
        request = self.request
        key = request['turn_id'] or request['started']
        if announce and key not in self.terminal_seen:
            self.terminal_seen.add(key)
            self.events.append(dict(state=state, turn_id=request['turn_id'], started=request['started'], ended=when,
                                    background=False))
        self.request = dict(state=state, turn_id=request['turn_id'], started=request['started'], ended=when)
        self.request_event = max(self.request_event, when)
        self.waiting_on.clear()

    def terminal(self, state, when):
        if not self.started or not self.active:
            return
        if not self.background:
            self.request = dict(self.request or {}, turn_id=self.turn_id, started=self.started)
            if state == 'done' and self.waiting_on:
                # Replied, but its own background commands are still running.
                self.request.update(state='awaiting', ended=0, last=when)
                self.request_event = max(self.request_event, when)
            else:
                self.finish_request(state, when)
        elif self.continuation:
            if state != 'done' or not self.waiting_on:
                self.finish_request(state, when)
            else:
                self.request['last'] = when
        else:
            key = self.turn_id or self.started
            if key not in self.terminal_seen:
                self.terminal_seen.add(key)
                self.events.append(dict(state=state, turn_id=self.turn_id, started=self.started, ended=when,
                                        background=True))
        self.continuation = False
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

    def touch(self, when):
        self.last_event = max(self.last_event, when)
        if self.active and (not self.background or self.continuation):
            self.request_event = max(self.request_event, when)

    def task_results(self, obj):
        """Background command results, wherever Claude recorded them: their own
        turn, a queued prompt, or an attachment delivered mid-turn."""
        texts = [obj.get('content'), (obj.get('attachment') or {}).get('prompt') if isinstance(obj.get('attachment'), dict) else None]
        content = (obj.get('message') or {}).get('content') if isinstance(obj.get('message'), dict) else None
        texts += [content] if isinstance(content, str) else [b.get('text') for b in content or [] if isinstance(b, dict)]
        for text in texts:
            if isinstance(text, str) and '<task-notification>' in text:
                for task, status in NOTIFICATION.findall(text):
                    if status.lower() != 'running':
                        self.waiting_on.pop(task, None)

    def expire(self, now):
        """A request waiting far too long on a command most likely left a
        long-lived process behind; settle it quietly."""
        if self.awaiting() and not self.active and now-self.last_event > WAIT_LIMIT:
            self.finish_request('done', self.request.get('last', self.last_event), announce=False)

    def accept_hook(self, item):
        when = item.get('at', 0)
        if when < self.last_hook or when < self.last_event:
            return
        self.last_hook = when
        self.touch(when)
        self.revision += 1
        self.project = item.get('project') or self.project
        event = item.get('event')
        tool = item.get('tool_name', '')
        if event == 'UserPromptSubmit':
            background = item.get('origin') == 'background'
            if not (self.active and not self.background and background):
                self.hook_prompt_id = item.get('prompt_id') or None
            self.start(when, item.get('prompt_id') or str(when), background)
        elif event == 'PreToolUse':
            if not self.active:
                # A turn that began without a typed prompt was started by
                # Claude itself: a task result, a schedule or another session.
                self.start(when, background=True)
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
        if obj.get('type') in ('custom-title', 'agent-name'):
            name = obj.get('customTitle') or obj.get('agentName')
            if isinstance(name, str) and name.strip():
                self.custom_title = ''.join(c for c in name if c.isprintable()).strip()[:80]
            return
        if not self.project and isinstance(obj.get('cwd'), str):
            self.project = pathlib.PureWindowsPath(obj['cwd']).name[:80]
        if self.waiting_on:
            self.task_results(obj)
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
                launched = obj.get('toolUseResult') if isinstance(obj.get('toolUseResult'), dict) else {}
                for result in results:
                    if self.launches.pop(result.get('tool_use_id'), None) and isinstance(launched.get('backgroundTaskId'), str) \
                            and self.request and (self.active and (not self.background or self.continuation)):
                        self.waiting_on[launched['backgroundTaskId']] = when
                    name = self.tools.pop(result.get('tool_use_id'), '')
                    if name.endswith('__wrap_up') and not result.get('is_error'):
                        self.terminal('done', when)
                self.tool_finished(when)
            elif not obj.get('isMeta') and not obj.get('toolUseResult'):
                self.start(when, obj.get('promptId') or ident, turn_is_background(obj))
            else:
                return
        elif kind == 'assistant':
            if not self.active:
                return
            tools = [b for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_use']
            for tool in tools:
                self.tool_started(tool.get('id'), tool.get('name', ''))
                given = tool.get('input') if isinstance(tool.get('input'), dict) else {}
                if given.get('run_in_background') and not SERVICE.search(f"{given.get('command', '')} {given.get('description', '')}"):
                    self.launches[tool.get('id')] = True
            if message.get('stop_reason') in ('end_turn', 'stop_sequence'):
                self.terminal('done', when)
            elif not tools:
                self.state = 'thinking'
        elif kind == 'system' and obj.get('subtype') == 'turn_duration':
            self.terminal('done', when)
        else:
            return
        self.touch(when)
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
                    start = last_request_line(file, size)
                    if start is not None and start < size-2_000_000:
                        file.seek(start)
                    else:
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
        self.background_mode = 'silent'
        self.followed = None
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
        mode = self.background_mode if self.background_mode in BACKGROUND_MODES else 'silent'
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
                if event['ended'] < self.observe_since:
                    continue
                # Only the person's own requests announce completion by default.
                if event.get('background') and mode != 'full':
                    continue
                events.append(dict(event, thread_id=sid, title=reader.title))
        events.sort(key=lambda event: event['ended'])
        for reader in self.readers.values():
            reader.expire(now)
        recent = sorted(self.readers, key=lambda sid: self.readers[sid].last_event, reverse=True)
        keep = set(recent[:64]) | {sid for sid, reader in self.readers.items() if reader.active or reader.in_progress()} | {self.selected}
        self.readers = {sid: reader for sid, reader in self.readers.items() if sid in keep}
        self.threads = [dict(id=sid, title=f'{reader.title} · {sid[:6]}') for sid, reader in self.readers.items()]

        def working(reader):
            """Handling one of the person's requests, or waiting on its commands."""
            return reader.awaiting() or (reader.in_progress() and now-reader.last_event < WAIT_LIMIT)

        def chatter(reader):
            """A turn Claude started by itself that belongs to no request."""
            return reader.active and reader.background and not reader.continuation and now-reader.last_event < 600
        background_count = sum(1 for reader in self.readers.values() if chatter(reader))
        # Every project's open request, so one finishing never hides another.
        requests = sorted((dict(reader.request, title=reader.title, thread_id=sid)
                           for sid, reader in self.readers.items() if working(reader)), key=lambda r: r['started'])
        finished = [reader for reader in self.readers.values() if reader.request and reader.request.get('ended')]
        latest = max(finished, key=lambda reader: reader.request['ended'], default=None)
        latest = dict(latest.request, title=latest.title) if latest else None
        common = dict(events=events, background_count=background_count, requests=requests, latest=latest)
        chosen = self.selected if self.selected in self.readers else None
        if self.selected == 'auto' and self.readers:
            busy = [sid for sid, reader in self.readers.items()
                    if working(reader) or (mode != 'silent' and chatter(reader))]
            # Stay with the project being followed until its request is done,
            # instead of jumping to whichever session wrote last.
            if self.followed in busy:
                chosen = self.followed
            elif busy:
                chosen = max(busy, key=lambda sid: self.readers[sid].last_event)
            else:
                chosen = max(self.readers, key=lambda sid: (self.readers[sid].request_event, self.readers[sid].last_event))
            self.followed = chosen
        if not chosen:
            return dict(state='idle', message=self.error or '', title='', thread_id=None, stale=False, revision=0,
                        request=None, **common)
        reader = self.readers[chosen]
        request = dict(reader.request, title=reader.title) if reader.request else None
        last = reader.request or {}
        if reader.awaiting() and not reader.active:
            # The reply is in, but its commands still run: busy, not finished.
            return dict(state='working', message='', title=reader.title, thread_id=chosen, turn_id=last.get('turn_id'),
                        stale=False, revision=reader.revision, last_event=reader.last_event, active=True,
                        background=True, age=now-reader.last_event, started=last.get('started', 0), ended=0,
                        request=request, **common)
        if chatter(reader) and mode == 'silent':
            # Present the session as it was after the person's last request.
            return dict(state='idle', message='', title=reader.title, thread_id=chosen, turn_id=last.get('turn_id'),
                        stale=False, revision=reader.revision, last_event=reader.request_event, active=False,
                        background=False, age=now-(reader.request_event or reader.last_event),
                        started=last.get('started', 0), ended=last.get('ended', 0), request=request, **common)
        age = now-reader.last_event
        stale = reader.active and age > 180
        return dict(state='unknown' if stale else reader.display_state(now), message='', title=reader.title,
                    thread_id=chosen, turn_id=reader.turn_id, stale=stale, revision=reader.revision,
                    last_event=reader.last_event, active=reader.active, background=reader.active and reader.background,
                    age=age, started=reader.started, ended=reader.ended, request=request, **common)

    def cached_rate(self):
        return None
