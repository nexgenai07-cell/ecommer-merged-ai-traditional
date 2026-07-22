# PATH: apps/stores/management/commands/index_products.py
#
# Ye command products ko Qdrant mein embed karke store karti hai.
# Ek baar chalao — phir naye products add hone pe dobara chalao.
#
# Run with: python manage.py index_products

import requests
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.products.models import Product
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from apps.ai.gemini_utils import gemini_keys, is_quota_error


def get_embedding(text):
    """Gemini gemini-embedding-001 se vector banata hai — retry/fallback ke sath."""
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
            raise Exception(f"Gemini API error: {result['error']['message']}")
        return result['embedding']['values']

    return call_with_fallback(attempt)


def get_primary_image_url(product):
    """
    NEW — product ki primary image ka URL nikalta hai (relative path,
    jaise /media/products/xyz.jpg). Ye search results/product cards mein
    dikhane k liye chahiye (PDF requirement — Product, Price, Stock, Image).

    Note: URL relative hai — agar frontend/WhatsApp ko full URL chahiye,
    domain prefix wahan add karna hoga (jaise settings.SITE_URL + is URL).
    """
    img = product.primary_image
    return img.image.url if img and img.image else None


class Command(BaseCommand):
    help = 'Embeds all active products and stores them in Qdrant for semantic search.'

    def handle(self, *args, **options):

        self.stdout.write('Connecting to Qdrant...')
        qdrant = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            timeout=60,
        )
        self.stdout.write(self.style.SUCCESS('Qdrant connected.'))

        COLLECTION = settings.QDRANT_COLLECTION
        VECTOR_SIZE = 3072

        existing = [c.name for c in qdrant.get_collections().collections]

        if COLLECTION not in existing:
            self.stdout.write(f'Creating collection "{COLLECTION}"...')
            qdrant.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            self.stdout.write(self.style.SUCCESS(f'Collection "{COLLECTION}" created.'))
        else:
            self.stdout.write(f'Collection "{COLLECTION}" already exists — will upsert.')

        products = Product.objects.filter(is_active=True).select_related('category')
        total = products.count()
        self.stdout.write(f'Found {total} active products to index...\n')

        if total == 0:
            self.stdout.write(self.style.WARNING('No products found. Run seed_data first.'))
            return

        points = []
        failed = []

        for idx, product in enumerate(products, 1):

            text_to_embed = f"""
Product: {product.name}
Category: {product.category.name if product.category else 'Uncategorized'}
Description: {product.description or ''}
Price: Rs. {product.price}
In Stock: {'Yes' if product.stock > 0 else 'No'}
SKU: {product.sku}
""".strip()

            try:
                vector = get_embedding(text_to_embed)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  [{idx}/{total}] FAILED: {product.name} — {e}'))
                failed.append(product.name)
                continue

            payload = {
                'product_id':     product.id,
                'name':           product.name,
                'category':       product.category.name if product.category else None,
                'category_id':    product.category.id if product.category else None,
                'price':          float(product.price),
                'original_price': float(product.original_price) if product.original_price else None,
                'stock':          product.stock,
                'in_stock':       product.stock > 0,
                'sku':            product.sku,
                'description':    (product.description or '')[:200],
                'image':          get_primary_image_url(product),  # NEW
            }

            points.append(PointStruct(
                id=product.id,
                vector=vector,
                payload=payload,
            ))

            self.stdout.write(f'  [{idx}/{total}] ✓ {product.name}')

        if points:
            self.stdout.write('\nUploading to Qdrant...')
            BATCH_SIZE = 25
            for i in range(0, len(points), BATCH_SIZE):
                batch = points[i:i + BATCH_SIZE]
                qdrant.upsert(collection_name=COLLECTION, points=batch)
                self.stdout.write(f'  Uploaded batch {i // BATCH_SIZE + 1} ({len(batch)} points)')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Indexing complete!'))
        self.stdout.write(self.style.SUCCESS(f'  Indexed:  {len(points)} products'))
        if failed:
            self.stdout.write(self.style.WARNING(f'  Failed:   {len(failed)} products'))
            for name in failed:
                self.stdout.write(self.style.WARNING(f'    - {name}'))
        self.stdout.write(self.style.SUCCESS(f'  Collection: {COLLECTION}'))
        self.stdout.write(self.style.SUCCESS('=' * 50))