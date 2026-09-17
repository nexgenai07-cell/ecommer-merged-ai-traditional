import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.development')

import django
django.setup()

from django.conf import settings

print("Testing cloud connections...\n")

# Test 1: Qdrant Cloud
try:
    from qdrant_client import QdrantClient
    client = QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
    )
    collections = client.get_collections()
    print(f"✅ Qdrant Cloud connected!")
    print(f"   Collections: {collections}\n")
except Exception as e:
    print(f"❌ Qdrant failed: {e}\n")

# Test 2: Redis Upstash
try:
    import redis
    r = redis.from_url(settings.REDIS_URL)
    r.set('test_key', 'hello')
    value = r.get('test_key')
    print(f"✅ Redis Upstash connected!")
    print(f"   Test value: {value}\n")
except Exception as e:
    print(f"❌ Redis failed: {e}\n")

# Test 3: Gemini (new package)
try:
    from google import genai
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='Say hello in one word only'
    )
    print(f"✅ Gemini connected!")
    print(f"   Response: {response.text}\n")
except Exception as e:
    print(f"❌ Gemini failed: {e}\n")