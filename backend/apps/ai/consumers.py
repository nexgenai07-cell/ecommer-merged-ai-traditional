# PATH: apps/ai/consumers.py

import json
import logging  # NEW — server-side error logging ke liye
import time
import asyncio
import base64
import mimetypes
from decimal import Decimal   # NEW — FIX: Decimal→JSON-safe conversion ke liye
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.core.cache import cache   # NEW — FIX: idle-nudge flag ko reconnects ke across persist karne ke liye
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.agents.shopping_agent import run_shopping_agent
from apps.ai.models import ChatSession, ChatMessage
from apps.ai.customer_context import get_customer_context
from apps.ai.rate_limiting import check_all_rate_limits
from apps.ai.message_sanitization import validate_message, escape_for_storage, unescape_for_context, MessageValidationError
from apps.ai.ws_auth import extract_token_from_scope, is_token_expired_or_invalid
from apps.ai.suggestions import get_initial_suggestions
from apps.stores.models import Store


def _json_safe(obj):
    """NEW — CRITICAL FIX: ChatMessage.metadata (Postgres JSONField) mein
    save karte waqt "Object of type Decimal is not JSON serializable"
    crash aata tha — kyunke Product model se seedha aane wali price/
    original_price jaisi fields Decimal type hoti hain, aur koi bhi tool
    (search/cart/trending/compare, ab ya future mein) inhe bina float()
    kiye wapis kar sakta hai. Ye poora crash silently pura AI response hi
    girva deta tha — customer ko sahi jawab ki jagah generic "kuch masla
    hua" error milta tha, chahe AI ne jawab sahi bana liya ho. Ab save
    karne se THEEK PEHLE, poore metadata dict/list ko recursively scan
    karke har Decimal ko float mein convert karte hain — chahe wo kisi
    bhi tool se, kisi bhi depth par kyun na aaya ho."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj

MAX_HISTORY_MESSAGES = 12
IDLE_THRESHOLD_SECONDS = 30
IDLE_NUDGE_TTL_SECONDS = 60 * 60 * 6   # NEW — 6 ghante, ek poori chat session ke liye kaafi

# NEW — FIX: raw Python exceptions (jaise "create_pending_action() missing
# 1 required positional argument") pehle seedha customer ko chat message
# mein dikh rahe thay. Ab hum ek logger banate hain (asal error server
# logs mein jayega) aur user ko hamesha ek generic, friendly message dete
# hain — kabhi bhi str(e) seedha frontend ko nahi bhejte.
logger = logging.getLogger(__name__)
FRIENDLY_ERROR_MESSAGE = "Sorry, kuch masla ho gaya hai. Please thodi dair baad dobara koshish karein."


def _idle_nudge_cache_key(session_key: str) -> str:   # NEW
    return f"chat_idle_nudge_sent:{session_key}"


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.session_key = self.scope['url_route']['kwargs']['session_key']

        # DEBUG — session_key mix-up track karne ke liye (customer bot)
        print(f"[CUSTOMER-BOT][CONNECT] session_key={self.session_key}")

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
        }, ensure_ascii=False))

        # NEW — Requirement 10: idle-detection state + background watcher.
        # last_activity resets har naye client message pe; idle_already_sent
        # bhi tab reset hota hai — taake "ek idle period mein sirf ek baar"
        # ka rule follow ho, lekin agli baar phir se idle hone par dobara fire ho sake.
        self.last_activity = time.monotonic()
        # NEW — FIX: pehle idle_already_sent sirf is CONNECTION ke andar
        # track hota tha (hamesha False se start) — is liye agar WebSocket
        # kisi bhi wajah se reconnect ho jaye (network blip, Railway proxy
        # timeout, frontend tab-switch/re-render), naya ChatConsumer
        # instance banta tha aur idle-nudge DOBARA fire ho jata tha — isi
        # se customer ko "kuch dhundne mein madad chahiye" wala msg
        # baar-baar milta tha. Ab Redis mein session_key ke against
        # persist karte hain — taake ye poori chat session mein SIRF EK
        # BAAR aaye, chahe WebSocket beech mein kitni bhi baar reconnect ho.
        self.idle_already_sent = bool(await sync_to_async(cache.get)(_idle_nudge_cache_key(self.session_key)))
        # NEW — DIAGNOSTIC: agar idle-nudge phir se baar-baar aa raha hai,
        # ye log line 2 cheezein confirm karegi: (1) session_key har
        # reconnect pe SAME rehta hai ya HAR BAAR NAYA banta hai (agar naya
        # banta hai, to masla frontend mein hai — wo session_key persist
        # nahi kar raha, is liye backend ka cache-based fix bhi kaam nahi
        # kar sakta), (2) idle_already_sent connect() ke waqt already True
        # mil raha hai ya nahi (agar True milta hai lekin phir bhi nudge
        # dobara chala jaye, to bug _send_proactive_message() ke aage hai).
        logger.warning(
            "[ChatConsumer.connect] session_key=%s idle_already_sent_from_cache=%s",
            self.session_key, self.idle_already_sent,
        )
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
        FIX: Ye sirf ek dafa chalta hai — connect hone ke baad agar customer
        30 second tak kuch na kahe to ek proactive message bhejta hai, phir
        khud ko hamesha ke liye band kar leta hai (return). Pehle ye har
        naye message ke baad dobara reset ho kar chalta rehta tha, isliye
        beech conversation mein bhi baar-baar ye nudge aa jata tha — ab
        customer ka pehla message aate hi receive() ise cancel kar deta hai,
        aur agar wo pehle hi fire ho chuka ho to loop khud return kar chuka hota hai.
        """
        try:
            while True:
                await asyncio.sleep(1)
                elapsed = time.monotonic() - self.last_activity
                if elapsed >= IDLE_THRESHOLD_SECONDS and not self.idle_already_sent:
                    await self._send_proactive_message()
                    self.idle_already_sent = True
                    return  # NEW — sirf ek baar bhejna hai, phir loop khatam
        except asyncio.CancelledError:
            pass

    async def _send_proactive_message(self):
        if self.page_context:
            message = f"Kuch aur dhoondna hai {self.page_context} ke ilawa?"
        else:
            message = "Kuch dhoondne mein madad chahiye?"

        # NEW — FIX: Redis mein hamesha ke liye (session ki TTL tak) mark
        # kar dete hain ke is session ke liye idle-nudge bheja ja chuka
        # hai — taake reconnect hone pe bhi dobara na chale (upar connect()
        # mein iska check dekhein).
        await sync_to_async(cache.set)(
            _idle_nudge_cache_key(self.session_key), True, timeout=IDLE_NUDGE_TTL_SECONDS
        )

        await self.send(text_data=json.dumps({
            "type": "message",
            "sender": "ai",
            "message": message,
            "proactive": True,
            "metadata": None,
            "suggestions": ["Find a product", "Talk to support"],
        }, ensure_ascii=False))

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self.send(text_data=json.dumps({"type": "error", "message": "Invalid message format — expected JSON."}, ensure_ascii=False))
            return

        # DEBUG — session_key mix-up track karne ke liye (customer bot)
        print(f"[CUSTOMER-BOT][MESSAGE] session_key={self.session_key} message={data.get('message', '')!r}")

        # FIX: customer ne chat mein kuch bhej diya — idle proactive-nudge
        # sirf connect ke baad, PEHLE message se pehle wali khamoshi ke liye
        # tha. Ab ise hamesha ke liye cancel kar dete hain taake ye beech
        # conversation mein dobara kabhi na aaye (pehle ye reset ho kar
        # dobara chalta rehta tha, is liye chat ke darmiyan bhi aa jata tha).
        if hasattr(self, 'idle_task') and not self.idle_task.done():
            self.idle_task.cancel()
        if data.get('page_context'):
            self.page_context = data['page_context']

        if getattr(self, 'token', None) and is_token_expired_or_invalid(self.token):
            await self.send(text_data=json.dumps({"type": "error", "code": "SESSION_EXPIRED", "message": "Session expired, please log in again."}, ensure_ascii=False))
            await self.close(code=4401)
            return

        user_id = await self.get_session_user_id()
        allowed = await sync_to_async(check_all_rate_limits)(self.session_key, user_id, getattr(self, 'client_ip', None))
        if not allowed:
            await self.send(text_data=json.dumps({"type": "error", "code": "RATE_LIMITED", "message": "Too many messages — please wait a moment before sending again."}, ensure_ascii=False))
            return

        user_message = data.get("message", "")
        attachment_file_id = (data.get("attachment") or {}).get("file_id")

        try:
            validate_message(user_message)
        except MessageValidationError as e:
            await self.send(text_data=json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False))
            return

        try:
            response_text, products_metadata, suggestions, ai_message_id = await self.get_agent_response(user_message, attachment_file_id)
        except Exception:
            # FIX — pehle yahan "f'Sorry, something went wrong: {str(e)}'"
            # bheja jata tha, jo raw Python error (jaise TypeError ka
            # message) seedha user ko dikha deta tha. Ab: asal error
            # logger.exception() se server logs mein jata hai (Railway
            # logs mein dekha ja sakta hai), aur user ko hamesha generic
            # friendly message milta hai.
            logger.exception("ChatConsumer.get_agent_response failed for session_key=%s", self.session_key)
            await self.send(text_data=json.dumps({"type": "error", "message": FRIENDLY_ERROR_MESSAGE}, ensure_ascii=False))
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
                # NEW — FIX: frontend ke paas is se pehle is AI message ka
                # koi real numeric ID nahi hota tha, is liye feedback
                # (thumbs up/down) bhejte waqt wo session_key (UUID) jaisi
                # galat cheez /api/v1/chat/message/<int:message_id>/feedback/
                # mein daal deta tha — jo URL pattern se match hi nahi karti
                # (404). Ab asal ChatMessage.id yahan diya ja raha hai.
                payload["message_id"] = ai_message_id
            await self.send(text_data=json.dumps(payload, ensure_ascii=False))

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

        # CRITICAL FIX: logged-in customers ke liye pehle `session__user=user,
        # session__channel='customer'` se query hoti thi — jo us customer ke
        # SAARE sessions (alag devices/tabs/purani sessions) ka history mila
        # deta tha, is CURRENT conversation tak scoped nahi tha (guest
        # customers ke liye niche wala `else` branch already sahi tha — sirf
        # ek session_key). Ab dono cases mein sirf ISI session ka history
        # milta hai.
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

        # NEW — FIX: _json_safe() yahan lagaya — is exact jagah crash aa
        # raha tha ("Decimal is not JSON serializable"), poora response
        # save hone se pehle fail ho jata tha aur customer ko generic
        # error milta tha. Ab products_metadata mein agar kahin bhi
        # Decimal ho (kisi bhi tool se), save hone se pehle float ban
        # jayega — crash hi nahi hoga.
        safe_products_metadata = _json_safe(products_metadata)

        ai_message = ChatMessage.objects.create(
            session=chat_session, sender='ai', message=output,
            metadata={"products": safe_products_metadata} if safe_products_metadata else None,
        )

        return output, safe_products_metadata, suggestions, ai_message.id