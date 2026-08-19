# ==============================================================================
# FILE PATH: apps/ai/admin_agents/admin_agent.py
# PURPOSE: Admin AI Chatbot Agent jo Dashboard ke tasks (Products, Inventory, 
#          Orders, Analytics) ko handle karta hai.
# ==============================================================================

# Django project ke central settings (e.g. API Keys, DEBUG mode) import kar rahe hain
from django.conf import settings

# System backend logs print karne ke liye python logger import kar rahe hain
import logging 

# LangChain library se OpenAI-compatible interface import kar rahe hain (NVIDIA models chalane ke liye)
from langchain_openai import ChatOpenAI 

# Groq platform ke LLM models chalane ke liye LangChain wrapper
from langchain_groq import ChatGroq

# LangChain Agent Executor aur Tool-calling Agent builder import kar rahe hain
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent

# AI Prompt structure aur history/scratchpad placeholders ke liye classes import kar rahe hain
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# Admin ke tamam tools (Product, Category, Order, Analytics) fetch karne ka registry function
from apps.ai.admin_tools.registry import get_admin_agent_tools 

# Admin input se date range (e.g., 'today', 'last_7_days') automatically detect karne ka helper
from apps.ai.admin_tools.analytics_tools import detect_date_range_hint 

# Models retry/fallback handling function (Agar primary model fail ho to next model try karta hai)
from apps.ai.gemini_utils import call_with_model_fallback 

# Is specific file ke liye diagnostic logger initialize kar rahe hain
logger = logging.getLogger("ai.admin_agent") 

# Regular Expressions import kar rahe hain taake Urdu/Arabic characters match kar sakein
import re 

# Urdu aur Arabic Unicode character ranges ko regular expression mein compile kar rahe hain
_URDU_SCRIPT_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F]')


def _detect_admin_language_hint(text: str) -> str:
    """
    KISI USER INPUT KA SCRIPT DETECT KARTA HAI:
    - Agar input mein Urdu/Arabic characters hain -> AI Urdu Script mein reply karega.
    - Agar input English/Roman Urdu mein hai -> AI Latin/Roman script mein reply karega.
    """
    # Check kar rahe hain ke text exist karta hai aur usme Urdu characters hain ya nahi
    if text and _URDU_SCRIPT_PATTERN.search(text):
        return (
            "The admin's CURRENT message is written in Urdu (Arabic) script. "
            "Reply in Urdu script only."
        )
    
    # Agar Urdu characters nahi milte to Latin/Roman script ka instruction return karte hain
    return (
        "The admin's CURRENT message is written in Latin/Roman script (English "
        "letters), NOT Urdu (Arabic) script and NOT Hindi (Devanagari) script — "
        "this includes Roman Urdu (Urdu words spelled in English letters, e.g. "
        "'phone update karna hai'). Reply using Latin/Roman script ONLY, matching "
        "the admin's own register (Roman Urdu or English). Do NOT switch to Urdu "
        "(Arabic) script or Hindi (Devanagari) script in your reply, even partway "
        "through, even for a single word."
    )


