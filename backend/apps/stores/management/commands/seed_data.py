"""
PATH: apps/stores/management/commands/seed_data.py

Seeds ONLY categories and products (with images).
Run with:  python manage.py seed_data
Optional:  python manage.py seed_data --clear
           (soft-deletes existing products and categories first)

Requires an existing Store and admin user. Creates no users or customers.
Safe to run multiple times: uses get_or_create.
"""

from decimal import Decimal

import cloudinary.uploader
from cloudinary import CloudinaryImage
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.categories.models import Category
from apps.products.models import Product, ProductImage
from apps.stores.models import Store
from apps.users.models import User


def p(price):
    return Decimal(str(price))


def upload_stock_photo(seed, folder):
    """
    Uploads a stock photo to Cloudinary and returns a CloudinaryImage.
    Returns None (and prints a warning) on failure, so one bad image
    never breaks the whole seed run.
    """
    try:
        source_url = f"https://picsum.photos/seed/{slugify(seed)}/600/600"
        result = cloudinary.uploader.upload(
            source_url,
            folder=folder,
            public_id=slugify(seed),
            overwrite=True,
            unique_filename=False,
        )
        return CloudinaryImage(
            result['public_id'],
            format=result.get('format'),
            version=result.get('version'),
        )
    except Exception as e:
        print(f"  [!] image upload failed for '{seed}': {e}")
        return None


