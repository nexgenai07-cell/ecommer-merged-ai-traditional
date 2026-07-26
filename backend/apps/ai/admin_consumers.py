# PATH: apps/ai/admin_consumers.py

import json
import logging  # NEW — server-side error logging ke liye
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.models import ChatSession, ChatMessage
from apps.ai.admin_agents.admin_agent import run_admin_agent
from apps.ai.rate_limiting import check_all_rate_limits
from apps.ai.message_sanitization import validate_message, escape_for_storage, unescape_for_context, MessageValidationError
from apps.ai.ws_auth import extract_token_from_scope, is_token_expired_or_invalid

MAX_HISTORY_MESSAGES = 12

# NEW — FIX: raw Python exceptions (jaise "create_pending_action() missing
# 1 required positional argument") pehle seedha admin ko chat message mein
# dikh rahe thay. Ab hum ek logger banate hain (asal error server logs
# mein jayega) aur admin ko hamesha ek generic, friendly message dete
# hain — kabhi bhi str(e) seedha frontend ko nahi bhejte.
logger = logging.getLogger(__name__)
FRIENDLY_ERROR_MESSAGE = "Sorry, kuch masla ho gaya hai. Please thodi dair baad dobara koshish karein."


class AdminChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.session_key = self.scope['url_route']['kwargs']['session_key']

        is_authorized = await self.check_admin_session()
        if not is_authorized:
            await self.close(code=4403)
            return

        self.token = extract_token_from_scope(self.scope)
        client = self.scope.get('client')
        self.client_ip = client[0] if client else None

        self.room_group_name = f"admin_chat_{self.session_key}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            "type": "connected",
            "message": "Admin WebSocket connected successfully",
            "session_key": self.session_key,
            "suggestions": [
                "View products", "Check inventory",
                "View sales report", "Check low stock",
            ],
        }))
        

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    @sync_to_async
    def check_admin_session(self):
        session = ChatSession.objects.select_related('user').filter(session_key=self.session_key).first()
        if session is None or session.is_deleted:
            return False
        return session.user is not None and getattr(session.user, 'role', None) == 'admin'

    # NEW — FIX: group-event handler for messages pushed from OUTSIDE this
    # consumer (e.g. apps/ai/admin_action_views.py ConfirmAdminActionView,
    # which runs as a plain synchronous DRF request — a completely separate
    # request/response cycle with no open `receive()` to reply through).
    # Until now, group_add() added this connection to "admin_chat_<session_key>"
    # but nothing ever called channel_layer.group_send() on that group, and
    # no handler existed to receive such an event even if something did — so
    # confirming via the REST button executed the action but the chat UI
    # never found out. Channels routes a group_send event to a method named
    # after its "type" key with dots turned into underscores, so a payload
    # sent with type="chat.message" arrives here.
    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self.send(text_data=json.dumps({"type": "error", "message": "Invalid message format — expected JSON."}))
            return

        if getattr(self, 'token', None) and is_token_expired_or_invalid(self.token):
            await self.send(text_data=json.dumps({
                "type": "error", "code": "SESSION_EXPIRED",
                "message": "Session expired, please log in again.",
            }))
            await self.close(code=4401)
            return

        user_id = await self.get_session_user_id()
        allowed = await sync_to_async(check_all_rate_limits)(self.session_key, user_id, getattr(self, 'client_ip', None))
        if not allowed:
            await self.send(text_data=json.dumps({
                "type": "error", "code": "RATE_LIMITED",
                "message": "Too many requests, please try again later.",
            }))
            return

        user_message = data.get("message", "")

        try:
            validate_message(user_message)
        except MessageValidationError as e:
            await self.send(text_data=json.dumps({"type": "error", "message": str(e)}))
            return

        try:
            # UPDATED: Receive 3 items now
            response_text, metadata, suggestions = await self.get_agent_response(user_message)
        except Exception:
            # FIX — pehle yahan "f'Sorry, something went wrong: {str(e)}'"
            # bheja jata tha, jo raw Python error (jaise TypeError ka
            # message: "create_pending_action() missing 1 required
            # positional argument: 'preview'") seedha admin ko dikha deta
            # tha. Ab: asal error logger.exception() se server logs mein
            # jata hai, admin ko hamesha generic friendly message milta hai.
            logger.exception("AdminChatConsumer.get_agent_response failed for session_key=%s", self.session_key)
            response_text, metadata, suggestions = FRIENDLY_ERROR_MESSAGE, None, []

        requires_confirmation = bool(metadata and metadata.get('pending_action'))

        await self.send(text_data=json.dumps({
            "type": "message", "sender": "ai", "message": response_text,
            "requires_confirmation": requires_confirmation,
            "metadata": metadata,
            "suggestions": suggestions,   # UPDATED: Sending suggestions to frontend WebSocket
        }))

    @sync_to_async
    def get_session_user_id(self):
        session = ChatSession.objects.filter(session_key=self.session_key).first()
        return session.user_id if session else None

    @sync_to_async
    def get_agent_response(self, user_message):
        chat_session = ChatSession.objects.select_related('user').get(session_key=self.session_key)
        user = chat_session.user

        ChatMessage.objects.create(session=chat_session, sender='user', message=escape_for_storage(user_message))

        previous_messages = list(
            ChatMessage.objects.filter(session__user=user, session__channel='admin')
            .select_related('session')
            .order_by('-created_at')[1:MAX_HISTORY_MESSAGES + 1]
        )
        previous_messages.reverse()

        chat_history = []
        for msg in previous_messages:
            text = unescape_for_context(msg.message)
            if msg.sender == 'user':
                chat_history.append(HumanMessage(content=text))
            else:
                chat_history.append(AIMessage(content=text))

        # UPDATED: Unpack 3-tuple returned from run_admin_agent
        output, metadata, suggestions = run_admin_agent(user_message, session_key=self.session_key, user=user, chat_history=chat_history)

        if isinstance(output, list):
            output = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in output).strip()

        ChatMessage.objects.create(session=chat_session, sender='ai', message=output, metadata=metadata)
        
        # UPDATED: Return 3 values
        return output, metadata, suggestions