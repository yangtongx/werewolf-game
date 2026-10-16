"""联机牌桌。每人自己选厂家并带上自己的密钥，密钥只活在这桌的内存里。"""

from __future__ import annotations

import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from werewolf.game.machine import PRESETS
from werewolf.play.vendors import Vendor, load_vendors

_PAGE = Path(__file__).resolve().parent / "index.html"
_TABLES: dict[str, "Table"] = {}
_LOCK = threading.Lock()
_MAX_BODY = 32_000


@dataclass
class Seat:
    vendor: Vendor
    api_key: str


class Table:
    def __init__(self, preset: str, seed: int, mock: bool, talk_rounds: int, wolf_rounds: int) -> None:
        self.id = uuid.uuid4().hex
        self.preset = preset
        self.seed = seed
        self.mock = mock
        self.talk_rounds = talk_rounds
        self.wolf_rounds = wolf_rounds
        self.host_token = secrets.token_urlsafe(24)
        self.seats: dict[str, Seat | None] = {f"p{index}": None for index in range(1, len(PRESETS[preset]) + 1)}
        self.started = False
        self.events: list[dict[str, Any]] = []
        self.cond = threading.Condition()
        self.closed = False
        self._lock = threading.Lock()

    def public(self) -> dict[str, Any]:
        filled = [seat for seat in self.seats.values() if seat is not None]
        return {
            "id": self.id,
            "preset": self.preset,
            "seed": self.seed,
            "mock": self.mock,
            "started": self.started,
            "size": len(self.seats),
            "filled": len(filled),
            "seats": [
                {
                    "id": seat_id,
                    "taken": seat is not None,
                    "vendor_id": "" if seat is None else seat.vendor.id,
                    "vendor_label": "" if seat is None else seat.vendor.label,
                    "model": "" if seat is None else seat.vendor.model,
                }
                for seat_id, seat in self.seats.items()
            ],
        }

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        with self.cond:
            self.events.append({"kind": kind, "payload": payload})
            self.cond.notify_all()

    def publish(self) -> None:
        self.emit("lobby", self.public())

    def claim(self, seat_id: str, vendor: Vendor, api_key: str) -> None:
        with self._lock:
            if self.started:
                raise ValueError("这桌已经开斗")
            if seat_id not in self.seats:
                raise ValueError("没有这个座位")
            if self.seats[seat_id] is not None:
                raise ValueError("这个座位已经有人")
            if not self.mock and not api_key.strip():
                raise ValueError("请填写你的密钥")
            self.seats[seat_id] = Seat(vendor, api_key.strip())
        self.publish()

    def begin(self, host_token: str, vendors: list[Vendor]) -> None:
        del vendors
        with self._lock:
            if not secrets.compare_digest(host_token, self.host_token):
                raise PermissionError("只有开桌的人可以开斗")
            if self.started:
                raise ValueError("这桌已经开斗")
            if any(seat is None for seat in self.seats.values()):
                raise ValueError("还没坐满")
            self.started = True
            assignment = {seat_id: seat.vendor for seat_id, seat in self.seats.items() if seat is not None}
            keys = {seat_id: seat.api_key for seat_id, seat in self.seats.items() if seat is not None}
            for seat in self.seats.values():
                if seat is not None:
                    seat.api_key = ""
        self.publish()
        threading.Thread(target=_play, args=(self, assignment, keys), daemon=True).start()

    def finish(self) -> None:
        with self.cond:
            self.closed = True
            self.cond.notify_all()