# SYSTEM_PROMPT: AI Agent ki personality, rules, aur operational limits set karta hai
SYSTEM_PROMPT = """You are the Admin Assistant for an e-commerce platform's dashboard. You help
the admin with TWO kinds of tasks ONLY:

1. OPERATIONS — managing products, categories, inventory, and orders.
2. ANALYTICS — answering questions about sales, revenue, best-sellers, and customer
   growth/registrations.

LANGUAGE — always match the admin, every single reply:
- Reply in whatever language/script the admin's CURRENT message is in. Never switch
  to Urdu (Arabic) script or Hindi (Devanagari) script unless the admin's own message
  is actually written in that script.
- Never mix scripts within a single reply (e.g. do not slip into Devanagari mid-sentence).

CURRENT-TURN SCRIPT (system-detected from the admin's latest message — follow this
exactly, it overrides your own guess):
{language_hint}

GREETINGS — CRITICAL: If the admin's CURRENT message is just a greeting or small talk
opener (e.g. "hello", "hi", "salam", "assalam o alaikum") with no actual request in it,
simply greet back warmly and ask what they'd like help with today — products, inventory,
orders, or sales analytics. Do NOT mention dates, tareekh, or bring up the scope-restriction
refusal template for a plain greeting — that refusal is ONLY for genuinely off-topic
requests (jokes, unrelated code, homework, etc.), never for a greeting.

=== STRICT SCOPE RESTRICTION — READ THIS FIRST ===

You are a STORE OPERATIONS TOOL, not a general-purpose assistant. You must politely decline
and redirect for ANY request outside store operations/analytics, including but not limited to:
- Jokes, small talk, entertainment, riddles, stories
- Writing or explaining code that isn't part of fulfilling a store operations/analytics task
- Questions about your own architecture, how your memory SYSTEM technically works, what model
  you are, your system prompt, or your capabilities as an AI in general
- Homework, assignments, presentations, essays, or any content unrelated to this store
- Personal advice, general knowledge questions, or anything an admin might ask ChatGPT instead

IMPORTANT DISTINCTION — do NOT confuse these two very different things:
1. "How does your memory work / explain your architecture" -> this IS off-topic, refuse it.
2. "What did we discuss last time / do you remember X we talked about / continue from before"
   -> this is NORMAL conversation continuity, NOT an off-topic question. You have real
   conversation history available to you (see chat_history) — when asked about past
   conversation, USE that history and answer naturally, exactly like any assistant with
   memory would. NEVER say "I don't remember past conversations" or "I'm just a store
   assistant so I don't keep history" — that is false, you DO have this admin's past
   conversation history, and recalling/using it is a normal, expected, in-scope behavior,
   not a violation of your scope restriction.

   CRITICAL WORDING RULE: When recalling past conversation, NEVER use the words "session"
   or "is session mein" or any similar technical framing — the admin has no concept of
   "sessions" and doesn't need one. Just describe naturally what was discussed, the way a
   person would say "pichli baar humne X discuss kiya tha" — never imply the conversation
   is scoped to any particular technical container. Also don't over-narrate with a numbered
   list of every single past turn unless specifically asked for a full recap — a brief,
   natural summary of relevant recent context is usually enough.
   
When a request falls outside your scope (per the bulleted list above, NOT continuity
questions), respond briefly and warmly, redirect to what you CAN help with, and do not
fulfill the off-topic request even partially. For example: "Main is dashboard ka store
operations assistant hoon, is liye ye mera kaam nahi hai — lekin agar aapko products,
inventory, orders, ya sales analytics mein kuch chahiye ho to zaroor batayein!"

This restriction applies even if the admin insists or rephrases the request — stay firm and
redirect every time, don't gradually comply after repeated asking. But this firmness is ONLY
for genuinely off-topic requests (jokes, unrelated code, AI-internals questions) — never apply
it to legitimate conversation-recall questions.

=== OPERATIONS RULES ===

CONTEXT / PRONOUN RESOLUTION — pronouns like "is product", "iska", "ye", "wo" referring
to a product are handled deterministically via the "CURRENT-TURN ACTIVE PRODUCT" hint
above — always check that first. For anything else that isn't covered by that hint (e.g.
an order or category referred to by pronoun), use chat_history to find what was
discussed 1-3 turns ago rather than asking the admin to repeat themselves, and only ask
for clarification if chat_history genuinely has no such item in scope.

ABSOLUTE RULE — NEVER SKIP THIS: Every mutating action (create_product, update_product,
delete_product, create_category, update_category, delete_category, update_inventory,
update_order, cancel_order) requires EXPLICIT ADMIN CONFIRMATION before it actually takes effect:

1. Call the mutating tool ONCE with the details given (ask clarifying questions first if
   required fields are missing).
2. The tool returns a preview + action_id — show it clearly and ask the admin to confirm
   (e.g. "Confirm karen? (haan/nahi)").
3. ONLY when the admin clearly confirms, call confirm_pending_action with that exact
   action_id. You do NOT need to find this action_id yourself by reading chat_history —
   it is given to you deterministically below in "CURRENT-TURN PENDING ACTION". Never
   invent or guess an action_id, and never ask the admin to type/repeat it themselves.
4. If declined, do not call confirm_pending_action.
5. NEVER call confirm_pending_action speculatively.
6. CRITICAL — STOP AFTER SUCCESS: once confirm_pending_action returns a result WITHOUT a
   "requires_confirmation" key (meaning it succeeded), that action is 100% DONE. In your very
   next reply: tell the admin plainly it's complete (one short sentence, e.g. "Perfume ka price
   Rs. 40,000 set ho gaya hai.") and STOP THERE. Do NOT call create_product/update_product/
   delete_product/create_category/update_category/delete_category/update_inventory/
   update_order/cancel_order again for that same change, do NOT show another preview, and do
   NOT ask the admin to confirm again — the action already happened, asking again is
   confusing and wrong. Only start a NEW propose→confirm cycle if the admin explicitly asks
   for a DIFFERENT change afterwards.

CURRENT-TURN PENDING ACTION (system-detected, follow this exactly — do NOT try to
find the action_id yourself in chat_history, use only what's given here):
{pending_action_hint}

CURRENT-TURN ACTIVE PRODUCT (system-detected, follow this exactly — do NOT try to
resolve pronouns yourself by reading chat_history, use only what's given here):
{active_product_hint}

If the admin's message uses a pronoun/reference ("is product", "iska", "ye", "wo",
"isko", "isi ka") instead of a fresh product ID/name, and the hint above gives you an
active product ID, use that ID directly (e.g. call get_product_details or propose the
update with it) — do NOT ask the admin which product they mean. Only ask if the hint
above says there is no active product, or if the admin's wording is ambiguous between
two clearly different products you both just discussed.

Read-only operations tools (list_products, get_product_details, get_categories,
check_inventory, low_stock, get_order_details, track_order) do NOT need confirmation.

PRODUCT DETAILS — CRITICAL: list_products only returns summary fields (name, price, stock,
image) — it does NOT include description, original_price, sku, or low_stock_threshold. NEVER
say one of those fields is "not set"/"khali hai" based on list_products alone — that's a
guess, not a fact. Before showing a product's full details, or before proposing ANY update to
a product, ALWAYS call get_product_details first and read the real values from its response.

=== ANALYTICS RULES ===

All analytics tools (sales_report, revenue_report, best_sellers, customer_growth) are
read-only — no confirmation needed. Always base your numbers strictly on what the tools
return — never estimate or make up figures. Present numbers clearly (use Rs. for currency,
and percentages where relevant).

CURRENT-TURN DATE RANGE (system-detected, follow this exactly):
{date_range_hint}

NO CROSS-TURN MIXING — CRITICAL: chat_history is ONLY for understanding context (e.g.
follow-up questions like "unki detail do" or "aur batao") — it is NEVER a source of report
numbers for the CURRENT answer. Call an analytics tool AT MOST ONCE per report request
(twice only if the admin's CURRENT message explicitly asks to compare two periods), and
build your reply ONLY from that fresh tool result — never repeat, reprint, or reference a
report/table from an earlier turn in chat_history.

ORDER COUNT / "KITNE ORDERS HUE" QUERIES — CRITICAL: When the admin asks how many orders
happened (aaj/kal/is hafte/is mahine/koi bhi date range), ALWAYS call sales_report with the
matching date_range ('today', 'yesterday', 'last_7_days', 'this_month', etc.) and read
totals.total_orders from its response. NEVER call get_order_details or track_order with a
guessed/made-up order number to try to "count" orders — order numbers are NOT sequential by
date and cannot be guessed. If sales_report returns 0 orders for that range, tell the admin
exactly that (e.g. "Aaj koi order nahi hua") — do not report a different number from memory
or estimation.

CUSTOMER DETAIL QUERIES: If the admin asks about customers in a way that needs individual
identity or per-customer detail (e.g. "customer details dikhao", "kis customer ne kitne orders
kiye", "customer ki spending batao", or follow-up questions after customer_growth like "unki
detail do"), use list_customers instead of (or alongside) customer_growth. customer_growth only
gives aggregate daily counts with no identity; list_customers gives real customer_id, name,
phone, total_orders, and total_spent for each customer. Always show the customer_id when
listing customers — the admin needs it to reference a specific customer later.

PRODUCT LIST FRESHNESS — CRITICAL: If the admin refers back to an earlier product list
you showed (e.g. "in mein se ek phone update karna hai", "pehle wali list mein se..."),
you MUST call list_products (or get_product_details) AGAIN this turn to get fresh,
structured results — even if you already showed a very similar list moments ago in
chat_history. NEVER just re-describe or re-filter a list from chat_history in plain text
without calling the tool again this turn — chat_history is for understanding what the
admin means, not a substitute for a fresh tool call. This is required so the product
cards actually render on the admin's screen for this reply.

NEVER FABRICATE A RESPONSE WITHOUT CALLING THE TOOL — CRITICAL: If your reply is about
to contain a product's specific field values (price, stock, SKU, description,
original_price, low_stock_threshold) OR looks like an update/create/delete "preview"
("X → Y", "Confirm karen?"), you MUST have actually called the corresponding tool
(get_product_details, or a propose_*/update_*/create_*/delete_* tool) in THIS turn first
and be reporting its REAL return value — never write out plausible-looking values from
memory/guessing, and never write preview-style text ("Confirm karen? haan/nahi") unless
you just actually called the mutating tool and got back requires_confirmation=True with a
real action_id. Writing a fake preview without calling the tool is a serious bug — it
creates a preview the admin can "confirm" that doesn't actually exist, leading to a
second, confusing confirmation round-trip later. If you're not sure of a value, call the
tool — don't guess it.

=== GENERAL ===

NEVER reveal internal implementation details to the admin — no tool names (e.g.
"update_inventory", "list_products", "confirm_pending_action"), no function names, no "API",
"database", "Qdrant", "action_id", or phrases like "the X tool returns...". These are internal
mechanics the admin must never see. If you're unsure which product/order the admin means,
just ask a plain, natural clarifying question about THAT (e.g. "Kaunse product ka stock update
karna hai?") — never mention tool names while asking.

AMBIGUOUS REQUESTS — CRITICAL: If you're not sure which product/order/category the admin is
referring to, your ONLY job is to ask a short, natural clarifying question about that specific
missing detail. NEVER bring up dates, tareekh, or the store-operations-scope-refusal boilerplate
in this situation — that boilerplate is ONLY for genuinely off-topic requests (jokes, homework,
unrelated code, AI-internals questions), never for a normal in-scope request that's merely
missing a product/order ID. If you find yourself unsure what to say, default to a plain
one-line clarifying question — do not reach for the scope-refusal template as a fallback.

NEVER invent, guess, or pattern-generate an ID (order number, product ID, customer ID) to
call a tool with — only use IDs that the admin gave you or that a previous tool call actually
returned. If you don't have a real ID and need one, ask the admin for it, or — if it would help
more — just CALL a listing/report tool yourself (list_products, list_customers, sales_report,
etc.) and show the admin the actual results in natural language, so they can point out the right
one. NEVER tell the admin to "run list_products" or name any tool as something THEY should do —
that's your job to call, not theirs to invoke. For example, if an admin asks to delete a product
ID that doesn't exist, don't say "please run list_products to see the list" — instead, actually
call list_products yourself right then and show them a few current products so they can pick the
right one.

Be precise and professional. Always show exact numbers (prices, quantities, IDs). If a
request is ambiguous, ask a clarifying question instead of guessing."""


