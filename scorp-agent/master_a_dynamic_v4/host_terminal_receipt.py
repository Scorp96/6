"""Model-free integrity check for browser-host terminal events.

The browser host (NOT the GPT reply text) must generate a secret key outside
Git and must sign a real explicit terminal event. This module only verifies
receipts; it does NOT infer a final event from page stability, Stop-button
absence, saved transcripts or host liveness. The current Chrome Use driver
has no terminal event emitter, so production must continue failing closed.

WARNING: possession of a key alone does not attest that a UI event exists.
The trusted host event producer and key custody require independent review.
This is a cryptographic *integrity gate*, not proof of browser provenance.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import re
from typing import Sequence

from .session_admission import _canonical_conversation_url


_PROTOCOL="scorp.browser-terminal-receipt/1"
_SHA256=re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID=re.compile(r"^[0-9a-f]{32}$")


@dataclasses.dataclass(frozen=True)
class HostTerminalReceipt:
    protocol: str
    event_id: str
    sequence: int
    session_id: str
    conversation_url: str
    binding_generation: int
    intent_id: str
    sampled_at_ms: int
    response_sha256: str
    generating: bool
    tool_pending: bool
    terminal_event: str
    signature: str = ""


def _unsigned_payload(receipt: HostTerminalReceipt) -> bytes:
    fields=dataclasses.asdict(receipt)
    fields.pop("signature")
    return json.dumps(fields,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")


def seal_test_host_receipt(receipt: HostTerminalReceipt, key: bytes) -> HostTerminalReceipt:
    """Test/isolated-host helper; never call on model-origin evidence.

    A runtime signer must *independently obtain* terminal events from its
    trusted browser transport; just copying a model field and signing it
    defeats the whole trust boundary.
    """
    if not isinstance(key,bytes) or len(key)<32:
        raise ValueError("HOST_RECEIPT_KEY_INVALID")
    return dataclasses.replace(
        receipt,
        signature=hmac.new(key,_unsigned_payload(receipt),hashlib.sha256).hexdigest(),
    )


def verify_host_terminal_receipts(
    samples: Sequence[object],
    receipts: Sequence[HostTerminalReceipt] | None,
    *,
    key: bytes | None,
) -> str | None:
    """Return a fixed failure reason, or None if both receipts verify.

    Strict last-two pairing, sequence progression, same event_id, full
    scope binding, and HMAC verification. Never return transcript data.
    """
    if not isinstance(key,bytes) or len(key)<32:
        return "HOST_ATTESTATION_KEY_UNAVAILABLE"
    if not isinstance(receipts,Sequence) or isinstance(receipts,(str,bytes)) or len(receipts)!=2:
        return "TWO_SIGNED_HOST_RECEIPTS_REQUIRED"
    if not isinstance(samples,Sequence) or len(samples)<2:
        return "TWO_FRESH_OBSERVATIONS_REQUIRED"
    a,b=receipts
    if not isinstance(a,HostTerminalReceipt) or not isinstance(b,HostTerminalReceipt):
        return "HOST_RECEIPT_SHAPE_INVALID"
    if a.protocol!=_PROTOCOL or b.protocol!=_PROTOCOL:
        return "HOST_RECEIPT_PROTOCOL_INVALID"
    if not _EVENT_ID.fullmatch(a.event_id) or a.event_id!=b.event_id:
        return "HOST_TERMINAL_EVENT_ID_INVALID"
    if type(a.sequence) is not int or type(b.sequence) is not int or a.sequence<0 or b.sequence<=a.sequence:
        return "HOST_RECEIPT_SEQUENCE_REPLAY"
    canonical_a=_canonical_conversation_url(a.conversation_url)
    canonical_b=_canonical_conversation_url(b.conversation_url)
    if not canonical_a or canonical_a!=a.conversation_url or canonical_b!=canonical_a:
        return "HOST_RECEIPT_CONVERSATION_INVALID"
    for sample,receipt in zip(samples[-2:],receipts):
        if type(receipt.binding_generation) is not int or type(receipt.sampled_at_ms) is not int:
            return "HOST_RECEIPT_SCOPE_INVALID"
        if type(receipt.generating) is not bool or type(receipt.tool_pending) is not bool:
            return "HOST_RECEIPT_PROGRESS_INVALID"
        if receipt.generating or receipt.tool_pending or receipt.terminal_event!="TURN_FINAL_CONFIRMED":
            return "HOST_RECEIPT_TERMINAL_NOT_VERIFIED"
        if not isinstance(receipt.session_id,str) or not receipt.session_id.strip():
            return "HOST_RECEIPT_SCOPE_INVALID"
        if not isinstance(receipt.intent_id,str) or not receipt.intent_id.strip():
            return "HOST_RECEIPT_SCOPE_INVALID"
        if not isinstance(receipt.response_sha256,str) or not _SHA256.fullmatch(receipt.response_sha256):
            return "HOST_RECEIPT_RESPONSE_INVALID"
        for field in ("session_id","conversation_url","binding_generation",
                      "intent_id","sampled_at_ms","response_sha256",
                      "generating","tool_pending"):
            observed=getattr(sample,field,None)
            expected=getattr(receipt,field)
            if type(observed)!=type(expected) or observed!=expected:
                return "HOST_RECEIPT_SAMPLE_MISMATCH"
        if not isinstance(receipt.signature,str) or not _SHA256.fullmatch(receipt.signature):
            return "HOST_RECEIPT_SIGNATURE_INVALID"
        digest=hmac.new(key,_unsigned_payload(receipt),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest,receipt.signature):
            return "HOST_RECEIPT_SIGNATURE_INVALID"
    if a.response_sha256!=b.response_sha256:
        return "HOST_RECEIPT_RESPONSE_CHANGED"
    return None
