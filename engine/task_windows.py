"""Optional authored work windows on the native calendar's absolute minute axis.

Windows constrain actual work/handoff, not just forecasts. Missing windows keep
the existing unrestricted contract. They never certify attendance or consent.
"""
from engine.calendar_v2 import minute_for


def compile_windows(tasks, calendar):
    result = {}
    for task, spec in tasks.items():
        if 'windows' not in spec:
            continue
        values = spec['windows']
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise ValueError('Task windows must be a bounded nonempty list')
        intervals = []
        for window in values:
            if not isinstance(window, dict) or set(window) != {'start', 'end'}:
                raise ValueError('Task window requires start and end')
            points = []
            for key in ('start', 'end'):
                value = window[key]
                if not isinstance(value, dict) or set(value) != {'date', 'time'}:
                    raise ValueError('Task window point requires date and time')
                points.append(minute_for(calendar, value['date'], value['time']))
            start, end = points
            if start >= end or (intervals and start < intervals[-1][1]):
                raise ValueError('Task windows must be ordered, positive and nonoverlapping')
            intervals.append((start, end))
        result[task] = tuple(intervals)
    return result


def permits(windows, task, start, finish):
    return task not in windows or any(a <= start < finish <= b for a, b in windows[task])
