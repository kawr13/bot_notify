#!/usr/bin/env python3
"""IMAP Monitor module for Bot Notify Desktop."""

from __future__ import annotations

import configparser
import imaplib
import json
import logging
import os
import queue
import re
import socket
import ssl
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from utilites import compact_whitespace, extract_entities

LOGGER = logging.getLogger("bot_notify.imap")

DATA_DIR = Path("data")
VK_RECIPIENTS_PATH = DATA_DIR / "vk_recipients.json"
CONFIG_JSON_PATH = Path("config.json")


def decode_mime_text(value: str | None) -> str:
    """Decode MIME encoded text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def decode_bytes(payload: bytes | None, preferred_charset: str | None = None) -> str:
    """Decode bytes to string with fallback charsets."""
    if not payload:
        return ""

    charsets = []
    if preferred_charset:
        charsets.append(preferred_charset)
    charsets.extend(["utf-8", "cp1251", "koi8-r", "latin-1"])

    for charset in charsets:
        try:
            return payload.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue

    return payload.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    """Convert HTML to plain text."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n", strip=True)


def extract_message_text(message: Message) -> str:
    """Extract text content from email message."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_disposition() == "attachment":
                continue

            payload = part.get_payload(decode=True)
            decoded = decode_bytes(payload, part.get_content_charset())
            content_type = part.get_content_type()

            if content_type == "text/plain":
                plain_parts.append(decoded)
            elif content_type == "text/html":
                html_parts.append(html_to_text(decoded))
    else:
        payload = message.get_payload(decode=True)
        decoded = decode_bytes(payload, message.get_content_charset())
        if message.get_content_type() == "text/html":
            html_parts.append(html_to_text(decoded))
        else:
            plain_parts.append(decoded)

    combined = "\n\n".join(part for part in plain_parts if part.strip())
    if not combined:
        combined = "\n\n".join(part for part in html_parts if part.strip())
    return compact_whitespace(combined)


def is_tcp_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_local_http_proxy_url() -> str | None:
    """Detect local HTTP proxy URL."""
    candidates = [
        "http://127.0.0.1:7890",
        "http://127.0.0.1:7897",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:9090",
    ]
    for proxy_url in candidates:
        parsed = urlparse(proxy_url)
        if parsed.hostname and parsed.port and is_tcp_port_open(parsed.hostname, parsed.port):
            LOGGER.info("Автоматически найден локальный HTTP proxy: %s", proxy_url)
            return proxy_url
    return None


class HttpProxyIMAP4_SSL(imaplib.IMAP4_SSL):
    """IMAP4_SSL with HTTP proxy support."""

    def __init__(self, host: str, port: int, proxy_url: str, timeout: float = 20) -> None:
        self._target_host = host
        self._target_port = port
        self._proxy = urlparse(proxy_url)
        if self._proxy.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported IMAP proxy scheme: {self._proxy.scheme}")
        if not self._proxy.hostname or not self._proxy.port:
            raise ValueError(f"Некорректный IMAP proxy URL: {proxy_url}")
        super().__init__(host=host, port=port, timeout=timeout)

    def _create_socket(self, timeout: float):  # type: ignore[override]
        sock = socket.create_connection((self._proxy.hostname, self._proxy.port), timeout)
        connect_request = (
            f"CONNECT {self._target_host}:{self._target_port} HTTP/1.1\r\n"
            f"Host: {self._target_host}:{self._target_port}\r\n"
            "Proxy-Connection: Keep-Alive\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(connect_request)

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk

        status_line = response.split(b"\r\n", 1)[0]
        if b" 200 " not in status_line and not status_line.endswith(b" 200"):
            sock.close()
            raise OSError(f"IMAP proxy CONNECT failed: {status_line.decode('latin1', 'ignore')}")

        ssl_context = self.ssl_context if self.ssl_context else ssl.create_default_context()
        return ssl_context.wrap_socket(sock, server_hostname=self._target_host)


def parse_int_list(raw_value: str | None) -> list[int]:
    """Parse comma-separated integers."""
    if not raw_value:
        return []
    result: list[int] = []
    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            LOGGER.warning("Не удалось разобрать целое значение: %s", item)
    return result


def parse_vk_peer_input(raw_value: str | None) -> int | None:
    """Parse VK peer ID from various formats."""
    if not raw_value:
        return None

    value = raw_value.strip()
    if not value:
        return None

    value = re.sub(
        r"^https?://(m\.)?vk\.com/", "", value, flags=re.IGNORECASE
    ).strip("/")
    if value.startswith("id") and value[2:].isdigit():
        return int(value[2:])

    if value.lstrip("-").isdigit():
        peer_id = int(value)
        return peer_id if peer_id != 0 else None

    return None


@dataclass(slots=True)
class NotificationRecord:
    """Notification record structure."""

    record_id: str
    uid: int
    created_at: str
    received_at: str
    sender: str
    subject: str
    preview: str
    body: str
    links: list[str]
    contracts: list[str]
    inns: list[str]
    ticket_ids: list[str]
    # Helpdesk ticket fields (parsed from HTML template)
    ticket_id: str = ""
    ticket_title: str = ""
    ticket_url: str = ""
    ticket_authors: str = ""
    ticket_user_email: str = ""
    ticket_creationdate: str = ""
    ticket_category: str = ""
    ticket_assigntogroups: str = ""
    ticket_priority: str = ""
    ticket_content: str = ""
    is_helpdesk: bool = False


    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NotificationRecord":
        """Create instance from dictionary."""
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically using temp file."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


@dataclass(slots=True)
class AppConfig:
    """Application configuration."""

    imap_server: str
    imap_port: int
    imap_username: str
    imap_password: str
    imap_http_proxy: str | None
    poll_seconds: int
    ignore_keywords: list[str]
    process_unseen_on_start: bool
    program_name: str = "Bot Notify Desktop"

    @classmethod
    def load(cls) -> "AppConfig":
        """Load configuration from .env, config.ini, and config.json."""
        from dotenv import dotenv_values, load_dotenv

        load_dotenv()
        dotenv_payload = {
            key.lower(): value
            for key, value in dotenv_values(CONFIG_JSON_PATH.parent / ".env").items()
            if value
        }

        parser = configparser.ConfigParser()
        parser.read(CONFIG_JSON_PATH.parent / "config.ini", encoding="utf-8")
        mail_section = parser["MAIL"] if parser.has_section("MAIL") else {}

        legacy_config: dict[str, Any] = {}
        if CONFIG_JSON_PATH.exists():
            try:
                legacy_config = json.loads(
                    CONFIG_JSON_PATH.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                LOGGER.warning("config.json не читается")

        def pick_env(
            env_names: tuple[str, ...],
            file_names: tuple[str, ...],
            fallback: str = "",
        ) -> str:
            for name in env_names:
                value = os.getenv(name)
                if value:
                    return value
            for name in file_names:
                value = dotenv_payload.get(name.lower())
                if value:
                    return value
            for name in file_names:
                if name in mail_section and mail_section[name]:
                    return str(mail_section[name])
            for name in file_names:
                if legacy_config.get(name):
                    return str(legacy_config[name])
            return fallback

        raw_ignore = os.getenv("IGNORE_KEYWORDS", "").strip()
        if raw_ignore:
            ignore_keywords = [
                item.strip()
                for item in raw_ignore.split(",")
                if item.strip()
            ]
        else:
            ignore_keywords = [
                str(item).strip()
                for item in legacy_config.get("ignore_keywords", [])
                if str(item).strip()
            ]

        server = pick_env(
            ("IMAP_SERVER", "EMAIL_SERVER"),
            ("server", "imap_server"),
            fallback="",
        )
        username = pick_env(
            ("IMAP_USERNAME", "EMAIL_USERNAME"),
            ("username", "email_address"),
            fallback="",
        )
        password = pick_env(
            ("IMAP_PASSWORD", "EMAIL_PASSWORD"), ("password",), fallback=""
        )
        port_text = pick_env(
            ("IMAP_PORT", "EMAIL_PORT"), ("port", "imap_port"), fallback="993"
        )

        if not server or not username or not password:
            raise RuntimeError(
                "Не хватает настроек IMAP. Укажи IMAP_SERVER, IMAP_USERNAME и IMAP_PASSWORD "
                "в .env или config.ini."
            )

        try:
            port = int(port_text)
        except ValueError as exc:
            raise RuntimeError(
                f"Некорректный IMAP порт: {port_text}"
            ) from exc

        imap_http_proxy = (
            os.getenv("IMAP_HTTP_PROXY", "").strip()
            or detect_local_http_proxy_url()
        )
        poll_text = (
            os.getenv("POLL_SECONDS", "45").strip() or "45"
        )
        poll_seconds = max(15, int(poll_text))
        process_unseen_on_start = (
            os.getenv("PROCESS_UNSEEN_ON_START", "1")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )

        return cls(
            imap_server=server,
            imap_port=port,
            imap_username=username,
            imap_password=password,
            imap_http_proxy=imap_http_proxy,
            poll_seconds=poll_seconds,
            ignore_keywords=ignore_keywords,
            process_unseen_on_start=process_unseen_on_start,
        )


class RuntimeStore:
    """Runtime state storage."""

    def __init__(
        self,
        state_path: Path,
        history_path: Path,
        history_limit: int = 300,
    ) -> None:
        self.state_path = state_path
        self.history_path = history_path
        self.history_limit = history_limit
        self.state_exists = state_path.exists()
        self.last_uid = 0
        self.processed_uids: set[int] = set()
        self.history: list[NotificationRecord] = []
        self._load()

    def _load(self) -> None:
        if self.state_path.exists():
            try:
                payload = json.loads(
                    self.state_path.read_text(encoding="utf-8")
                )
                self.last_uid = int(payload.get("last_uid", 0))
                self.processed_uids = {
                    int(item)
                    for item in payload.get("processed_uids", [])
                    if str(item).strip()
                }
            except (ValueError, json.JSONDecodeError):
                LOGGER.warning("state.json поврежден")
                self.last_uid = 0
                self.processed_uids = set()

        if self.history_path.exists():
            try:
                payload = json.loads(
                    self.history_path.read_text(encoding="utf-8")
                )
                self.history = [
                    NotificationRecord.from_dict(item)
                    for item in payload
                ]
            except (TypeError, json.JSONDecodeError):
                LOGGER.warning("История уведомлений повреждена")
                self.history = []

    def save_state(self) -> None:
        """Save state to file."""
        processed_snapshot = sorted(self.processed_uids)[-5000:]
        write_json(
            self.state_path,
            {
                "last_uid": self.last_uid,
                "processed_uids": processed_snapshot,
            },
        )

    def has_processed(self, uid: int) -> bool:
        """Check if UID was processed."""
        return uid in self.processed_uids

    def mark_processed(self, uid: int) -> None:
        """Mark UID as processed."""
        self.processed_uids.add(uid)
        self.last_uid = max(self.last_uid, uid)
        if len(self.processed_uids) > 5000:
            self.processed_uids = set(
                sorted(self.processed_uids)[-5000:]
            )
        self.save_state()

    def mark_processed_many(self, uids: list[int]) -> None:
        """Mark multiple UIDs as processed."""
        if not uids:
            return
        self.processed_uids.update(uids)
        self.last_uid = max(self.last_uid, max(uids))
        if len(self.processed_uids) > 5000:
            self.processed_uids = set(
                sorted(self.processed_uids)[-5000:]
            )
        self.save_state()

    def append_history(self, record: NotificationRecord) -> None:
        """Append record to history."""
        self.history.insert(0, record)
        self.history = self.history[: self.history_limit]
        write_json(
            self.history_path,
            [asdict(item) for item in self.history],
        )

    def clear_history(self) -> None:
        """Clear history."""
        self.history = []
        write_json(self.history_path, [])


def parse_ticket_html(html: str) -> dict[str, str]:
    """Parse helpdesk notification HTML and extract ticket fields."""
    result: dict[str, str] = {}
    soup = BeautifulSoup(html, "html.parser")
    # Get all text with newlines preserved
    text = soup.get_text(separator="\n")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Extract ticket ID and title from "Заявка №123: Title"
    for line in lines:
        m = re.search(r"Заявка\s+№?\s*([A-Za-z0-9_-]+)[:\s]+(.+)$", line, re.IGNORECASE)
        if m:
            result["ticket_id"] = m.group(1)
            result["ticket_title"] = m.group(2).strip()
            break
    # Extract ticket URL
    for line in lines:
        m = re.search(r"Открыть\s+заявку[:\s]+(.+)$", line, re.IGNORECASE)
        if m:
            result["ticket_url"] = m.group(1).strip()
            break
    # Extract author - label and value may be on separate lines
    for i, line in enumerate(lines):
        if re.search(r"(?:Автор|От|Отправитель)$", line, re.IGNORECASE):
            # Look at next line for the value
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # Skip leading colon
                if next_line.startswith(":"):
                    next_line = next_line[1:].strip()
                m = re.search(r"(.+?)(?:\(([^)]+)\))?$", next_line)
                if m:
                    author = m.group(1).strip()
                    if author:
                        result["ticket_authors"] = author
                    email = m.group(2)
                    if email:
                        result["ticket_user_email"] = email
                if m:
                    author = m.group(1).strip()
                    if author:
                        result["ticket_authors"] = author
                    email = m.group(2)
                    if email:
                        result["ticket_user_email"] = email
            break
    # Extract creation date - label and value may be on separate lines
    for i, line in enumerate(lines):
        if re.search(r"(?:Дата\s+создания|Created)$", line, re.IGNORECASE):
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith(":"):
                    next_line = next_line[1:].strip()
                result["ticket_creationdate"] = next_line
            break
    # Extract category - label and value may be on separate lines
    for i, line in enumerate(lines):
        if re.search(r"(?:Категория|Category)$", line, re.IGNORECASE):
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith(":"):
                    next_line = next_line[1:].strip()
                result["ticket_category"] = next_line
            break
    # Extract assigned groups - label and value may be on separate lines
    for i, line in enumerate(lines):
        if re.search(r"(?:Группа|Группы|Assignto)$", line, re.IGNORECASE):
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith(":"):
                    next_line = next_line[1:].strip()
                result["ticket_assigntogroups"] = next_line
            break
    # Extract priority - label and value may be on separate lines
    for i, line in enumerate(lines):
        if re.search(r"(?:Приоритет|Priority)$", line, re.IGNORECASE):
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith(":"):
                    next_line = next_line[1:].strip()
                result["ticket_priority"] = next_line
            break
    for line in lines:
        m = re.search(r"(?:Автор|От|Отправитель)[:\s]+([^\(]+?)(?:\(([^)]+)\))?", line, re.IGNORECASE)
        if m:
            author = m.group(1).strip()
            if author:
                result["ticket_authors"] = author
            email = m.group(2)
            if email:
                result["ticket_user_email"] = email
            break
    # Extract creation date
    for line in lines:
        m = re.search(r"(?:Дата\s+создания|Created)[:\s]+(.+)$", line, re.IGNORECASE)
        if m:
            result["ticket_creationdate"] = m.group(1).strip()
            break
    # Extract category
    for line in lines:
        m = re.search(r"(?:Категория|Category)[:\s]+(.+)$", line, re.IGNORECASE)
        if m:
            result["ticket_category"] = m.group(1).strip()
            break
    # Extract assigned groups
    for line in lines:
        m = re.search(r"(?:Группа|Группы|Assignto)[:\s]+(.+)$", line, re.IGNORECASE)
        if m:
            result["ticket_assigntogroups"] = m.group(1).strip()
            break
    # Extract priority
    for line in lines:
        m = re.search(r"(?:Приоритет|Priority)[:\s]+(.+)$", line, re.IGNORECASE)
        if m:
            result["ticket_priority"] = m.group(1).strip()
            break
    # Extract content - find text after "Содержание:" or "Content:"
    # The label and colon may be on separate lines due to HTML parsing
    content_start = False
    content_parts: list[str] = []
    for i, line in enumerate(lines):
        if re.search(r"(?:Содержание|Текст\s+заявки|Content)$", line, re.IGNORECASE):
            content_start = True
            continue
        if content_start:
            # Skip standalone colon line
            if line == ":":
                continue
            # Stop if we hit another section header
            if re.match(r"^(?:Заявка|Автор|Дата|Категория|Приоритет|Группа|Открыть)", line, re.IGNORECASE):
                break
            content_parts.append(line)
    if content_parts:
        result["ticket_content"] = "\n".join(content_parts)
    # Extract TicketID from URL as fallback
    if "ticket_id" not in result and "ticket_url" in result:
        url_match = re.search(r"[?&]TicketID=([A-Za-z0-9_-]+)", result["ticket_url"])
        if url_match:
            result["ticket_id"] = url_match.group(1)
    return result



def detect_helpdesk_template(html: str) -> bool:
    """Detect if the email uses the helpdesk notification template."""
    patterns = [
        r"Зарегистрирована\s+новая\s+заявка",
        r"Заявка\s+№",
        r"Открыть\s+заявку",
        r"(?:Автор|От)[:\s]",
        r"(?:Приоритет|Priority)",
    ]
    for pattern in patterns:
        if re.search(pattern, html, re.IGNORECASE):
            return True
    return False



class ImapMonitor:
    """IMAP email monitor that parses messages and creates NotificationRecords."""
    
    def __init__(
        self,
        config: AppConfig,
        store: RuntimeStore,
        event_queue: queue.Queue,
    ) -> None:
        self.config = config
        self.store = store
        self.event_queue = event_queue
        self._stop_event = threading.Event()
    
    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._stop_event.set()
    
    def run(self) -> None:
        """Main monitoring loop."""
        LOGGER.info("IMAP монитор запущен для %s", self.config.imap_username)
        
        # Process unseen emails on start if configured
        if self.config.process_unseen_on_start:
            self._check_emails()
        
        # Poll loop
        while not self._stop_event.is_set():
            self._stop_event.wait(self.config.poll_seconds)
            if not self._stop_event.is_set():
                self._check_emails()
    
    def _check_emails(self) -> None:
        """Check for new emails."""
        try:
            imap = self._connect_imap()
            if imap is None:
                return
            
            imap.select("INBOX", readonly=False)
            status, messages = imap.search(None, "UNSEEN")
            
            if status != "OK":
                LOGGER.warning("Не удалось получить список писем: %s", status)
                imap.logout()
                return
            
            msg_nums = messages[0].split() if messages[0] else []
            if not msg_nums:
                return
            
            LOGGER.info("Найдено %d непрочитанных писем", len(msg_nums))
            
            new_records: list[NotificationRecord] = []
            uids_to_process: list[int] = []
            
            for msg_num in msg_nums:
                try:
                    _, msg_data = imap.fetch(msg_num, "(RFC822)")
                    if not msg_data:
                        continue
                    
                    email_msg = message_from_bytes(msg_data[0][1])
                    uid = self._get_uid(email_msg, msg_num)
                    
                    if self.store.has_processed(uid):
                        continue
                    
                    record = self._parse_email(email_msg, uid)
                    if record:
                        new_records.append(record)
                        uids_to_process.append(uid)
                except Exception as exc:
                    LOGGER.warning("Ошибка обработки письма %s: %s", msg_num, exc)
            
            # Mark processed
            if uids_to_process:
                self.store.mark_processed_many(uids_to_process)
            
            # Push to event queue
            for record in new_records:
                self.store.append_history(record)
                self.event_queue.put(("notification", record))
                LOGGER.info("Уведомление: %s", record.subject)
            
            imap.logout()
            
        except Exception as exc:
            LOGGER.exception("Ошибка IMAP монитора: %s", exc)
    
    def _connect_imap(self) -> imaplib.IMAP4_SSL | HttpProxyIMAP4_SSL | None:
        """Connect to IMAP server."""
        try:
            if self.config.imap_http_proxy:
                imap = HttpProxyIMAP4_SSL(
                    self.config.imap_server,
                    self.config.imap_port,
                    self.config.imap_http_proxy,
                )
            else:
                imap = imaplib.IMAP4_SSL(
                    self.config.imap_server,
                    self.config.imap_port,
                )
            imap.login(self.config.imap_username, self.config.imap_password)
            return imap
        except Exception as exc:
            LOGGER.warning("Не удалось подключиться к IMAP: %s", exc)
            return None
    
    @staticmethod
    def _get_uid(message: Message, msg_num: bytes) -> int:
        """Get UID from message or fallback to msg_num."""
        uid = message.get("Message-ID")
        if uid:
            return hash(uid) & 0x7FFFFFFF
        return int(msg_num)
    
    def _parse_email(self, message: Message, uid: int) -> NotificationRecord | None:
        """Parse email message and create NotificationRecord."""
        try:
            sender = decode_mime_text(message.get("From", ""))
            subject = decode_mime_text(message.get("Subject", ""))
            received_at = decode_mime_text(message.get("Date", ""))
            
            # Extract plain and HTML content
            plain_text = ""
            html_text = ""
            
            if message.is_multipart():
                for part in message.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    if part.get_content_disposition() == "attachment":
                        continue
                    
                    payload = part.get_payload(decode=True)
                    decoded = decode_bytes(payload, part.get_content_charset())
                    content_type = part.get_content_type()
                    
                    if content_type == "text/plain":
                        plain_text = decoded
                    elif content_type == "text/html":
                        html_text = decoded
            else:
                payload = message.get_payload(decode=True)
                decoded = decode_bytes(payload, message.get_content_charset())
                if message.get_content_type() == "text/html":
                    html_text = decoded
                else:
                    plain_text = decoded
            
            # Use plain text as body, HTML for template detection
            body = compact_whitespace(plain_text) if plain_text else compact_whitespace(html_to_text(html_text))
            preview = body[:300] if body else ""
            
            # Extract entities from body
            entities = extract_entities(body)
            
            # Try to parse helpdesk HTML template
            ticket_data: dict[str, str] = {}
            is_helpdesk = False
            if html_text and detect_helpdesk_template(html_text):
                ticket_data = parse_ticket_html(html_text)
                is_helpdesk = True
                LOGGER.info("Обнаружен шаблон helpdesk: %s", ticket_data.get("ticket_id", "unknown"))
            
            # Create record
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            record_id = f"{uid}-{int(time.time() * 1000)}"
            
            record = NotificationRecord(
                record_id=record_id,
                uid=uid,
                created_at=now,
                received_at=received_at or now,
                sender=sender,
                subject=subject,
                preview=preview,
                body=body,
                links=entities["urls"],
                contracts=entities["contracts"],
                inns=entities["inns"],
                ticket_ids=entities["ticket_ids"],
                # Helpdesk ticket fields
                ticket_id=ticket_data.get("ticket_id", ""),
                ticket_title=ticket_data.get("ticket_title", ""),
                ticket_url=ticket_data.get("ticket_url", ""),
                ticket_authors=ticket_data.get("ticket_authors", ""),
                ticket_user_email=ticket_data.get("ticket_user_email", ""),
                ticket_creationdate=ticket_data.get("ticket_creationdate", ""),
                ticket_category=ticket_data.get("ticket_category", ""),
                ticket_assigntogroups=ticket_data.get("ticket_assigntogroups", ""),
                ticket_priority=ticket_data.get("ticket_priority", ""),
                ticket_content=ticket_data.get("ticket_content", ""),
                is_helpdesk=is_helpdesk,
            )
            return record
        except Exception as exc:
            LOGGER.exception("Ошибка парсинга письма: %s", exc)
            return None

