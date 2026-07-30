# PATH: apps/ai/debug_middleware.py
#
# DEBUG ONLY — ye middleware har WebSocket connection ke around baithta hai
# aur agar connection ke andar KAHIN BHI (chahe consumer ke bahar, channel
# layer ke andar hi kyun na ho) koi unhandled exception aaye, to:
#   1. Poora traceback print karta hai
#   2. Connect hone se lekar crash hone tak kitne SECONDS guzre, wo bhi print karta hai
# Isse hume pata chalega ke crash exactly kitne second pe hota hai — agar ye
# hamesha ~5 sec pe fixed ho, to socket_timeout wala theory confirm ho jayegi.

import time
import traceback


class WebSocketDebugMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'websocket':
            return await self.inner(scope, receive, send)

        path = scope.get('path', '?')
        start_time = time.monotonic()
        print(f"[WS-DEBUG][middleware] connection OPENING for path={path}", flush=True)

        try:
            result = await self.inner(scope, receive, send)
            elapsed = time.monotonic() - start_time
            print(f"[WS-DEBUG][middleware] connection ENDED NORMALLY for path={path} after {elapsed:.2f}s", flush=True)
            return result
        except Exception as e:
            elapsed = time.monotonic() - start_time
            print(f"[WS-DEBUG][middleware] ❌ CRASHED for path={path} after EXACTLY {elapsed:.2f} seconds", flush=True)
            print(f"[WS-DEBUG][middleware] Exception type: {type(e).__name__} — {e}", flush=True)
            traceback.print_exc()
            raise