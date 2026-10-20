"""Claude's independent usage windows; never infer a credit balance."""
from dataclasses import dataclass
from datetime import datetime
import math
import time


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
        label = {'five_hour': '5 小时', 'seven_day': '每周', 'spend_limit': '支出上限'}.get(
            key, str(key).replace('seven_day_', '每周 · ')[:40])
        windows.append(dict(bucket=key, label=label, remaining=max(0, min(100, 100-value['used_percentage'])),
                            resets_at=reset if finite(reset) else None,
                            used_usd=value.get('used_usd'), limit_usd=value.get('limit_usd')))
    windows.sort(key=lambda w: ({'five_hour': 0, 'seven_day': 1}.get(w['bucket'], 2), w['bucket']))
    return dict(windows=windows, updated_at=item.get('at', 0), source='statusline', error=None,
                session_id=item.get('session_id'), model=item.get('model'),
                context=item.get('context_window') or {}, cost=item.get('cost') or {})


@dataclass(frozen=True)
class QuotaView:
    mode: str
    label: str
    remaining: float | None = None
    bucket: str | None = None


def is_cached(data, now=None):
    return bool(data.get('error') or (time.time() if now is None else now)-data.get('updated_at', 0) > 300)


def quota_rows(data, now=None):
    now = time.time() if now is None else now
    rows = []
    for window in data.get('windows') or []:
        if finite(window.get('resets_at')) and window['resets_at'] <= now:
            continue
        left = window.get('remaining')
        if not finite(left):
            continue
        label = f"{window['label']}剩余：{left:g}%"
        if is_cached(data, now):
            label += ' *'
        rows.append(QuotaView('plan' if left > 0 else 'depleted', label, left, window['bucket']))
    return rows or [QuotaView('unknown', '额度：待同步')]


def quota_view(data):
    return quota_rows(data)[0]


def quota_label(data, compact=False):
    return ' / '.join(row.label for row in quota_rows(data))


def quota_details(data):
    lines = [quota_label(data)]
    for window in data.get('windows') or []:
        try:
            reset = datetime.fromtimestamp(window['resets_at']).astimezone().strftime('%m/%d %H:%M') if window.get('resets_at') else '未知'
        except (ValueError, OSError, OverflowError):
            reset = '未知'
        lines.append(f"{window['label']} · 重置时间 {reset}")
        if window['bucket'] == 'spend_limit' and finite(window.get('used_usd')) and finite(window.get('limit_usd')):
            lines.append(f"网关支出：${window['used_usd']:g} / ${window['limit_usd']:g}（不是账户余额）")
    context = data.get('context') or {}
    if finite(context.get('remaining_percentage')):
        lines.append(f"当前会话上下文剩余：{context['remaining_percentage']:g}%（不是套餐额度）")
    cost = (data.get('cost') or {}).get('total_cost_usd')
    if finite(cost):
        lines.append(f"会话标价估算：${cost:.4f}（不代表实际扣款或余额）")
    if data.get('model'):
        lines.append('最近会话模型：'+data['model'])
    lines += ['5 小时与每周限额独立计算，不能相加。',
              '模型专属限额仅在接口返回时显示；额外用量余额未获取。']
    if data.get('updated_at'):
        lines.append('最近同步：'+datetime.fromtimestamp(data['updated_at']).astimezone().strftime('%m/%d %H:%M:%S'))
    lines += ['* 表示超过 5 分钟未更新或读取失败；到期窗口等待重新同步。',
              '刷新仅重读本机数据；Claude Code 状态栏提供新数据后自动更新。',
              '可在右键菜单打开 Claude 官方用量页面。']
    return '\n'.join(lines)


def refresh_seconds(data):
    return 3
