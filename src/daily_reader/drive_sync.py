"""Read-only Soundcore Drive import, with resumable local folder traversal.

User OAuth is interactive only through ``python -m daily_reader.drive_sync auth``.
Service-account authentication requires explicit configuration and a shared folder.
The server worker never opens a browser and never shares the Gmail token.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import google_auth_httplib2
import httplib2
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"
AUDIO_MIMES = {"audio/ogg", "audio/mpeg"}
MAX_AUDIO_BYTES = 2 * 1024**3
MIN_FREE_BYTES = 5 * 1024**3
CHUNK_BYTES = 1024**2
MAX_FILES = 10
MAX_PAGES = 50
RUN_SECONDS = 15 * 60
FILE_FIELDS = (
    "id,name,mimeType,size,modifiedTime,md5Checksum,version,parents,trashed,"
    "capabilities(canDownload)"
)
_ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_MD5 = re.compile(r"[a-fA-F0-9]{32}\Z")


class DriveSyncError(Exception):
    """Only static, non-sensitive codes cross CLI/log/status boundaries."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DriveSyncError("invalid_identifier")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise DriveSyncError("local_state_unreadable") from None
    if not isinstance(value, dict):
        raise DriveSyncError("local_state_unreadable")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".drive-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise DriveSyncError("busy") from None
        yield
    finally:
        os.close(descriptor)


def _scopes(credentials: Credentials) -> set[str]:
    granted = credentials.granted_scopes
    return set((credentials.scopes if granted is None else granted) or ())


def _safe_token_path(client_secret: Path, token_path: Path) -> None:
    protected = {client_secret.resolve(), Path("secrets/gmail-token.json").resolve()}
    if token_path.resolve() in protected or token_path.name == "gmail-token.json":
        raise DriveSyncError("separate_drive_token_required")
    if token_path.is_symlink():
        raise DriveSyncError("unsafe_token_path")


def _read_token(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise DriveSyncError("token_unreadable") from None


def load_credentials(
    client_secret: Path, token_path: Path, *, interactive: bool = False, force: bool = False
) -> Credentials:
    _safe_token_path(client_secret, token_path)
    if force and not interactive:
        raise DriveSyncError("interactive_auth_required")
    with _lock(token_path.with_name(token_path.name + ".lock")):
        original = _read_token(token_path)
        token = {}
        with suppress(ValueError, TypeError):
            parsed = json.loads(original)
            if isinstance(parsed, dict):
                token = parsed
        credentials = None
        scopes = token.get("scopes")
        if not force and isinstance(scopes, list) and DRIVE_SCOPE in scopes:
            with suppress(TypeError, ValueError):
                credentials = Credentials.from_authorized_user_info(token)
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError as error:
                invalid_grant = any(
                    isinstance(detail, dict) and detail.get("error") == "invalid_grant"
                    for detail in error.args
                )
                if not invalid_grant:
                    raise DriveSyncError("auth_refresh_failed") from None
                credentials = None
            except Exception:
                raise DriveSyncError("auth_refresh_failed") from None
            else:
                if credentials.valid and DRIVE_SCOPE in _scopes(credentials):
                    _atomic_json(token_path, json.loads(credentials.to_json()))
                else:
                    credentials = None
        if credentials and credentials.valid and DRIVE_SCOPE in _scopes(credentials):
            os.chmod(token_path, 0o600)
            return credentials
    if not interactive:
        raise DriveSyncError("auth_required")

    # Both the prompt and callback request log can contain credentials. Auth is
    # a standalone CLI operation: suppress library logging for its duration.
    logging_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), [DRIVE_SCOPE])
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            authorization_prompt_message=None,
            success_message="認証を受け付けました。この画面を閉じてください。",
            timeout_seconds=180,
            access_type="offline",
            prompt="consent",
        )
    except (Exception, KeyboardInterrupt):
        raise DriveSyncError("auth_cancelled_or_failed") from None
    finally:
        logging.disable(logging_level)
    if (
        not credentials.valid
        or not credentials.refresh_token
        or DRIVE_SCOPE not in _scopes(credentials)
    ):
        raise DriveSyncError("auth_scope_or_offline_access_missing")
    with _lock(token_path.with_name(token_path.name + ".lock")):
        if _read_token(token_path) != original:
            raise DriveSyncError("auth_changed_during_consent")
        _atomic_json(token_path, json.loads(credentials.to_json()))
    return credentials


