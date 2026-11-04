"""Shared presentation of plan validity and explicitly verified triggers."""
from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')


def trigger_status(report):
    check = report.get('trigger_verification') or {}
    if check.get('status') != 'verified':
        return None, '盘中触发未核验'
    count = check.get('triggered_count')
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None, '盘中触发未核验'
    return count, '已核验触发数'


def plan_status(report, now=None):
    now = now or datetime.now(SHANGHAI)
    now = now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)
    plan = report.get('plan_date')
    try:
        day = datetime.strptime(plan, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return '预案日期待核验；不可直接执行。'
    if now.date() < day:
        return f'{plan}条件预案；盘前和盘中条件尚待核验。'
    if now.date() == day and now.hour < 15:
        return f'{plan}计划日进行中；静态页面不提供实时触发结果。'
    count, _ = trigger_status(report)
    result = '盘中触发结果仍未核验' if count is None else f'已核验触发{count}次'
    return f'{plan}计划日已结束，{result}。本页保留当时条件，不可沿用为下一交易日买单。'
