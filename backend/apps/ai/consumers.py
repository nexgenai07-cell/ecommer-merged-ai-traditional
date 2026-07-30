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

logger = logging.getLogger(__name__)
FRIENDLY_ERROR_MESSAGE = "Sorry, kuch masla ho gaya hai. Please thodi dair baad dobara koshish karein."


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        # DEBUG — print(..., flush=True) taake Daphne terminal mein FORAN dikhe,
        # chahe logging config kuch bhi ho.
        print("[WS-DEBUG] ===== customer connect() CALLED =====", flush=True)

        self.session_key = self.scope['url_route']['kwargs']['session_key']
        print(f"[WS-DEBUG] session_key from URL = {self.session_key}", flush=True)

        try:
            session_valid = await self.check_session_not_deleted()
            print(f"[WS-DEBUG] check_session_not_deleted() -> {session_valid}", flush=True)
        except Exception:
            print("[WS-DEBUG] ❌ CRASHED inside check_session_not_deleted() — see traceback below", flush=True)
            logger.exception("[WS-DEBUG] check_session_not_deleted failed for session_key=%s", self.session_key)
            await self.close(code=4500)
            return

        if not session_valid:
            print("[WS-DEBUG] ❌ REJECTED — session not found or is_deleted=True (close code 4404)", flush=True)
            await self.close(code=4404)
            return

        try:
            self.token = extract_token_from_scope(self.scope)
            print(f"[WS-DEBUG] token from query string = {'<present>' if self.token else None}", flush=True)
        except Exception:
            print("[WS-DEBUG] ❌ CRASHED inside extract_token_from_scope()", flush=True)
            logger.exception("[WS-DEBUG] extract_token_from_scope failed")
            await self.close(code=4500)
            return

        client = self.scope.get('client')
        self.client_ip = client[0] if client else None
        print(f"[WS-DEBUG] client_ip = {self.client_ip}", flush=True)

        self.room_group_name = f"chat_{self.session_key}"
        try:
            print(f"[WS-DEBUG] calling channel_layer.group_add('{self.room_group_name}') ...", flush=True)
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
            print("[WS-DEBUG] ✅ group_add() succeeded", flush=True)
        except Exception:
            # Ye wahi jagah hai jahan Redis/Upstash timeout crash hota tha
            print("[WS-DEBUG] ❌ CRASHED inside channel_layer.group_add() — most likely Redis/channel-layer issue", flush=True)
            logger.exception("[WS-DEBUG] group_add failed for room=%s", self.room_group_name)
            await self.close(code=4500)
            return

        try:
            await self.accept()
            print("[WS-DEBUG] ✅ accept() succeeded — handshake complete", flush=True)
        except Exception:
            print("[WS-DEBUG] ❌ CRASHED inside accept()", flush=True)
            logger.exception("[WS-DEBUG] accept() failed for session_key=%s", self.session_key)
            return

        try:
            session_user = await self.get_session_user()
            print(f"[WS-DEBUG] get_session_user() -> {session_user}", flush=True)
            initial_suggestions = await sync_to_async(get_initial_suggestions)(session_user)
            print(f"[WS-DEBUG] get_initial_suggestions() -> {initial_suggestions}", flush=True)
        except Exception:
            print("[WS-DEBUG] ❌ CRASHED after accept() while preparing initial data — this silently killed the socket before", flush=True)
            logger.exception("[WS-DEBUG] post-accept setup failed for session_key=%s", self.session_key)
            try:
                await self.send(text_data=json.dumps({"type": "error", "message": FRIENDLY_ERROR_MESSAGE}))
            except Exception:
                pass
            await self.close(code=4500)
            return

        await self.send(text_data=json.dumps({
            "type": "connected",
            "message": "WebSocket connected successfully",
            "session_key": self.session_key,
            "suggestions": initial_suggestions,
        }))
        print("[WS-DEBUG] ✅ 'connected' message sent to client — connect() finished successfully", flush=True)

        self.last_activity = time.monotonic()
        self.idle_already_sent = False
        self.page_context = None
        self.idle_task = asyncio.create_task(self._idle_watcher())

    async def disconnect(self, close_code):
        # DEBUG — ye batayega ke disconnect kis close_code ke sath hua
        print(f"[WS-DEBUG] ===== customer disconnect() CALLED — close_code={close_code} =====", flush=True)
        if hasattr(self, 'room_group_name'):
            try:
                await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
            except Exception:
                print("[WS-DEBUG] ❌ CRASHED inside group_discard() during disconnect", flush=True)
                logger.exception("[WS-DEBUG] group_discard failed during disconnect")
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
        attachment_image = None
        if attachment_file_id:
            from apps.ai.models import ChatUpload
            upload = ChatUpload.objects.filter(id=attachment_file_id).first()
            if upload:
                user_msg_metadata = {'attachment_url': upload.file.url}
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
            image=attachment_image,
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