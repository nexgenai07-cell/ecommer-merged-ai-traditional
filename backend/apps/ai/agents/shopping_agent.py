# PATH: apps/ai/agents/shopping_agent.py

# FLOW: apps/ai/consumers.py ke get_agent_response() se yahan aata hai.
# Ye file LangChain Agent banati hai — jo tools decide karta hai ke
# customer ke message ka jawab dene ke liye kaunsa tool call karna hai.

from django.conf import settings
import logging   # NEW — diagnostic logging: konsa model jawab de raha hai, tool call hua ya nahi
from langchain_openai import ChatOpenAI   # CHANGED — primary model ab NVIDIA (OpenAI-compatible) hai
from langchain_groq import ChatGroq
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage  # NEW — FIX: image attachment ko message content block ke tor pe bhejne ke liye

from apps.ai.tools.registry import SHOPPING_AGENT_TOOLS     # FLOW → apps/ai/tools/registry.py
from apps.ai.tools.cart_order_tools import get_cart_order_tools      # FLOW → apps/ai/tools/cart_order_tools.py
from apps.ai.gemini_utils import call_with_model_fallback    # FLOW → apps/ai/gemini_utils.py (LLM chain ke liye — Gemini key rotation wala call_with_fallback NAHI, wo sirf embeddings ke liye hai)

logger = logging.getLogger("ai.shopping_agent")   # NEW

import re   # NEW — FIX: deterministic script detection ke liye
from apps.ai.language_preference import (   # NEW — sticky language preference (chip-select)
    get_language_preference, set_language_preference, detect_language_selection,
)

# NEW — FIX: LLM (khaaskar fallback/weaker models) customer ke "Roman Urdu"
# ko reliably pehchan kar Roman Urdu mein hi reply nahi kar rahe thay — kabhi
# kabhi seedha pure Urdu (Arabic) script mein switch ho jate thay, jo customer
# ne kabhi maanga hi nahi tha. Isi problem ko admin_agent.py mein
# date_range_hint/pending_action_hint pattern se fix kiya gaya tha — LLM se
# "detect karo" kehne ke bajaye Python khud detect karta hai (regex se, jo
# 100% reliable hai) aur is turn ke liye ek concrete instruction deta hai.
#
# NOTE: hum "Roman Urdu" ko "English" se text se algorithmically differentiate
# nahi kar sakte (dono Latin script hain) — na hi zaroorat hai, JAB TAK
# customer khud explicitly ek language select nahi karta (neeche wala
# resolve_language_for_turn() dekhein). Bina explicit selection ke, customer
# jo bhi Latin letters mein likhe (Roman Urdu ho ya English), reply bhi
# Latin script mein hi hona chahiye — bas Urdu (Arabic) script mein switch
# nahi hona chahiye.
URDU_SCRIPT_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F]')  # Arabic + Arabic Supplement blocks


def _detect_script(text: str) -> str:
    """Returns 'urdu_script' ya 'latin_script' based on customer's current message."""
    if text and URDU_SCRIPT_PATTERN.search(text):
        return 'urdu_script'
    return 'latin_script'