def make_server(host: str, port: int, config: str) -> ThreadingHTTPServer:
    vendors = load_vendors(config)
    by_id = {vendor.id: vendor for vendor in vendors}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/" or _room_id(path):
                self._bytes(200, _PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/api/vendors":
                body = json.dumps([vendor.public_dict() for vendor in vendors], ensure_ascii=False).encode()
                self._bytes(200, body, "application/json; charset=utf-8")
                return
            room_id = _api_room_id(path)
            if room_id and path.endswith("/events"):
                table = _table(room_id)
                if table is None:
                    self._json(404, {"error": "没有这桌"})
                    return
                self._stream(table)
                return
            if room_id and path == f"/api/rooms/{room_id}":
                table = _table(room_id)
                if table is None:
                    self._json(404, {"error": "没有这桌"})
                    return
                self._json(200, table.public())
                return
            self._json(404, {"error": "没有这个页面"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                data = _read_json(self)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            if path == "/api/rooms":
                self._create(data)
                return
            room_id = _api_room_id(path)
            table = _table(room_id) if room_id else None
            if table is None:
                self._json(404, {"error": "没有这桌"})
                return
            try:
                if path.endswith("/start"):
                    table.begin(str(data.get("hostToken") or ""), vendors)
                    self._json(200, {"ok": True})
                    return
                seat_id = _seat_id(path)
                if seat_id is None:
                    self._json(404, {"error": "没有这个页面"})
                    return
                vendor = by_id.get(str(data.get("vendorId") or ""))
                if vendor is None:
                    raise ValueError("请选择厂家")
                table.claim(seat_id, vendor, str(data.get("apiKey") or ""))
            except PermissionError as exc:
                self._json(403, {"error": str(exc)})
                return
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"ok": True})

        def _create(self, data: dict[str, Any]) -> None:
            try:
                preset = str(data.get("preset") or "classic6")
                if preset not in PRESETS:
                    raise ValueError("未知牌局")
                seed = int(data.get("seed", 7))
                talk_rounds = int(data.get("talk_rounds", 1))
                wolf_rounds = int(data.get("wolf_rounds", 1))
                if talk_rounds < 1 or wolf_rounds < 1:
                    raise ValueError("发言轮数至少为 1")
            except (TypeError, ValueError) as exc:
                self._json(400, {"error": str(exc) if str(exc) in {"未知牌局", "发言轮数至少为 1"} else "种子和轮数要是整数"})
                return
            table = Table(preset, seed, bool(data.get("mock")), talk_rounds, wolf_rounds)
            with _LOCK:
                _TABLES[table.id] = table
            table.publish()
            self._json(200, {"id": table.id, "hostToken": table.host_token})

        def _stream(self, table: Table) -> None:
            self.protocol_version = "HTTP/1.0"
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            cursor = 0
            try:
                while True:
                    with table.cond:
                        while cursor >= len(table.events) and not table.closed:
                            table.cond.wait(timeout=15)
                        if cursor >= len(table.events) and table.closed:
                            break
                        batch = table.events[cursor:]
                        cursor = len(table.events)
                    for event in batch:
                        self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    if not batch:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    if table.closed and cursor >= len(table.events):
                        break
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def _json(self, status: int, payload: dict[str, object]) -> None:
            self._bytes(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8765, config: str = "config.yaml") -> None:
    server = make_server(host, port, config)
    print(f"牌桌页面 http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _table(room_id: str) -> Table | None:
    with _LOCK:
        return _TABLES.get(room_id)


def _room_id(path: str) -> str:
    if path.startswith("/r/") and path.count("/") == 2:
        return path[3:]
    return ""


def _api_room_id(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "rooms":
        return parts[2]
    return ""


def _seat_id(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[3] == "seats":
        return parts[4]
    return None


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0 or length > _MAX_BODY:
        raise ValueError("请求体无效")
    try:
        data = json.loads(handler.rfile.read(length))
    except json.JSONDecodeError as exc:
        raise ValueError("请求不是 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("请求不是 JSON 对象")
    api_key = data.get("apiKey")
    if isinstance(api_key, str) and len(api_key) > 500:
        raise ValueError("密钥太长")
    return data


def _play(table: Table, assignment: dict[str, Vendor], keys: dict[str, str]) -> None:
    import asyncio

    from werewolf.play.director import play_match

    secrets_used = [value for value in keys.values() if value]
    if table.mock:
        keys.clear()
        secrets_used.clear()

    def emit(kind: str, payload: dict[str, Any]) -> None:
        table.emit(kind, payload)

    try:
        asyncio.run(
            play_match(
                preset=table.preset,
                seed=table.seed,
                mock=table.mock,
                model="",
                api_key=None,
                base_url=None,
                talk_rounds=table.talk_rounds,
                wolf_rounds=table.wolf_rounds,
                printer=lambda _line: None,
                emit=emit,
                keys=None if table.mock else keys,
                assignment=assignment,
            )
        )
    except Exception as exc:
        message = str(exc)
        for secret in secrets_used:
            message = message.replace(secret, "***")
        table.emit("error", {"message": message})
    finally:
        keys.clear()
        secrets_used.clear()
        table.emit("done", {})
        table.finish()