def _private_service_account_file(path: Path) -> bool:
    """Stat only: status must not read a signing key or attempt authentication."""
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_uid == os.getuid()
    )


def load_service_account_credentials(path: Path) -> service_account.Credentials:
    """Read only a private key file, with no delegated user or alternate issuer."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise DriveSyncError("service_account_key_unavailable") from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
            ):
                raise DriveSyncError("service_account_key_not_private")
            raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise DriveSyncError("service_account_key_invalid")
            info = json.loads(raw)
    except DriveSyncError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise DriveSyncError("service_account_key_invalid") from None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise DriveSyncError("service_account_key_invalid")
    if info.get("token_uri") != GOOGLE_TOKEN_URI:
        raise DriveSyncError("service_account_token_endpoint_invalid")
    if info.get("universe_domain", "googleapis.com") != "googleapis.com":
        raise DriveSyncError("service_account_universe_invalid")
    if any(key in info for key in ("subject", "delegated_subject", "additional_claims")):
        raise DriveSyncError("service_account_delegation_not_supported")
    email = info.get("client_email")
    if (
        not isinstance(email, str)
        or not email.endswith(".gserviceaccount.com")
        or email.count("@") != 1
        or any(character.isspace() for character in email)
    ):
        raise DriveSyncError("service_account_key_invalid")
    # Feed the SDK only fields used for standard Google service-account auth.
    # In particular, imported JSON must not introduce claims or trust boundaries.
    selected = {
        key: info[key]
        for key in (
            "type",
            "private_key",
            "private_key_id",
            "client_email",
            "project_id",
            "token_uri",
        )
        if key in info
    }
    selected["universe_domain"] = "googleapis.com"
    try:
        return service_account.Credentials.from_service_account_info(
            selected,
            scopes=[DRIVE_SCOPE],
            subject=None,
            always_use_jwt_access=False,
        )
    except Exception:
        raise DriveSyncError("service_account_key_invalid") from None


def _auth_type(config: dict[str, Any]) -> str:
    value = config.get("auth_type", "user_oauth")
    if not isinstance(value, str) or value not in {"user_oauth", "service_account"}:
        raise DriveSyncError("auth_configuration_invalid")
    return value


def _configured_service_account_key(config: dict[str, Any]) -> Path:
    value = config.get("service_account_key")
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise DriveSyncError("auth_configuration_invalid")
    return Path(value)


def _drive_service(credentials: Credentials | service_account.Credentials) -> Any:
    # This transport also refreshes SA credentials. Do not use requests.Request
    # for SA refresh: its DEBUG request logger can include the signed assertion.
    transport = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=60))
    return build("drive", "v3", http=transport, cache_discovery=False)


def _execute(request: Any) -> dict[str, Any]:
    try:
        result = request.execute(num_retries=0)
    except HttpError as error:
        code = "drive_api_failed"
        if error.resp.status in {401, 403}:
            code = "drive_permission_denied"
        elif error.resp.status == 404:
            code = "drive_item_unavailable"
        raise DriveSyncError(code) from None
    except Exception:
        raise DriveSyncError("drive_api_failed") from None
    if not isinstance(result, dict):
        raise DriveSyncError("drive_response_invalid")
    return result


class LocalImports:
    """Audio can only be sent to this machine, never to a caller-supplied host."""

    def __init__(self, port: int = 8787):
        if not 1 <= port <= 65535:
            raise DriveSyncError("invalid_server_port")
        self.port = port

    def request(
        self,
        method: str,
        path: str,
        *,
        metadata: dict[str, Any] | None = None,
        audio: Path | None = None,
        mime: str = "application/octet-stream",
        check: Any = lambda: None,
    ) -> dict[str, Any]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            if audio is None:
                body = json.dumps(metadata).encode() if metadata is not None else None
                connection.request(
                    method,
                    path,
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                    },
                )
            else:
                connection.putrequest(method, path)
                connection.putheader("Content-Type", mime)
                connection.putheader("Content-Length", str(audio.stat().st_size))
                connection.endheaders()
                with audio.open("rb") as stream:
                    while chunk := stream.read(CHUNK_BYTES):
                        check()
                        connection.send(chunk)
            response = connection.getresponse()
            raw = response.read(1024**2 + 1)
            if response.status == 409:
                try:
                    problem = json.loads(raw)
                except ValueError:
                    problem = {}
                code = problem.get("code") if isinstance(problem, dict) else None
                if code == "source_conflict":
                    raise DriveSyncError("source_conflict")
                # Capacity/busy and unknown conflicts cannot drop an original.
                raise DriveSyncError("daymeld_busy")
            if response.status in {400, 411, 413, 415, 422}:
                raise DriveSyncError("daymeld_input_rejected")
            if response.status == 404 and path.endswith("/audio"):
                raise DriveSyncError("daymeld_import_missing")
            if not 200 <= response.status < 300:
                raise DriveSyncError("daymeld_unavailable")
            if len(raw) > 1024**2:
                raise DriveSyncError("daymeld_response_invalid")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise DriveSyncError("daymeld_response_invalid")
            return result
        except DriveSyncError:
            raise
        except Exception:
            raise DriveSyncError("daymeld_unavailable") from None
        finally:
            connection.close()

    def find(self, file_id: str, check: Any) -> dict[str, Any] | None:
        # The server's paginated state is authoritative, including manual retry.
        offset = 0
        while True:
            check()
            result = self.request(
                "GET", f"/api/conversations/drive-imports?limit=100&offset={offset}"
            )
            items = result.get("items")
            if not isinstance(items, list):
                raise DriveSyncError("daymeld_response_invalid")
            for item in items:
                if item.get("file_id") == file_id:
                    return item
            following = result.get("next_offset")
            if following is None:
                return None
            if not isinstance(following, int) or following <= offset:
                raise DriveSyncError("daymeld_response_invalid")
            offset = following


def _queue(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "drive-sync.sqlite3"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT,
            page_token TEXT, done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, metadata TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0, error TEXT
        );
    """)
    return connection