def resolve_language_for_turn(user_input: str, session_key: str):
    """
    FLOW: run_shopping_agent() se call hota hai. Ye function decide karta
    hai ke IS TURN mein LLM ko kaunsi language mein reply karni chahiye,
    priority order mein:

    1. Agar customer ne ABHI (isi message mein) ek language-chip select ki
       hai (jaise "Wanna talk in Roman Urdu?" tap kiya) -> wahi language use
       hoti hai, AUR session ke liye "sticky" store ho jaati hai
       (language_preference.py) taake agle turns mein bhi yaad rahe.
    2. Warna, agar is session ke liye pehle se koi sticky preference stored
       hai (kisi pichle turn mein select ki gayi thi) -> wahi use hoti hai —
       chahe is particular message ka apna script kuch bhi ho (jaise
       customer ne pehle "Roman Urdu" select ki thi, ab sirf "haan" likha —
       phir bhi Roman Urdu mein hi jawab chahiye, "haan" khud se guess
       nahi karwa sakte).
    3. Warna is turn ke message ka script deterministically detect karke
       (Urdu script vs Latin) generic "match customer's script" instruction
       banti hai — jaisa pehle tha.

    Returns: (language_code_or_None, hint_text, just_selected: bool)
      language_code: 'english' | 'roman_urdu' | 'urdu_script' | None (auto)
    """
    just_selected = False
    selected = detect_language_selection(user_input)
    if selected:
        set_language_preference(session_key, selected)
        just_selected = True
        language_code = selected
    else:
        language_code = get_language_preference(session_key)

    if language_code is None:
        # Koi sticky preference nahi — purana per-message script-match behavior
        script = _detect_script(user_input)
        if script == 'urdu_script':
            return None, (
                "The customer's CURRENT message is written in Urdu (Arabic) script. "
                "Reply in Urdu script."
            ), False
        return None, (
            "The customer's CURRENT message is written in Latin/Roman script "
            "(English letters), NOT Urdu script — this includes Roman Urdu (Urdu "
            "words spelled in English letters, e.g. 'kya price hai'). Reply using "
            "Latin/Roman script ONLY. Do NOT switch to Urdu (Arabic) script in your "
            "reply, even if some of the words or phrasing are Urdu vocabulary — "
            "keep it in Roman letters, matching the customer's own register (Roman "
            "Urdu or English)."
        ), False

    STICKY_HINTS = {
        'english': (
            "The customer has explicitly chosen ENGLISH as their conversation "
            "language. Reply in English only, for this and every future reply in "
            "this conversation, until they explicitly choose a different language."
        ),
        'roman_urdu': (
            "The customer has explicitly chosen ROMAN URDU (Urdu words spelled in "
            "English/Latin letters, NOT Arabic script) as their conversation "
            "language. Reply in Roman Urdu only — never switch to Urdu (Arabic) "
            "script and never switch to plain English — for this and every future "
            "reply in this conversation, until they explicitly choose a different "
            "language."
        ),
        'urdu_script': (
            "The customer has explicitly chosen URDU SCRIPT (Arabic script) as "
            "their conversation language. Reply in Urdu (Arabic) script only, for "
            "this and every future reply in this conversation, until they "
            "explicitly choose a different language."
        ),
    }
    hint = STICKY_HINTS[language_code]
    if just_selected:
        hint += (
            " The customer just picked this language this turn (via a language "
            "option) — briefly and warmly acknowledge the switch in the NEW "
            "language, then continue helping with whatever you were discussing "
            "(don't restart the conversation, just carry on naturally)."
        )
    return language_code, hint, just_selected


