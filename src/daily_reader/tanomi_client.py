from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

TASK_ID = re.compile(r"^[0-9a-f]{12}$")
MODEL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MODES = {"acceptEdits", "plan", "bypassPermissions", "manual"}
MAX_RESPONSE = 2 * 1024 * 1024
DEFAULT_BASE_URL = "https://xh23040023-l.tailc193b2.ts.net"
USAGE_CACHE_TTL = 300.0
USAGE_STALE_TTL = 3600.0
USAGE_ERROR_TTL = 60.0


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
    _opener: urllib.request.OpenerDirector = field(
        default_factory=lambda: urllib.request.build_opener(_NoRedirect),
        init=False,
        repr=False,
        compare=False,
    )
    _usage_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    _usage_cached: tuple[float, dict[str, Any]] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _usage_error: tuple[float, Exception] | None = field(
        default=None, init=False, repr=False, compare=False
    )

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
        return self._opener.open(request, timeout=timeout)

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

    def request_usage(self) -> dict[str, Any]:
        """Fetch usage sparingly and retain a bounded stale snapshot on failure."""
        now = time.monotonic()
        with self._usage_lock:
            if self._usage_cached and now - self._usage_cached[0] < USAGE_CACHE_TTL:
                return dict(self._usage_cached[1])
            if self._usage_error and now - self._usage_error[0] < USAGE_ERROR_TTL:
                if self._usage_cached and now - self._usage_cached[0] < USAGE_STALE_TTL:
                    return self._stale_usage(self._usage_cached[1])
                raise self._usage_error[1]
            try:
                payload = self.request_json("GET", "/api/usage")
                snapshot = self._validate_usage(payload)
            except Exception as error:  # noqa: BLE001 - upstream errors are isolated from tasks
                object.__setattr__(self, "_usage_error", (now, error))
                if self._usage_cached and now - self._usage_cached[0] < USAGE_STALE_TTL:
                    return self._stale_usage(self._usage_cached[1])
                raise
            object.__setattr__(self, "_usage_cached", (now, snapshot))
            object.__setattr__(self, "_usage_error", None)
            return dict(snapshot)

    @staticmethod
    def _validate_usage(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("limits"), dict):
            raise TanomiProtocolError("tanomi の使用状況が不正です")
        limits = payload["limits"]
        if "error" in limits:
            raise TanomiUnavailable(f"tanomi の使用状況を取得できません: {limits['error']}")
        valid_limits: dict[str, dict[str, Any]] = {}
        for name, value in limits.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                raise TanomiProtocolError("tanomi の使用状況が不正です")
            utilization = value.get("utilization")
            if isinstance(utilization, bool) or not isinstance(utilization, (int, float)):
                raise TanomiProtocolError("tanomi の使用状況が不正です")
            reset = value.get("resets_at")
            if reset is not None and not isinstance(reset, str):
                raise TanomiProtocolError("tanomi の使用状況が不正です")
            valid_limits[name] = {"utilization": utilization, "resets_at": reset}
        running = payload.get("running", 0)
        if isinstance(running, bool) or not isinstance(running, int):
            raise TanomiProtocolError("tanomi の使用状況が不正です")
        return {"limits": valid_limits, "running": running}

    @staticmethod
    def _stale_usage(snapshot: dict[str, Any]) -> dict[str, Any]:
        result = dict(snapshot)
        result["stale"] = True
        return result

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
