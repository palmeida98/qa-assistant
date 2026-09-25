"""Browser session: one engine, one context per run (docs/qa-audit.md §3, §8, §13).

Playwright is imported lazily so the core and its unit tests never need it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from .. import __version__
from ..core.config import ConfigError

# Playwright already disables data reporting and connectivity checks in Firefox; these make the rest explicit
# (docs/qa-audit.md §8). Chromium gets Playwright's defaults: --disable-background-networking,
# --disable-component-update, --disable-sync, --metrics-recording-only, --no-service-autorun, …
FIREFOX_PREFS = {
    "toolkit.telemetry.enabled": False,
    "toolkit.telemetry.unified": False,
    "datareporting.healthreport.uploadEnabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "network.captive-portal-service.enabled": False,
    "network.connectivity-service.enabled": False,
    "browser.safebrowsing.malware.enabled": False,
    "browser.safebrowsing.phishing.enabled": False,
    "browser.safebrowsing.downloads.enabled": False,
    "browser.safebrowsing.downloads.remote.enabled": False,
    "extensions.update.enabled": False,
    "app.normandy.enabled": False,
    "browser.ping-centre.telemetry": False,
}


class BrowserError(RuntimeError):
    """The browser could not start (exit code 4)."""


def origin(url: str) -> str:
    p = urlsplit(url)
    port = p.port
    default = {"http": 80, "https": 443}.get(p.scheme)
    host = (p.hostname or "").lower()
    return f"{p.scheme}://{host}" + (f":{port}" if port and port != default else "")


def launch_hint(engine: str, message: str) -> str:
    m = message.lower()
    if "executable doesn't exist" in m or "please run the following command" in m:
        return f"the {engine} browser is not installed: python -m playwright install {engine}"
    if "missing dependencies" in m or "application control" in m:
        return (f"{engine} is missing system libraries, or Windows application control (Smart App Control / WDAC) "
                "blocks its files; on Linux: python -m playwright install-deps "
                f"{engine}; on Windows use chromium or firefox (docs/qa-audit.md §13)")
    if "spawn unknown" in m or "eperm" in m or "access is denied" in m:
        return "the operating system refused to start the browser process (sandbox, antivirus or permissions)"
    return message.strip().splitlines()[0] if message.strip() else "unknown error"


class Session:
    """with Session(cfg, run, target) as s: page = s.new_page() …"""

    def __init__(self, cfg: dict, run, target: str, proxy: dict | None = None, launch_args: list[str] | None = None,
                 firefox_prefs: dict | None = None):
        self.cfg, self.run, self.target = cfg, run, target
        self.proxy, self.launch_args = proxy, launch_args or []
        self.firefox_prefs = {**FIREFOX_PREFS, **(firefox_prefs or {})}
        self.engine = cfg["browser"]["engine"]
        self.blocked: list[str] = []          # third-party requests blocked (--block-third-party)
        self.version = ""
        self.user_agent = ""
        self._pw = self.browser = self.context = None
        self._tracing = False

    # --- lifecycle -------------------------------------------------------------------------------------------
    def __enter__(self):
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import sync_playwright

        b = self.cfg["browser"]
        self._pw = sync_playwright().start()
        kwargs: dict = {"headless": bool(b.get("headless", True))}
        if self.engine == "firefox":
            kwargs["firefox_user_prefs"] = self.firefox_prefs
        if self.proxy:
            kwargs["proxy"] = self.proxy
        if self.launch_args:
            kwargs["args"] = self.launch_args
        try:
            self.browser = getattr(self._pw, self.engine).launch(**kwargs)
        except PWError as e:
            self._stop_pw()
            raise BrowserError(launch_hint(self.engine, str(e))) from e
        self.version = self.browser.version
        try:
            self.context = self.browser.new_context(**self._context_options())
        except (PWError, ValueError, TypeError) as e:
            self.__exit__(None, None, None)
            raise ConfigError(f"browser context options not supported by {self.engine}: {e}") from e
        self.context.set_default_timeout(int(b.get("timeout_ms", 30000)))
        self.context.set_default_navigation_timeout(int(b.get("timeout_ms", 30000)))
        if self.cfg["network"].get("block_third_party"):
            self.context.route("**/*", self._only_target_origin)
        if str(self.cfg["evidence"].get("trace")) != "off":
            self.context.tracing.start(screenshots=True, snapshots=True)
            self._tracing = True
        return self

    def __exit__(self, *exc):
        self.close(keep_trace=False)

    def close(self, keep_trace: bool) -> str | None:
        """Close everything; returns the trace path (inside the run folder) when kept."""
        trace_path = None
        try:
            if self.context is not None:
                if self._tracing:
                    if keep_trace:
                        trace_path, _ = self.run.new_file("traces", "trace", "zip")
                        self.context.tracing.stop(path=trace_path)
                    else:
                        self.context.tracing.stop()
                    self._tracing = False
                self.context.close()
        finally:
            self.context = None
            if self.browser is not None:
                self.browser.close()
                self.browser = None
            self._stop_pw()
        return trace_path

    def _stop_pw(self):
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    # --- options ---------------------------------------------------------------------------------------------
    def _context_options(self) -> dict:
        b, e = self.cfg["browser"], self.cfg["evidence"]
        opts: dict = {}
        device = b.get("device")
        if device:
            try:
                opts.update(self._pw.devices[device])
            except KeyError:
                raise ConfigError(f"unknown device {device!r} (Playwright device names, e.g. 'iPhone 15', "
                                  "'Pixel 7', 'Desktop Firefox')") from None
            if self.engine == "firefox":
                opts.pop("is_mobile", None)       # not supported by Firefox
            opts.pop("default_browser_type", None)
        else:
            opts["viewport"] = dict(b["viewport"])
        base_ua = opts.get("user_agent") or self._default_user_agent()
        self.user_agent = f"{base_ua} qa-audit/{__version__}"
        opts["user_agent"] = self.user_agent
        if e.get("video"):
            opts["record_video_dir"] = self.run.sub("videos")
            opts["record_video_size"] = opts.get("viewport") or dict(b["viewport"])
        return opts

    def _default_user_agent(self) -> str:
        page = self.browser.new_page()
        try:
            return page.evaluate("navigator.userAgent")
        finally:
            page.close()

    def _only_target_origin(self, route):
        url = route.request.url
        if url.startswith(("data:", "blob:")) or origin(url) == origin(self.target):
            route.continue_()
        else:
            self.blocked.append(url)
            route.abort("blockedbyclient")

    def retarget(self, new_target: str) -> None:
        """The start page redirected (e.g. http -> https): the audited origin follows it."""
        self.target = new_target

    def new_page(self):
        return self.context.new_page()

    def request(self):
        """The context's HTTP client: same user agent and cookies as the pages."""
        return self.context.request
