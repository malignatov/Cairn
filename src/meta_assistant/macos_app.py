"""Cairn menu-bar app — runs Cairn as an HTTP MCP server with a status menu.

This is the default entry for the macOS .app bundle. It serves on localhost by
default; see ``packaging/macos-app/README.md`` for the auth/connection caveats
(short version: hosted apps like ChatGPT/claude.ai need OAuth, not the optional
token, and any remote exposure is a deliberate act).

``rumps`` is imported lazily so the module loads — and ``--serve-only`` runs —
without a GUI. ``--serve-only`` runs the server headless (for testing the
bundle, or under a LaunchAgent); with no flag you get the menu-bar app.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from .remote import ServerController, serve

log = logging.getLogger("meta_assistant.app")

HOST = os.environ.get("META_HOST", "127.0.0.1")
PORT = int(os.environ.get("META_PORT", "8000"))


def _clip(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=False)
    except Exception:  # pragma: no cover - best effort
        log.warning("could not copy to clipboard", exc_info=True)


def _run_menu_bar() -> None:
    import rumps

    token = os.environ.get("META_AUTH_TOKEN") or None

    def _notify(title: str, subtitle: str, message: str) -> None:
        try:
            rumps.notification(title, subtitle, message)
        except Exception:  # notifications need a bundled app; ignore in dev
            log.info("%s: %s", subtitle, message)

    class CairnApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("Cairn", quit_button=None)
            self.controller = ServerController(host=HOST, port=PORT, token=token)
            self._status = rumps.MenuItem("Starting…")
            self._url = rumps.MenuItem("Copy server URL", callback=self._copy_url)
            self._token_item = rumps.MenuItem(
                "Copy auth token" if token else "Auth: off (open server)",
                callback=self._copy_token if token else None,
            )
            self._toggle = rumps.MenuItem("Stop server", callback=self._toggle)
            self.menu = [
                self._status,
                None,
                self._url,
                self._token_item,
                None,
                self._toggle,
                rumps.MenuItem("Quit Cairn", callback=self._quit),
            ]
            self.controller.start()
            self._refresh()

        def _refresh(self) -> None:
            running = self.controller.running
            self.title = "● Cairn" if running else "○ Cairn"
            self._status.title = (
                f"Running — {self.controller.url}" if running else "Stopped"
            )
            self._toggle.title = "Stop server" if running else "Start server"

        def _copy_url(self, _) -> None:
            _clip(self.controller.url)
            _notify("Cairn", "Server URL copied", self.controller.url)

        def _copy_token(self, _) -> None:
            if token:
                _clip(token)
                _notify("Cairn", "Auth token copied", "Paste it into your client's header.")

        def _toggle(self, _) -> None:
            self.controller.stop() if self.controller.running else self.controller.start()
            self._refresh()

        def _quit(self, _) -> None:
            self.controller.stop()
            rumps.quit_application()

    CairnApp().run()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if "--serve-only" in sys.argv[1:]:
        serve(host=HOST, port=PORT, token=os.environ.get("META_AUTH_TOKEN") or None)
        return
    _run_menu_bar()


if __name__ == "__main__":
    main()
