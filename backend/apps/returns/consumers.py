# PATH: apps/returns/consumers.py
#
# NEW (Sep 2026 — Complaint chat live updates via WebSockets).
#
# Reuses the SAME infra apps/ai/consumers.py (ChatConsumer) and
# apps/ai/admin_consumers.py (AdminChatConsumer) already run on —
# same Redis-backed CHANNEL_LAYERS, same daphne ASGI server, same
# ?token=<access_token> query-string auth pattern (apps/ai/ws_auth.py).
# No new app, no new settings/CHANNEL_LAYERS/asgi wiring beyond adding
# this route to core/asgi.py.
#
# One group per complaint ("complaint_<id>") — the customer who filed it
# and whichever admin is handling it both join that group when they open
# the complaint's chat, so a message either side sends is pushed to the
# other instantly. No polling, no manual refresh, no reopening the tab.
#
# GET /api/v1/complaints/{id}/messages/ (ComplaintMessageView in
# apps/orders/complaint_views.py) is UNCHANGED and still used to load
# the existing thread history when the chat screen first opens — the
# socket only carries NEW messages from that point on. POST on that
# same REST endpoint still works too (fallback for any client not using
# the socket) and now also broadcasts into this same group via
# broadcast_complaint_message() below, so both paths stay in sync.

import logging

from asgiref.sync import async_to_sync, sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model

from apps.ai.ws_auth import extract_token_from_scope, get_user_from_token
from apps.notifications.utils import create_notification

from .models import Complaint, ComplaintMessage

logger = logging.getLogger(__name__)

User = get_user_model()


def complaint_group_name(complaint_id):
    return f"complaint_{complaint_id}"


def broadcast_complaint_message(complaint_id, payload):
    """
    Pushes a complaint-message payload to every WebSocket client
    currently connected to ws/complaints/{complaint_id}/.

    Called from ComplaintMessageView.post() in
    apps/orders/complaint_views.py — the plain REST reply path — so a
    reply still reaches the other side live even from a client that
    isn't using the socket to send. (A message sent OVER the socket
    already gets broadcast directly inside receive_json below; this is
    only for the REST fallback.)

    Safe no-op if the channel layer isn't configured — never raises,
    since a live-update push must not be able to break the REST reply.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        complaint_group_name(complaint_id),
        {"type": "complaint.message", "payload": payload},
    )


class ComplaintConsumer(AsyncJsonWebsocketConsumer):
    """
    ws://.../ws/complaints/{complaint_id}/?token=<access_token>

    Client -> server: {"message": "some reply text"}
    Server -> client (broadcast to both sides):
        {
            "id": 123,
            "complaint": 39,
            "sender": 7,
            "sender_name": "Test Admin 2",
            "sender_role": "admin",
            "message": "some reply text",
            "created_at": "2026-09-15T10:17:00Z"
        }

    Access rules mirror ComplaintMessageView._get_complaint_for_user in
    apps/orders/complaint_views.py: a customer can only join their OWN
    complaint's room; an admin can join any complaint's room.
    """

    async def connect(self):
        self.complaint_id = self.scope["url_route"]["kwargs"]["complaint_id"]
        self.group_name = None

        token = extract_token_from_scope(self.scope)
        self.user = await sync_to_async(get_user_from_token)(token)

        if self.user is None:
            await self.close(code=4401)  # missing/invalid/expired token
            return

        self.complaint = await self._get_authorized_complaint()
        if self.complaint is None:
            await self.close(code=4403)  # not found / not this user's complaint
            return

        self.group_name = complaint_group_name(self.complaint_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Handles an incoming {"message": "..."} frame from the client.
    async def receive_json(self, content, **kwargs):
        message = content.get("message")

        if not isinstance(message, str) or not message.strip():
            await self.send_json({"error": "message is required."})
            return

        payload = await self._create_message_and_notify(message.strip())

        await self.channel_layer.group_send(
            self.group_name,
            {"type": "complaint.message", "payload": payload},
        )

    # Channel-layer event handler — "type": "complaint.message" maps to
    # this method name (dots become underscores). Delivers to each
    # connected client, whether group_send was called from here or from
    # broadcast_complaint_message() (the REST fallback path) above.
    async def complaint_message(self, event):
        await self.send_json(event["payload"])

    @sync_to_async
    def _get_authorized_complaint(self):
        try:
            complaint = Complaint.objects.select_related(
                "customer__user",
                "customer__store",
                "order__store",
                "resolved_by",
            ).get(id=self.complaint_id)
        except Complaint.DoesNotExist:
            return None

        is_admin = self.user.role == "admin"
        if not is_admin and complaint.customer.user_id != self.user.id:
            return None

        return complaint

    @sync_to_async
    def _create_message_and_notify(self, message_text):
        # Same recipient-resolution + notification logic as
        # ComplaintMessageView.post() in apps/orders/complaint_views.py,
        # kept in sync deliberately so a reply notifies the right person
        # whether it came in over the socket or the plain REST endpoint.
        complaint = self.complaint
        sender_is_admin = self.user.role == "admin"

        if sender_is_admin:
            recipient = complaint.customer.user
        else:
            recipient = (
                complaint.resolved_by
                or User.objects.filter(role="admin", is_active=True).first()
            )

        complaint_message = ComplaintMessage.objects.create(
            complaint=complaint,
            sender=self.user,
            message=message_text,
        )

        if recipient is not None:
            create_notification(
                user=recipient,
                store=(
                    complaint.order.store
                    if complaint.order
                    else complaint.customer.store
                ),
                title=f"New reply on complaint #{complaint.id}",
                message=f"There is a new reply on your complaint #{complaint.id}.",
                notification_type="system",
                reference_type="complaint",
                reference_id=complaint.id,
            )

        return {
            "id": complaint_message.id,
            "complaint": complaint.id,
            "sender": complaint_message.sender_id,
            "sender_name": complaint_message.sender.name,
            "sender_role": "admin" if sender_is_admin else "customer",
            "message": complaint_message.message,
            "created_at": complaint_message.created_at.isoformat(),
        }