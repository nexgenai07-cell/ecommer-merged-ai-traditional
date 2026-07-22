# PATH: apps/ai/tools/faq_tools.py

# FLOW: registry.py ke answer_faq tool se yahan aata hai. Ye RAG
# (Retrieval-Augmented Generation) pattern hai — AI khud jawab nahi
# banata, pehle Qdrant se relevant FAQ chunks nikalta hai, phir
# unhi ke basis pe Agent (LLM) jawab likhta hai.

import requests
from django.conf import settings
from qdrant_client import QdrantClient

from apps.ai.gemini_utils import gemini_keys, call_with_fallback

FAQ_COLLECTION = "faq_knowledge"     # FLOW: ye collection apps/ai/faq_indexing.py se admin panel ke through fill hoti hai


def _get_qdrant_client():
    return QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)


def _get_faq_query_embedding(text):
    """FLOW: product_tools.py ke get_query_embedding() jaisa hi pattern —
    query ko vector banata hai, taskType='RETRIEVAL_QUERY' se."""
    """taskType='RETRIEVAL_QUERY' — retry/fallback ke sath."""
    from apps.ai.gemini_utils import call_with_fallback

    def attempt():
        response = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={gemini_keys.current_key}',
            json={
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_QUERY"
            }
        )
        result = response.json()
        if 'error' in result:
            raise Exception(f"Embedding error: {result['error']['message']}")
        return result['embedding']['values']

    return call_with_fallback(attempt)


def answer_faq_tool(query: str, limit: int = 3) -> dict:

    """
    FLOW: registry.py se call hota hai.
    Flow: customer ka sawal → embedding banti hai (upar wala function) →
    Qdrant ki faq_knowledge collection search hoti hai → matching
    Q&A pairs milte hain → ye poora list wapis Agent ko jata hai →
    Agent inhi answers ke basis pe apna jawab likhta hai (khud se
    policy invent nahi karta — is liye RAG kehte hain isay).
    """

    """
    TOOL: answer_faq_tool

    Store ki FAQ knowledge base (shipping, returns, payments, orders,
    account policies) mein customer ke sawal se semantic search karta hai.
    Use this for policy/general questions — NOT for live product search,
    prices, or order-specific tracking (those have their own tools).

    Args:
        query: Customer ka sawal — jaise "return kaise karun" ya "cash on delivery hai?"
        limit: Kitne relevant FAQ entries chahiye (default 3)

    Returns:
        dict with matching FAQ entries (question + answer pairs)
    """
    try:
        qdrant = _get_qdrant_client()
        query_vector = _get_faq_query_embedding(query)

        # FLOW: ye admin ke upload kiye hue FAQ PDF se bane chunks search karta hai
        
        search_response = qdrant.query_points(
            collection_name=FAQ_COLLECTION,
            query=query_vector,
            limit=limit,
            score_threshold=0.3,
            with_payload=True,
        )

        results = [
            {
                'question': r.payload['question'],
                'answer': r.payload['answer'],
                'source_document': r.payload.get('document_title'),
                'relevance_score': round(r.score, 3),
            }
            for r in search_response.points
        ]

        return {
            'success': True,
            'results': results,
            'found': len(results),
        }

    except Exception as e:
        return {'success': False, 'error': str(e), 'results': [], 'found': 0}