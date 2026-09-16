from __future__ import annotations

import unittest

from v4_physical_rebind import ReadOnlyBrowserRebinder


class _StoreNeverBound:
    def get_browser_binding(self, project_id, channel):
        return None


class _NoBrowserAdapter:
    def rebind(self, *args, **kwargs):
        raise AssertionError("rebind must not be called for a never-bound local-only Master")


class _NoBrowserDriver:
    async def observe_current_binding(self, channel):
        raise AssertionError("physical browser must not be observed for a never-bound local-only Master")


async def _no_auth(_channel):
    raise AssertionError("auth probe must not run for a never-bound local-only Master")


class LocalOnlyMasterRebindTests(unittest.TestCase):
    def _rebinder(self):
        return ReadOnlyBrowserRebinder(
            store=_StoreNeverBound(), browser_adapter=_NoBrowserAdapter(), driver=_NoBrowserDriver(),
            auth_probe=_no_auth, project_id="p1", channel="master", actor_id="A", timeout_seconds=1)

    def test_resume_without_any_prior_master_binding_is_local_only_and_needs_no_browser(self):
        result = self._rebinder()({"master_epoch": 2})
        self.assertEqual("LOCAL_ONLY_MASTER", result["status"])
        self.assertEqual(2, result["master_epoch"])
        self.assertIsNone(result["conversation_url"])

    def test_health_without_any_prior_master_binding_is_healthy_local_only(self):
        result = self._rebinder().health_probe()
        self.assertEqual("HEALTHY", result["status"])
        self.assertEqual("LOCAL_ONLY_MASTER", result["mode"])


if __name__ == "__main__":
    unittest.main()