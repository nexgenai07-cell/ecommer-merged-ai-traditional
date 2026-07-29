# PATH: apps/ai/consumers.py

import json
import logging  # NEW — server-side error logging ke liye
import time
import asyncio
import base64
import mimetypes
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.agents.shopping_agent import run_shopping_agent
from apps.ai.models import ChatSession, ChatMessage
from apps.ai.customer_context import get_customer_context
from apps.ai.rate_limiting import check_all_rate_limits
from apps.ai.message_sanitization import validate_message, escape_for_storage, unescape_for_context, MessageValidationError
from apps.ai.ws_auth import extract_token_from_scope, is_token_expired_or_invalid
from apps.ai.suggestions import get_initial_suggestions
from apps.stores.models import Store

MAX_HISTORY_MESSAGES = 12
IDLE_THRESHOLD_SECONDS = 30

# NEW — FIX: raw Python exceptions (jaise "create_pending_action() missing
# 1 required positional argument") pehle seedha customer ko chat message
# mein dikh rahe thay. Ab hum ek logger banate hain (asal error server
# logs mein jayega) aur user ko hamesha ek generic, friendly message dete
# hain — kabhi bhi str(e) seedha frontend ko nahi bhejte.
logger = logging.getLogger(__name__)
FRIENDLY_ERROR_MESSAGE = "Sorry, kuch masla ho gaya hai. Please thodi dair baad dobara koshish karein."


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.session_key = self.scope['url_route']['kwargs']['session_key']

        session_valid = await self.check_session_not_deleted()
        if not session_valid:
            await self.close(code=4404)
            return

        self.token = extract_token_from_scope(self.scope)
        client = self.scope.get('client')
        self.client_ip = client[0] if client else None

        self.room_group_name = f"chat_{self.session_key}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        session_user = await self.get_session_user()
        initial_suggestions = await sync_to_async(get_initial_suggestions)(session_user)

        await self.send(text_data=json.dumps({
            "type": "connected",
            "message": "WebSocket connected successfully",
            "session_key": self.session_key,
            "suggestions": initial_suggestions,
        }))

        # NEW — Requirement 10: idle-detection state + background watcher.
        # last_activity resets har naye client message pe; idle_already_sent
        # bhi tab reset hota hai — taake "ek idle period mein sirf ek baar"
        # ka rule follow ho, lekin agli baar phir se idle hone par dobara fire ho sake.
        self.last_activity = time.monotonic()
        self.idle_already_sent = False
        self.page_context = None  # frontend agar "page_context" bheje to yahan store hoga
        self.idle_task = asyncio.create_task(self._idle_watcher())

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        # NEW — background task ko band karna zaroori hai, warna connection
        # band hone ke baad bhi ye chalta rahega
        if hasattr(self, 'idle_task'):
            self.idle_task.cancel()

    @sync_to_async
    def check_session_not_deleted(self):
        session = ChatSession.objects.filter(session_key=self.session_key).first()
        return session is None or not session.is_deleted

    @sync_to_async
    def get_session_user(self):
        session = ChatSession.objects.select_related('user').filter(session_key=self.session_key).first()
        return session.user if session else None

    async def _idle_watcher(self):
        """
        NEW — Requirement 10. Har second check karta hai: agar last message
        se 30 second guzar chuke hain AUR is idle-period mein pehle se
        proactive message nahi bheja gaya, to ek proactive message bhejta
        hai — aur uske baad chup ho jata hai jab tak user dobara active na ho.
        """
        try:
            while True:
                await asyncio.sleep(1)
                elapsed = time.monotonic() - self.last_activity
                if elapsed >= IDLE_THRESHOLD_SECONDS and not self.idle_already_sent:
                    await self._send_proactive_message()
                    self.idle_already_sent = True
        except asyncio.CancelledError:
            pass

    async def _send_proactive_message(self):
        if self.page_context:
            message = f"Kuch aur dhoondna hai {self.page_context} ke ilawa?"
        else:
            message = "Kuch dhoondne mein madad chahiye?"

        await self.send(text_data=json.dumps({
            "type": "message",
            "sender": "ai",
            "message": message,
            "proactive": True,
            "metadata": None,
            "suggestions": ["Find a product", "Talk to support"],
        }))
        
        
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self.send(text_data=json.dumps({"type": "error", "message": "Invalid message format — expected JSON."}))
            return

        # NEW — Requirement 10: har naye message pe idle-timer reset
        self.last_activity = time.monotonic()
        self.idle_already_sent = False
        if data.get('page_context'):
            self.page_context = data['page_context']

        if getattr(self, 'token', None) and is_token_expired_or_invalid(self.token):
            await self.send(text_data=json.dumps({"type": "error", "code": "SESSION_EXPIRED", "message": "Session expired, please log in again."}))
            await self.close(code=4401)
            return

        user_id = await self.get_session_user_id()
        allowed = await sync_to_async(check_all_rate_limits)(self.session_key, user_id, getattr(self, 'client_ip', None))
        if not allowed:
            await self.send(text_data=json.dumps({"type": "error", "code": "RATE_LIMITED", "message": "Too many requests, please try again later."}))
            return

        user_message = data.get("message", "")
        attachment_file_id = (data.get("attachment") or {}).get("file_id")

        try:
            validate_message(user_message)
        except MessageValidationError as e:
            await self.send(text_data=json.dumps({"type": "error", "message": str(e)}))
            return

        try:
            response_text, products_metadata, suggestions = await self.get_agent_response(user_message, attachment_file_id)
        except Exception:
            # FIX — pehle yahan "f'Sorry, something went wrong: {str(e)}'"
            # bheja jata tha, jo raw Python error (jaise TypeError ka
            # message) seedha user ko dikha deta tha. Ab: asal error
            # logger.exception() se server logs mein jata hai (Railway
            # logs mein dekha ja sakta hai), aur user ko hamesha generic
            # friendly message milta hai.
            logger.exception("ChatConsumer.get_agent_response failed for session_key=%s", self.session_key)
            await self.send(text_data=json.dumps({"type": "error", "message": FRIENDLY_ERROR_MESSAGE}))
            return

        words = response_text.split(' ')
        CHUNK_SIZE_WORDS = 4
        chunks = [' '.join(words[i:i + CHUNK_SIZE_WORDS]) + (' ' if i + CHUNK_SIZE_WORDS < len(words) else '')
                  for i in range(0, len(words), CHUNK_SIZE_WORDS)]
        if not chunks:
            chunks = ['']

        for idx, chunk in enumerate(chunks):
            is_last = (idx == len(chunks) - 1)
            payload = {"type": "message_chunk", "sender": "ai", "chunk": chunk, "done": is_last}
            if is_last:
                payload["metadata"] = {"products": products_metadata} if products_metadata else None
                payload["suggestions"] = suggestions
            await self.send(text_data=json.dumps(payload))

    @sync_to_async
    def get_session_user_id(self):
        session = ChatSession.objects.filter(session_key=self.session_key).first()
        return session.user_id if session else None

    @sync_to_async
    def get_agent_response(self, user_message, attachment_file_id=None):
        chat_session, _ = ChatSession.objects.get_or_create(
            session_key=self.session_key,
            defaults={'store': Store.objects.first(), 'channel': 'customer'},
        )
        user = chat_session.user

        user_msg_metadata = None
        attachment_image = None  # NEW — FIX: ye actual image data hai jo ab agent ko jayega
        if attachment_file_id:
            from apps.ai.models import ChatUpload
            upload = ChatUpload.objects.filter(id=attachment_file_id).first()
            if upload:
                user_msg_metadata = {'attachment_url': upload.file.url}
                # NEW — FIX: pehle yahan sirf URL metadata mein save ho kar
                # reh jaata tha, file ka content kabhi read/use nahi hota
                # tha — is liye AI ko image kabhi milti hi nahi thi. Ab hum
                # actual bytes read karke base64 mein agent ko dete hain
                # (storage-agnostic — local disk ya S3 dono ke sath chalega,
                # publicly-accessible URL par depend nahi karta).
                try:
                    with upload.file.open('rb') as f:
                        image_bytes = f.read()
                    mime_type = mimetypes.guess_type(upload.file.name)[0] or 'image/jpeg'
                    attachment_image = {
                        'base64': base64.b64encode(image_bytes).decode('utf-8'),
                        'mime_type': mime_type,
                    }
                except Exception:
                    logger.exception(
                        "Failed to read chat attachment file_id=%s for session_key=%s",
                        attachment_file_id, self.session_key,
                    )

        ChatMessage.objects.create(
            session=chat_session, sender='user',
            message=escape_for_storage(user_message), metadata=user_msg_metadata,
        )

        if user is not None:
            messages_qs = ChatMessage.objects.filter(
                session__user=user, session__channel='customer'
            ).select_related('session').order_by('-created_at')
        else:
            messages_qs = chat_session.messages.order_by('-created_at')

        previous_messages = list(messages_qs[1:MAX_HISTORY_MESSAGES + 1])
        previous_messages.reverse()

        chat_history = []
        for msg in previous_messages:
            text = unescape_for_context(msg.message)
            if msg.sender == 'user':
                chat_history.append(HumanMessage(content=text))
            else:
                chat_history.append(AIMessage(content=text))

        customer_context = get_customer_context(user)

        output, products_metadata, suggestions = run_shopping_agent(
            user_message,
            session_key=self.session_key,
            user=user,
            chat_history=chat_history,
            customer_context=customer_context,
            image=attachment_image,   # NEW — FIX: ab image agent tak pohanchti hai
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

        return output, products_metadata, suggestions