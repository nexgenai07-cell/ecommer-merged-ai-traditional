# PATH: apps/ai/admin_consumers.py

# FLOW: apps/ai/admin_routing.py se yahan jump hota hai. Customer wale
# consumers.py jaisa hi structure hai, FARQ: connect() mein role check
# hota hai, aur ek hi merged run_admin_agent() call hoti hai (customer
# side pe alag Shopping Agent hai, yahan alag Admin Agent hai).

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.models import ChatSession, ChatMessage
from apps.ai.admin_agents.admin_agent import run_admin_agent  # FLOW → apps/ai/admin_agents/admin_agent.py

MAX_HISTORY_MESSAGES = 12


class AdminChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.session_key = self.scope['url_route']['kwargs']['session_key']

        # FLOW: Role dobara yahan check hota hai (defense-in-depth) —
        # StartAdminChatSessionView mein already check ho chuka hai,
        # lekin yahan bhi confirm karte hain koi bypass na kar sake
        is_authorized = await self.check_admin_session()
        if not is_authorized:
            await self.close(code=4403)     # FLOW: yahan connection reject ho jata hai
            return

        self.room_group_name = f"admin_chat_{self.session_key}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            "type": "connected",
            "message": "Admin WebSocket connected successfully",
            "session_key": self.session_key,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    @sync_to_async
    def check_admin_session(self):
        session = ChatSession.objects.select_related('user').filter(session_key=self.session_key).first()
        if session is None:
            return False
        if session.is_deleted:   # NEW — Requirement 2
            return False
        return session is not None and session.user is not None and getattr(session.user, 'role', None) == 'admin'

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self.send(text_data=json.dumps({
                "type": "error",
                "message": "Invalid message format — expected JSON.",
            }))
            return
        user_message = data.get("message", "")

        try:
            response_text, metadata = await self.get_agent_response(user_message)
        except Exception as e:
            response_text, metadata = f"Sorry, something went wrong: {str(e)}", None

        await self.send(text_data=json.dumps({
            "type": "message",
            "sender": "ai",
            "message": response_text,
            "metadata": metadata,
        }))

    @sync_to_async
    def get_agent_response(self, user_message):
        chat_session = ChatSession.objects.select_related('user').get(session_key=self.session_key)
        user = chat_session.user

        # Save standard incoming user message
        ChatMessage.objects.create(session=chat_session, sender='user', message=user_message)

        # FIX: Admin hamesha logged-in hota hai, isliye customer side ke
        # logged-in pattern jaisa hi cross-session memory honi chahiye —
        # sirf isi session_key tak mehdood nahi.
        previous_messages = list(
            ChatMessage.objects.filter(session__user=user, session__channel='admin')   # NEW — sirf admin-channel history)
            .select_related('session')
            .order_by('-created_at')[1:MAX_HISTORY_MESSAGES + 1]
        )
        previous_messages.reverse()

        chat_history = []
        for msg in previous_messages:
            if msg.sender == 'user':
                chat_history.append(HumanMessage(content=msg.message))
            else:
                chat_history.append(AIMessage(content=msg.message))

        # FLOW → apps/ai/admin_agents/admin_agent.py — YAHAN SE ASAL AI KAAM SHURU HOTA HAI
        output, metadata = run_admin_agent(user_message, session_key=self.session_key, user=user, chat_history=chat_history)

        if isinstance(output, list):
            output = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in output
            ).strip()

        # Save AI generation response along with its metadata
        ChatMessage.objects.create(session=chat_session, sender='ai', message=output, metadata=metadata)

        return output, metadata