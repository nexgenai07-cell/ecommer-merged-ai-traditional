# PATH: apps/ai/admin_agents/admin_agent.py

# FLOW: admin_consumers.py se yahan aata hai. Customer wale
# shopping_agent.py jaisa hi pattern hai (LLM + tools + fallback),
# FARQ: tools apps/ai/admin_tools/registry.py se aate hain (product +
# category + inventory + order + analytics — sab ek sath).

from django.conf import settings
import logging   # NEW — diagnostic logging: konsa model jawab de raha hai, tool call hua ya nahi
from langchain_openai import ChatOpenAI   # CHANGED — primary model ab NVIDIA (OpenAI-compatible) hai
from langchain_groq import ChatGroq
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from apps.ai.admin_tools.registry import get_admin_agent_tools      # FLOW → apps/ai/admin_tools/registry.py
from apps.ai.admin_tools.analytics_tools import detect_date_range_hint   # NEW — FIX: deterministic date_range detection
from apps.ai.gemini_utils import call_with_fallback    # FLOW → apps/ai/gemini_utils.py (ab sirf retry/fallback wrapper ke liye)

logger = logging.getLogger("ai.admin_agent")   # NEW


SYSTEM_PROMPT = """You are the Admin Assistant for an e-commerce platform's dashboard. You help
the admin with TWO kinds of tasks ONLY:

1. OPERATIONS — managing products, categories, inventory, and orders.
2. ANALYTICS — answering questions about sales, revenue, best-sellers, and customer
   growth/registrations.

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

ABSOLUTE RULE — NEVER SKIP THIS: Every mutating action (create_product, update_product,
delete_product, create_category, update_category, delete_category, update_inventory,
update_order, cancel_order) requires EXPLICIT ADMIN CONFIRMATION before it actually takes effect:

1. Call the mutating tool ONCE with the details given (ask clarifying questions first if
   required fields are missing).
2. The tool returns a preview + action_id — show it clearly and ask the admin to confirm
   (e.g. "Confirm karen? (haan/nahi)").
3. ONLY when the admin clearly confirms, call confirm_pending_action with that exact action_id.
4. If declined, do not call confirm_pending_action.
5. NEVER call confirm_pending_action speculatively.

Read-only operations tools (list_products, get_categories, check_inventory, low_stock,
get_order_details, track_order) do NOT need confirmation.

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

=== GENERAL ===

NEVER invent, guess, or pattern-generate an ID (order number, product ID, customer ID) to
call a tool with — only use IDs that the admin gave you or that a previous tool call actually
returned. If you don't have a real ID and need one, ask the admin or use a listing/report tool
instead (list_products, list_customers, sales_report, etc.).

Be precise and professional. Always show exact numbers (prices, quantities, IDs). If a
request is ambiguous, ask a clarifying question instead of guessing."""