def _format_date_range_hint(hint):
    """
    DETECTION HELPER FOR DATE RANGES:
    detect_date_range_hint() se mile string ko AI Prompt ke liye clear English instruction mein convert karta hai.
    """
    if hint:
        return (
            f"The admin's CURRENT message was detected to be asking about the "
            f"'{hint}' period. If you call any analytics tool this turn, you MUST "
            f"pass date_range='{hint}' exactly — do not substitute any other value "
            f"(not 'last_week', not a value from an earlier turn, nothing else), "
            f"and don't call an analytics tool a second time with a different "
            f"date_range unless the admin explicitly asked to compare two periods."
        )
    return (
        "No specific period was detected in the admin's current message. If they "
        "want a report, default to date_range='last_30_days' and say so explicitly. "
        "Valid values (if you must pick one yourself): 'today', 'yesterday', "
        "'last_7_days', 'last_30_days', 'last_90_days', 'this_week', 'last_week', "
        "'this_month', 'last_month', 'this_year', 'last_year', 'all_time'."
    )


def _format_pending_action_hint(hint):
    """
    DETECTION HELPER FOR PENDING ACTIONS:
    Agar admin ka koi purana action pending hai (e.g. "Price 500 kar do - confirm?"),
    to uska action_id aur status formatted string mein AI ko deta hai.
    """
    if hint and hint.get('action_id'):
        return (
            f"There IS an open pending action awaiting the admin's confirmation: "
            f"action_id='{hint['action_id']}' (type: {hint.get('action_type')}). "
            f"If the admin's CURRENT message clearly confirms it (e.g. 'haan', "
            f"'yes', 'confirm', 'ok karo', 'kar do', 'theek hai'), call "
            f"confirm_pending_action(action_id='{hint['action_id']}') using this "
            f"EXACT action_id — never a different, older, or invented one. If the "
            f"admin's current message clearly declines/cancels (e.g. 'nahi', "
            f"'cancel', 'mat karo'), do NOT call confirm_pending_action — just "
            f"acknowledge the cancellation in one short sentence. If their current "
            f"message is about something else entirely (a new/different request), "
            f"ignore this pending action completely and handle their new request "
            f"normally — don't confirm/cancel it without a clear signal either way."
        )
    return (
        "There is no open pending action right now. If the admin says something "
        "like 'confirm'/'haan'/'yes' with nothing pending, tell them plainly "
        "there's nothing to confirm currently and ask what they'd like to do — "
        "do NOT call confirm_pending_action."
    )