SYSTEM_PROMPT = """You are a warm, proactive, and highly engaging shopping assistant
for an e-commerce store. Prices are in Pakistani Rupees (Rs.). You work for
BOTH anonymous (guest) and logged-in customers — never assume login is required
just to chat, search, or add things to cart.

You have access to the recent conversation history — use it. If the customer
already told you something earlier in this chat (their preference, occasion,
budget, name, phone, etc.), don't ask again — just use it.

LANGUAGE — always match the customer, every single reply:
- Reply in whatever language/script the customer's CURRENT message is in.
  Urdu script -> reply in Urdu script. Roman Urdu (Urdu written in English
  letters) -> reply in Roman Urdu. English -> reply in English.
- If the customer explicitly asks you to switch language (e.g. "Urdu mein
  baat karo", "reply in Urdu script", "English mein baat karo"), switch
  immediately and KEEP replying in that language for the rest of the
  conversation, until they switch again themselves.
- Never mix an unrelated language into your reply, and never default to
  English just because the system instructions here are in English.

CURRENT-TURN SCRIPT (system-detected from the customer's latest message —
follow this exactly, it overrides your own guess):
{language_hint}

KNOWN CUSTOMER CONTEXT (from past orders, may span previous conversations):
{customer_context}

CORE BEHAVIOR — never leave the customer with a dead end:

1. If the exact product the customer asked for is NOT available or not found:
   - Do NOT just say "not available" and stop.
   - Immediately use search_products with a broader/related query (same category,
     similar type of product) and recommend those alternatives instead.

2. Always mention if any of the products you show have a discount (compare
   'original_price' vs 'price'). If a sale is running, call it out enthusiastically.

3. BE CONVERSATIONAL AND CURIOUS — ask relevant follow-up questions instead of
   just dumping a product list, the way a good in-store salesperson would:
   - If the customer mentions clothing, a dress, or an outfit: ask what occasion
     it's for, and once you know, tailor your search and suggestions to that occasion.
   - If relevant, ask about preferences like color, size, or design/style.
   - When you show products, add a short opinion on why something would suit them.

4. CROSS-SELL: Whenever a customer shows interest in a product or adds it to
   cart, proactively suggest 1-2 related/complementary products, using
   search_products as needed. Never invent products.
   - IMPORTANT: Only the products from your FIRST search_products call in a
     turn are shown to the customer as image cards automatically. Any
     cross-sell items you find via a LATER search_products call in the same
     turn will NOT show an image automatically — so mention them BY NAME in
     your text (e.g. "Agar chahein to main ek matching handbag bhi dikha
     sakta hoon") and let the customer explicitly ask before assuming
     they've already seen a picture of it. Do not describe a cross-sell item
     as if its image is already visible when it isn't.

5. KEEP THE CONVERSATION GOING — always end with a natural next step. Only
   stop this pattern if the customer clearly says they're done.

6. CART & ORDERS:
   - Use add_to_cart when the customer clearly wants to buy/add a specific product.
   - Use get_cart whenever the customer asks what's in their cart/basket, or
     to confirm what they're buying before checkout.
   - Use get_wishlist whenever the customer asks what's in their
     wishlist/favorites/saved items. Requires the customer to be logged in.
   - Use create_order when the customer wants to checkout/place their order.
     - GUEST CHECKOUT IS ALLOWED: collect name, phone, and shipping address first.
     - If logged in, you only need the shipping address.
   - Use list_my_orders whenever the customer asks about their order
     history, their order numbers, or how many orders they've placed —
     never ask them to go find their order number elsewhere when you can
     just look it up for them.
   - Use track_order whenever the customer asks ANYTHING about a specific
     order — status, tracking, amount paid, discount received, or items in
     it — once you have the order number (from the customer, or from
     list_my_orders if they only have one/two orders and it's clear which
     one they mean).
   - Use cancel_order only when the customer gives you their order number
     and wants to cancel it.
   - list_my_orders, track_order, and cancel_order all require the customer
     to be logged in.

7. FAQ / POLICY QUESTIONS: Use the answer_faq tool for policy questions.
   Base your answer strictly on what it returns.

7b. SALES / BUSINESS QUESTIONS: If the customer asks anything about sales
   numbers, revenue, how business is doing, or similar owner/admin-style
   questions ("aaj kitni sales hui hain?"), that data is not something you
   have or can share with a customer — but NEVER just say "I don't have
   that data" and stop there, that's a dead end. Instead, call
   get_trending_products and show them the current top-selling/popular
   products as a warm, natural redirect (e.g. "Sales figures to available
   nahi hain mere paas, lekin ye hamare abhi ke top-selling items hain —
   inhein zaroor dekhein!").

8. Never make up product names, prices, stock, or order details — always
   base your answer on what the tools actually return.

9. NEVER reveal internal implementation details to the customer — no tool
   names, function names, "API", "database", "Qdrant", or phrases like
   "the track_order tool only returns...". These are internal mechanics the
   customer must never see. If a piece of info genuinely isn't available,
   just say so in plain, natural language (e.g. "I don't have that on hand
   right now") and offer the next best step — never explain WHY in terms of
   what a tool/system does or doesn't return.

10. IMAGES: The customer may attach an image along with their message (e.g. a
   photo of a product they want, or something similar they saw elsewhere). If
   an image is present, look at it and describe in your own words what you
   see relevant to shopping (item type, color, style), then use search_products
   with a query based on that description to find matching or similar items —
   never claim you can't see an attached image if one was actually provided.

11. PAYMENT — CRITICAL, NEVER INVENT: This store accepts payment through
   Stripe ONLY. You have NO tool to generate a payment link or QR code, and
   you must NEVER invent one (no "https://pay.example.com/...", no fake
   links, no JazzCash/EasyPaisa/bank transfer/card-entry instructions,
   no QR codes) — those payment methods do not exist here and a made-up
   link will not work, which actively harms the customer's trust.
   - After an order is placed, simply tell the customer their order is
     pending payment and that they can complete payment via Stripe from
     their order/checkout page — do not describe steps you're not certain
     of and do not fabricate a URL.
   - If the customer asks for a payment link or how to pay, be honest that
     you can't generate one yourself right now and, if answer_faq has
     relevant info, use it — otherwise tell them to use the payment option
     shown on their order/checkout page, or contact support for help.

Be warm and natural, like a helpful friend in a shop — not robotic or
transactional."""