# Each product: (name, price, original_price, stock, sku, description)
CATEGORIES = [
    {
        "name": "Jewellery",
        "description": "Necklaces, earrings, bangles and bridal jewellery sets",
        "products": [
            ("Kundan Bridal Necklace Set", 45999, 54999, 12, "JWL-KND-BRD01", "Traditional kundan necklace with matching earrings and tikka. Gold-plated, ideal for weddings."),
            ("Pearl Drop Earrings", 3499, 4500, 60, "JWL-PRL-ERR02", "Freshwater pearl drops on sterling silver hooks. Lightweight and elegant for daily wear."),
            ("Gold Plated Jhumka Earrings", 2999, 3999, 70, "JWL-JHM-GLD03", "Handcrafted gold plated jhumkas with fine filigree work and pearl beads."),
            ("Antique Bangles Set of 6", 5499, 6999, 40, "JWL-BNG-ANT04", "Set of six antique-finish bangles with meenakari detailing. Adjustable sizes available."),
            ("Layered Chain Necklace", 2499, 3299, 55, "JWL-LYR-CHN05", "Double layer gold-tone chain with tiny crystal charms. Anti-tarnish coating."),
            ("Emerald Green Choker Set", 8999, 11999, 20, "JWL-CHK-EMR06", "Choker necklace with green stones and matching earrings, perfect for mehndi and nikkah events."),
        ],
    },
    {
        "name": "Cosmetics",
        "description": "Makeup, lipsticks, eye products and beauty essentials",
        "products": [
            ("Matte Liquid Lipstick Nude Rose", 1499, 1999, 90, "COS-LIP-NDR01", "Long-wear matte liquid lipstick in a soft nude rose shade. Smudge-proof for up to 12 hours."),
            ("Waterproof Kajal Pencil", 599, 799, 150, "COS-KJL-WTR02", "Intense black waterproof kajal with a creamy, smooth glide. Smudge resistant."),
            ("Eyeshadow Palette 18 Shades", 3299, 4199, 45, "COS-EYE-PAL03", "Mix of matte and shimmer shades in warm and neutral tones. Highly pigmented."),
            ("HD Compact Powder", 1199, 1599, 80, "COS-CMP-HD04", "Oil control compact powder with a natural matte finish and SPF 15."),
            ("Volumizing Mascara", 1299, 1699, 85, "COS-MSC-VOL05", "Lengthening and volumizing mascara with a curved brush. Clump-free formula."),
            ("Makeup Brush Set 12 Pcs", 2799, 3599, 50, "COS-BRS-12PC06", "Soft synthetic bristles brush set with a travel pouch. Includes face and eye brushes."),
        ],
    },
    {
        "name": "Watches",
        "description": "Analog, digital and smart watches for men and women",
        "products": [
            ("Rose Gold Ladies Analog Watch", 6999, 8999, 30, "WTC-LDY-RSG01", "Slim rose gold case with mesh strap and a crystal-studded dial. Water resistant."),
            ("Classic Leather Strap Men's Watch", 7999, 9999, 35, "WTC-MEN-LTH02", "Minimalist dial, genuine leather strap, date display and scratch-resistant glass."),
            ("Smart Watch Pro Fitness Tracker", 12999, 16999, 25, "WTC-SMT-PRO03", "1.8-inch AMOLED display, heart rate and SpO2 monitor, 7-day battery, IP68."),
            ("Stainless Steel Chronograph", 14999, 18999, 18, "WTC-CHR-STL04", "Stainless steel chronograph with three sub-dials, luminous hands, 50m water resistance."),
            ("Couple Watch Set Black and Silver", 9999, 12999, 22, "WTC-CPL-SET05", "Matching pair for him and her, stainless steel bands with a gift box."),
            ("Kids Digital Sports Watch", 1999, 2599, 70, "WTC-KID-DGT06", "Colourful digital watch with backlight, stopwatch and an alarm. Shock resistant."),
        ],
    },
    {
        "name": "Rings",
        "description": "Diamond, gemstone, silver and adjustable rings",
        "products": [
            ("Sterling Silver Solitaire Ring", 4999, 6499, 40, "RNG-SLV-SOL01", "925 sterling silver ring with a cubic zirconia solitaire. Rhodium plated."),
            ("Gold Plated Adjustable Ring", 1499, 1999, 90, "RNG-GLD-ADJ02", "Adjustable open band ring with delicate leaf design. Fits most sizes."),
            ("Turquoise Firoza Stone Ring", 5999, 7499, 30, "RNG-FRZ-STN03", "Natural firoza stone set in oxidized silver, traditional design."),
            ("Rose Gold Couple Rings Pair", 3999, 4999, 45, "RNG-CPL-RSG04", "Matching promise rings in rose gold plating with a polished finish."),
            ("Emerald Cut Engagement Ring", 24999, 29999, 10, "RNG-ENG-EMR05", "Emerald cut stone with side accents, 18k gold plated silver base. Comes in a velvet box."),
            ("Stackable Ring Set of 5", 2299, 2999, 65, "RNG-STK-SET06", "Five thin stackable rings in mixed gold and silver tones, mix and match daily."),
        ],
    },
    {
        "name": "Abayas",
        "description": "Modern, embroidered and everyday abayas and jilbabs",
        "products": [
            ("Nida Black Open Abaya", 5999, 7499, 50, "ABY-NDA-BLK01", "Premium nida fabric open front abaya with a belt. Lightweight, wrinkle resistant."),
            ("Embroidered Butterfly Abaya", 8999, 11499, 30, "ABY-EMB-BTF02", "Butterfly cut abaya with hand embroidered sleeves and cuffs. Comes with a matching scarf."),
            ("Dubai Style Kaftan Abaya", 7499, 9499, 35, "ABY-DXB-KFT03", "Loose kaftan fit with wide sleeves in soft crepe. Available in navy and black."),
            ("Pearl Detail Formal Abaya", 12999, 15999, 20, "ABY-PRL-FRM04", "Formal abaya with pearl trimmed sleeves and front panel. Ideal for events and Eid."),
            ("Everyday Zipper Abaya", 3999, 4999, 80, "ABY-ZIP-DLY05", "Simple front zipper abaya in breathable fabric, comfortable for daily wear."),
            ("Kids Girls Abaya Set", 2999, 3799, 45, "ABY-KID-GRL06", "Girls abaya with a matching hijab. Soft fabric, sizes for ages 5 to 12."),
        ],
    },
    {
        "name": "Hijabs & Scarves",
        "description": "Chiffon, jersey and silk hijabs, scarves and underscarves",
        "products": [
            ("Premium Chiffon Hijab", 1299, 1699, 120, "HJB-CHF-PRM01", "Soft georgette chiffon hijab, 180x75cm, easy to drape in 20+ colours."),
            ("Instant Jersey Hijab", 999, 1299, 140, "HJB-JRS-INS02", "Ready to wear slip-on jersey hijab, breathable and stretchable, no pins needed."),
            ("Pure Silk Printed Scarf", 3499, 4499, 40, "HJB-SLK-PRT03", "Pure silk scarf with a floral print and hand rolled edges. Large square size."),
            ("Cotton Underscarf Cap Pack of 3", 899, 1199, 160, "HJB-CAP-3PK04", "Anti-slip cotton caps in black, white and beige. Keep the hijab firmly in place."),
            ("Lawn Printed Dupatta Hijab", 1599, 2099, 75, "HJB-LWN-DUP05", "Lightweight lawn hijab with digital print, ideal for summer."),
            ("Hijab Pins and Magnets Set", 799, 1099, 200, "HJB-PIN-SET06", "Set of 40 pearl head pins plus 6 magnetic hijab pins, no fabric damage."),
        ],
    },
    {
        "name": "Perfumes & Attar",
        "description": "Eau de parfum, oud, musk and traditional attar oils",
        "products": [
            ("Royal Oud Eau de Parfum 100ml", 8999, 11499, 30, "PRF-OUD-100M01", "Rich woody fragrance with oud, amber and sandalwood notes. Long lasting."),
            ("Rose Musk Attar 12ml", 1999, 2599, 80, "PRF-ROS-ATR02", "Alcohol-free attar oil with Taif rose and white musk. Roll-on bottle."),
            ("Floral Bloom Women's Perfume 50ml", 4999, 6499, 45, "PRF-FLR-BLM03", "Fresh floral scent with jasmine, peony and vanilla. Everyday wear."),
            ("Sport Fresh Men's Body Spray", 1299, 1699, 100, "PRF-SPT-FRS04", "Citrus and mint body spray for a lasting freshness after workouts."),
            ("Bakhoor Incense Gift Box", 2999, 3799, 55, "PRF-BKH-GFT05", "Assorted bakhoor chips in a wooden gift box. Includes a small burner."),
            ("Mini Perfume Travel Set of 4", 3499, 4499, 60, "PRF-MNI-TRV06", "Four 15ml travel-size fragrances, two for her and two for him."),
        ],
    },
    {
        "name": "Handbags & Clutches",
        "description": "Tote bags, shoulder bags, wallets and bridal clutches",
        "products": [
            ("Faux Leather Tote Bag", 6999, 8999, 35, "BAG-TOT-LTH01", "Spacious tote with an inner zip pocket and a laptop sleeve. Sturdy metal hardware."),
            ("Embroidered Bridal Clutch", 3999, 5199, 40, "BAG-CLT-BRD02", "Handmade clutch with zardozi work and a detachable chain strap."),
            ("Mini Crossbody Sling Bag", 3499, 4499, 60, "BAG-SLG-MNI03", "Compact crossbody bag with an adjustable strap, holds phone, cards and keys."),
            ("Women's Long Zip Wallet", 2299, 2999, 85, "BAG-WLT-ZIP04", "Long wallet with 12 card slots, a coin pocket and a phone compartment."),
            ("Quilted Shoulder Bag", 7999, 9999, 28, "BAG-QLT-SHD05", "Classic quilted design with a gold chain strap and a magnetic flap closure."),
            ("Backpack Style Ladies Bag", 5499, 6999, 38, "BAG-BKP-LDY06", "Water resistant fashion backpack with multiple compartments and padded straps."),
        ],
    },
    {
        "name": "Sunglasses & Eyewear",
        "description": "Sunglasses, blue light glasses and eyewear accessories",
        "products": [
            ("Oversized Cat Eye Sunglasses", 2999, 3999, 70, "EYE-CAT-OVR01", "Trendy oversized cat eye frame with UV400 protection lenses."),
            ("Polarized Aviator Sunglasses", 4499, 5799, 50, "EYE-AVT-POL02", "Metal aviator frame with polarized lenses that reduce glare while driving."),
            ("Blue Light Blocking Glasses", 2499, 3299, 65, "EYE-BLU-BLK03", "Anti blue light computer glasses to reduce eye strain, lightweight frame."),
            ("Round Retro Sunglasses", 2799, 3599, 60, "EYE-RND-RTR04", "Vintage round frame with tinted lenses, unisex design, UV400."),
            ("Eyeglasses Hard Case with Cloth", 699, 999, 150, "EYE-CSE-HRD05", "Protective hard shell case with a microfiber cleaning cloth included."),
            ("Kids Flexible Sunglasses", 1499, 1999, 75, "EYE-KID-FLX06", "Bendable rubber frame sunglasses with UV protection for ages 3 to 8."),
        ],
    },
    {
        "name": "Hair Accessories",
        "description": "Clips, scrunchies, headbands and bridal hair pieces",
        "products": [
            ("Pearl Hair Clip Set of 6", 1299, 1699, 110, "HAC-PRL-CLP01", "Six pearl embellished snap clips in assorted sizes for styling."),
            ("Silk Scrunchies Pack of 5", 999, 1399, 130, "HAC-SLK-SCR02", "Soft satin scrunchies that are gentle on hair and prevent breakage."),
            ("Bridal Hair Brooch Jooda Pin", 3499, 4499, 35, "HAC-BRD-JDA03", "Decorative jooda pin with stones and pearls, for bridal and party hairstyles."),
            ("Velvet Padded Headband", 1199, 1599, 90, "HAC-VLV-HDB04", "Wide padded velvet headband, comfortable all day, available in 6 colours."),
            ("Claw Clip Set Matte Finish", 1099, 1499, 100, "HAC-CLW-MAT05", "Set of 4 large matte claw clips with a strong grip for thick hair."),
            ("Hair Bun Maker and Pins Kit", 899, 1199, 120, "HAC-BUN-KIT06", "Foam bun maker with 20 hair pins for quick, neat updos."),
        ],
    },
]


