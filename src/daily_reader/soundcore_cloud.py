"""Read Soundcore's shared audio. No account credentials or browser session are used."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import ssl
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SHARE_HOST = "speaker-eu.eufylife.com"
# Observed in the EU share response. Add regions only after verifying their service.
AUDIO_HOSTS = frozenset({"d2htfo7ft368vg.cloudfront.net"})
API_PATH = "/h5/speak-api/v1/sharelink/get_data"
MAX_METADATA_BYTES = 10 * 1024**2
MAX_AUDIO_BYTES = 2 * 1024**3
MINIMUM_FREE_BYTES = 5 * 1024**3
TRANSFER_SECONDS = 600
SOCKET_SECONDS = 25


class SoundcoreCloudError(ValueError):
    MESSAGES = {
        "url": "対応するSoundcoreの共有リンクを入力してください。",
        "expired": "共有リンクが期限切れ、または共有が解除されています。",
        "network": "Soundcoreへ接続できませんでした。時間をおいて再試行してください。",
        "response": "Soundcoreの応答形式を確認できませんでした。",
        "no_audio": "このリンクには音声が共有されていません。音声を含むリンクを作成してください。",
        "audio_host": "この音声の配信先にはまだ対応していません。",
        "size": "共有データが取り込み上限を超えています。",
        "audio": "共有されたファイルをOGGまたはMP3音声として確認できませんでした。",
        "storage": "録音の保存に必要な空き容量がありません。5 GiB以上の余裕が必要です。",
    }

    def __init__(self, code: str):
        self.code = code
        self.retryable = code == "network"
        super().__init__(self.MESSAGES[code])


@dataclass(frozen=True)
class CloudAudio:
    path: Path
    filename: str
    recorded_at: str | None
    metadata: dict


def _url_parts(url: object):
    if not isinstance(url, str) or len(url) > 4096:
        raise SoundcoreCloudError("url")
    url = url.strip()
    if not url or any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url:
        raise SoundcoreCloudError("url")
    try:
        parts = urllib.parse.urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
        ):
            raise ValueError
    except ValueError:
        raise SoundcoreCloudError("url") from None
    return parts


def validate_share_url(url: object) -> str:
    parts = _url_parts(url)
    if parts.hostname != SHARE_HOST or not re.fullmatch(
        r"/knowledge/sharelink/[A-Za-z0-9]{6,64}", parts.path
    ):
        raise SoundcoreCloudError("url")
    try:
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise SoundcoreCloudError("url") from None
    if any(
        key != "language" or not re.fullmatch(r"[A-Za-z-]{0,20}", value) for key, value in query
    ):
        raise SoundcoreCloudError("url")
    return f"https://{SHARE_HOST}{parts.path}"


def _audio_url(url: object) -> str:
    if not url:
        raise SoundcoreCloudError("no_audio")
    parts = _url_parts(url)
    if parts.hostname not in AUDIO_HOSTS or parts.fragment:
        raise SoundcoreCloudError("audio_host")
    if Path(parts.path).suffix.lower() not in {".ogg", ".mp3"}:
        raise SoundcoreCloudError("audio")
    return str(url).strip()


def _public_addresses(host: str) -> list[str]:
    try:
        addresses = list(
            dict.fromkeys(
                row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            )
        )
    except OSError:
        raise SoundcoreCloudError("network") from None
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise SoundcoreCloudError("network")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        super().__init__(host, timeout=SOCKET_SECONDS, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Resolve once, validate every answer, then connect to that exact address.
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


@contextmanager
def _open(url: str, *, body: bytes | None = None):
    parts = _url_parts(url)
    host = parts.hostname
    if host not in AUDIO_HOSTS | {SHARE_HOST}:
        raise SoundcoreCloudError("audio_host")
    addresses = _public_addresses(host)
    connection = _PinnedHTTPSConnection(host, addresses[0])
    response = None
    try:
        path = urllib.parse.urlunsplit(("", "", parts.path, parts.query, ""))
        headers = {"User-Agent": "Daymeld-Soundcore-Import/1", "Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request("POST" if body is not None else "GET", path, body, headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException):
            raise SoundcoreCloudError("network") from None
        if response.status in (404, 410):
            raise SoundcoreCloudError("expired")
        if response.status == 429 or response.status >= 500:
            raise SoundcoreCloudError("network")
        # The observed service has no redirects. Do not forward bearer paths or POSTs.
        if (
            response.status != 200
            or response.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise SoundcoreCloudError("response")
        yield response
    finally:
        if response is not None:
            response.close()
        connection.close()


def _read(response, size: int) -> bytes:
    try:
        return response.read(size)
    except (OSError, http.client.HTTPException):
        raise SoundcoreCloudError("network") from None


def _length(response, limit: int) -> int | None:
    raw = response.getheader("Content-Length")
    if raw is None:
        return None
    try:
        result = int(raw)
    except (ValueError, TypeError):
        raise SoundcoreCloudError("response") from None
    if result <= 0:
        raise SoundcoreCloudError("response")
    if result > limit:
        raise SoundcoreCloudError("size")
    return result


def _recorded_at(data: dict) -> str | None:
    """Confirm the epoch against Soundcore's unchanged Japan-time recording title."""
    timestamp = data.get("timestamp")
    title = data.get("title")
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        return None
    if not isinstance(title, str) or not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", title):
        return None
    seconds = timestamp / 1000 if timestamp >= 10**12 else timestamp
    try:
        recorded = datetime.fromtimestamp(seconds, UTC)
        named = datetime.strptime(title, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    except (ValueError, OverflowError, OSError):
        return None
    if not datetime(2000, 1, 1, tzinfo=UTC) <= recorded <= datetime.now(UTC) + timedelta(days=1):
        return None
    if abs((recorded - named).total_seconds()) >= 1:
        return None
    return recorded.isoformat()


def read_share(url: object) -> tuple[str, dict]:
    canonical = validate_share_url(url)
    code = canonical.rsplit("/", 1)[1]
    body = json.dumps({"share_code": code}).encode()
    with _open(f"https://{SHARE_HOST}{API_PATH}", body=body) as response:
        expected = _length(response, MAX_METADATA_BYTES)
        content = _read(response, MAX_METADATA_BYTES + 1)
    if len(content) > MAX_METADATA_BYTES:
        raise SoundcoreCloudError("size")
    if expected is not None and len(content) != expected:
        raise SoundcoreCloudError("network")
    try:
        envelope = json.loads(content)
    except (ValueError, UnicodeError):
        raise SoundcoreCloudError("response") from None
    if not isinstance(envelope, dict):
        raise SoundcoreCloudError("response")
    if envelope.get("res_code") == 5016:
        raise SoundcoreCloudError("expired")
    data = envelope.get("data")
    if (
        type(envelope.get("res_code")) is not int
        or envelope["res_code"] != 1
        or not isinstance(data, dict)
    ):
        raise SoundcoreCloudError("response")
    return canonical, data


def download_share(url: object, directory: Path) -> CloudAudio:
    started = time.monotonic()
    canonical, data = read_share(url)
    audio_url = _audio_url(data.get("audio_url"))
    suffix = Path(urllib.parse.urlsplit(audio_url).path).suffix.lower()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / ("soundcore-original" + suffix)
    written = 0
    created = False
    try:
        with _open(audio_url) as response:
            expected = _length(response, MAX_AUDIO_BYTES)
            # Storage will copy this original into the permanent recording directory.
            if expected and shutil.disk_usage(directory).free - 2 * expected < MINIMUM_FREE_BYTES:
                raise SoundcoreCloudError("storage")
            with destination.open("xb") as output:
                created = True
                os.chmod(destination, 0o600)
                while True:
                    if time.monotonic() - started > TRANSFER_SECONDS:
                        raise SoundcoreCloudError("network")
                    chunk = _read(response, 1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_AUDIO_BYTES:
                        raise SoundcoreCloudError("size")
                    if (
                        shutil.disk_usage(directory).free - len(chunk) - written
                        < MINIMUM_FREE_BYTES
                    ):
                        raise SoundcoreCloudError("storage")
                    output.write(chunk)
            if not written or (expected is not None and written != expected):
                raise SoundcoreCloudError("network")
        with destination.open("rb") as saved:
            prefix = saved.read(12)
        valid = (
            prefix.startswith(b"OggS")
            if suffix == ".ogg"
            else (
                prefix.startswith(b"ID3")
                or len(prefix) >= 2
                and prefix[0] == 255
                and prefix[1] & 224 == 224
            )
        )
        if not valid:
            raise SoundcoreCloudError("audio")
    except SoundcoreCloudError:
        if created:
            destination.unlink(missing_ok=True)
        raise
    except OSError:
        if created:
            destination.unlink(missing_ok=True)
        raise SoundcoreCloudError("storage") from None
    metadata = {
        "provider": "soundcore_share",
        "schema_version": 1,
        "share_code_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "fetched_at": datetime.now(UTC).isoformat(),
        "audio_format": suffix.removeprefix("."),
        "date_basis": "epoch_matches_unchanged_title_jst",
    }
    # Preserve original cloud text separately; never pretend a summary is speech.
    for key in ("title", "timestamp", "file_name", "audio_duration", "transcript", "summary"):
        value = data.get(key)
        if isinstance(value, str) or value is None or type(value) in (int, float):
            metadata[key] = value
    recorded_at = _recorded_at(data)
    metadata["recorded_at_confirmed"] = recorded_at is not None
    filename = data.get("file_name")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or Path(filename).suffix.lower() != suffix
        or len(filename) > 240
    ):
        filename = "Soundcore" + suffix
    return CloudAudio(destination, filename, recorded_at, metadata)
