# PATH: apps/ai/consumers.py

# FLOW: apps/ai/routing.py se yahan jump hota hai jab WebSocket connect
# hota hai. Ye poori conversation ki "control room" hai — har message
# yahan se guzarta hai.

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.agents.shopping_agent import run_shopping_agent    # FLOW → apps/ai/agents/shopping_agent.py
from apps.ai.models import ChatSession, ChatMessage
from apps.ai.customer_context import get_customer_context   # FLOW → apps/ai/customer_context.py
from apps.stores.models import Store

MAX_HISTORY_MESSAGES = 12


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):

        # FLOW: connection accept hoti hai, "connected" message wapis
        # bheja jata hai — frontend ko pata chal jata hai socket ready hai

        self.session_key = self.scope['url_route']['kwargs']['session_key']

        # NEW — Requirement 2: soft-deleted session pe connect na hone dein
        session_valid = await self.check_session_not_deleted()
        if not session_valid:
            await self.close(code=4404)
            return
        
        self.room_group_name = f"chat_{self.session_key}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            "type": "connected",
            "message": "WebSocket connected successfully",
            "session_key": self.session_key
        }))

    @sync_to_async
    def check_session_not_deleted(self):
        # Session abhi exist nahi karti (pehli baar connect ho rahi hai) -> allow (naya session banega)
        session = ChatSession.objects.filter(session_key=self.session_key).first()
        return session is None or not session.is_deleted

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):

        # FLOW: Frontend se message aate hi YE function chalta hai.
        # Yahan se get_agent_response() call hoti hai (neeche), jo
        # asal AI logic tak le jaati hai.

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
            response_text, products_metadata = await self.get_agent_response(user_message)
            # FLOW ↑: get_agent_response() se do cheezein wapis aati hain —
            # AI ka text jawab, aur products/categories ka structured data
        except Exception as e:
            response_text, products_metadata = f"Sorry, something went wrong: {str(e)}", []

            # FLOW: yahan se response WAPIS frontend ko WebSocket se jata hai — flow ka aakhri step

        # metadata' field mein product_id/category_id waale products
        # bheje jate hain, taake frontend UI cards render kar sake.
        await self.send(text_data=json.dumps({
            "type": "message",
            "sender": "ai",
            "message": response_text,
            "metadata": {"products": products_metadata} if products_metadata else None,
        }))

    @sync_to_async
    def get_agent_response(self, user_message):

        # FLOW: Ye function 3 kaam karta hai — (1) session/history DB se
        # fetch karta hai, (2) AI agent ko call karta hai (asal "sochne"
        # wala kaam), (3) result ko DB mein save karta hai.

        chat_session, _ = ChatSession.objects.select_related('user').get_or_create(
            session_key=self.session_key,
            defaults={'store': Store.objects.first()},
        )
        user = chat_session.user
        # User ka message pehle hi save kar dete hain

        ChatMessage.objects.create(session=chat_session, sender='user', message=user_message)
        # Pichli conversation history nikalte hain — AI ko context dene ke liye

        if user is not None:
            messages_qs = ChatMessage.objects.filter(
                session__user=user, session__channel='customer'   # NEW — sirf customer-channel history
            ).select_related('session').order_by('-created_at')
        else:
            messages_qs = chat_session.messages.order_by('-created_at')

        previous_messages = list(messages_qs[1:MAX_HISTORY_MESSAGES + 1])
        previous_messages.reverse()

        chat_history = []
        for msg in previous_messages:
            if msg.sender == 'user':
                chat_history.append(HumanMessage(content=msg.message))
            else:
                chat_history.append(AIMessage(content=msg.message))

                # FLOW → apps/ai/customer_context.py — purchase history summary nikalti hai

        customer_context = get_customer_context(user)

        # FLOW → apps/ai/agents/shopping_agent.py — YAHAN SE ASAL AI KAAM SHURU HOTA HAI

        output, products_metadata = run_shopping_agent(
            user_message,
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

            # FLOW: AI ka jawab bhi DB mein save hota hai — agli baar history mein aayega

        # metadata bhi ChatMessage mein save hoti hai (jaisa model
        # comment mein already documented tha: "product cards, order info...")
        ChatMessage.objects.create(
            session=chat_session,
            sender='ai',
            message=output,
            metadata={"products": products_metadata} if products_metadata else None,
        )

        return output, products_metadata