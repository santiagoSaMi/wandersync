"""Cliente GraphQL minimo (solo libreria estandar) para los scripts de verificacion y demo."""
import http.client
import json
import urllib.parse


class Client:
    def __init__(self, base_url: str = "http://localhost:3000"):
        u = urllib.parse.urlparse(base_url)
        self.host, self.port, self.https = u.hostname, u.port or (443 if u.scheme == "https" else 80), u.scheme == "https"
        self.path = (u.path.rstrip("/") or "") + "/graphql"

    def _conn(self):
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=60)

    def request(self, method: str, path: str, body=None, headers=None):
        conn = self._conn()
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        return resp.status, resp.getheaders(), raw

    def gql(self, query: str, variables=None, cookie: str | None = None):
        """Devuelve (status, set_cookies[list[str]], json). `cookie` = valor de ws_sid a enviar."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if cookie:
            headers["Cookie"] = f"ws_sid={cookie}"
        status, hdrs, raw = self.request("POST", self.path, json.dumps({"query": query, "variables": variables or {}}), headers)
        set_cookies = [v for k, v in hdrs if k.lower() == "set-cookie"]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"raw": raw}
        return status, set_cookies, data


def session_id(set_cookies: list[str]) -> str | None:
    for c in set_cookies:
        if c.startswith("ws_sid="):
            value = c.split(";", 1)[0][len("ws_sid="):]
            return value or None
    return None


def error_code(payload: dict) -> str | None:
    errs = payload.get("errors") or []
    return ((errs[0].get("extensions") or {}).get("code")) if errs else None
