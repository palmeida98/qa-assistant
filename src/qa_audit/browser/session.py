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
    # found by the recording-proxy test in CI (8.7): after about a minute Firefox contacted aus5.mozilla.org.
    # Application, system add-on and media plugin (GMP) updates, remote settings, push and search updates off.
    "app.update.enabled": False,
    "app.update.auto": False,
    "app.update.url": "",
    "app.update.checkInstallTime": False,
    "app.update.staging.enabled": False,
    "extensions.systemAddon.update.enabled": False,
    "extensions.systemAddon.update.url": "",
    "extensions.getAddons.cache.enabled": False,
    "extensions.blocklist.enabled": False,
    "media.gmp-manager.url": "",
    "media.gmp-manager.updateEnabled": False,
    "media.gmp-gmpopenh264.autoupdate": False,
    "media.gmp-gmpopenh264.enabled": False,
    "media.gmp-widevinecdm.autoupdate": False,
    "services.settings.server": "",
    "security.remote_settings.crlite_filters.enabled": False,
    "security.OCSP.enabled": 0,
    "dom.push.connection.enabled": False,
    "browser.search.update": False,
    "browser.region.update.enabled": False,
    "network.trr.mode": 5,
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
                f"{engine}; on Windows use another engine (docs/qa-audit.md §13)")
    if "ns_error_failure" in m:
        return (f"{engine} failed at the network layer (NS_ERROR_FAILURE); on Windows with application control its "
                "cryptography can be blocked, so HTTPS never loads: run 'qa-audit doctor', use chromium")
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
        # cookie name -> SameSite declared in a Set-Cookie header ("" = header without it); values never kept
        self.declared_cookies: dict[str, str] = {}
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
        headless = bool(b.get("headless", True))
        if (self.cfg.get("auth") or {}).get("method") == "manual":
            headless = False      # a person logs in: the window must be visible (docs/qa-audit.md §20)
        kwargs: dict = {"headless": headless}
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
        self.context.on("response", self._record_set_cookie)
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
        opts: dict = {"ignore_https_errors": bool(b.get("ignore_https_errors"))}
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
        a = self.cfg.get("auth") or {}
        if a.get("method") == "storage-state":
            from ..core.auth import state_path
            opts["storage_state"] = state_path(self.cfg, self.target)   # saved by qa-audit login (§20.1)
        base_ua = opts.get("user_agent") or self._default_user_agent()
        self.user_agent = f"{base_ua} qa-audit/{__version__}"
        opts["user_agent"] = self.user_agent
        if e.get("video"):
            opts["record_video_dir"] = self.run.sub("videos")
            opts["record_video_size"] = opts.get("viewport") or dict(b["viewport"])
        return opts

    def _record_set_cookie(self, response) -> None:
        """SameSite as declared by the server (docs/qa-audit.md §16): the browser's cookie store cannot tell a
        declared SameSite=None from a missing attribute (Firefox and WebKit report both as None)."""
        try:
            if "set-cookie" not in response.headers:
                return
            lines = [h["value"] for h in response.headers_array() if h["name"].lower() == "set-cookie"]
        except Exception:  # noqa: BLE001 - a response that went away: nothing to record
            return
        for line in (x for value in lines for x in value.split("\n")):
            parts = [x.strip() for x in line.split(";")]
            if not parts or "=" not in parts[0]:
                continue
            samesite = ""
            for attr in parts[1:]:
                key, _, val = attr.partition("=")
                if key.strip().lower() == "samesite":
                    samesite = val.strip()
            self.declared_cookies[parts[0].split("=", 1)[0].strip()] = samesite

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
