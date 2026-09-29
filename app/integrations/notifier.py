"""Notification dispatcher (ntfy / Webhook) using standard library urllib."""

import logging
import urllib.error
import urllib.request

log = logging.getLogger("warden.notify")


class Notifier:
    def __init__(self, notify_url: str = ""):
        self.notify_url = notify_url

    def notify(self, title: str, message: str, tags: list[str] | None = None) -> bool:
        if not self.notify_url:
            return False

        try:
            req = urllib.request.Request(
                self.notify_url,
                data=message.encode("utf-8"),
                method="POST",
            )
            if title:
                req.add_header("Title", title)
            if tags:
                req.add_header("Tags", ",".join(tags))

            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status in (200, 201, 204)
        except Exception as exc:
            log.warning("Notification dispatch failed to %s: %s", self.notify_url, exc)
            return False
