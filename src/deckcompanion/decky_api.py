"""Decky 3.3.1 local API, preserving Steam's single websocket and install prompt.

Never open /ws here: WSRouter disconnects its previous (Steam) client. Instead,
the authenticated legacy HTTP utility evaluates one fixed call in that client.
Tokens stay in memory and errors never include HTTP headers or response bodies.
"""
from http.client import HTTPConnection
import json
from .common import Error

TABS = ("SharedJSContext", "Steam Shared Context presented by Valve™", "Steam", "SP")
MAX_RESPONSE = 64 * 1024


def request(method, route, payload=None, token=None):
    connection = None
    try:
        connection = HTTPConnection("127.0.0.1", 1337, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Decky-Auth"] = token
        connection.request(method, route, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE + 1)
        if response.status != 200 or len(body) > MAX_RESPONSE:
            raise ValueError("Invalid local API response")
        return body.decode("utf-8") if route == "/auth/token" else json.loads(body)
    except Exception:
        raise Error("decky_api_unavailable", "Decky's local API is unavailable; retry in Game Mode or use --password-file") from None
    finally:
        if connection is not None:
            connection.close()


def execute(tab, code, token):
    reply = request("POST", "/methods/execute_in_tab",
                    {"tab": tab, "run_async": True, "code": code}, token)
    # Outer legacy utility response wraps execute_in_tab's own result.
    result = reply.get("result", {}) if isinstance(reply, dict) and reply.get("success") is True else {}
    return result.get("result") if isinstance(result, dict) and result.get("success") is True else None


def install(artifact, version, sha256, install_type=0):
    token = request("GET", "/auth/token")
    if not isinstance(token, str) or not token or len(token) > 256 or any(c in token for c in "\r\n"):
        raise Error("decky_api_unavailable", "Decky authentication unavailable")
    # Probe without mutations; never try another tab after submitting a request.
    ready = "Boolean(window.DeckyPluginLoader && window.DeckyBackend && window.DeckyBackend.ws && window.DeckyBackend.ws.readyState === 1)"
    for tab in TABS:
        try:
            found = execute(tab, ready, token) is True
        except Error:
            # Decky's legacy endpoint can return HTTP 500 for an absent tab.
            found = False
        if found:
            args = json.dumps([artifact, "DeckCompanion", version, sha256, install_type])
            code = "(async () => { await window.DeckyBackend.call('utilities/install_plugin', ..." + args + "); return 'deckcompanion-requested'; })()"
            try:
                accepted = execute(tab, code, token) == "deckcompanion-requested"
            except Error:
                accepted = False
            if accepted:
                return
            # It may have reached the backend even if the acknowledgement was lost.
            # Never submit a duplicate via another tab or fall through to file writes.
            raise Error("decky_api_request_failed", "Decky request could not be confirmed; check Steam and plugin-status before retrying")
    raise Error("decky_api_unavailable", "Decky's Steam frontend is unavailable; retry in Game Mode or use --password-file")
