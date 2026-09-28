# PATH: apps/notifications/live_consumers.py
#
# FLOW: core/asgi.py -> apps/notifications/live_routing.py -> yahan.
# Ek hi WebSocket connection har page pe: ws(s)://<backend>/ws/live/?token=<jwt>
#
#   token nahi diya      -> anonymous visitor: sirf public product updates
#   token sahi           -> + us user ke apne orders/payments/notifications
#   token sahi + admin   -> + apne stores ke naye orders aur order/payment changes
#   token diya magar galat/expired -> connection accept karke close code 4401
#                          (frontend ko pata chal jaye ke token refresh karke dobara connect kare)

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.ai.ws_auth import extract_token_from_scope, get_user_from_token
from .live_events import PUBLIC_GROUP, user_group, admin_store_group


@database_sync_to_async
def _admin_store_ids(user):
    if getattr(user, "role", None) != "admin":
        return []
    return list(user.administered_stores.values_list("id", flat=True))


class LiveConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.joined_groups = []
        self.live_user = None

        token = extract_token_from_scope(self.scope)
        if token:
            user = await database_sync_to_async(get_user_from_token)(token)
            if user is None:
                await self.accept()
                await self.close(code=4401)
                return
            self.live_user = user

        groups = [PUBLIC_GROUP]
        if self.live_user is not None:
            groups.append(user_group(self.live_user.id))
            for store_id in await _admin_store_ids(self.live_user):
                groups.append(admin_store_group(store_id))

        for group in groups:
            await self.channel_layer.group_add(group, self.channel_name)
        self.joined_groups = groups

        await self.accept()

    async def disconnect(self, code):
        for group in getattr(self, "joined_groups", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Keep-alive: proxies/Nginx idle connection kaat dete hain, frontend
        # har ~25s "ping" bhejta hai.
        if text_data == "ping":
            await self.send(text_data="pong")

    # live_events.push() ka {"type": "live_event"} yahan aata hai
    async def live_event(self, message):
        await self.send(text_data=json.dumps({
            "event": message["event"],
            "data": message["data"],
        }))
