"""Read-only physical ChatGPT session rebind for the V4 Master supervisor."""

from __future__ import annotations

import asyncio
import concurrent.futures
import datetime as dt
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any

from gui_transport import validate_conversation_url


class PhysicalRebindError(RuntimeError):
    """The physical session could not be proven safe to rebind."""


def _run_sync(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, value).result()


class ReadOnlyBrowserRebinder:
    """Verify and rebind an existing logical conversation without sending.

    The driver must provide ``observe_current_binding(channel)``.  That
    observation is required to report the driver's durable URL, the physical
    URL currently visible in the browser, and a read-only snapshot.  The
    rebinder never calls a URL-targeted snapshot method because those methods
    may navigate to the requested URL and would silently erase a three-way
    binding conflict.
    """

    def __init__(
        self,
        *,
        store: Any,
        browser_adapter: Any,
        driver: Any,
        auth_probe: Callable[[str], Any],
        project_id: str,
        channel: str = "master",
        actor_id: str = "A",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.browser_adapter = browser_adapter
        self.driver = driver
        self.auth_probe = auth_probe
        self.project_id = str(project_id or "").strip()
        self.channel = str(channel or "").strip()
        self.actor_id = str(actor_id or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        if not self.project_id or not self.channel or not self.actor_id:
            raise ValueError("PHYSICAL_REBIND_IDENTITY_INVALID")
        if self.timeout_seconds <= 0:
            raise ValueError("PHYSICAL_REBIND_TIMEOUT_INVALID")

    def __call__(self, resume: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(resume, Mapping):
            raise PhysicalRebindError("RESUME_RESULT_INVALID")
        try:
            epoch = int(resume["master_epoch"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PhysicalRebindError("MASTER_EPOCH_MISSING") from exc

        binding = self.store.get_browser_binding(self.project_id, self.channel)
        if binding is None:
            return {
                "status": "LOCAL_ONLY_MASTER",
                "master_epoch": epoch,
                "conversation_url": None,
                "evidence": {"source": "NO_MASTER_BROWSER_BINDING"},
            }
        binding, url, evidence = self._verify_binding(
            source="READ_ONLY_PHYSICAL_REBIND", binding=binding
        )
        evidence = {**evidence, "master_epoch": epoch}
        persisted = self.browser_adapter.rebind(
            self.project_id,
            self.channel,
            actor_id=self.actor_id,
            conversation_url=url,
            predecessor_url=url,
            reason="PHYSICAL_SESSION_REBOUND",
            evidence=evidence,
        )
        return {
            "status": "REBOUND",
            "master_epoch": epoch,
            "conversation_url": url,
            "binding": dict(persisted) if isinstance(persisted, Mapping) else persisted,
            "evidence": evidence,
        }

    def verify_current(
        self,
        *,
        daemon_epoch: int,
        master_epoch: int,
    ) -> dict[str, Any]:
        daemon = int(daemon_epoch)
        master = int(master_epoch)
        if daemon < 1 or master < 0:
            raise PhysicalRebindError("PHYSICAL_VERIFY_EPOCH_INVALID")
        try:
            binding, url, evidence = self._verify_binding(
                source="READ_ONLY_PHYSICAL_VERIFY"
            )
        except PhysicalRebindError as exc:
            # A missing/dead physical Chrome session may be restored only to
            # the already durable canonical URL.  URL conflicts and auth
            # blockers remain fail-closed and never navigate.
            if not str(exc).startswith("BROWSER_OBSERVATION_FAILED:"):
                raise
            binding = self.store.get_browser_binding(
                self.project_id, self.channel
            )
            if not isinstance(binding, Mapping):
                raise PhysicalRebindError(
                    "MASTER_BROWSER_BINDING_MISSING"
                ) from exc
            try:
                url = validate_conversation_url(
                    str(binding.get("conversation_url") or "")
                )
            except (TypeError, ValueError) as url_exc:
                raise PhysicalRebindError(
                    "MASTER_BROWSER_URL_INVALID"
                ) from url_exc
            restore = getattr(self.driver, "restore_known_binding", None)
            if not callable(restore):
                raise
            try:
                observed = _run_sync(
                    asyncio.wait_for(
                        restore(self.channel, url),
                        timeout=self.timeout_seconds,
                    )
                )
            except Exception as restore_exc:
                raise PhysicalRebindError(
                    "BROWSER_RESTORE_FAILED:"
                    + type(restore_exc).__name__
                    + ":"
                    + str(restore_exc)
                ) from restore_exc
            if not isinstance(observed, Mapping):
                raise PhysicalRebindError(
                    "BROWSER_RESTORE_OBSERVATION_INVALID"
                )
            auth = _run_sync(self.auth_probe(self.channel))
            if (
                not isinstance(auth, Mapping)
                or str(auth.get("status") or "") != "AUTHENTICATED"
            ):
                status = str(
                    auth.get("status")
                    if isinstance(auth, Mapping)
                    else "INVALID"
                )
                raise PhysicalRebindError(
                    f"AUTH_BLOCKED:{status}"
                )
            try:
                driver_url = validate_conversation_url(
                    str(observed.get("driver_url") or "")
                )
                physical_url = validate_conversation_url(
                    str(observed.get("physical_url") or "")
                )
            except (TypeError, ValueError) as observed_exc:
                raise PhysicalRebindError(
                    "BROWSER_RESTORED_URL_INVALID"
                ) from observed_exc
            if (
                driver_url != url
                or physical_url != url
                or driver_url != physical_url
            ):
                raise PhysicalRebindError(
                    "RECONCILE_REQUIRED:"
                    f"sqlite={url};driver={driver_url};physical={physical_url}"
                )
            snapshot = str(observed.get("snapshot") or "")
            if url not in snapshot:
                raise PhysicalRebindError(
                    "BROWSER_URL_NOT_CONFIRMED"
                )
            session = str(observed.get("session") or "").strip()
            if not session:
                raise PhysicalRebindError(
                    "BROWSER_RESTORED_SESSION_MISSING"
                )
            evidence = {
                "source": "READ_ONLY_PHYSICAL_VERIFY",
                "auth_status": "AUTHENTICATED",
                "driver_url": driver_url,
                "physical_url": physical_url,
                "session": session,
                "snapshot_sha256": hashlib.sha256(
                    snapshot.encode("utf-8")
                ).hexdigest(),
                "binding_generation": int(
                    binding.get("generation", 0)
                ),
                "restore_source": "KNOWN_DURABLE_CANONICAL_URL",
            }
        evidence = {
            **evidence,
            "daemon_epoch": daemon,
            "master_epoch": master,
            "verified_at": dt.datetime.now(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        persisted = self.browser_adapter.rebind(
            self.project_id,
            self.channel,
            actor_id=self.actor_id,
            conversation_url=url,
            predecessor_url=url,
            reason="PHYSICAL_SESSION_VERIFIED",
            evidence=evidence,
        )
        return {
            "status": "VERIFIED",
            "daemon_epoch": daemon,
            "master_epoch": master,
            "conversation_url": url,
            "binding": (
                dict(persisted)
                if isinstance(persisted, Mapping)
                else persisted
            ),
            "evidence": evidence,
        }

    def health_probe(self) -> dict[str, Any]:
        """Prove the bound physical session without changing SQLite state."""

        binding = self.store.get_browser_binding(self.project_id, self.channel)
        if binding is None:
            return {
                "status": "HEALTHY",
                "mode": "LOCAL_ONLY_MASTER",
                "conversation_url": None,
                "evidence": {"source": "NO_MASTER_BROWSER_BINDING"},
            }
        try:
            _binding, url, evidence = self._verify_binding(
                source="READ_ONLY_PHYSICAL_HEALTH", binding=binding
            )
        except PhysicalRebindError as exc:
            return {
                "status": "PHYSICAL_UNAVAILABLE",
                "reason": str(exc),
            }
        return {
            "status": "HEALTHY",
            "conversation_url": url,
            "evidence": evidence,
        }

    def _verify_binding(
        self, *, source: str, binding: Mapping[str, Any] | None = None
    ) -> tuple[Mapping[str, Any], str, dict[str, Any]]:
        if binding is None:
            binding = self.store.get_browser_binding(self.project_id, self.channel)
        if not isinstance(binding, Mapping):
            raise PhysicalRebindError("MASTER_BROWSER_BINDING_MISSING")
        try:
            url = validate_conversation_url(str(binding.get("conversation_url") or ""))
        except (TypeError, ValueError) as exc:
            raise PhysicalRebindError("MASTER_BROWSER_URL_INVALID") from exc

        auth = _run_sync(self.auth_probe(self.channel))
        if not isinstance(auth, Mapping) or str(auth.get("status") or "") != "AUTHENTICATED":
            status = str(auth.get("status") if isinstance(auth, Mapping) else "INVALID")
            raise PhysicalRebindError(f"AUTH_BLOCKED:{status}")

        observe = getattr(self.driver, "observe_current_binding", None)
        if not callable(observe):
            raise PhysicalRebindError("PHYSICAL_OBSERVER_UNAVAILABLE")
        try:
            observed = _run_sync(
                asyncio.wait_for(
                    observe(self.channel),
                    timeout=self.timeout_seconds,
                )
            )
        except Exception as exc:
            raise PhysicalRebindError(f"BROWSER_OBSERVATION_FAILED:{type(exc).__name__}") from exc
        if not isinstance(observed, Mapping):
            raise PhysicalRebindError("BROWSER_OBSERVATION_INVALID")
        try:
            driver_url = validate_conversation_url(str(observed.get("driver_url") or ""))
            physical_url = validate_conversation_url(str(observed.get("physical_url") or ""))
        except (TypeError, ValueError) as exc:
            raise PhysicalRebindError("BROWSER_OBSERVED_URL_INVALID") from exc
        if driver_url != url or physical_url != url or driver_url != physical_url:
            raise PhysicalRebindError(
                "RECONCILE_REQUIRED:"
                f"sqlite={url};driver={driver_url};physical={physical_url}"
            )
        text = str(observed.get("snapshot") or "")
        if url not in text:
            raise PhysicalRebindError("BROWSER_URL_NOT_CONFIRMED")
        session = str(observed.get("session") or "").strip()

        evidence = {
            "source": source,
            "auth_status": "AUTHENTICATED",
            "driver_url": driver_url,
            "physical_url": physical_url,
            "session": session or None,
            "snapshot_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "binding_generation": int(binding.get("generation", 0)),
        }
        return binding, url, evidence

