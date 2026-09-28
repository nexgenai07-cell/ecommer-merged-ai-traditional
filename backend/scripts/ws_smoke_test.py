"""
LOCAL SMOKE TEST — deploy se pehle live WebSocket connection check.

Setup (ek baar):
    pip install websockets

Terminal 1 (server):
    daphne -p 8000 core.asgi:application

Terminal 2 (is script ko chalao, project root se):
    # anonymous (sirf product/category events sunega):
    python scripts/ws_smoke_test.py

    # logged-in user ke saath (token Django shell se nikalo, neeche dekho):
    python scripts/ws_smoke_test.py --token <ACCESS_TOKEN>

Terminal 3 (Django shell) — token banane aur events trigger karne ke liye:
    python manage.py shell
    >>> from rest_framework_simplejwt.tokens import AccessToken
    >>> from django.contrib.auth import get_user_model
    >>> u = get_user_model().objects.get(email="customer@example.com")   # apna user
    >>> print(AccessToken.for_user(u))
    >>>
    >>> from apps.products.models import Product
    >>> p = Product.objects.first(); p.price = p.price + 1; p.save()     # -> "product_update" aana chahiye

Script har event ka naam aur data print karti hai. Ctrl+C se band.
Exit codes: 0 = connect + ping/pong OK, 1 = connect nahi hua, 2 = token reject hua (4401).
"""
import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    sys.exit("pip install websockets")


async def main(url, token, listen_seconds):
    full = f"{url}?token={token}" if token else url
    print(f"Connecting: {url}  ({'with token' if token else 'anonymous'})")
    try:
        async with websockets.connect(full) as ws:
            print("CONNECTED")
            await ws.send("ping")
            reply = await asyncio.wait_for(ws.recv(), timeout=5)
            if reply == "pong":
                print("PING/PONG OK  -> connection healthy")
            else:
                # koi event pehle aa gaya ho to bhi theek hai
                print("first message:", reply)
            print(f"Listening {listen_seconds}s for live events (ab Django shell/admin se kuch change karo)...\n")
            end = asyncio.get_event_loop().time() + listen_seconds
            while (left := end - asyncio.get_event_loop().time()) > 0:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=left)
                except asyncio.TimeoutError:
                    break
                if raw == "pong":
                    continue
                msg = json.loads(raw)
                print(f"EVENT  {msg['event']:<18} {json.dumps(msg['data'])[:160]}")
    except websockets.exceptions.ConnectionClosed as e:
        code = getattr(e.rcvd, "code", None)
        if code == 4401:
            print("SERVER ne token reject kiya (4401): token galat ya expire hai.")
            return 2
        print("Connection closed:", e)
        return 1
    except Exception as e:
        print(f"CONNECT FAILED: {type(e).__name__}: {e}")
        print("Check: server chal raha hai? URL/port sahi hai? REDIS_URL local mein set hai to Redis reachable hai?")
        return 1
    print("\nDone.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8000/ws/live/")
    ap.add_argument("--token", default=None)
    ap.add_argument("--seconds", type=int, default=120)
    a = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(a.url, a.token, a.seconds)))
    except KeyboardInterrupt:
        print("\nStopped.")
