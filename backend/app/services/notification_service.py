import httpx
import logging
from typing import Dict, Any, Optional
import json

logger = logging.getLogger("app.services.notification")

class NotificationService:
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize notification service.
        Expects a config dict with optional:
        - slack_webhook_url
        - discord_webhook_url
        - enabled (bool)
        """
        self.config = config
        self.slack_webhook_url = config.get("slack_webhook_url")
        self.discord_webhook_url = config.get("discord_webhook_url")
        self.enabled = config.get("enabled", False)
        self._client = httpx.AsyncClient(timeout=10.0)
        
        if self.enabled:
            logger.info("NotificationService initialized and enabled.")
            if self.slack_webhook_url:
                logger.info("Slack notifications configured.")
            if self.discord_webhook_url:
                logger.info("Discord notifications configured.")
        else:
            logger.info("NotificationService initialized but disabled.")

    async def notify_incident(self, incident_data: Dict[str, Any]):
        """Sends a notification for a new incident."""
        if not self.enabled:
            return

        severity = incident_data.get("severity", "MEDIUM")
        # Only notify for HIGH or CRITICAL incidents by default
        if severity not in ["HIGH", "CRITICAL"]:
            return

        title = f"🚨 New {incident_data.get('type', 'Traffic').replace('_', ' ')} Incident Detected! 🚨"
        description = incident_data.get('description', 'No description provided.')
        source = incident_data.get('source_feed_id', 'Manual/Unknown')
        timestamp = incident_data.get('timestamp')
        
        # Slack payload
        slack_msg = {
            "text": f"*{title}*\n*Severity:* {severity}\n*Description:* {description}\n*Source:* {source}\n*Time:* {timestamp}"
        }

        # Discord payload (supports richer embeds, but we'll start simple)
        discord_msg = {
            "content": f"**{title}**\n**Severity:** {severity}\n**Description:** {description}\n**Source:** {source}\n**Time:** {timestamp}"
        }

        if self.slack_webhook_url:
            await self._send_webhook(self.slack_webhook_url, slack_msg, "Slack")
        
        if self.discord_webhook_url:
            await self._send_webhook(self.discord_webhook_url, discord_msg, "Discord")

    async def _send_webhook(self, url: str, payload: Dict[str, Any], service_name: str):
        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.error(f"Failed to send {service_name} notification: {resp.status_code} - {resp.text}")
            else:
                logger.debug(f"Successfully sent {service_name} notification.")
        except Exception as e:
            logger.error(f"Error sending {service_name} notification: {e}")

    async def close(self):
        """Closes the HTTP client."""
        await self._client.aclose()
        logger.info("NotificationService client closed.")
