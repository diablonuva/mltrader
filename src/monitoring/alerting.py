from __future__ import annotations

import json
import logging
import smtplib
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional

from src.monitoring.logger import StructuredLogger

logger = logging.getLogger(__name__)

# Severity ordering (higher index = more severe)
_LEVELS = ["INFO", "WARNING", "ERROR", "CRITICAL"]


class AlertingSystem:
    """Dispatches alerts to the terminal, email (SMTP), and webhooks.

    Always writes to the StructuredLogger and prints a Rich panel.
    Email and webhook delivery is controlled by config flags and only fires
    for ERROR / CRITICAL level alerts.
    """

    def __init__(self, config: dict, structured_logger: StructuredLogger) -> None:
        mon = config["monitoring"]
        self._logger = structured_logger

        self._email_enabled: bool = bool(mon.get("alert_email_enabled", False))
        self._webhook_enabled: bool = bool(mon.get("alert_webhook_enabled", False))
        self._email_address: str = mon.get("alert_email_address", "")
        self._webhook_url: str = mon.get("alert_webhook_url", "")

        # Optional SMTP config (can be absent in settings.yaml)
        smtp = config.get("smtp", {})
        self._smtp_host: str = smtp.get("host", "localhost")
        self._smtp_port: int = int(smtp.get("port", 587))
        self._smtp_user: str = smtp.get("user", "")
        self._smtp_password: str = smtp.get("password", "")
        self._smtp_from: str = smtp.get("from_address", self._email_address)

    # ------------------------------------------------------------------
    # Core dispatcher
    # ------------------------------------------------------------------

    def alert(
        self,
        level: str,
        title: str,
        message: str,
        data: Optional[dict] = None,
    ) -> None:
        """Dispatch an alert through all configured channels.

        Args:
            level:   One of INFO / WARNING / ERROR / CRITICAL.
            title:   Short one-line subject / heading.
            message: Human-readable body.
            data:    Optional key/value context dict (serialised to JSON).
        """
        data = data or {}
        level = level.upper()

        # 1. Structured log (session.log via root logger)
        log_fn = {
            "INFO": logger.info,
            "WARNING": logger.warning,
            "ERROR": logger.error,
            "CRITICAL": logger.critical,
        }.get(level, logger.info)
        log_fn("[ALERT:%s] %s — %s", level, title, message)

        # 2. Rich terminal panel
        self._print_panel(level, title, message, data)

        # 3. Email (CRITICAL / ERROR only)
        if self._email_enabled and level in ("CRITICAL", "ERROR"):
            self._send_email(title, message, data)

        # 4. Webhook (all levels)
        if self._webhook_enabled:
            self._post_webhook(level, title, message, data)

    # ------------------------------------------------------------------
    # Domain-specific alert shortcuts
    # ------------------------------------------------------------------

    def regime_change(
        self,
        asset: str,
        old: str,
        new: str,
        confidence: float,
        timestamp: datetime,
    ) -> None:
        self.alert(
            level="INFO",
            title=f"Regime Change — {asset}",
            message=f"{old} → {new}  (confidence={confidence:.3f})",
            data={"asset": asset, "old": old, "new": new,
                  "confidence": confidence,
                  "timestamp": str(timestamp)},
        )

    def circuit_breaker_triggered(
        self,
        reason: str,
        equity: float,
        timestamp: datetime,
    ) -> None:
        self.alert(
            level="CRITICAL",
            title="Circuit Breaker Activated",
            message=f"reason={reason}  equity=${equity:,.2f}",
            data={"reason": reason, "equity": equity,
                  "timestamp": str(timestamp)},
        )

    def daily_loss_approaching(
        self,
        current_pct: float,
        limit_pct: float,
        timestamp: datetime,
    ) -> None:
        self.alert(
            level="WARNING",
            title="Daily Loss Approaching Limit",
            message=(
                f"Current drawdown {current_pct*100:.2f}% approaching "
                f"limit {limit_pct*100:.2f}%"
            ),
            data={"current_pct": current_pct, "limit_pct": limit_pct,
                  "timestamp": str(timestamp)},
        )

    def hmm_failure(self, asset: str, error_message: str) -> None:
        self.alert(
            level="ERROR",
            title=f"HMM Failure — {asset}",
            message=error_message,
            data={"asset": asset, "error": error_message},
        )

    def lgbm_failure(
        self, asset: str, regime: str, error_message: str
    ) -> None:
        self.alert(
            level="ERROR",
            title=f"LGBM Failure — {asset} [{regime}]",
            message=error_message,
            data={"asset": asset, "regime": regime, "error": error_message},
        )

    def eod_flat_failed(self, asset: str, timestamp: datetime) -> None:
        self.alert(
            level="CRITICAL",
            title=f"EOD Flat Failed — {asset}",
            message=(
                f"Position in {asset} could not be closed at EOD. "
                "Manual intervention required."
            ),
            data={"asset": asset, "timestamp": str(timestamp)},
        )

    def pdt_approaching(self, current_count: int, max_count: int) -> None:
        self.alert(
            level="WARNING",
            title="PDT Limit Approaching",
            message=(
                f"Used {current_count}/{max_count} day trades "
                "in the rolling 5-day window."
            ),
            data={"current_count": current_count, "max_count": max_count},
        )

    # ------------------------------------------------------------------
    # Private: delivery backends
    # ------------------------------------------------------------------

    def _print_panel(
        self,
        level: str,
        title: str,
        message: str,
        data: dict,
    ) -> None:
        _COLOURS = {
            "INFO": "cyan",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold red",
        }
        colour = _COLOURS.get(level, "white")
        try:
            from rich.console import Console
            from rich.panel import Panel

            body = message
            if data:
                body += "\n" + json.dumps(data, indent=2, default=str)
            Console().print(
                Panel(body, title=f"[{colour}][ALERT:{level}] {title}[/{colour}]",
                      border_style=colour)
            )
        except ImportError:
            print(f"\n*** ALERT [{level}] {title} ***")
            print(f"  {message}")
            if data:
                print(f"  {data}")

    def _send_email(self, title: str, message: str, data: dict) -> None:
        if not self._email_address:
            logger.warning("Email alert skipped: alert_email_address not configured")
            return
        try:
            body = f"{message}\n\n{json.dumps(data, indent=2, default=str)}"
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[Trader Alert] {title}"
            msg["From"] = self._smtp_from or self._email_address
            msg["To"] = self._email_address

            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as smtp:
                smtp.ehlo()
                if self._smtp_port == 587:
                    smtp.starttls()
                if self._smtp_user:
                    smtp.login(self._smtp_user, self._smtp_password)
                smtp.sendmail(msg["From"], [msg["To"]], msg.as_string())

            logger.info("Alert email sent → %s", self._email_address)
        except Exception:
            logger.error("Email delivery failed:\n%s", traceback.format_exc())

    def _post_webhook(
        self,
        level: str,
        title: str,
        message: str,
        data: dict,
    ) -> None:
        if not self._webhook_url:
            return
        try:
            import requests  # optional dependency

            payload = {
                "level": level,
                "title": title,
                "message": message,
                "data": data,
            }
            resp = requests.post(
                self._webhook_url,
                json=payload,
                timeout=5,
            )
            if not resp.ok:
                logger.warning(
                    "Webhook delivery returned %s: %s",
                    resp.status_code, resp.text[:200],
                )
        except ImportError:
            logger.debug("requests not installed — webhook skipped")
        except Exception:
            logger.error("Webhook delivery failed:\n%s", traceback.format_exc())
