from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

TASK_ID = re.compile(r"^[0-9a-f]{12}$")
MODEL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MODES = {"acceptEdits", "plan", "bypassPermissions", "manual"}
MAX_RESPONSE = 2 * 1024 * 1024
DEFAULT_BASE_URL = "https://xh23040023-l.tailc193b2.ts.net"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any):
        return None


class TanomiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TanomiUnavailable(TanomiError):
    pass


class TanomiProtocolError(TanomiError):
    pass


@dataclass(frozen=True)
class TanomiClient:
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 5.0

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid tanomi base URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("tanomi base URL must not contain credentials or query")
        if parsed.path not in {"", "/"}:
            raise ValueError("tanomi base URL must not contain a path")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        if not path.startswith("/") or ".." in path.split("/"):
            raise ValueError("invalid tanomi path")
        url = f"{self.base_url}{path}"
        return f"{url}?{urllib.parse.urlencode(query)}" if query else url

    def _open(self, request: urllib.request.Request, timeout: float):
        return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)

    def request_json(
        self,
        method: str,
        path: str,
        body: object | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        request = urllib.request.Request(self._url(path, query), data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._open(request, self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            payload = error.read(MAX_RESPONSE)
            try:
                parsed = json.loads(payload)
                message = (
                    parsed.get("detail", parsed.get("error", str(error)))
                    if isinstance(parsed, dict)
                    else str(error)
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = str(error)
            raise TanomiError(str(message), error.code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TanomiUnavailable("tanomi に接続できません") from error
        if len(raw) > MAX_RESPONSE:
            raise TanomiProtocolError("tanomi の応答が大きすぎます")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TanomiProtocolError("tanomi の応答が不正です") from error

    def stream(self, task_id: str, offset: int = 0) -> Iterator[bytes]:
        if not TASK_ID.fullmatch(task_id) or offset < 0:
            raise ValueError("invalid tanomi stream parameters")
        request = urllib.request.Request(
            self._url(f"/api/tasks/{task_id}/stream", {"offset": str(offset)}),
            headers={"Accept": "text/event-stream"},
        )
        try:
            response = self._open(request, max(self.timeout, 20.0))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TanomiUnavailable("tanomi に接続できません") from error
        try:
            for line in response:
                if len(line) > MAX_RESPONSE:
                    raise TanomiProtocolError("tanomi のストリーム行が大きすぎます")
                yield line
        finally:
            response.close()
