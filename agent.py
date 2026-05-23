from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from brain import AgentBrain
from config import get_settings
from orderbot import OrderBot
from storage import Store


settings = get_settings()
store = Store(settings.database_path)
brain = AgentBrain(settings, store)
orderbot = OrderBot(settings, store)


class WhatsAppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self._send_json({"ok": True})
            return
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(build_index_html())
            return

        path, _, query = self.path.partition("?")
        if path != "/webhook":
            self.send_error(404)
            return

        params = parse_query(query)
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and token == settings.whatsapp_verify_token and challenge:
            self._send_text(challenge)
            return
        self.send_error(403)

    def do_POST(self) -> None:
        if self.path not in {"/webhook", "/simulate/client", "/simulate/distributor", "/simulate/payment", "/simulate/pickup"}:
            self.send_error(404)
            return

        try:
            payload = self._read_json()
            if self.path == "/simulate/client":
                self._simulate_client(payload)
                return
            if self.path == "/simulate/distributor":
                self._simulate_distributor(payload)
                return
            if self.path == "/simulate/payment":
                self._simulate_payment(payload)
                return
            if self.path == "/simulate/pickup":
                self._simulate_pickup(payload)
                return

            messages = extract_messages(payload)
            for item in messages:
                phone = item["from"]
                text = item["text"]
                store.add_message(phone, "user", text)
                result = orderbot.handle_client(phone, text)
                if result.client_reply:
                    send_whatsapp_message(phone, result.client_reply)
                if result.distributor_message:
                    print(f"[supplier-request] {result.distributor}: {result.distributor_message}")
            self._send_json({"ok": True, "messages": len(messages)})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _send_json(self, body: dict, status: int = 200) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, text: str, status: int = 200) -> None:
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str, status: int = 200) -> None:
        raw = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _simulate_client(self, payload: dict) -> None:
        phone = str(payload.get("phone") or "client-1")
        text = str(payload.get("text") or "")
        result = orderbot.handle_client(phone, text)
        if result.client_reply:
            store.add_message(phone, "assistant", result.client_reply)
        self._send_json(result.__dict__)

    def _simulate_distributor(self, payload: dict) -> None:
        distributor = str(payload.get("distributor") or "DISTRIBUTOR_A")
        valid_distributors = {name for names in orderbot.routes.values() for name in names}
        if distributor not in valid_distributors:
            self._send_json({"error": "Unknown distributor"}, status=400)
            return
        text = str(payload.get("text") or "")
        result = orderbot.handle_distributor(distributor, text)
        self._send_json(result.__dict__)

    def _simulate_payment(self, payload: dict) -> None:
        phone = str(payload.get("phone") or "client-1")
        amount = float(payload.get("amount") or 0)
        received = bool(payload.get("received", True))
        result = orderbot.verify_payment(phone, amount, received)
        self._send_json(result.__dict__)

    def _simulate_pickup(self, payload: dict) -> None:
        phone = str(payload.get("phone") or "client-1")
        name = str(payload.get("name") or "Client")
        pickup_time = str(payload.get("pickup_time") or "today")
        result = orderbot.coordinate_pickup(phone, name, pickup_time)
        self._send_json(result.__dict__)


def parse_query(query: str) -> dict[str, str]:
    from urllib.parse import parse_qs

    return {key: values[0] for key, values in parse_qs(query).items() if values}


def extract_messages(payload: dict) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                text = message.get("text", {}).get("body")
                sender = message.get("from")
                if text and sender:
                    messages.append({"from": sender, "text": text})
    return messages


def send_whatsapp_message(to: str, text: str) -> None:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        print(f"[dry-run] To {to}: {text}")
        return

    url = f"https://graph.facebook.com/v20.0/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WhatsApp API error {exc.code}: {detail}") from exc


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), WhatsAppHandler)
    print(f"WhatsApp agent listening on http://localhost:{settings.port}")
    print("Webhook path: /webhook")
    if not settings.whatsapp_access_token:
        print("Running in dry-run mode because WHATSAPP_ACCESS_TOKEN is empty.")
    server.serve_forever()