def _build_tools(session_key, user):
    return SHOPPING_AGENT_TOOLS + get_cart_order_tools(session_key, user)


def _build_executor(llm, session_key, user):

    # FLOW: yahan dono jagah se tools ikattha hote hain —
    # search/compare/FAQ tools (registry.py) + cart/order tools (cart_order_tools.py)

    """Ab llm object directly leta hai — Gemini ya Groq dono chal sakte hain."""
    tools = SHOPPING_AGENT_TOOLS + get_cart_order_tools(session_key, user)

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("image_message", optional=True),  # NEW — FIX: attachment ke liye
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)

    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=settings.DEBUG,
        return_intermediate_steps=True,
    )


# NEW — FIX: attachment ka actual image data (consumers.py se base64 + mime_type
# ke roop mein aata hai) ko LangChain message content block mein convert karta hai.
# Groq ke configured models (llama-3.3-70b-versatile, llama-3.1-8b-instant) VISION
# CAPABLE NAHI hain — unhe raw image bhejne se error aayega — is liye unke liye
# sirf ek text note bhejte hain taake agent ko pata ho ke image thi, chahe dekh na sake.
def _build_image_message(image: dict, vision_capable: bool):
    if not image:
        return []
    if vision_capable:
        content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image['mime_type']};base64,{image['base64']}"},
            }
        ]
        return [HumanMessage(content=content)]
    return [HumanMessage(content=(
        "[Customer attached an image, but this fallback assistant can't view images "
        "right now — if relevant, ask them to briefly describe what's in it.]"
    ))]


