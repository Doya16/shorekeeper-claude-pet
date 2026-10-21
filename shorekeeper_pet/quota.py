"""Claude's independent usage windows; never infer a credit balance."""
from dataclasses import dataclass
from datetime import datetime
import math
import time

SOURCES = {
    'statusline': ('Claude Code 状态栏', 300),
    # The desktop app samples about every 15 minutes while it is open.
    'desktop': ('Claude 桌面版用量记录', 1200),
}
# Short keys in the desktop app's plan-usage-history.json samples.
DESKTOP_WINDOWS = {'fh': ('five_hour', 5*3600), 'sd': ('seven_day', 7*86400)}
LABELS = {'five_hour': '5 小时', 'seven_day': '每周', 'spend_limit': '支出上限'}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sort_windows(windows):
    windows.sort(key=lambda w: ({'five_hour': 0, 'seven_day': 1}.get(w['bucket'], 2), w['bucket']))
    return windows


def quota_from_status(item, now=None):
    now = time.time() if now is None else now
    windows = []
    rates = item.get('rate_limits') or {}
    for key, value in rates.items():
        if not isinstance(value, dict) or not finite(value.get('used_percentage')):
            continue
        reset = value.get('resets_at')
        if finite(reset) and reset <= now:
            continue
        label = LABELS.get(key, str(key).replace('seven_day_', '每周 · ').replace('fable', 'Fable')[:40])
        windows.append(dict(bucket=key, label=label, remaining=max(0, min(100, 100-value['used_percentage'])),
                            resets_at=reset if finite(reset) else None,
                            used_usd=value.get('used_usd'), limit_usd=value.get('limit_usd')))
    return dict(windows=sort_windows(windows), updated_at=item.get('at', 0), source='statusline', error=None,
                session_id=item.get('session_id'), model=item.get('model'),
                context=item.get('context_window') or {}, cost=item.get('cost') or {})


def quota_from_desktop(sample):
    """A desktop sample has used percentages but no reset times."""
    at = sample.get('t', 0)/1000 if finite(sample.get('t')) else 0
    windows = []
    usage = sample.get('u') if isinstance(sample.get('u'), dict) else {}
    for key, (bucket, length) in DESKTOP_WINDOWS.items():
        used = usage.get(key)
        if not finite(used):
            continue
        # A sample older than its window says nothing about the current one.
        windows.append(dict(bucket=bucket, label=LABELS[bucket], remaining=max(0, min(100, 100-used)),
                            resets_at=None, expires_by=at+length))
    return dict(windows=sort_windows(windows), updated_at=at, source='desktop', error=None,
                context={}, cost={})


@dataclass(frozen=True)
class QuotaView:
    mode: str
    label: str
    remaining: float | None = None
    bucket: str | None = None


def stale_after(data):
    return SOURCES.get(data.get('source'), ('', 300))[1]


def is_cached(data, now=None):
    return bool(data.get('error') or (time.time() if now is None else now)-data.get('updated_at', 0) > stale_after(data))


def live_windows(data, now):
    for window in data.get('windows') or []:
        if finite(window.get('resets_at')) and window['resets_at'] <= now:
            continue
        if finite(window.get('expires_by')) and window['expires_by'] <= now:
            continue
        if finite(window.get('remaining')):
            yield window


# Items the person can show or hide; Fable covers any Fable-specific window.
QUOTA_ITEMS = (('five_hour', '5 小时', '5 小时'), ('seven_day', '每周', '每周'), ('fable', 'Fable 每周', 'Fable'))


def quota_items(data, show, now=None):
    """Rows for the merged quota box. A shown item without data stays visible
    as "待同步" so a missing window is never mistaken for a hidden one."""
    now = time.time() if now is None else now
    stale = is_cached(data, now)
    live = {window['bucket']: window for window in live_windows(data, now)}
    fable = next((w for bucket, w in live.items() if 'fable' in str(bucket).lower()), None)
    items = []
    for key, label, short in QUOTA_ITEMS:
        if not show.get(key, True):
            continue
        window = fable if key == 'fable' else live.get(key)
        items.append(dict(key=key, label=label, short=short, remaining=window['remaining'] if window else None,
                          stale=bool(window) and stale))
    if show.get('other', True):
        for bucket, window in live.items():
            if bucket in ('five_hour', 'seven_day') or window is fable:
                continue
            items.append(dict(key=bucket, label=window['label'], short=window['label'].split(' · ')[-1][:8],
                              remaining=window['remaining'], stale=stale))
    return items