def _format_date_range_hint(hint):
    """
    FLOW: run_admin_agent() detect_date_range_hint() se milne wale
    result (ya None) ko is turn ke liye ek concrete, unambiguous
    instruction mein badalta hai — taake model ko khud enum se
    "guess"/"map" na karna pade, jo Railway logs ke mutabiq reliably
    nahi ho pa raha tha (primary model ne "last year" ko galti se
    'last_week' bhej diya tha).
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


def _build_executor(llm, session_key, user):

    tools = get_admin_agent_tools(session_key, user)

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)

    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=settings.DEBUG,
        return_intermediate_steps=True,
    )


def run_admin_agent(user_input: str, session_key: str, user, chat_history=None):
    chat_history = chat_history or []
    date_range_hint = _format_date_range_hint(detect_date_range_hint(user_input))   # NEW — FIX

    # NVIDIA model chain — (model_id, extra llm kwargs).
    # Order = priority: [0] primary, baaki fallback (upar wala fail ho
    # tabhi neeche wala try hota hai). Sab slugs docs.api.nvidia.com ke
    # OFFICIAL reference se verify kiye hain (pehli list mein 3 models
    # galat naam ki wajah se 404 de rahe thay, aur ek retire ho chuka tha
    # -> 410). gpt-oss-120b aur deepseek-v4-flash tumhare apne pehle
    # successful test mein bhi chal chuke hain, is liye unhi ko top pe
    # rakha hai. Sab alag providers hain taake ek ka outage doosre ko
    # affect na kare.
    NVIDIA_MODEL_CHAIN = [
        ("openai/gpt-oss-120b", {}),                        # PRIMARY — tumhare pehle test mein already kaam kar chuka
        ("deepseek-ai/deepseek-v4-flash", {}),              # tumhare pehle test mein bhi kaam kar chuka, fast
        ("deepseek-ai/deepseek-v4-pro", {}),                # strong reasoning, same family jo already chal chuki
        ("nvidia/nemotron-3-super-120b-a12b", {}),          # NVIDIA's own agentic model — sahi slug (-a12b zaroori tha)
        ("meta/llama-3.3-70b-instruct", {}),                # well-established, stable, reliable tool-calling
    ]

    def make_nvidia_attempt(model_id, extra_kwargs):
        def attempt():
            llm = ChatOpenAI(
                model=model_id,
                api_key=settings.NVIDIA_API_KEY,
                base_url="https://integrate.api.nvidia.com/v1",
                temperature=0.2,
                max_retries=1,
                **extra_kwargs,
            )
            executor = _build_executor(llm, session_key, user)

            logger.warning(f"[admin_agent] TRYING model={model_id}")   # NEW — diagnostic

            result = executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
                "date_range_hint": date_range_hint,   # NEW — FIX
            })

            steps = result.get("intermediate_steps", [])
            tool_names = [step[0].tool for step in steps] if steps else []
            logger.warning(   # NEW — diagnostic: ye line saaf batayegi tool call hua ya nahi
                f"[admin_agent] model={model_id} SUCCEEDED — tools_called={tool_names or 'NONE (model answered directly)'}"
            )

            # Metadata and Suggestions extraction
            from apps.ai.admin_response_metadata import extract_admin_metadata
            from apps.ai.suggestions import get_admin_followup_suggestions

            metadata = extract_admin_metadata(result.get("intermediate_steps", []))
            suggestions = get_admin_followup_suggestions(metadata.get('pending_action'))

            return result["output"], metadata, suggestions
        return attempt

    def make_groq_attempt(model_name):
        def attempt():
            llm = ChatGroq(model=model_name, groq_api_key=settings.GROQ_API_KEY, temperature=0.2)
            executor = _build_executor(llm, session_key, user)
            
            result = executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
                "date_range_hint": date_range_hint,   # NEW — FIX
            })
            
            # Metadata and Suggestions extraction
            from apps.ai.admin_response_metadata import extract_admin_metadata
            from apps.ai.suggestions import get_admin_followup_suggestions
            
            metadata = extract_admin_metadata(result.get("intermediate_steps", []))
            suggestions = get_admin_followup_suggestions(metadata.get('pending_action'))
            
            return result["output"], metadata, suggestions
        return attempt

    primary_model_id, primary_kwargs = NVIDIA_MODEL_CHAIN[0]
    nvidia_attempt = make_nvidia_attempt(primary_model_id, primary_kwargs)

    fallback_fns = [
        make_nvidia_attempt(model_id, extra_kwargs)
        for model_id, extra_kwargs in NVIDIA_MODEL_CHAIN[1:]
    ]

    if settings.GROQ_API_KEY:
        # Last-resort fallback — sirf tab try hota hai jab SAARE NVIDIA models
        # (upar wali chain) fail/quota-exhaust ho chuke hon.
        fallback_fns.append(make_groq_attempt("llama-3.3-70b-versatile"))
        fallback_fns.append(make_groq_attempt("llama-3.1-8b-instant"))

    # Return 3 values (output, metadata, suggestions)
    return call_with_fallback(nvidia_attempt, fallback_fns=fallback_fns)