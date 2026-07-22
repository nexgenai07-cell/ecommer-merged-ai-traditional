# PATH: apps/ai/agents/shopping_agent.py

# FLOW: apps/ai/consumers.py ke get_agent_response() se yahan aata hai.
# Ye file LangChain Agent banati hai — jo tools decide karta hai ke
# customer ke message ka jawab dene ke liye kaunsa tool call karna hai.

from django.conf import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from apps.ai.tools.registry import SHOPPING_AGENT_TOOLS     # FLOW → apps/ai/tools/registry.py
from apps.ai.tools.cart_order_tools import get_cart_order_tools      # FLOW → apps/ai/tools/cart_order_tools.py
from apps.ai.gemini_utils import gemini_keys, call_with_fallback    # FLOW → apps/ai/gemini_utils.py


SYSTEM_PROMPT = """You are a warm, proactive, and highly engaging shopping assistant
for an e-commerce store. Prices are in Pakistani Rupees (Rs.). You work for
BOTH anonymous (guest) and logged-in customers — never assume login is required
just to chat, search, or add things to cart.

You have access to the recent conversation history — use it. If the customer
already told you something earlier in this chat (their preference, occasion,
budget, name, phone, etc.), don't ask again — just use it.

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

5. KEEP THE CONVERSATION GOING — always end with a natural next step. Only
   stop this pattern if the customer clearly says they're done.

6. CART & ORDERS:
   - Use add_to_cart when the customer clearly wants to buy/add a specific product.
   - Use create_order when the customer wants to checkout/place their order.
     - GUEST CHECKOUT IS ALLOWED: collect name, phone, and shipping address first.
     - If logged in, you only need the shipping address.
   - Use track_order / cancel_order only when the customer gives you their
     order number. These two require the customer to be logged in.

7. FAQ / POLICY QUESTIONS: Use the answer_faq tool for policy questions.
   Base your answer strictly on what it returns.

8. Never make up product names, prices, stock, or order details — always
   base your answer on what the tools actually return.

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
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)

    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=settings.DEBUG,
        return_intermediate_steps=True,
    )


def run_shopping_agent(user_input: str, session_key: str, user=None, chat_history=None, customer_context: str = ""):
    from apps.ai.response_metadata import extract_product_metadata      # FLOW → apps/ai/response_metadata.py

    chat_history = chat_history or []

    def gemini_attempt():
        llm = ChatGoogleGenerativeAI(
            model="gemini-3.5-flash",
            google_api_key=gemini_keys.current_key,
            temperature=0.4,
            max_retries=1,
        )
        executor = _build_executor(llm, session_key, user)

        # FLOW: YAHAN LLM ASAL MEIN CALL HOTA HAI — Gemini decide karta
        # hai kaunsa tool call karna hai (jaise search_products,
        # add_to_cart, ya seedha jawab de dena bina tool ke)

        result = executor.invoke({
            "input": user_input,
            "chat_history": chat_history,
            "customer_context": customer_context,
        })

        # FLOW: result["intermediate_steps"] mein har tool call ka record hai —
        # ye extract_product_metadata() ko diya jata hai

        return result["output"], extract_product_metadata(result.get("intermediate_steps", []))

    def make_groq_attempt(model_name):
        def attempt():
            llm = ChatGroq(model=model_name, groq_api_key=settings.GROQ_API_KEY, temperature=0.4)
            executor = _build_executor(llm, session_key, user)
            result = executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
                "customer_context": customer_context,
            })
            return result["output"], extract_product_metadata(result.get("intermediate_steps", []))
        return attempt

    fallback_fns = []
    if settings.GROQ_API_KEY:
        
        fallback_fns.append(make_groq_attempt("llama-3.3-70b-versatile"))
        fallback_fns.append(make_groq_attempt("llama-3.1-8b-instant"))

        # FLOW → apps/ai/gemini_utils.py — key rotation/retry/fallback yahan hota hai,
        # phir wapis (output, metadata) tuple deta hai — ye consumers.py mein jata hai

    return call_with_fallback(gemini_attempt, fallback_fns=fallback_fns)