def run_shopping_agent(user_input: str, session_key: str, user=None, chat_history=None, customer_context: str = "", image: dict = None):
    from apps.ai.response_metadata import extract_product_metadata      # FLOW → apps/ai/response_metadata.py

    chat_history = chat_history or []
    _, language_hint, _ = resolve_language_for_turn(user_input, session_key)   # NEW — FIX: sticky + explicit-selection aware

    # NVIDIA model chain — (model_id, vision_capable, extra llm kwargs).
    # Order = priority: [0] primary, baaki fallback (upar wala fail ho
    # tabhi neeche wala try hota hai).
    #
    # UPDATED — admin ki request pe (data-accuracy/tool-calling reliability
    # behtar karne ki umeed mein) "openai/gpt-oss-120b" ko wapis PRIMARY
    # bana diya. NOTE: production logs (2026-08-03) mein isay consistently
    # slow/unreliable paya gaya tha (~21s fail hone mein) — lekin us waqt
    # ka slow-fallback bug (gemini-key-count-based retry loop) alag se fix
    # ho chuka hai, is liye ab worst-case sirf ~8s (single timeout) lagega
    # is model ke fail hone tak, phir turant deepseek-v4-flash pe fallback
    # ho jayega. Agar response time phir bhi kharab lage, is list ka order
    # wapis palat dena (deepseek-v4-flash ko index 0 pe le aana).
    NVIDIA_MODEL_CHAIN = [
        ("openai/gpt-oss-120b", False, {}),                         # NEW PRIMARY — admin ki request pe try kiya ja raha
        ("deepseek-ai/deepseek-v4-flash", False, {}),               # fast fallback — pehle primary tha
        ("meta/llama-3.2-90b-vision-instruct", True, {}),           # vision fallback (image search ke liye)
        ("deepseek-ai/deepseek-v4-pro", False, {}),                 # strong reasoning, same family
        ("nvidia/nemotron-3-super-120b-a12b", False, {}),           # NVIDIA's own agentic model — sahi slug (-a12b zaroori tha)
    ]

    def make_nvidia_attempt(model_id, vision_capable, extra_kwargs):
        def attempt():
            llm = ChatOpenAI(
                model=model_id,
                api_key=settings.NVIDIA_API_KEY,
                base_url="https://integrate.api.nvidia.com/v1",
                temperature=0.4,
                max_retries=0,   # NEW — FIX: pehle 1 tha — client apni taraf se chhupi hui retry karta tha jo `timeout` ke UPAR extra wait jorti thi. Retry ab sirf call_with_model_fallback level pe.
                timeout=8,   # NEW — FIX: 10s se 8s kiya
                **extra_kwargs,
            )
            executor = _build_executor(llm, session_key, user)

            # FLOW: YAHAN LLM ASAL MEIN CALL HOTA HAI — is model_id ka model decide
            # karta hai kaunsa tool call karna hai (jaise search_products,
            # add_to_cart, ya seedha jawab de dena bina tool ke)

            logger.warning(f"[shopping_agent] TRYING model={model_id}")   # NEW — diagnostic

            result = executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
                "customer_context": customer_context,
                "language_hint": language_hint,   # NEW — FIX
                "image_message": _build_image_message(image, vision_capable=vision_capable),
            })

            steps = result.get("intermediate_steps", [])
            tool_names = [step[0].tool for step in steps] if steps else []
            logger.warning(   # NEW — diagnostic: ye line saaf batayegi tool call hua ya nahi
                f"[shopping_agent] model={model_id} SUCCEEDED — tools_called={tool_names or 'NONE (model answered directly)'}"
            )

            # FLOW: result["intermediate_steps"] mein har tool call ka record hai —
            # ye extract_product_metadata() aur get_customer_followup_suggestions() dono ko diya jata hai

            from apps.ai.suggestions import get_customer_followup_suggestions   # NEW
            steps = result.get("intermediate_steps", [])

            return result["output"], extract_product_metadata(steps), get_customer_followup_suggestions(steps, session_key)
        return attempt

    def make_groq_attempt(model_name):
        def attempt():
            llm = ChatGroq(model=model_name, groq_api_key=settings.GROQ_API_KEY, temperature=0.4, timeout=8)   # NEW — FIX: timeout 8s
            executor = _build_executor(llm, session_key, user)
            result = executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
                "customer_context": customer_context,
                "language_hint": language_hint,   # NEW — FIX
                "image_message": _build_image_message(image, vision_capable=False),  # NEW — FIX
            })

            from apps.ai.suggestions import get_customer_followup_suggestions   # NEW
            steps = result.get("intermediate_steps", [])

            return result["output"], extract_product_metadata(steps), get_customer_followup_suggestions(steps, session_key)
        return attempt

    primary_model_id, primary_vision, primary_kwargs = NVIDIA_MODEL_CHAIN[0]
    nvidia_attempt = make_nvidia_attempt(primary_model_id, primary_vision, primary_kwargs)

    fallback_fns = [
        make_nvidia_attempt(model_id, vision_capable, extra_kwargs)
        for model_id, vision_capable, extra_kwargs in NVIDIA_MODEL_CHAIN[1:]
    ]

    if settings.GROQ_API_KEY:
        # Last-resort fallback — sirf tab try hota hai jab SAARE NVIDIA models
        # (upar wali chain) fail/quota-exhaust ho chuke hon.
        fallback_fns.append(make_groq_attempt("llama-3.3-70b-versatile"))
        fallback_fns.append(make_groq_attempt("llama-3.1-8b-instant"))

    # FLOW → apps/ai/gemini_utils.py — retry/fallback yahan hota hai (chain mein
    # order se ek-ek model try hota hai), phir wapis (output, metadata) tuple
    # deta hai — ye consumers.py mein jata hai
    return call_with_model_fallback(nvidia_attempt, fallback_fns=fallback_fns)   # FIX — ab needlessly gemini-key-count baar repeat nahi hoga