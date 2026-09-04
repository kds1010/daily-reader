from __future__ import annotations

import json
import stat
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_RESPONSE = 8 * 1024 * 1024


class SoanError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SoanClient:
    token_file: Path
    base_url: str = "http://127.0.0.1:7337"
    timeout: float = 125.0

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Soan backend must use loopback HTTP")
        if (
            parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid Soan base URL")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def _token(self) -> str:
        try:
            if (
                self.token_file.is_symlink()
                or stat.S_IMODE(self.token_file.stat().st_mode) != 0o600
            ):
                raise SoanError("Soan接続トークンの権限が不正です")
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise SoanError("Soan接続トークンを読み込めません") from error
        if len(token) < 40:
            raise SoanError("Soan接続トークンが不正です")
        return token

    def request_json(self, method: str, path: str, body: object | None = None) -> Any:
        allowed = {"/v1/catalog", "/v1/document/open", "/v1/document/save", "/v1/document/edit"}
        if path not in allowed:
            raise ValueError("invalid Soan path")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token()}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            raw = error.read(MAX_RESPONSE)
            try:
                message = json.loads(raw).get("error", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = str(error)
            raise SoanError(str(message), error.code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SoanError("Soanバックエンドに接続できません") from error
        if len(raw) > MAX_RESPONSE:
            raise SoanError("Soanの応答が大きすぎます")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SoanError("Soanの応答が不正です") from error

    def request_image(self, root: str, source: str) -> tuple[bytes, str]:
        query = urllib.parse.urlencode({"root": root, "source": source})
        request = urllib.request.Request(f"{self.base_url}/v1/document/image?{query}")
        request.add_header("Authorization", f"Bearer {self._token()}")
        request.add_header("Accept", "image/png,image/jpeg,image/gif")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                content_type = response.headers.get_content_type()
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            raise SoanError("画像を取得できません", error.code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SoanError("Soanバックエンドに接続できません") from error
        if len(raw) > MAX_RESPONSE or content_type not in {"image/png", "image/jpeg", "image/gif"}:
            raise SoanError("Soan画像の応答が不正です")
        return raw, content_type
