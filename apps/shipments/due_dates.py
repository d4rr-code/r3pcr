import datetime

from apps.supervisor.models import SystemConfig


URGENCY_BUSINESS_DAYS = {
    'rush': 3,
    'urgent': 5,
    'priority': 10,
    'standard': 15,
    'normal': 15,
}


def urgency_business_days():
    values = dict(URGENCY_BUSINESS_DAYS)
    for key in ('standard', 'priority', 'urgent', 'rush'):
        raw = SystemConfig.get(f'urgency_days_{key}', '')
        try:
            days = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= days <= 60:
            values[key] = days
    values['normal'] = values['standard']
    return values


def urgency_days_for(urgency):
    return urgency_business_days().get(
        urgency or 'standard',
        URGENCY_BUSINESS_DAYS['standard'],
    )


def add_business_days(start_dt, days):
    """Return the date a number of weekdays after the starting date."""
    current = start_dt.date() if hasattr(start_dt, 'date') else start_dt
    added = 0
    while added < days:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def business_days_diff(from_date, to_date):
    """Return a signed weekday count; positive means the target is ahead."""
    from_date = from_date.date() if hasattr(from_date, 'date') else from_date
    to_date = to_date.date() if hasattr(to_date, 'date') else to_date
    if from_date == to_date:
        return 0

    sign = 1 if to_date > from_date else -1
    start, end = (
        (from_date, to_date) if to_date > from_date else (to_date, from_date)
    )
    count = 0
    current = start
    while current < end:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return sign * count


def annotate_due(shipments, today):
    """Attach deadline display attributes to each shipment."""
    urgency_days = urgency_business_days()
    for shipment in shipments:
        allocated_days = urgency_days.get(
            shipment.urgency or 'standard',
            urgency_days['standard'],
        )
        shipment.due_date = add_business_days(
            shipment.submitted_at,
            allocated_days,
        )
        shipment.due_days_left = business_days_diff(today, shipment.due_date)
        if shipment.due_days_left < 0:
            shipment.due_color = 'red'
        elif shipment.due_days_left <= 1:
            shipment.due_color = 'orange'
        else:
            shipment.due_color = 'green'