def _check_folder(service: Any, folder_id: str) -> dict[str, Any]:
    try:
        folder = _execute(
            service.files().get(
                fileId=folder_id,
                fields="id,name,mimeType,parents,trashed",
                supportsAllDrives=True,
            )
        )
    except DriveSyncError as error:
        if error.code == "drive_item_unavailable":
            raise DriveSyncError("source_folder_unavailable") from None
        raise
    if folder.get("trashed") or folder.get("mimeType") != FOLDER_MIME:
        raise DriveSyncError("source_folder_unavailable")
    return folder


def _metadata(file: dict[str, Any], parent: dict[str, Any]) -> dict[str, Any]:
    if file.get("trashed") or file.get("mimeType") not in AUDIO_MIMES:
        raise DriveSyncError("source_audio_unavailable")
    if parent["id"] not in file.get("parents", []):
        raise DriveSyncError("source_moved")
    try:
        size = int(file["size"])
        modified = datetime.fromisoformat(file["modifiedTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        raise DriveSyncError("source_metadata_invalid") from None
    if not 1 <= size <= MAX_AUDIO_BYTES or modified.tzinfo is None:
        raise DriveSyncError("source_metadata_invalid")
    result: dict[str, Any] = {
        "file_id": _identifier(file.get("id")),
        "parent_folder_id": _identifier(parent.get("id")),
        "parent_folder_name": parent.get("name", ""),
        "file_name": file.get("name", ""),
        "mime_type": file["mimeType"],
        "size": size,
        "modified_time": modified.isoformat(),
    }
    if not all(
        isinstance(result[key], str) and 0 < len(result[key]) <= 240
        for key in ("file_name", "parent_folder_name")
    ):
        raise DriveSyncError("source_metadata_invalid")
    if checksum := file.get("md5Checksum"):
        if not isinstance(checksum, str) or not _MD5.fullmatch(checksum):
            raise DriveSyncError("source_metadata_invalid")
        result["md5_checksum"] = checksum.lower()
    if version := file.get("version"):
        if not isinstance(version, str) or not version.isascii() or not version.isdigit():
            raise DriveSyncError("source_metadata_invalid")
        result["version"] = version
    return result


def _current_parent(
    service: Any, connection: sqlite3.Connection, parent_id: str, root_id: str, check: Any
) -> dict[str, Any]:
    """Recheck the recorded ancestor chain before downloading a moved file."""
    first = None
    current = parent_id
    visited: set[str] = set()
    while True:
        check()
        if current in visited or len(visited) >= 128:
            raise DriveSyncError("source_ancestry_invalid")
        visited.add(current)
        folder = _check_folder(service, current)
        if first is None:
            first = folder
        if current == root_id:
            return first
        stored = connection.execute("SELECT parent FROM folders WHERE id=?", (current,)).fetchone()
        if not stored or stored["parent"] not in folder.get("parents", []):
            raise DriveSyncError("source_moved")
        current = stored["parent"]


def _list_page(service: Any, connection: sqlite3.Connection, folder: sqlite3.Row) -> None:
    options = {
        "q": f"'{_identifier(folder['id'])}' in parents and trashed = false and "
        f"(mimeType = '{FOLDER_MIME}' or mimeType = 'audio/ogg' or mimeType = 'audio/mpeg')",
        "fields": f"nextPageToken,incompleteSearch,files({FILE_FIELDS})",
        "pageSize": 100,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    if folder["page_token"]:
        options["pageToken"] = folder["page_token"]
    try:
        page = service.files().list(**options).execute(num_retries=0)
    except HttpError as error:
        if folder["page_token"] and error.resp.status in {400, 410}:
            # Tokens can expire between runs. Restart this folder only; queues
            # and server idempotency make replay safe and prevent omissions.
            connection.execute("UPDATE folders SET page_token=NULL WHERE id=?", (folder["id"],))
            connection.commit()
            return
        raise DriveSyncError("drive_api_failed") from None
    except Exception:
        raise DriveSyncError("drive_api_failed") from None
    if not isinstance(page, dict) or page.get("incompleteSearch"):
        raise DriveSyncError("drive_search_incomplete")
    items = page.get("files", [])
    following = page.get("nextPageToken")
    if not isinstance(items, list) or (following is not None and not isinstance(following, str)):
        raise DriveSyncError("drive_response_invalid")
    if following and following == folder["page_token"]:
        raise DriveSyncError("drive_cursor_stalled")
    with connection:
        for item in items:
            file_id = _identifier(item.get("id"))
            if item.get("mimeType") == FOLDER_MIME:
                connection.execute(
                    """
                    INSERT INTO folders(id,name,parent) VALUES(?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,parent=excluded.parent,
                      done=CASE WHEN folders.parent=excluded.parent THEN folders.done ELSE 0 END,
                      page_token=CASE WHEN folders.parent=excluded.parent
                                      THEN folders.page_token ELSE NULL END
                    WHERE folders.parent IS NOT NULL
                """,
                    (file_id, item.get("name", ""), folder["id"]),
                )
            elif item.get("mimeType") in AUDIO_MIMES:
                payload = json.dumps({"file": item, "parent_id": folder["id"]}, sort_keys=True)
                connection.execute(
                    """
                    INSERT INTO files(id,metadata) VALUES(?,?)
                    ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata,
                      status=CASE WHEN files.metadata=excluded.metadata
                             THEN files.status ELSE 'pending' END,
                      attempts=CASE WHEN files.metadata=excluded.metadata
                               THEN files.attempts ELSE 0 END,
                      retry_at=CASE WHEN files.metadata=excluded.metadata
                               THEN files.retry_at ELSE 0 END
                """,
                    (file_id, payload),
                )
        connection.execute(
            "UPDATE folders SET page_token=?,done=? WHERE id=?",
            (following, int(not following), folder["id"]),
        )


class _DownloadSink:
    def __init__(self, stream: Any, path: Path, size: int, check: Any):
        self.stream, self.path, self.size, self.check = stream, path, size, check
        self.written = 0
        self.digest = hashlib.md5(usedforsecurity=False)

    def write(self, chunk: bytes) -> int:
        self.check()
        if self.written + len(chunk) > self.size:
            raise DriveSyncError("audio_size_mismatch")
        # Keep room for the remaining temporary download AND the server copy.
        if shutil.disk_usage(self.path).free < MIN_FREE_BYTES + 2 * self.size - self.written:
            raise DriveSyncError("insufficient_disk_space")
        count = self.stream.write(chunk)
        self.written += count
        self.digest.update(chunk[:count])
        return count


def _download(service: Any, metadata: dict[str, Any], path: Path, check: Any) -> None:
    size = metadata["size"]
    if shutil.disk_usage(path.parent).free < MIN_FREE_BYTES + 2 * size:
        raise DriveSyncError("insufficient_disk_space")
    with path.open("wb") as stream:
        os.chmod(path, 0o600)
        sink = _DownloadSink(stream, path, size, check)
        download = MediaIoBaseDownload(
            sink,
            service.files().get_media(fileId=metadata["file_id"], supportsAllDrives=True),
            chunksize=CHUNK_BYTES,
        )
        try:
            done = False
            while not done:
                check()
                _, done = download.next_chunk(num_retries=0)
        except DriveSyncError:
            raise
        except Exception:
            raise DriveSyncError("audio_download_failed") from None
        if sink.written != size:
            raise DriveSyncError("audio_size_mismatch")
        if metadata.get("md5_checksum") and sink.digest.hexdigest() != metadata["md5_checksum"]:
            raise DriveSyncError("audio_checksum_mismatch")
        stream.flush()
        os.fsync(stream.fileno())


def _import_file(
    service: Any,
    api: LocalImports,
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    root_id: str,
    staging: Path,
    check: Any,
    retry_failed: bool,
) -> str:
    payload = json.loads(row["metadata"])
    parent = _current_parent(service, connection, payload["parent_id"], root_id, check)
    file = _execute(
        service.files().get(
            fileId=row["id"],
            fields=FILE_FIELDS,
            supportsAllDrives=True,
        )
    )
    metadata = _metadata(file, parent)
    existing = api.find(row["id"], check)

    def register() -> dict[str, Any]:
        job = api.request("POST", "/api/conversations/drive-imports", metadata=metadata)
        job_id = _identifier(job.get("id"))
        if retry_failed and existing and job.get("status") == "failed":
            job = api.request(
                "POST", f"/api/conversations/drive-imports/{job_id}/retry", metadata={}
            )
        if job.get("status") == "failed":
            raise DriveSyncError("daymeld_import_failed")
        return job

    job = register() if existing else None
    if job is not None and not job.get("needs_audio"):
        return "already_registered"
    if not file.get("capabilities", {}).get("canDownload"):
        raise DriveSyncError("drive_download_not_allowed")
    with tempfile.TemporaryDirectory(prefix="audio-", dir=staging) as temporary:
        (Path(temporary) / ".owner").write_text("daymeld-drive-sync-v1", encoding="ascii")
        audio = Path(temporary) / "original.audio"
        _download(service, metadata, audio, check)
        check()
        # Detect edits or moves during a long download before forwarding bytes.
        parent = _current_parent(service, connection, parent["id"], root_id, check)
        refreshed = _execute(
            service.files().get(
                fileId=row["id"],
                fields=FILE_FIELDS,
                supportsAllDrives=True,
            )
        )
        if _metadata(refreshed, parent) != metadata:
            raise DriveSyncError("source_changed_during_download")
        # A new source gets a server queue entry only after its original has
        # downloaded and passed integrity checks. Existing IDs remain idempotent.
        if job is None:
            job = register()
        if not job.get("needs_audio"):
            return "already_registered"
        job_id = _identifier(job.get("id"))
        api.request(
            "POST",
            f"/api/conversations/drive-imports/{job_id}/audio",
            audio=audio,
            mime=metadata["mime_type"],
            check=check,
        )
    return "uploaded"


def status(data_dir: Path, token_path: Path) -> dict[str, Any]:
    """Read-only, local diagnostic: contains no IDs, source names or secrets."""
    config = _read_json(data_dir / "drive-sync.json")
    auth_type = _auth_type(config)
    if auth_type == "service_account":
        private = _private_service_account_file(_configured_service_account_key(config))
        authorized = private
    else:
        token = _read_json(token_path)
        authorized = DRIVE_SCOPE in (token.get("scopes") or []) and bool(token.get("refresh_token"))
        private = (token_path.stat().st_mode & 0o777) == 0o600 if token else None
    result = {
        "configured": bool(config.get("folder_id")),
        "auth_type": auth_type,
        "authorized": authorized,
        "status": config.get("status", "idle") if authorized else "auth_required",
        "credential_private": private,
        "token_private": private if auth_type == "user_oauth" else None,
        "last_run_at": config.get("last_run_at"),
        "last_result": config.get("last_result"),
    }
    if not config.get("folder_id"):
        result["status"] = "not_configured"
    return result


def sync_once(
    data_dir: Path,
    client_secret: Path,
    token_path: Path,
    *,
    folder_id: str | None = None,
    service_account_key: Path | None = None,
    use_user_oauth: bool = False,
    server_port: int = 8787,
    retry_failed: bool = False,
    stop: threading.Event | None = None,
    max_files: int = MAX_FILES,
    max_pages: int = MAX_PAGES,
) -> dict[str, Any]:
    config_path = data_dir / "drive-sync.json"
    with _lock(data_dir / "drive-sync.lock", blocking=False):
        config = _read_json(config_path)
        if service_account_key is not None and use_user_oauth:
            raise DriveSyncError("invalid_command_options")
        changing_auth = service_account_key is not None or use_user_oauth
        requested_auth = (
            "service_account"
            if service_account_key is not None
            else ("user_oauth" if use_user_oauth else _auth_type(config))
        )
        if folder_id is not None:
            folder_id = _identifier(folder_id)
            if config.get("folder_id") and config["folder_id"] != folder_id:
                raise DriveSyncError("folder_already_configured")
        root_id = folder_id or config.get("folder_id")
        if not root_id:
            if changing_auth:
                raise DriveSyncError("folder_required_for_auth_configuration")
            return {"status": "not_configured"}
        root_id = _identifier(root_id)
        deadline = time.monotonic() + RUN_SECONDS

        def check() -> None:
            if stop is not None and stop.is_set():
                raise DriveSyncError("stopped")
            if time.monotonic() >= deadline:
                raise DriveSyncError("run_time_limit")

        service = None
        connection = None
        auth_committed = False
        counts = {
            "uploaded": 0,
            "already_registered": 0,
            "failed": 0,
            "pages": 0,
            "skipped_folders": 0,
        }
        try:
            if requested_auth == "service_account":
                key_path = service_account_key or _configured_service_account_key(config)
                credentials = load_service_account_credentials(key_path)
            else:
                credentials = load_credentials(client_secret, token_path)
            service = _drive_service(credentials)
            root = _check_folder(service, root_id)
            candidate = {
                **config,
                "version": 1,
                "folder_id": root_id,
                "status": "syncing",
                "auth_type": requested_auth,
            }
            if requested_auth == "service_account":
                candidate["service_account_key"] = str(key_path.resolve())
            else:
                candidate.pop("service_account_key", None)
            # Neither key parsing nor an unsuccessful Drive root read may
            # replace a working authentication mode or its saved diagnostics.
            _atomic_json(config_path, candidate)
            config = candidate
            auth_committed = True
            connection = _queue(data_dir)
            staging = data_dir / "drive-sync-staging"
            if staging.is_symlink():
                raise DriveSyncError("unsafe_staging_path")
            staging.mkdir(mode=0o700, exist_ok=True)
            os.chmod(staging, 0o700)
            # The sync lock proves no live download owns these crash remnants.
            for old in staging.glob("audio-*"):
                if not old.is_dir() or old.is_symlink():
                    continue
                marker = old / ".owner"
                if marker.is_symlink() or not marker.is_file():
                    continue
                names = {item.name for item in old.iterdir()}
                if (
                    names <= {".owner", "original.audio"}
                    and marker.read_text(encoding="ascii") == "daymeld-drive-sync-v1"
                ):
                    shutil.rmtree(old)
            with connection:
                if retry_failed:
                    connection.execute(
                        "UPDATE files SET status='pending',attempts=0,retry_at=0,error=NULL "
                        "WHERE status='failed'"
                    )
                unfinished = connection.execute(
                    "SELECT 1 FROM folders WHERE done=0 UNION ALL "
                    "SELECT 1 FROM files WHERE status='pending' LIMIT 1"
                ).fetchone()
                if not unfinished:
                    # Retain ancestry for failed-file retries and recheck each
                    # folder's current membership before enumerating it again.
                    connection.execute("UPDATE folders SET done=0,page_token=NULL")
                    connection.execute("DELETE FROM files WHERE status='done'")
                    connection.execute(
                        "INSERT OR IGNORE INTO folders(id,name) VALUES(?,?)",
                        (root_id, root.get("name", "")),
                    )
            processed = 0
            api = LocalImports(server_port)
            while processed < min(MAX_FILES, max_files):
                check()
                row = connection.execute(
                    "SELECT * FROM files WHERE status='pending' AND retry_at<=? "
                    "ORDER BY rowid LIMIT 1",
                    (time.time(),),
                ).fetchone()
                if row:
                    try:
                        outcome = _import_file(
                            service, api, connection, row, root_id, staging, check, retry_failed
                        )
                    except DriveSyncError as error:
                        if error.code in {
                            "daymeld_unavailable",
                            "daymeld_busy",
                            "drive_api_failed",
                            "drive_permission_denied",
                            "insufficient_disk_space",
                            "stopped",
                        }:
                            raise
                        attempts = row["attempts"] + 1
                        terminal = attempts >= 3 or error.code in {
                            "source_conflict",
                            "daymeld_import_failed",
                            "drive_download_not_allowed",
                            "source_metadata_invalid",
                            "source_moved",
                            "source_ancestry_invalid",
                            "source_folder_unavailable",
                            "drive_item_unavailable",
                            "daymeld_input_rejected",
                        }
                        with connection:
                            connection.execute(
                                "UPDATE files SET status=?,attempts=?,retry_at=?,error=? "
                                "WHERE id=?",
                                (
                                    "failed" if terminal else "pending",
                                    attempts,
                                    time.time() + 900 * 2 ** (attempts - 1),
                                    error.code,
                                    row["id"],
                                ),
                            )
                        counts["failed"] += 1
                        if error.code == "run_time_limit":
                            # A slow recording must not monopolize every run.
                            # Persist its bounded retry before ending this run.
                            raise
                    else:
                        with connection:
                            connection.execute(
                                "UPDATE files SET status='done',error=NULL WHERE id=?", (row["id"],)
                            )
                        counts[outcome] += 1
                    processed += 1
                    continue
                if counts["pages"] >= min(MAX_PAGES, max_pages):
                    break
                folder = connection.execute(
                    "SELECT * FROM folders WHERE done=0 ORDER BY rowid LIMIT 1"
                ).fetchone()
                if folder is None:
                    break
                try:
                    _current_parent(service, connection, folder["id"], root_id, check)
                except DriveSyncError as error:
                    if error.code not in {
                        "source_moved",
                        "source_folder_unavailable",
                        "source_ancestry_invalid",
                    }:
                        raise
                    with connection:
                        connection.execute("UPDATE folders SET done=1 WHERE id=?", (folder["id"],))
                    counts["skipped_folders"] += 1
                    counts["pages"] += 1
                    continue
                _list_page(service, connection, folder)
                counts["pages"] += 1
            counts["pending_files"] = connection.execute(
                "SELECT count(*) FROM files WHERE status='pending'"
            ).fetchone()[0]
            counts["pending_folders"] = connection.execute(
                "SELECT count(*) FROM folders WHERE done=0"
            ).fetchone()[0]
            counts["failed_files"] = connection.execute(
                "SELECT count(*) FROM files WHERE status='failed'"
            ).fetchone()[0]
            outcome = "attention_required" if counts["failed_files"] else "ready"
            config.update({"status": outcome, "last_run_at": _now(), "last_result": counts})
            _atomic_json(config_path, config)
            return {"status": outcome, **counts}
        except DriveSyncError as error:
            # Initial unverified folder IDs are never persisted as configured.
            if config.get("folder_id") and (not changing_auth or auth_committed):
                config.update({"status": error.code, "last_run_at": _now(), "last_result": counts})
                _atomic_json(config_path, config)
            raise
        except Exception:
            if config.get("folder_id") and (not changing_auth or auth_committed):
                config.update(
                    {"status": "sync_failed", "last_run_at": _now(), "last_result": counts}
                )
                _atomic_json(config_path, config)
            raise DriveSyncError("sync_failed") from None
        finally:
            if connection is not None:
                connection.close()
            if service is not None:
                with suppress(Exception):
                    service.close()


class DriveSyncWorker:
    def __init__(
        self,
        data_dir: Path,
        client_secret: Path = Path("secrets/gmail-client.json"),
        token_path: Path = Path("secrets/drive-token.json"),
        *,
        server_port: int = 8787,
        interval: float = 900,
    ):
        self.data_dir, self.client_secret, self.token_path = data_dir, client_secret, token_path
        self.server_port, self.interval = server_port, max(60, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="drive-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                sync_once(
                    self.data_dir,
                    self.client_secret,
                    self.token_path,
                    server_port=self.server_port,
                    stop=self._stop,
                )
                failures = 0
            except DriveSyncError as error:
                if error.code == "stopped":
                    return
                failures = 0 if error.code in {"auth_required", "busy"} else min(failures + 1, 4)
            except Exception:
                # No raw SDK/HTTP exception is safe to send to server logs.
                failures = min(failures + 1, 4)
            if self._stop.wait(min(self.interval * 2**failures, 6 * 3600)):
                return


def main() -> None:
    parser = argparse.ArgumentParser(description="DriveのSoundcore原音をDaymeldへ同期します。")
    parser.add_argument("command", choices=("auth", "status", "sync"))
    parser.add_argument("--client-secret", type=Path, default=Path("secrets/gmail-client.json"))
    parser.add_argument("--token", type=Path, default=Path("secrets/drive-token.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--folder-id")
    parser.add_argument("--server-port", type=int, default=8787)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    authentication = parser.add_mutually_exclusive_group()
    authentication.add_argument(
        "--service-account-key",
        type=Path,
        help="syncのみ: 対象フォルダーの読み取り確認後、専用サービスアカウントへ切り替える",
    )
    authentication.add_argument(
        "--user-oauth",
        action="store_true",
        help="syncのみ: 対象フォルダーの読み取り確認後、既存ユーザーOAuthへ戻す",
    )
    args = parser.parse_args()
    try:
        if (
            (args.force and args.command != "auth")
            or (args.retry_failed and args.command != "sync")
            or (args.folder_id and args.command != "sync")
            or (
                (args.service_account_key is not None or args.user_oauth) and args.command != "sync"
            )
        ):
            raise DriveSyncError("invalid_command_options")
        if args.command == "auth":
            load_credentials(args.client_secret, args.token, interactive=True, force=args.force)
            result = {"status": "authorized"}
        elif args.command == "status":
            result = status(args.data_dir, args.token)
        else:
            result = sync_once(
                args.data_dir,
                args.client_secret,
                args.token,
                folder_id=args.folder_id,
                service_account_key=args.service_account_key,
                use_user_oauth=args.user_oauth,
                server_port=args.server_port,
                retry_failed=args.retry_failed,
            )
    except DriveSyncError as error:
        print(json.dumps({"status": error.code}, ensure_ascii=False))
        raise SystemExit(0 if error.code == "busy" else 1) from None
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"status": "sync_failed"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
