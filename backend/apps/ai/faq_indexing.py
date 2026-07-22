# PATH: apps/ai/faq_indexing.py
#
# Shared FAQ indexing logic. Admin panel action aur management command
# dono isi function ko call karte hain — taake logic ek hi jagah rahe.
#
# Design: Har KnowledgeDocument ke Qdrant point IDs uske database ID se
# derive hote hain (document.id * 100000 + chunk_index), taake:
#   - Re-index karne pe purane chunks automatically overwrite ho jayein
#   - Document delete/deactivate hone pe uske sirf apne chunks delete ho sakein

import re
import pdfplumber
import requests
from django.conf import settings
from django.utils import timezone
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, PayloadSchemaType,
)

from apps.ai.gemini_utils import gemini_keys, is_quota_error

FAQ_COLLECTION = "faq_knowledge"
MAX_CHUNKS_PER_DOC = 100_000  # point-id scheme ki safety limit


def _get_qdrant_client():
    return QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, timeout=60)


def _ensure_collection(qdrant):
    existing = [c.name for c in qdrant.get_collections().collections]
    if FAQ_COLLECTION not in existing:
        qdrant.create_collection(
            collection_name=FAQ_COLLECTION,
            vectors_config=VectorParams(size=3072, distance=Distance.COSINE),
        )
    
    # Yeh line ab if condition se BAHAR hai, taake agar collection pehle se ho tab bhi chalay
    try:
        qdrant.create_payload_index(
            collection_name=FAQ_COLLECTION,
            field_name="document_id",
            field_schema=PayloadSchemaType.INTEGER,
        )
    except Exception:
        # Agar index pehle se bana hua ho toh error ignore kar de
        pass


def _get_faq_embedding(text):
    """taskType='RETRIEVAL_DOCUMENT' — retry/fallback ke sath."""
    from apps.ai.gemini_utils import call_with_fallback

    def attempt():
        response = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={gemini_keys.current_key}',
            json={
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_DOCUMENT"
            }
        )
        result = response.json()
        if 'error' in result:
            raise Exception(f"Embedding error: {result['error']['message']}")
        return result['embedding']['values']

    return call_with_fallback(attempt)

def _extract_qa_chunks(pdf_path):
    """
    PDF se text nikal kar 'Q: ... A: ...' blocks mein split karta hai.
    Agar PDF is format mein nahi hai (admin ne koi aur tarah ka document
    upload kiya), fallback ke tor par paragraph-based split karta hai.
    """
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                full_text += page_text + "\n"

    pattern = re.compile(r'Q:\s*(.+?)\s*A:\s*(.+?)(?=Q:|$)', re.DOTALL)
    matches = pattern.findall(full_text)

    chunks = []
    if matches:
        for question, answer in matches:
            question, answer = question.strip(), answer.strip()
            if question and answer:
                chunks.append({
                    'question': question,
                    'answer': answer,
                    'text': f"Q: {question}\nA: {answer}",
                })
    else:
        # Fallback — Q:/A: format nahi mila, paragraphs ko chunks banao
        paragraphs = [p.strip() for p in full_text.split('\n\n') if p.strip()]
        for para in paragraphs:
            chunks.append({
                'question': para[:80],
                'answer': para,
                'text': para,
            })

    return chunks


def remove_document_from_index(document):
    """Is document ke sare chunks Qdrant se hata deta hai (deactivate/delete pe use hota hai)."""
    qdrant = _get_qdrant_client()
    _ensure_collection(qdrant)

    qdrant.delete(
        collection_name=FAQ_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document.id))]
        ),
    )

    document.is_indexed = False
    document.chunk_count = 0
    document.indexed_at = None
    document.save(update_fields=['is_indexed', 'chunk_count', 'indexed_at'])


def index_document(document):
    """
    Ek KnowledgeDocument ko chunk + embed + Qdrant mein upsert karta hai.
    Pehle is document ke purane chunks hata deta hai (agar re-indexing ho
    rahi ho), phir naye chunks daalta hai — taake edits/duplicates na ho.

    Returns: (success: bool, message: str)
    """
    qdrant = _get_qdrant_client()
    _ensure_collection(qdrant)

    # Purane chunks (agar re-index ho rahi hai) pehle hata dete hain
    qdrant.delete(
        collection_name=FAQ_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document.id))]
        ),
    )

    try:
        chunks = _extract_qa_chunks(document.file.path)
    except Exception as e:
        return False, f"Could not read PDF: {e}"

    if not chunks:
        return False, "No content found in the document."

    if len(chunks) > MAX_CHUNKS_PER_DOC:
        return False, f"Document has too many chunks ({len(chunks)}). Split it into smaller documents."

    points = []
    failed = 0

    for idx, chunk in enumerate(chunks, 1):
        try:
            vector = _get_faq_embedding(chunk['text'])
        except Exception:
            failed += 1
            continue

        points.append(PointStruct(
            id=document.id * MAX_CHUNKS_PER_DOC + idx,
            vector=vector,
            payload={
                'document_id': document.id,
                'document_title': document.title,
                'question': chunk['question'],
                'answer': chunk['answer'],
            },
        ))

    if points:
        BATCH_SIZE = 25
        for i in range(0, len(points), BATCH_SIZE):
            qdrant.upsert(collection_name=FAQ_COLLECTION, points=points[i:i + BATCH_SIZE])

    document.is_indexed = True
    document.chunk_count = len(points)
    document.indexed_at = timezone.now()
    document.save(update_fields=['is_indexed', 'chunk_count', 'indexed_at'])

    msg = f"Indexed {len(points)} chunks."
    if failed:
        msg += f" ({failed} chunks failed to embed.)"
    return True, msg