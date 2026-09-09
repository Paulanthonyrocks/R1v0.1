import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock

from app.services.traffic_signal_service import (
    TrafficSignalService,
    TrafficSignalControlError,
)


def _unconfigured_service():
    # No controller URL: placeholder default means no hardware to read/command.
    return TrafficSignalService(config={}, connection_manager=MagicMock())


class TestTrafficSignalServiceHonesty(unittest.TestCase):
    def test_unconfigured_states_empty(self):
        svc = _unconfigured_service()
        self.assertEqual(asyncio.run(svc.get_all_signal_states()), [])

    def test_unconfigured_warns_once(self):
        svc = _unconfigured_service()
        with self.assertLogs("app.services.traffic_signal_service", level="WARNING") as logs:
            asyncio.run(svc.get_all_signal_states())
            asyncio.run(svc.get_all_signal_states())
        warnings = [r for r in logs.records if "No traffic signal controller" in r.getMessage()]
        self.assertEqual(len(warnings), 1)

    def test_unconfigured_set_phase_raises(self):
        svc = _unconfigured_service()
        with self.assertRaises(TrafficSignalControlError):
            asyncio.run(svc.set_signal_phase("s1", "green"))

    def test_configured_service_reads_seed_states(self):
        svc = TrafficSignalService(
            config={
                "traffic_signal_controller": {
                    "api_base_url": "http://controller.local:8080",
                    "default_signals": [
                        {"signal_id": "sig_1", "location_description": "A & B"},
                    ],
                }
            },
            connection_manager=MagicMock(),
        )
        states = asyncio.run(svc.get_all_signal_states())
        self.assertEqual([s.signal_id for s in states], ["sig_1"])

    def test_str_phase_accepted_like_enum(self):
        # Router passes a validated phase string; the service must coerce it.
        svc = TrafficSignalService(
            config={"traffic_signal_controller": {"api_base_url": "http://x"}},
            connection_manager=AsyncMock(),
        )
        svc._client = MagicMock()

        async def fake_post(*args, **kwargs):
            resp = MagicMock()
            resp.json.return_value = {"status": "accepted", "message": "ok"}
            return resp

        svc._client.post = MagicMock(side_effect=fake_post)
        from datetime import datetime, timezone
        from app.models.signals import (
            SignalState,
            SignalPhaseEnum,
            SignalOperationalStatusEnum,
        )
        svc._signal_states["s1"] = SignalState(
            signal_id="s1",
            current_phase=SignalPhaseEnum.RED,
            operational_status=SignalOperationalStatusEnum.ONLINE,
            last_updated=datetime.now(timezone.utc),
        )
        out = asyncio.run(svc.set_signal_phase("s1", "GREEN"))
        self.assertEqual(out.signal_id, "s1")
        self.assertEqual(out.status.value, "accepted")


if __name__ == "__main__":
    unittest.main()
