"""WB-WORLDGROW-001 段階5c-2: epoch chain progress strip + waiting hint.

Read-only rendering over execution.epoch_chain.EpochChain.current() -- never
writes. Used by viewer/run_workspace.py (result/status page, and the demand
tab's waiting hint) so a page with no active chain stays byte-identical to
before this stage."""
import time

from execution.provenance import ConfigError
from viewer import pages

E = pages._escape

STEP_LABELS = {"run": "実験中", "propose": "提案中", "approve": "承認待ち", "retire": "淘汰中", "done": "完了"}
STATE_LABELS = {"waiting": "承認待ち", "stopping": "停止中", "stopped": "停止", "failed": "失敗", "completed": "完了"}


def relevant_chain(handler, config_id):
    """The current epoch chain, if config_id is either its base_config_id
    (viewer/run_browse.conditions -- the user-selected config, shown before
    a run starts and in the gaps between an epoch's jobs, when there is no
    running job to derive a per-epoch config from) or its LAST epoch's own
    derived config_id (viewer/run_workspace.render -- an already-running or
    finished epoch's job always has that config_id, never the base one). An
    older epoch's own result page (still viewable once the chain has moved
    on) shows no strip, only the epoch the chain is currently sitting on.
    None whenever there is no job_store (read-only viewer), no active/
    recent chain at all, or the chain store can't be read right now (M2,
    Opus review: execution.epoch_chain.EpochChain._all() already skips a
    single broken chain directory, but this is a second, independent guard
    -- any other read failure must never turn an unrelated page's own GET
    into a 404/500)."""
    if not config_id:
        return None
    job_store = getattr(handler.server, "job_store", None)
    if job_store is None:
        return None
    from execution.epoch_chain import EpochChain
    try:
        chain = EpochChain(job_store, getattr(handler.server, "settings_path", None)).current()
    except (ConfigError, ValueError, OSError):
        return None
    if chain is None or config_id not in (chain["base_config_id"], chain["epochs"][-1]["config_id"]):
        return None
    return chain


def _step_label(chain):
    return STATE_LABELS.get(chain["state"]) or STEP_LABELS.get(chain["epochs"][-1]["step"], chain["epochs"][-1]["step"])


def _extra_status(chain):
    """R1 (Opus review): a failed chain names why (chain.error.message);
    a running one shows how stale its last tick was, the same "N分前"
    phrasing viewer/local_status.py's GPU lease row already uses."""
    if chain["state"] == "failed":
        message = (chain.get("error") or {}).get("message")
        return f" · {message}" if message else ""
    # R-1 (Opus re-review): updated_at only moves on _save, and the run step
    # never saves while its GA job is running -- a healthy multi-hour run
    # would read as stalled. Show staleness only for the short steps.
    if chain["state"] == "running" and chain["epochs"][-1]["step"] != "run":
        updated_at = chain.get("updated_at")
        if isinstance(updated_at, (int, float)):
            minutes = max(0, int((time.time() - updated_at) / 60))
            return " · 最終更新たった今" if minutes == 0 else f" · 最終更新{minutes}分前"
    return ""


def _summary_text(chain):
    epochs = chain["epochs"]
    approved = sum(1 for e in epochs if e.get("approved_rev"))
    retired = sum(len(e.get("retired") or []) for e in epochs)
    return (f'エポック連鎖 {len(epochs)}/{chain["max_epochs"]} · 段階: {_step_label(chain)} · '
            f'承認 {approved} 件・淘汰 {retired} 件{_extra_status(chain)}')


def _history_row(epoch):
    n = epoch["index"] + 1
    job_id = epoch.get("run_job_id")
    head = f'<a href="/jobs/{E(job_id)}">第{n}エポック</a>' if job_id else f"第{n}エポック"
    parts = [head]
    if epoch.get("patch_id"):
        parts.append(f'拡張 {E(epoch["patch_id"])}')
    if epoch.get("notes"):
        parts.append("・".join(E(note) for note in epoch["notes"]))
    return f'<li>{" ・ ".join(parts)}</li>'


def strip_html(chain):
    """"" when chain is None -- callers must only insert this where a
    missing chain leaves the page byte-identical to before this stage."""
    if not chain:
        return ""
    can_stop = chain["state"] in ("running", "waiting")
    can_continue = chain["state"] == "waiting"
    history = "".join(_history_row(e) for e in chain["epochs"])
    return (
        f'<div class="epoch-chain-strip" data-epoch-chain data-chain-id="{E(chain["chain_id"])}" '
        f'data-state="{E(chain["state"])}">'
        '<p class="epoch-chain-status">'
        f'<span data-epoch-summary>{E(_summary_text(chain))}</span> '
        f'<button type="button" data-epoch-stop{"" if can_stop else " hidden"}>停止</button>'
        f'<button type="button" data-epoch-continue{"" if can_continue else " hidden"}>次のエポックへ</button>'
        "</p>"
        '<details class="epoch-chain-history"><summary>エポックの履歴</summary><ol>'
        + history + "</ol></details></div>"
    )


def waiting_hint(chain):
    """需要タブの先頭に出す手順文。chain が waiting でなければ空文字。"""
    if chain is None or chain["state"] != "waiting":
        return ""
    n = chain["epochs"][-1]["index"] + 1
    return (
        f'<p class="epoch-chain-hint">第 {n} エポック: 承認待ち。'
        "承認または却下 →（必要なら）枯らす → 『次のエポックへ』</p>"
    )