def quota_rows(data, now=None):
    now = time.time() if now is None else now
    rows = []
    for window in live_windows(data, now):
        left = window['remaining']
        label = f"{window['label']}剩余：{left:g}%"
        if is_cached(data, now):
            label += ' *'
        rows.append(QuotaView('plan' if left > 0 else 'depleted', label, left, window['bucket']))
    return rows or [QuotaView('unknown', '额度：待同步')]


def quota_view(data):
    return quota_rows(data)[0]


def quota_label(data, compact=False):
    return ' / '.join(row.label for row in quota_rows(data))


def stamp(value, pattern='%m/%d %H:%M'):
    try:
        return datetime.fromtimestamp(value).astimezone().strftime(pattern)
    except (TypeError, ValueError, OSError, OverflowError):
        return '未知'


def source_lines(data):
    """Where each local source stands, for the settings page."""
    lines = []
    sources = data.get('sources') or {}
    for key, (name, _) in SOURCES.items():
        item = sources.get(key) or {}
        if item.get('available'):
            lines.append(f"{name}：已同步，最近 {stamp(item['updated_at'], '%m/%d %H:%M:%S')}")
        elif key == 'desktop':
            lines.append(f"{name}：{'已找到，暂无额度数据' if item.get('found') else '未找到（需要已登录的 Claude 桌面版）'}")
        else:
            lines.append(f"{name}：{'已连接，暂无额度数据' if item.get('found') else '未连接（仅终端 Claude Code 需要）'}")
    return lines


def refresh_note(data, now=None):
    """Feedback for a manual refresh: what was read and when it can change."""
    now = time.time() if now is None else now
    if not data.get('updated_at') or not data.get('windows'):
        return '没有读到额度：桌面版用量记录和终端状态栏都还没有数据。'
    taken = stamp(data['updated_at'], '%H:%M')
    if data.get('source') == 'desktop':
        # The desktop app samples on a fixed cadence; reading sooner cannot help.
        upcoming = data['updated_at']+900
        when = f"下次约 {stamp(upcoming, '%H:%M')}" if upcoming > now else '桌面版下次采样后自动更新'
        return f"已重读：{' / '.join(row.label for row in quota_rows(data, now))}\n这是桌面版 {taken} 的采样，约每 15 分钟一次，{when}。"
    return f"已重读：{' / '.join(row.label for row in quota_rows(data, now))}\n这是状态栏 {taken} 的数据，Claude Code 每次回复后更新。"


def quota_brief(data, now=None):
    """Hover lines: only what the status bar itself cannot show."""
    now = time.time() if now is None else now
    name, limit = SOURCES.get(data.get('source'), ('', 300))
    if not data.get('updated_at') or not name:
        return ['额度待同步：设置 → Claude 额度与提醒 查看来源']
    short = '桌面版记录' if data.get('source') == 'desktop' else '状态栏'
    stale = is_cached(data, now)
    lines = [f"额度同步于 {stamp(data['updated_at'], '%H:%M')}（{short}）" +
             (f' · 已超过 {limit//60} 分钟未更新' if stale else '')]
    windows = list(live_windows(data, now))
    resets = [f"{w['label']} {stamp(w['resets_at'])}" for w in windows if finite(w.get('resets_at'))]
    if resets:
        lines.append('重置：' + ' · '.join(resets))
    for w in windows:
        if w['bucket'] == 'spend_limit' and finite(w.get('used_usd')) and finite(w.get('limit_usd')):
            lines.append(f"网关支出 ${w['used_usd']:g} / ${w['limit_usd']:g}（不是账户余额）")
    return lines


def refresh_seconds(data):
    return 3
