# PATH: apps/ai/admin_consumers.py

import json
import logging  # NEW — server-side error logging ke liye
import re   # NEW — FIX: fabricated-preview detection ke liye
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from apps.ai.models import ChatSession, ChatMessage
from apps.ai.admin_agents.admin_agent import run_admin_agent
from apps.ai.admin_tools.pending_actions import get_pending_action, is_expired   # NEW — FIX: pending_action_hint build karne ke liye
from apps.ai.admin_tools.product_tools import get_product_details as _fetch_product_details   # NEW — FIX: metadata safety-net ke liye
from apps.ai.admin_response_metadata import _normalize_product   # NEW — FIX: safety-net mein bhi wahi consistent shape use karne ke liye
from apps.ai.rate_limiting import check_all_rate_limits
from apps.ai.message_sanitization import validate_message, escape_for_storage, unescape_for_context, MessageValidationError
from apps.ai.ws_auth import extract_token_from_scope, is_token_expired_or_invalid

MAX_HISTORY_MESSAGES = 12

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
                "message": "Too many messages — please wait a moment before sending again.",
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

        # CRITICAL FIX: pehle ye `session__user=user, session__channel='admin'`
        # se query hota tha — jo is USER ke SAARE admin sessions (alag browser
        # tabs, purani testing sessions, etc.) ka history ek sath mila deta
        # tha, is CURRENT conversation tak scoped nahi tha. Isi wajah se model
        # ko kabhi-kabhi bilkul unrelated purani conversations ka context mil
        # jata tha (jaise ek purani test session mein poocha gaya "what is
        # date today", ya kisi aur session mein discuss huay products) aur wo
        # unhe current turn ke sath confuse kar deta tha. Ab sirf ISI session
        # (`chat_session`) ka history milta hai — bilkul isolated.
        previous_messages = list(
            ChatMessage.objects.filter(session=chat_session)
            .order_by('-created_at')[1:MAX_HISTORY_MESSAGES + 1]
        )
        previous_messages.reverse()

        # NEW — CRITICAL FIX: run_admin_agent() ka `pending_action_hint` param
        # pehle YAHAN SE KABHI PASS HI NAHI HOTA THA — is liye SYSTEM_PROMPT
        # ko hamesha "koi pending action nahi" wala hint milta tha, chahe
        # abhi-abhi ek propose_* tool ne pending_action bana kar diya ho.
        # Isi wajah se "haan confirm kar do" bolne par bhi bot "filhal koi
        # pending action nahi hai" keh deta tha aur asal update kabhi hoti
        # hi nahi thi.
        #
        # Fix: is admin ke sabse recent AI ChatMessage ka stored metadata
        # dekhte hain (jahan pending_action save hota hai — models.py mein
        # ChatMessage.metadata JSONField), aur us action_id ko pending_actions
        # cache (pending_actions.py) se dobara verify karte hain — taake
        # agar wo already resolve ho chuka ho ya 5-minute expiry se guzar
        # chuka ho to hint None hi rahe (aur model sahi keh sake "expire ho
        # gaya, dobara try karein").
        pending_action_hint = None
        last_ai_message = (
            ChatMessage.objects.filter(session=chat_session, sender='ai')
            .order_by('-created_at')
            .first()
        )
        if last_ai_message and last_ai_message.metadata:
            pending = last_ai_message.metadata.get('pending_action')
            if pending and pending.get('action_id'):
                cached = get_pending_action(pending['action_id'])
                if cached and not cached.get('resolved') and not is_expired(cached):
                    pending_action_hint = {
                        'action_id': pending['action_id'],
                        'action_type': pending.get('action_type'),
                    }

        # NEW — CRITICAL FIX: "is product ka stock update karo" jaisi
        # pronoun-based follow-ups pehle bar-bar "kaunsa product?" poochti
        # thin, chahe abhi-abhi ussi product ki poori details dikhayi gayi
        # ho. Model ko chat_history se khud pronoun resolve karne ka
        # instruction dena reliable nahi nikla (khaaskar weaker fallback
        # models ke sath) — is liye ab bilkul pending_action_hint jaisa
        # deterministic tareeka: is admin ke sabse recent AI message ka
        # metadata['products'] dekhte hain — agar us turn mein EXACTLY 1
        # product diya gaya tha, wahi "active product" maan lete hain aur
        # is turn ke liye hint bana kar model ko dete hain.
        active_product_hint = None
        if last_ai_message and last_ai_message.metadata:
            products = last_ai_message.metadata.get('products') or []
            if len(products) == 1:
                active_product_hint = {
                    'product_id': products[0].get('product_id'),
                    'name': products[0].get('name'),
                }

        chat_history = []
        for msg in previous_messages:
            text = unescape_for_context(msg.message)
            if msg.sender == 'user':
                chat_history.append(HumanMessage(content=text))
            else:
                # FIX — CRITICAL BUG: pending_action ka action_id sirf
                # ChatMessage.metadata mein save hota hai, "message" text
                # ke andar kabhi nahi likha jata (taake admin ko raw UUID
                # na dikhe). Lekin agle turn mein chat_history sirf isi
                # plain "text" se banta tha — is liye jab admin "haan"/
                # "confirm" bolta, LLM ke paas us action_id ka koi record
                # hi context mein nahi hota tha. Model confuse ho kar
                # bilkul unrelated tool call kar deta tha (jaisa production
                # mein hua: "confirm" ke jawab mein "fan" search kar diya).
                #
                # Fix: agar is AI message ke sath ek open pending_action
                # tha, uska action_id ek internal marker ke tor pe text ke
                # end mein jod dete hain — SIRF LLM context ke liye. Ye
                # already-frontend-ko-bheja-ja-chuka response text ko
                # touch nahi karta (wo pehle hi ja chuka tha) — sirf agli
                # baar history reconstruct karte waqt add hota hai.
                if msg.metadata and isinstance(msg.metadata, dict):
                    pending = msg.metadata.get('pending_action')
                    if pending and pending.get('action_id'):
                        text += f"\n\n[internal: open pending_action_id = {pending['action_id']}]"
                chat_history.append(AIMessage(content=text))

        output, metadata, suggestions = run_admin_agent(
            user_message, session_key=self.session_key, user=user,
            chat_history=chat_history, pending_action_hint=pending_action_hint,   # NEW — FIX
            active_product_hint=active_product_hint,   # NEW — FIX
        )

        # NEW — CRITICAL SAFETY NET: kabhi kabhi model product ki poori
        # details (SKU, description, original_price, low_stock_threshold)
        # apne jawab mein likh deta hai lekin us turn mein get_product_details
        # tool actually call nahi karta (chahe SYSTEM_PROMPT mein saaf mana
        # hai) — is se metadata['products'] khali reh jata tha, chahe
        # response text mein poori details dikh rahi hon. Ab agar metadata
        # khali hai lekin humein pata hai ke ek "active product" is
        # conversation mein zeri-e-baat hai, hum khud (Python se,
        # deterministically) uski REAL details fetch kar ke metadata
        # bhar dete hain — is se frontend ko hamesha sahi, real data
        # milega chahe model ne tool call kiya ho ya nahi.
        #
        # FIX — CRITICAL BUG: pehle yahan hamesha `active_product_hint`
        # (pichle AI turn mein dikhaya gaya product) use hota tha — chahe
        # is CURRENT turn ka pending_action (delete_product/update_product/
        # update_inventory) ek BILKUL ALAG product_id ke liye ho. Isi
        # wajah se "product 108 delete karo" jaisi request pe metadata
        # mein purani/stale product (jaise 86) ki details aa rahi thin,
        # 108 ki nahi. Ab pehle IS TURN ke pending_action preview se
        # target product_id nikalte hain (delete/update/inventory teeno
        # ke preview mein 'product_id' hota hai) — active_product_hint
        # sirf tab fallback ke tor pe use hota hai jab is turn ka
        # pending_action product-related na ho (jaise category/order).
        target_product_id = None
        pending = (metadata or {}).get('pending_action')
        if pending and pending.get('action_type') in ('delete_product', 'update_product', 'update_inventory'):
            preview_product_id = (pending.get('preview') or {}).get('product_id')
            if preview_product_id is not None:
                target_product_id = preview_product_id
        if target_product_id is None and active_product_hint:
            target_product_id = active_product_hint.get('product_id')

        if target_product_id is not None and not (metadata or {}).get('products'):
            try:
                fetched = _fetch_product_details(user, target_product_id)
                if fetched.get('success') and fetched.get('product'):
                    if metadata is None:
                        metadata = {'products': [], 'categories': [], 'customers': [], 'analytics': None}
                    normalized = _normalize_product(fetched['product'])
                    if normalized:
                        metadata['products'] = [normalized]
            except Exception:
                logger.exception(
                    "Metadata safety-net fetch failed for product_id=%s session_key=%s",
                    target_product_id, self.session_key,
                )

        # NEW — CRITICAL FIX: "FABRICATED PREVIEW" guard.
        #
        # Production logs (2026-08-03) se confirm hua: kabhi kabhi model
        # ("Confirm karen? haan/nahi" jaisa preview-shaped text) likh deta
        # hai OB bina asal mutating tool (update_product/create_product/
        # etc.) actually call kiye — is turn ka tools_called literally
        # NONE hota hai. Ye ek FAKE preview hai jise admin "confirm" nahi
        # kar sakta (koi real action_id/pending_action hai hi nahi) — is
        # se admin ko lagta hai system ne update accept kar liya, phir
        # "haan confirm karo" bolne pe kuch nahi hota, confusing multi-
        # round-trip banta hai.
        #
        # SYSTEM_PROMPT mein isay explicitly mana kiya gaya hai, lekin
        # weaker/overloaded-fallback models kabhi ye instruction miss kar
        # dete hain — is liye ab yahan deterministically catch karte hain:
        # agar response text preview jaisa dikhta hai ("Confirm karen")
        # LEKIN metadata mein koi real pending_action nahi hai, to is
        # misleading response ko ek honest, clear message se replace kar
        # dete hain — taake admin confuse na ho ke kya asal mein hua.
        looks_like_fake_preview = (
            re.search(r'confirm\s*karen\?\s*\(?\s*haan', output, re.IGNORECASE)
            and ('preview' in output.lower() or '→' in output or '->' in output)
            and not (metadata or {}).get('pending_action')
        )
        if looks_like_fake_preview:
            logger.warning(
                "Fabricated preview detected (no real pending_action) for session_key=%s — overriding response",
                self.session_key,
            )
            output = (
                "Sorry, ye update abhi properly propose nahi ho paya (system thoda "
                "busy tha) — koi pending action banaya nahi gaya, is liye upar wala "
                "preview asal nahi tha. Apna update request dobara bhejein, main "
                "turant ek asal preview bana kar confirm karwaunga."
            )
            suggestions = []

        if isinstance(output, list):
            output = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in output).strip()

        ChatMessage.objects.create(session=chat_session, sender='ai', message=output, metadata=metadata)

        return output, metadata, suggestions