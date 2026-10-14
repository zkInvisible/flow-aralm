from __future__ import annotations

import argparse
import html
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable


LOGGER = logging.getLogger("skinflow_alarm")
DEFAULT_API_URL = "https://api.skinflow.gg/ticket-giveaway"
SKINFLOW_URL = "https://skinflow.gg/free-skins"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/health"):
            self.send_error(404)
            return
        body = b"OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.debug("Health server: " + format, *args)


def start_health_server() -> ThreadingHTTPServer:
    port_value = os.getenv("PORT", "10000")
    server = ThreadingHTTPServer(("0.0.0.0", int(port_value)), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info("Health endpoint 0.0.0.0:%s/health üzerinde hazır", port_value)
    return server


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} negatif olamaz")
    return value


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    api_url: str
    poll_seconds: int
    alert_before_seconds: int
    price_cutoff_cents: int
    low_price_ticket_limit: int
    high_price_ticket_limit: int
    request_timeout_seconds: int
    state_file: Path

    @classmethod
    def from_env(cls, require_telegram: bool = True) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if require_telegram and (not token or not chat_id):
            raise ValueError(
                "TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID .env dosyasında tanımlanmalı"
            )

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            api_url=os.getenv("SKINFLOW_API_URL", DEFAULT_API_URL).strip(),
            poll_seconds=env_int("POLL_SECONDS", 20),
            alert_before_seconds=env_int("ALERT_BEFORE_SECONDS", 300),
            price_cutoff_cents=env_int("PRICE_CUTOFF_CENTS", 35),
            low_price_ticket_limit=env_int("LOW_PRICE_TICKET_LIMIT", 800),
            high_price_ticket_limit=env_int("HIGH_PRICE_TICKET_LIMIT", 1000),
            request_timeout_seconds=env_int("REQUEST_TIMEOUT_SECONDS", 15),
            state_file=Path(os.getenv("STATE_FILE", "alarm_state.json")),
        )


@dataclass(frozen=True)
class Giveaway:
    giveaway_id: str
    start_at: datetime
    end_at: datetime
    prize_cents: int
    total_tickets: int
    people_entered: int
    item_name: str

    @property
    def duration_seconds(self) -> float:
        return (self.end_at - self.start_at).total_seconds()

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Giveaway":
        item = payload.get("item") or {}
        return cls(
            giveaway_id=str(payload["id"]),
            start_at=parse_datetime(payload["start_date"]),
            end_at=parse_datetime(payload["end_date"]),
            prize_cents=int(payload["giveaway_offered"]),
            total_tickets=int(payload["total_ticket_amount"]),
            people_entered=int(payload.get("people_entered", 0)),
            item_name=str(item.get("market_hash_name") or "Bilinmeyen skin"),
        )


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ticket_limit(giveaway: Giveaway, config: Config) -> int:
    if giveaway.prize_cents <= config.price_cutoff_cents:
        return config.low_price_ticket_limit
    return config.high_price_ticket_limit


def select_alerts(
    giveaways: Iterable[Giveaway],
    config: Config,
    alerted_ids: set[str],
    now: datetime | None = None,
) -> list[Giveaway]:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected = []

    for giveaway in giveaways:
        remaining = (giveaway.end_at - current_time).total_seconds()
        is_hourly = 0 < giveaway.duration_seconds <= 60 * 60
        is_in_alert_window = 0 < remaining <= config.alert_before_seconds
        is_below_limit = giveaway.total_tickets <= ticket_limit(giveaway, config)
        if (
            is_hourly
            and is_in_alert_window
            and is_below_limit
            and giveaway.giveaway_id not in alerted_ids
        ):
            selected.append(giveaway)

    return selected