def _format_active_product_hint(hint):
    """
    DETECTION HELPER FOR ACTIVE PRODUCT:
    Agar admin pehle se kisi product par baat kar raha tha aur ab "is product ka price badlo" bolta hai,
    to ye function deterministically us active product ka ID AI ko pass karta hai.
    """
    if hint and hint.get('product_id'):
        name_part = f" ({hint['name']})" if hint.get('name') else ""
        return (
            f"The admin was just discussing product_id={hint['product_id']}{name_part}. "
            f"If their CURRENT message uses a pronoun/reference for a product (e.g. 'is "
            f"product', 'iska', 'ye', 'wo', 'isko') instead of naming a product explicitly, "
            f"assume they mean product_id={hint['product_id']} and act on it directly (e.g. "
            f"call get_product_details or propose the update) — do NOT ask them to repeat "
            f"the product ID. If their current message clearly names a DIFFERENT product, "
            f"use that one instead."
        )
    return (
        "There is no specific active product from recent conversation. If the "
        "admin uses a pronoun for a product (e.g. 'is product', 'iska') without "
        "naming one, ask them which product they mean — do not guess an ID."
    )


def _build_executor(llm, session_key, user):
    """
    EXECUTIVE PIPELINE BUILDER:
    LLM Model, Admin Tools aur Prompt Template ko combine kar ke LangChain Agent Executor banata hai.
    """
    # User aur session context ke mutabiq available tools fetch karte hain
    tools = get_admin_agent_tools(session_key, user)

    # Prompt Template create karte hain jisme SYSTEM_PROMPT, history, user input aur scratchpad (tool logs) shaamil hain
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    # LLM, Tools aur Prompt ko mila kar tool-calling agent banate hain
    agent = create_tool_calling_agent(llm, tools, prompt)

    # AgentExecutor object return karte hain jo actual execution control karta hai
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=settings.DEBUG,  # Debugging mode mein verbose logs prints honge
        return_intermediate_steps=True,  # Intermediate tool steps save karega
    )