class Command(BaseCommand):
    help = 'Seeds categories and products (with images). Does not create users or customers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Soft-delete existing products and categories before seeding',
        )

    @transaction.atomic
    def handle(self, *args, **options):

        if options['clear']:
            self.stdout.write(self.style.WARNING('Soft-deleting existing products and categories...'))
            Product.objects.update(is_delete=True, is_active=False)
            Category.objects.update(is_delete=True, is_active=False)
            self.stdout.write(self.style.SUCCESS('Old products and categories cleared.'))

        # ── Store and admin must already exist ────────────────────────────
        store = Store.objects.first()
        if not store:
            self.stdout.write(self.style.ERROR('No store found!'))
            return
        self.stdout.write(f'Using store: {store.name}')

        if not User.objects.filter(role='admin').exists():
            self.stdout.write(self.style.ERROR('No admin user found!'))
            return

        # ── Categories ────────────────────────────────────────────────────
        self.stdout.write('Creating categories...')
        category_objs = {}
        cat_images_added = 0

        for cat_data in CATEGORIES:
            cat, created = Category.objects.get_or_create(
                name=cat_data['name'],
                store=store,
                defaults={
                    'description': cat_data['description'],
                    'is_active': True,
                    'is_delete': False,
                },
            )

            # Revive the category if an earlier --clear soft-deleted it
            if not created and (not cat.is_active or cat.is_delete):
                cat.is_active = True
                cat.is_delete = False
                cat.save(update_fields=['is_active', 'is_delete'])

            image = upload_stock_photo(cat_data['name'], folder='categories')
            if image:
                cat.image = image
                cat.save(update_fields=['image'])
                cat_images_added += 1

            category_objs[cat_data['name']] = cat

        self.stdout.write(self.style.SUCCESS(
            f'  {len(category_objs)} categories ready ({cat_images_added} images uploaded)'
        ))

        # ── Products ──────────────────────────────────────────────────────
        self.stdout.write('Creating products...')
        new_count = 0
        total = 0
        images_added = 0

        for cat_data in CATEGORIES:
            cat_obj = category_objs[cat_data['name']]

            for name, price, original_price, stock, sku, description in cat_data['products']:
                prod, created = Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        'store': store,
                        'category': cat_obj,
                        'name': name,
                        'description': description,
                        'price': p(price),
                        'original_price': p(original_price),
                        'stock': stock,
                        'low_stock_threshold': 5,
                        'is_active': True,
                    },
                )
                if created:
                    new_count += 1
                elif not prod.is_active or prod.is_delete:
                    prod.is_active = True
                    prod.is_delete = False
                    prod.save(update_fields=['is_active', 'is_delete'])

                total += 1

                image = upload_stock_photo(sku, folder='products')
                if image:
                    prod.images.all().delete()
                    ProductImage.objects.create(product=prod, image=image, is_primary=True)
                    images_added += 1

        self.stdout.write(self.style.SUCCESS(
            f'  {new_count} new products created ({total} total, {images_added} images uploaded)'
        ))

        # ── Summary ───────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Seeding complete!'))
        self.stdout.write(f'  Categories: {Category.objects.filter(is_delete=False).count()}')
        self.stdout.write(f'  Products:   {Product.objects.filter(is_delete=False).count()}')