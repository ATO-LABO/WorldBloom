"""Opt-in app/worker lifetime guard. No effects when the integration is disabled."""
from contextlib import contextmanager
from pathlib import Path
import os

from .errors import DeliveryError
from .lease import lease
from .contract import MAX_MANIFEST, is_link, safe_child, strict_json

STATE_ENV = "WORLDBLOOM_DELIVERY_STATE"
ENABLED_ENV = "WORLDBLOOM_DELIVERY_GUARD"


def require_no_pending_transaction(state):
    """Called under the shared lease, before loading mutable application code."""
    state = Path(state)
    transactions = safe_child(state, "transactions", managed=False)
    if not transactions.exists():
        return
    if not transactions.is_dir():
        raise DeliveryError("invalid_transaction_store")
    for folder in transactions.iterdir():
        if is_link(folder) or not folder.is_dir():
            raise DeliveryError("invalid_transaction_store")
        journal = safe_child(state, "transactions/" + folder.name + "/journal.json", managed=False)
        if not journal.exists():
            continue  # An abandoned staging folder precedes any target writes.
        if not journal.is_file() or journal.stat().st_size > MAX_MANIFEST:
            raise DeliveryError("invalid_journal")
        with journal.open("rb") as stream:
            blob = stream.read(MAX_MANIFEST + 1)
        if len(blob) > MAX_MANIFEST:
            raise DeliveryError("invalid_journal")
        value = strict_json(blob)
        if not isinstance(value, dict):
            raise DeliveryError("invalid_journal")
        if value.get("status") not in ("committed", "rolled_back"):
            raise DeliveryError("recovery_required")


@contextmanager
def hold_runtime_lease(*, enabled=False, state=None):
    if enabled is not True:
        yield
        return
    if not isinstance(state, (str, Path)) or not Path(state).is_absolute():
        raise DeliveryError("runtime_lease_config")
    with lease(Path(state) / "update.lock", exclusive=False):
        require_no_pending_transaction(state)
        yield


@contextmanager
def from_environment(environment=None):
    environment = os.environ if environment is None else environment
    if environment.get(ENABLED_ENV) not in (None, "0", "1"):
        raise DeliveryError("runtime_lease_config")
    with hold_runtime_lease(enabled=environment.get(ENABLED_ENV) == "1", state=environment.get(STATE_ENV)):
        yield


def run_guarded(entrypoint, *args, environment=None, **kwargs):
    with from_environment(environment):
        return entrypoint(*args, **kwargs)
