import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from skinflow_alarm import AlertState, Config, Giveaway, select_alerts, ticket_limit


NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def config(state_file: Path = Path("state.json")) -> Config:
    return Config(
        telegram_bot_token="token",
        telegram_chat_id="chat",
        api_url="https://example.test",
        poll_seconds=20,
        alert_before_seconds=300,
        price_cutoff_cents=35,
        low_price_ticket_limit=800,
        high_price_ticket_limit=1000,
        request_timeout_seconds=15,
        state_file=state_file,
    )


def giveaway(
    *,
    giveaway_id: str = "hourly",
    prize_cents: int = 35,
    tickets: int = 800,
    remaining_seconds: int = 299,
    duration_seconds: int = 3600,
) -> Giveaway:
    end_at = NOW + timedelta(seconds=remaining_seconds)
    return Giveaway(
        giveaway_id=giveaway_id,
        start_at=end_at - timedelta(seconds=duration_seconds),
        end_at=end_at,
        prize_cents=prize_cents,
        total_tickets=tickets,
        people_entered=12,
        item_name="Test Skin",
    )


class AlarmRulesTest(unittest.TestCase):
    def test_35_cents_and_below_uses_800_limit(self):
        self.assertEqual(ticket_limit(giveaway(prize_cents=35), config()), 800)

    def test_above_35_cents_uses_1000_limit(self):
        self.assertEqual(ticket_limit(giveaway(prize_cents=36), config()), 1000)

    def test_selects_low_ticket_hourly_giveaway_in_last_five_minutes(self):
        selected = select_alerts([giveaway()], config(), set(), now=NOW)
        self.assertEqual([item.giveaway_id for item in selected], ["hourly"])

    def test_does_not_select_when_ticket_limit_is_exceeded(self):
        selected = select_alerts(
            [giveaway(tickets=801)], config(), set(), now=NOW
        )
        self.assertEqual(selected, [])

    def test_does_not_select_before_alert_window(self):
        selected = select_alerts(
            [giveaway(remaining_seconds=301)], config(), set(), now=NOW
        )
        self.assertEqual(selected, [])

    def test_does_not_select_non_hourly_giveaway(self):
        selected = select_alerts(
            [giveaway(duration_seconds=86_400)], config(), set(), now=NOW
        )
        self.assertEqual(selected, [])

    def test_does_not_repeat_an_alerted_giveaway(self):
        selected = select_alerts(
            [giveaway()], config(), {"hourly"}, now=NOW
        )
        self.assertEqual(selected, [])


class AlertStateTest(unittest.TestCase):
    def test_persists_alerted_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = AlertState(state_path)
            state.mark_alerted("giveaway-1")

            self.assertEqual(AlertState(state_path).alerted_ids, {"giveaway-1"})


if __name__ == "__main__":
    unittest.main()