def build_index_html() -> str:
    options = "\n".join(
        f'        <option>{name}</option>'
        for names in orderbot.routes.values()
        for name in names
    )
    return INDEX_HTML.replace("{{DISTRIBUTOR_OPTIONS}}", options)


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OrderBot Console</title>
  <style>
    :root { color-scheme: light; font-family: Arial, sans-serif; }
    body { margin: 0; background: #f7f7f4; color: #161616; }
    main { max-width: 980px; margin: 0 auto; padding: 28px; }
    h1 { margin: 0 0 6px; font-size: 30px; }
    p { color: #555; line-height: 1.45; }
    section { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 18px; margin-top: 16px; }
    label { display: block; font-weight: 700; margin: 10px 0 6px; }
    input, textarea, select { width: 100%; box-sizing: border-box; border: 1px solid #bbb; border-radius: 6px; padding: 10px; font: inherit; }
    textarea { min-height: 82px; resize: vertical; }
    button { margin-top: 12px; border: 0; border-radius: 6px; padding: 10px 14px; background: #146c5d; color: white; font-weight: 700; cursor: pointer; }
    button:hover { background: #0f574b; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .log { white-space: pre-wrap; background: #111; color: #f2f2f2; border-radius: 8px; padding: 14px; min-height: 180px; }
    @media (max-width: 760px) { .grid { grid-template-columns: 1fr; } main { padding: 18px; } }
  </style>
</head>
<body>
<main>
  <h1>OrderBot Console</h1>
  <p>Simulate client messages, supplier replies, payment checks, and pickup coordination. The bot keeps each client state independently.</p>

  <section>
    <label for="phone">Client ID or phone</label>
    <input id="phone" value="client-1">
  </section>

  <div class="grid">
    <section>
      <h2>Client Message</h2>
      <label for="clientText">Incoming WhatsApp text</label>
      <textarea id="clientText">How much for Samsung Galaxy Buds?</textarea>
      <button onclick="sendClient()">Send client message</button>
    </section>

    <section>
      <h2>Distributor Reply</h2>
      <label for="distributor">Distributor</label>
      <select id="distributor">
{{DISTRIBUTOR_OPTIONS}}
      </select>
      <label for="distText">Supplier response</label>
      <textarea id="distText">cost 90 available 4 units</textarea>
      <button onclick="sendDistributor()">Send distributor reply</button>
    </section>

    <section>
      <h2>Payment</h2>
      <label for="amount">Amount received</label>
      <input id="amount" value="115">
      <button onclick="verifyPayment()">Verify payment</button>
    </section>

    <section>
      <h2>Pickup</h2>
      <label for="clientName">Client name</label>
      <input id="clientName" value="Jane">
      <label for="pickupTime">Pickup time</label>
      <input id="pickupTime" value="today 4 PM">
      <button onclick="coordinatePickup()">Coordinate pickup</button>
    </section>
  </div>

  <section>
    <h2>Conversation Log</h2>
    <div id="log" class="log">Ready.</div>
  </section>
</main>
<script>
const log = document.getElementById("log");
function append(title, data) {
  log.textContent += "\n\n" + title + "\n" + JSON.stringify(data, null, 2);
}
async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const data = await response.json();
  append(url, data);
}
function phone() { return document.getElementById("phone").value || "client-1"; }
function sendClient() {
  post("/simulate/client", { phone: phone(), text: document.getElementById("clientText").value });
}
function sendDistributor() {
  post("/simulate/distributor", {
    distributor: document.getElementById("distributor").value,
    text: document.getElementById("distText").value
  });
}
function verifyPayment() {
  post("/simulate/payment", { phone: phone(), amount: Number(document.getElementById("amount").value), received: true });
}
function coordinatePickup() {
  post("/simulate/pickup", {
    phone: phone(),
    name: document.getElementById("clientName").value,
    pickup_time: document.getElementById("pickupTime").value
  });
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
