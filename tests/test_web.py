import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from werewolf.web import make_server

_PAGE = Path(__file__).resolve().parents[1] / "werewolf" / "web" / "index.html"
_SECRET = "sk-test-do-not-store"


class WebPageTests(unittest.TestCase):
    def test_page_masks_keys_and_does_not_store_them(self) -> None:
        text = _PAGE.read_text(encoding="utf-8")
        self.assertIn('input.type = "password"', text)
        self.assertIn('autocomplete = "new-password"', text)
        for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            self.assertNotIn(banned, text)

    def test_players_bring_their_own_keys_and_the_room_hides_them(self) -> None:
        server = make_server("127.0.0.1", 0, str(Path(__file__).resolve().parents[1] / "config.yaml"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        try:
            created = _post(base + "/api/rooms", {"preset": "classic6", "seed": 7, "mock": True})
            self.assertNotIn(_SECRET, json.dumps(created))
            room = json.load(urllib.request.urlopen(base + "/api/rooms/" + created["id"]))
            self.assertNotIn("hostToken", room)
            self.assertEqual(room["filled"], 0)

            _post(
                base + "/api/rooms/" + created["id"] + "/seats/p1",
                {"vendorId": "openai", "apiKey": _SECRET},
            )
            room = json.load(urllib.request.urlopen(base + "/api/rooms/" + created["id"]))
            self.assertEqual(room["seats"][0]["vendor_id"], "openai")
            self.assertNotIn(_SECRET, json.dumps(room))
            with self.assertRaises(urllib.error.HTTPError) as taken:
                _post(
                    base + "/api/rooms/" + created["id"] + "/seats/p1",
                    {"vendorId": "deepseek", "apiKey": "another"},
                )
            self.assertEqual(taken.exception.code, 400)

            for index, vendor_id in enumerate(["deepseek", "qwen", "zhipu", "moonshot", "gemini"], start=2):
                _post(
                    base + "/api/rooms/" + created["id"] + "/seats/p" + str(index),
                    {"vendorId": vendor_id, "apiKey": _SECRET + str(index)},
                )
            with self.assertRaises(urllib.error.HTTPError) as denied:
                _post(base + "/api/rooms/" + created["id"] + "/start", {"hostToken": "nope"})
            self.assertEqual(denied.exception.code, 403)

            _post(base + "/api/rooms/" + created["id"] + "/start", {"hostToken": created["hostToken"]})
            with urllib.request.urlopen(base + "/api/rooms/" + created["id"] + "/events", timeout=40) as stream:
                raw = stream.read().decode("utf-8")
            self.assertNotIn(_SECRET, raw)
            kinds = [json.loads(line[len("data: ") :])["kind"] for line in raw.split("\n") if line.startswith("data: ")]
            self.assertIn("lobby", kinds)
            self.assertIn("table", kinds)
            self.assertIn("winner", kinds)
            self.assertIn("done", kinds)
        finally:
            server.shutdown()
            server.server_close()


def _post(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


if __name__ == "__main__":
    unittest.main()
