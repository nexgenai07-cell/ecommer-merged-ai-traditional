import json
import time
import websocket

# ⚠️ Apna Django Channels WS URL yahan replace karein
WS_URL = "ws://127.0.0.1:8000/ws/your-endpoint/"


def on_open(ws):
    print("⚡ Connected to Django WebSocket!")

    for i in range(1, 12):  # 11 messages bhejne ke liye
        payload = json.dumps({"type": "ping", "message": f"Test message {i}"})
        print(f"Sending message {i}...")
        ws.send(payload)
        time.sleep(0.05)  # Fast delay (< 10 seconds total)


def on_message(ws, message):
    data = json.loads(message)
    print("📩 Received:", data)

    if data.get("code") == "RATE_LIMITED":
        print("\n✅ TEST PASSED: 11th message par RATE_LIMITED error mil gaya!")


def on_close(ws, close_status_code, close_msg):
    print(f"❌ Connection Closed: {close_status_code} - {close_msg}")


def on_error(ws, error):
    print("Error:", error)


if __name__ == "__main__":
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_close=on_close,
        on_error=on_error,
    )
    ws.run_forever()