from __future__ import annotations

from .browser_adapter import BrowserAdapter
from .models import IntentState


def recover_pending_intents(adapter: BrowserAdapter) -> list[tuple[str, str]]:
    outcomes: list[tuple[str, str]] = []
    for row in adapter.store.pending_intents():
        intent_id = str(row["intent_id"])
        if str(row.get("action_kind") or "") == "LOCAL_EXECUTION":
            if row["state"] == IntentState.RESPONSE_CAPTURED.value:
                adapter.store.finalize_intent(intent_id)
                outcomes.append((intent_id, "FINALIZED"))
            elif row["state"] == IntentState.PREPARED.value:
                outcomes.append((intent_id, "LOCAL_EXECUTION_PREPARED"))
            else:
                # A local process has no reliable remote reconciliation API.
                # Never rerun a possibly-started process automatically.
                outcomes.append((intent_id, "LOCAL_EXECUTION_RECONCILIATION_REQUIRED"))
            continue
        if row["state"] == IntentState.RESPONSE_CAPTURED.value:
            adapter.store.finalize_intent(intent_id)
            outcomes.append((intent_id, "FINALIZED"))
            continue
        result = adapter.reconcile(intent_id)
        outcomes.append((intent_id, str(result["state"])))
    return outcomes