class SkinflowClient:
    def __init__(self, config: Config):
        self.config = config

    def fetch_giveaways(self) -> list[Giveaway]:
        request = urllib.request.Request(
            self.config.api_url,
            headers={
                "Accept": "application/json",
                "Origin": "https://skinflow.gg",
                "Referer": f"{SKINFLOW_URL}/",
                "User-Agent": "SkinflowHourlyAlarm/1.0",
            },
        )
        with urllib.request.urlopen(
            request, timeout=self.config.request_timeout_seconds
        ) as response:
            payload = json.load(response)

        if not isinstance(payload, list):
            raise ValueError("Skinflow API beklenmeyen bir yanıt döndürdü")
        return [Giveaway.from_api(item) for item in payload]


class TelegramClient:
    def __init__(self, config: Config):
        self.config = config

    def send(self, message: str) -> None:
        url = (
            f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        )
        body = urllib.parse.urlencode(
            {
                "chat_id": self.config.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(
            request, timeout=self.config.request_timeout_seconds
        ) as response:
            payload = json.load(response)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram mesajı reddedildi: {payload}")


class AlertState:
    def __init__(self, path: Path):
        self.path = path
        self.alerted_ids = self._load()

    def _load(self) -> set[str]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(value) for value in payload.get("alerted_ids", [])}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return set()

    def mark_alerted(self, giveaway_id: str) -> None:
        self.alerted_ids.add(giveaway_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps({"alerted_ids": sorted(self.alerted_ids)}, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)


def format_alert(giveaway: Giveaway, config: Config, now: datetime | None = None) -> str:
    current_time = now or datetime.now(timezone.utc)
    seconds_left = max(0, int((giveaway.end_at - current_time).total_seconds()))
    minutes, seconds = divmod(seconds_left, 60)
    limit = ticket_limit(giveaway, config)
    return (
        "🚨 <b>Skinflow düşük bilet alarmı</b>\n\n"
        f"🎁 <b>{html.escape(giveaway.item_name)}</b>\n"
        f"💵 Değer: <b>${giveaway.prize_cents / 100:.2f}</b>\n"
        f"🎟 Toplam bilet: <b>{giveaway.total_tickets:,}</b> "
        f"(limit: {limit:,})\n"
        f"👥 Katılan kişi: {giveaway.people_entered:,}\n"
        f"⏳ Kalan süre: <b>{minutes:02d}:{seconds:02d}</b>\n\n"
        f'<a href="{SKINFLOW_URL}">Çekilişe git</a>'
    )


def run_cycle(
    config: Config,
    skinflow: SkinflowClient,
    telegram: TelegramClient | None,
    state: AlertState,
    dry_run: bool,
) -> int:
    giveaways = skinflow.fetch_giveaways()
    alerts = select_alerts(giveaways, config, state.alerted_ids)
    hourly_count = sum(0 < item.duration_seconds <= 60 * 60 for item in giveaways)
    LOGGER.info("%d çekiliş, %d saatlik çekiliş bulundu", len(giveaways), hourly_count)

    for giveaway in alerts:
        message = format_alert(giveaway, config)
        if dry_run:
            print(message)
        else:
            assert telegram is not None
            telegram.send(message)
            state.mark_alerted(giveaway.giveaway_id)
        LOGGER.info("Alarm üretildi: %s", giveaway.item_name)

    return len(alerts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Skinflow saatlik çekilişlerini izleyip Telegram alarmı gönderir."
    )
    parser.add_argument("--once", action="store_true", help="Bir kez kontrol edip çık")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Telegram'a göndermeden oluşan alarmı terminale yaz",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    try:
        config = Config.from_env(require_telegram=not args.dry_run)
    except (ValueError, TypeError) as error:
        LOGGER.error("Ayar hatası: %s", error)
        return 2

    skinflow = SkinflowClient(config)
    telegram = None if args.dry_run else TelegramClient(config)
    state = AlertState(config.state_file)
    health_server = None if args.once else start_health_server()

    try:
        while True:
            try:
                run_cycle(config, skinflow, telegram, state, args.dry_run)
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
                LOGGER.error("Kontrol başarısız: %s", error)
                if args.once:
                    return 1
            except Exception:
                LOGGER.exception("Beklenmeyen hata")
                if args.once:
                    return 1

            if args.once:
                return 0
            time.sleep(config.poll_seconds)
    finally:
        if health_server is not None:
            health_server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
