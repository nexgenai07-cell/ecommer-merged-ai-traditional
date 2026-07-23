# PATH: apps/ai/consumers.py

import json
import html
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.agents.shopping_agent import run_shopping_agent
from apps.ai.models import ChatSession, ChatMessage
from apps.ai.customer_context import get_customer_context
from apps.ai.rate_limiting import check_all_rate_limits
from apps.ai.message_sanitization import validate_message, escape_for_storage, unescape_for_context, MessageValidationError
from apps.ai.ws_auth import extract_token_from_scope, is_token_expired_or_invalid
from apps.stores.models import Store

MAX_HISTORY_MESSAGES = 12


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.session_key = self.scope['url_route']['kwargs']['session_key']

        session_valid = await self.check_session_not_deleted()
        if not session_valid:
            await self.close(code=4404)
            return

        # Requirement 13 — token (optional; guest connections won't have one)
        self.token = extract_token_from_scope(self.scope)
        # Requirement 11 — best-effort client IP for guest rate limiting
        client = self.scope.get('client')
        self.client_ip = client[0] if client else None

        self.room_group_name = f"chat_{self.session_key}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            "type": "connected",
            "message": "WebSocket connected successfully",
            "session_key": self.session_key
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    @sync_to_async
    def check_session_not_deleted(self):
        session = ChatSession.objects.filter(session_key=self.session_key).first()
        return session is None or not session.is_deleted

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self.send(text_data=json.dumps({"type": "error", "message": "Invalid message format — expected JSON."}))
            return

        # Requirement 13 — expired/invalid token pe connection band
        if getattr(self, 'token', None) and is_token_expired_or_invalid(self.token):
            await self.send(text_data=json.dumps({
                "type": "error", "code": "SESSION_EXPIRED",
                "message": "Session expired, please log in again.",
            }))
            await self.close(code=4401)
            return

        # Requirement 11 — rate limiting
        user_id = await self.get_session_user_id()
        allowed = await sync_to_async(check_all_rate_limits)(self.session_key, user_id, getattr(self, 'client_ip', None))
        if not allowed:
            await self.send(text_data=json.dumps({
                "type": "error", "code": "RATE_LIMITED",
                "message": "Too many messages — please wait a moment before sending again.",
            }))
            return

        user_message = data.get("message", "")

        # Requirement 12 — validate before doing anything else
        try:
            validate_message(user_message)
        except MessageValidationError as e:
            await self.send(text_data=json.dumps({"type": "error", "message": str(e)}))
            return

        try:
            response_text, products_metadata = await self.get_agent_response(user_message)
        except Exception as e:
            response_text, products_metadata = f"Sorry, something went wrong: {str(e)}", []

        await self.send(text_data=json.dumps({
            "type": "message",
            "sender": "ai",
            "message": response_text,
            "metadata": {"products": products_metadata} if products_metadata else None,
        }))

    @sync_to_async
    def get_session_user_id(self):
        session = ChatSession.objects.filter(session_key=self.session_key).first()
        return session.user_id if session else None

    @sync_to_async
    def get_agent_response(self, user_message):
        chat_session, _ = ChatSession.objects.get_or_create(
            session_key=self.session_key,
            defaults={'store': Store.objects.first(), 'channel': 'customer'},
        )
        user = chat_session.user

        # Requirement 12 — escaped version DB mein save hota hai
        ChatMessage.objects.create(session=chat_session, sender='user', message=escape_for_storage(user_message))

        if user is not None:
            messages_qs = ChatMessage.objects.filter(session__user=user, session__channel='customer').select_related('session').order_by('-created_at')
        else:
            messages_qs = chat_session.messages.order_by('-created_at')

        previous_messages = list(messages_qs[1:MAX_HISTORY_MESSAGES + 1])
        previous_messages.reverse()

        chat_history = []
        for msg in previous_messages:
            # Requirement 12 — unescape karke LLM ko context dena, taake escaped entities pollute na karein
            text = unescape_for_context(msg.message)
            if msg.sender == 'user':
                chat_history.append(HumanMessage(content=text))
            else:
                chat_history.append(AIMessage(content=text))

        customer_context = get_customer_context(user)

        output, products_metadata = run_shopping_agent(
            user_message,  # LLM is turn ka raw (validated) message dekhta hai
            session_key=self.session_key,
            user=user,
            chat_history=chat_history,
            customer_context=customer_context,
        )

        if isinstance(output, list):
            output = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in output
            ).strip()

        ChatMessage.objects.create(
            session=chat_session, sender='ai', message=output,
            metadata={"products": products_metadata} if products_metadata else None,
        )

        return output, products_metadata