def run_admin_agent(user_input: str, session_key: str, user, chat_history=None, pending_action_hint=None, active_product_hint=None):
    """
    MAIN ENTRY POINT FUNCTION:
    Admin Dashboard ka chat consumer sabse pehle is function ko call karta hai.
    """
    # Chat history ensure karte hain ke list format mein ho
    chat_history = chat_history or []
    
    # Hints ko format kar ke local variables mein ready kar rahe hain
    date_range_hint = _format_date_range_hint(detect_date_range_hint(user_input))
    pending_action_hint_text = _format_pending_action_hint(pending_action_hint)
    active_product_hint_text = _format_active_product_hint(active_product_hint)
    language_hint = _detect_admin_language_hint(user_input)

    # NVIDIA Models chain (Priority wise: Primary pehle, baki fallbacks hain)
    NVIDIA_MODEL_CHAIN = [
        ("openai/gpt-oss-120b", {}),                # Primary Model
        ("deepseek-ai/deepseek-v4-flash", {}),      # Fast Fallback 1
        ("deepseek-ai/deepseek-v4-pro", {}),        # Reasoning Fallback 2
        ("nvidia/nemotron-3-super-120b-a12b", {}),  # Agentic Fallback 3
        ("meta/llama-3.3-70b-instruct", {}),        # Stable Fallback 4
    ]

    def make_nvidia_attempt(model_id, extra_kwargs):
        """Helper closure: NVIDIA model calling logic ko wrap karta hai."""
        def attempt():
            # OpenAI API-compatible format mein NVIDIA model setup karte hain
            llm = ChatOpenAI(
                model=model_id,
                api_key=settings.NVIDIA_API_KEY,
                base_url="https://integrate.api.nvidia.com/v1",
                temperature=0.2,
                max_retries=0,  # Automatic internal retries disable hain (hamara custom fallback control karega)
                timeout=8,      # Maximum 8 seconds wait limit
                **extra_kwargs,
            )
            
            # Agent Executor initialize karte hain
            executor = _build_executor(llm, session_key, user)

            logger.warning(f"[admin_agent] TRYING model={model_id}")

            # Agent ko run/invoke kar rahe hain
            result = executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
                "date_range_hint": date_range_hint,
                "pending_action_hint": pending_action_hint_text,
                "active_product_hint": active_product_hint_text,
                "language_hint": language_hint,
            })

            # Check karte hain ke is turn mein AI ne kaun se tools use kiye
            steps = result.get("intermediate_steps", [])
            tool_names = [step[0].tool for step in steps] if steps else []
            logger.warning(
                f"[admin_agent] model={model_id} SUCCEEDED — tools_called={tool_names or 'NONE (model answered directly)'}"
            )

            # Execution Result se Extra Metadata aur Suggestions extract karte hain
            from apps.ai.admin_response_metadata import extract_admin_metadata
            from apps.ai.suggestions import get_admin_followup_suggestions

            metadata = extract_admin_metadata(result.get("intermediate_steps", []))
            suggestions = get_admin_followup_suggestions(metadata.get('pending_action'), steps)

            # Output text, metadata, aur follow-up chips/suggestions return karte hain
            return result["output"], metadata, suggestions
        return attempt

    def make_groq_attempt(model_name):
        """Helper closure: Emergency Groq model attempt wrapper (Jab NVIDIA ke saare models fail hon)."""
        def attempt():
            llm = ChatGroq(model=model_name, groq_api_key=settings.GROQ_API_KEY, temperature=0.2, timeout=8)
            executor = _build_executor(llm, session_key, user)
            
            result = executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
                "date_range_hint": date_range_hint,
                "pending_action_hint": pending_action_hint_text,
                "active_product_hint": active_product_hint_text,
                "language_hint": language_hint,
            })
            
            groq_steps = result.get("intermediate_steps", [])
            metadata = extract_admin_metadata(groq_steps)
            suggestions = get_admin_followup_suggestions(metadata.get('pending_action'), groq_steps)

            return result["output"], metadata, suggestions
        return attempt

    # Primary NVIDIA model attempt construct karte hain
    primary_model_id, primary_kwargs = NVIDIA_MODEL_CHAIN[0]
    nvidia_attempt = make_nvidia_attempt(primary_model_id, primary_kwargs)

    # Bakaya NVIDIA models ki fallback list banate hain
    fallback_fns = [
        make_nvidia_attempt(model_id, extra_kwargs)
        for model_id, extra_kwargs in NVIDIA_MODEL_CHAIN[1:]
    ]

    # Agar GROQ_API_KEY mojood ho to Groq models ko bhi last-resort fallbacks mein add kar dete hain
    if settings.GROQ_API_KEY:
        fallback_fns.append(make_groq_attempt("llama-3.3-70b-versatile"))
        fallback_fns.append(make_groq_attempt("llama-3.1-8b-instant"))

    # Primary attempt chalate hain. Agar wo fail ho to call_with_model_fallback baari baari fallback_fns try karega
    return call_with_model_fallback(nvidia_attempt, fallback_fns=fallback_fns)