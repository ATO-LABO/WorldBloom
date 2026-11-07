"""Atomic interval reservations shared by individual and joint providers."""
from copy import deepcopy


class ActivityBookings:
    def __init__(self, external=lambda key, start, finish: False):
        self.entries = {}
        self.external = external

    def conflicts(self, key, start, finish, *, excluding=None):
        if self.external(key, start, finish):
            return True
        return any(ident != excluding and key in entry['keys']
                   and start < entry['finish'] and entry['start'] < finish
                   for ident, entry in self.entries.items())

    def reserve_all(self, ident, start, finish, keys):
        if (ident in self.entries or type(start) is not int or type(finish) is not int
                or start < 0 or finish <= start or not keys or len(set(keys)) != len(keys)):
            raise ValueError('Invalid activity booking')
        if any(self.conflicts(key, start, finish) for key in keys):
            return False
        self.entries[ident] = {'start': start, 'finish': finish, 'keys': sorted(keys)}
        return True

    def release(self, ident):
        self.entries.pop(ident, None)

    def is_busy(self, actor, at):
        return any('actor:'+actor in e['keys'] and e['start'] <= at < e['finish']
                   for e in self.entries.values())

    def snapshot(self):
        return deepcopy(self.entries)

    def validate(self, entries):
        staged = ActivityBookings()
        for ident, entry in entries.items():
            if set(entry) != {'start', 'finish', 'keys'} or not staged.reserve_all(
                    ident, entry['start'], entry['finish'], entry['keys']):
                raise ValueError('Conflicting restored activity bookings')
        return staged.entries
