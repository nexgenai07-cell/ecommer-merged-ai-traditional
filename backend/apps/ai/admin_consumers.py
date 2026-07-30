# PATH: apps/ai/admin_consumers.py

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.models import ChatSession, ChatMessage
from apps.ai.admin_agents.admin_agent import run_admin_agent
from apps.ai.rate_limiting import check_all_rate_limits
from apps.ai.message_sanitization import validate_message, escape_for_storage, unescape_for_context, MessageValidationError
from apps.ai.ws_auth import extract_token_from_scope, is_token_expired_or_invalid

MAX_HISTORY_MESSAGES = 12

logger = logging.getLogger(__name__)
FRIENDLY_ERROR_MESSAGE = "Sorry, kuch masla ho gaya hai. Please thodi dair baad dobara koshish karein."


class AdminChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        print("[WS-DEBUG] ===== admin connect() CALLED =====", flush=True)

        self.session_key = self.scope['url_route']['kwargs']['session_key']
        print(f"[WS-DEBUG] session_key from URL = {self.session_key}", flush=True)

        try:
            session = await self.get_session_debug_info()
        except Exception:
            print("[WS-DEBUG] ❌ CRASHED inside check_admin_session() DB query", flush=True)
            logger.exception("[WS-DEBUG] admin session lookup failed for session_key=%s", self.session_key)
            await self.close(code=4500)
            return

        if session is None:
            print("[WS-DEBUG] ❌ REJECTED — no ChatSession row exists for this session_key at all", flush=True)
            await self.close(code=4403)
            return

        print(f"[WS-DEBUG] session found -> channel={session['channel']}  is_deleted={session['is_deleted']}  "
              f"user_id={session['user_id']}  user_role={session['user_role']}", flush=True)

        if session['is_deleted']:
            print("[WS-DEBUG] ❌ REJECTED — session.is_deleted=True", flush=True)
            await self.close(code=4403)
            return

        if session['user_id'] is None:
            print("[WS-DEBUG] ❌ REJECTED — session.user is None (session was created WITHOUT an authenticated "
                  "user — check if the 'start session' request sent the admin's Authorization/JWT header)", flush=True)
            await self.close(code=4403)
            return

        if session['user_role'] != 'admin':
            print(f"[WS-DEBUG] ❌ REJECTED — session.user.role = '{session['user_role']}' (expected 'admin')", flush=True)
            await self.close(code=4403)
            return

        print("[WS-DEBUG] ✅ check_admin_session passed", flush=True)

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

        self.room_group_name = f"admin_chat_{self.session_key}"
        try:
            print(f"[WS-DEBUG] calling channel_layer.group_add('{self.room_group_name}') ...", flush=True)
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
            print("[WS-DEBUG] ✅ group_add() succeeded", flush=True)
        except Exception:
            print("[WS-DEBUG] ❌ CRASHED inside channel_layer.group_add() — most likely Redis/channel-layer issue", flush=True)
            logger.exception("[WS-DEBUG] group_add failed for room=%s", self.room_group_name)
            await self.close(code=4500)
            return

        try:
            await self.accept()
            print("[WS-DEBUG] ✅ accept() succeeded — admin handshake complete", flush=True)
        except Exception:
            print("[WS-DEBUG] ❌ CRASHED inside accept()", flush=True)
            logger.exception("[WS-DEBUG] accept() failed for session_key=%s", self.session_key)
            return

        await self.send(text_data=json.dumps({
            "type": "connected",
            "message": "Admin WebSocket connected successfully",
            "session_key": self.session_key,
            "suggestions": [
                "View products", "Check inventory",
                "View sales report", "Check low stock",
            ],
        }))
        print("[WS-DEBUG] ✅ 'connected' message sent to admin — connect() finished successfully", flush=True)

    async def disconnect(self, close_code):
        print(f"[WS-DEBUG] ===== admin disconnect() CALLED — close_code={close_code} =====", flush=True)
        if hasattr(self, 'room_group_name'):
            try:
                await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
            except Exception:
                print("[WS-DEBUG] ❌ CRASHED inside group_discard() during disconnect", flush=True)
                logger.exception("[WS-DEBUG] group_discard failed during disconnect")

    @sync_to_async
    def get_session_debug_info(self):
        # DEBUG — check_admin_session() ki jagah, taake exact wajah pata chale
        # (session missing? user None? role galat?) — sirf True/False nahi.
        session = ChatSession.objects.select_related('user').filter(session_key=self.session_key).first()
        if session is None:
            return None
        return {
            'channel': session.channel,
            'is_deleted': session.is_deleted,
            'user_id': session.user_id,
            'user_role': getattr(session.user, 'role', None) if session.user else None,
        }

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
            response_text, metadata, suggestions = await self.get_agent_response(user_message)
        except Exception:
            logger.exception("AdminChatConsumer.get_agent_response failed for session_key=%s", self.session_key)
            response_text, metadata, suggestions = FRIENDLY_ERROR_MESSAGE, None, []

        requires_confirmation = bool(metadata and metadata.get('pending_action'))

        await self.send(text_data=json.dumps({
            "type": "message", "sender": "ai", "message": response_text,
            "requires_confirmation": requires_confirmation,
            "metadata": metadata,
            "suggestions": suggestions,
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

        output, metadata, suggestions = run_admin_agent(user_message, session_key=self.session_key, user=user, chat_history=chat_history)

        if isinstance(output, list):
            output = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in output).strip()

        ChatMessage.objects.create(session=chat_session, sender='ai', message=output, metadata=metadata)

        return output, metadata, suggestions