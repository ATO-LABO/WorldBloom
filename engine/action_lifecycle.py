"""One action-window lifecycle over caller-owned, checkpointed pending data."""
from engine.time_kernel import ActionWindow

class ActionLifecycle:
    def __init__(self,kernel,pending,*,start_key='start',finish_key='finish',actor_key='actor'):
        self.kernel=kernel
        self.pending=pending
        self.start_key=start_key;self.finish_key=finish_key;self.actor_key=actor_key

    def _items(self):
        store=self.pending()
        return list(store.items()) if isinstance(store,dict) else [(p[self.actor_key],p) for p in store]

    def start(self,actor,payload,duration,*,start=None):
        store=self.pending()
        if any(a==actor for a,_ in self._items()):raise ValueError('Actor already has a pending action')
        window=self.kernel.window(duration,start=start)
        if isinstance(store,dict):store[actor]=payload
        else:store.append(payload)
        return window

    def complete_due(self,at,*,key):
        store=self.pending()
        for actor,payload in sorted(self._items(),key=key):
            if payload[self.finish_key]==at:
                if isinstance(store,dict):del store[actor]
                else:store.remove(payload)
                yield actor,payload

    def interrupt(self,actor):
        store=self.pending()
        if isinstance(store,dict):return store.pop(actor)
        payload=next(p for a,p in self._items() if a==actor);store.remove(payload);return payload

    def accrued(self,payload,at,*,cursor='last_progress'):
        return ActionWindow(payload[self.start_key],payload[self.finish_key],self.kernel.axis.unit).elapsed(payload[cursor],at)

    def instant(self,actor,payload,apply):
        self.start(actor,payload,0)
        try:return apply()
        finally:self.interrupt(actor)
