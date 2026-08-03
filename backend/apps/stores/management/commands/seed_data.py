"""
PATH: apps/stores/management/commands/seed_data.py

Complete ecommerce seed script.
Run with: python manage.py seed_data

What it creates:
  - 1 Store (if not exists)
  - 1 Admin user (if not exists)
  - 5 Customer users
  - 12 Categories (Electronics, Clothing, Shoes, etc.)
  - 60 Products across all categories (with prices, stock, SKUs)
  - 4 Active discount codes
  - 5 Sample customer profiles
  - 8 Sample orders with items and payments
  - 3 Sample returns
  - 3 Sample complaints

Safe to run multiple times — uses get_or_create everywhere.
"""

import os
import random
from decimal import Decimal
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from requests import options

from apps.users.models import User
from apps.stores.models import Store
from apps.categories.models import Category
from apps.products.models import Product, ProductImage, Discount
from apps.orders.models import Customer, Order, OrderItem, Payment
from apps.returns.models import Return, Complaint


# ─── Helpers ────────────────────────────────────────────────────────────────

def p(price):
    return Decimal(str(price))


def pick(lst):
    return random.choice(lst)


# ─── Data ───────────────────────────────────────────────────────────────────

CATEGORIES = [
    {
        "name": "Electronics",
        "description": "Smartphones, laptops, tablets, and all digital gadgets",
        "products": [
            {"name": "Samsung Galaxy S24 Ultra", "price": 299999, "original_price": 329999, "stock": 25, "sku": "SAM-S24U-BLK", "description": "6.8-inch Dynamic AMOLED, 200MP camera, 5000mAh battery, S Pen included. The flagship Samsung experience."},
            {"name": "iPhone 15 Pro Max", "price": 399999, "original_price": 429999, "stock": 18, "sku": "APL-15PM-NTT", "description": "6.7-inch Super Retina XDR, A17 Pro chip, 48MP camera system, titanium design."},
            {"name": "Xiaomi Redmi Note 13 Pro", "price": 54999, "original_price": 62999, "stock": 60, "sku": "XMI-RN13P-BLU", "description": "6.67-inch AMOLED, 200MP camera, 5100mAh battery, 67W fast charging."},
            {"name": "Dell XPS 15 Laptop", "price": 389999, "original_price": 419999, "stock": 10, "sku": "DEL-XPS15-2024", "description": "15.6-inch 4K OLED, Intel Core i9, 32GB RAM, 1TB SSD, NVIDIA RTX 4070."},
            {"name": "HP Pavilion Gaming Laptop", "price": 159999, "original_price": 179999, "stock": 22, "sku": "HP-PAV-G15-AMD", "description": "15.6-inch FHD 144Hz, AMD Ryzen 7, 16GB RAM, 512GB SSD, RTX 3050."},
            {"name": "Apple iPad Pro 12.9-inch", "price": 249999, "original_price": 269999, "stock": 15, "sku": "APL-IPAD-PRO12", "description": "M2 chip, Liquid Retina XDR display, Face ID, USB-C with Thunderbolt."},
            {"name": "Sony WH-1000XM5 Headphones", "price": 79999, "original_price": 89999, "stock": 35, "sku": "SNY-WH1000XM5", "description": "Industry-leading noise cancellation, 30-hour battery life, crystal clear hands-free calling."},
            {"name": "JBL Charge 5 Speaker", "price": 29999, "original_price": 34999, "stock": 45, "sku": "JBL-CHG5-BLK", "description": "Portable Bluetooth speaker, IP67 waterproof, 20 hours playtime, built-in powerbank."},
            {"name": "Samsung 65-inch 4K Smart TV", "price": 199999, "original_price": 229999, "stock": 8, "sku": "SAM-TV65-4K", "description": "QLED 4K display, Tizen OS, HDR10+, built-in Alexa and Google Assistant."},
            {"name": "Canon EOS R50 Camera", "price": 149999, "original_price": 164999, "stock": 12, "sku": "CAN-EOSR50-KIT", "description": "24.2MP APS-C sensor, 4K video, dual pixel autofocus, lightweight mirrorless design."},
        ]
    },
    {
        "name": "Men's Clothing",
        "description": "Shirts, trousers, kurtas, jackets and formal wear for men",
        "products": [
            {"name": "Bonanza Satrangi Men's Kurta", "price": 3499, "original_price": 4500, "stock": 80, "sku": "BON-KRT-MN001", "description": "Premium lawn kurta with embroidered detailing. Available in comfortable summer fabric."},
            {"name": "Levi's 511 Slim Fit Jeans", "price": 8999, "original_price": 11000, "stock": 55, "sku": "LEV-511-32X30", "description": "Classic slim fit jeans in stretch denim. 5-pocket styling, button fly."},
            {"name": "Khaadi Men's Casual Shirt", "price": 2899, "original_price": 3800, "stock": 90, "sku": "KHD-CAS-SHT01", "description": "Pure cotton casual shirt with minimalist design, suitable for everyday wear."},
            {"name": "Men's Formal Suit (3 Piece)", "price": 15999, "original_price": 19999, "stock": 20, "sku": "FRM-SUIT-3PC", "description": "Premium polyester-wool blend, tailored fit. Includes coat, waistcoat and trousers."},
            {"name": "Nike Dri-FIT T-Shirt", "price": 3999, "original_price": 4999, "stock": 100, "sku": "NIK-DRF-TEE-M", "description": "Moisture-wicking fabric keeps you dry and comfortable during workouts."},
        ]
    },
    {
        "name": "Women's Clothing",
        "description": "Shalwar kameez, western wear, abayas and ethnic collections",
        "products": [
            {"name": "Gul Ahmed Stitched 3-Piece Suit", "price": 5999, "original_price": 7500, "stock": 65, "sku": "GA-3PC-SUM001", "description": "Premium lawn 3-piece with printed dupatta and trouser. Machine washable."},
            {"name": "Sapphire Women's Embroidered Kurta", "price": 4499, "original_price": 5999, "stock": 50, "sku": "SAP-EMB-KRT02", "description": "Floral embroidered kurta in viscose fabric. Semi-formal, ideal for gatherings."},
            {"name": "Zara Women's Midi Dress", "price": 9999, "original_price": 13999, "stock": 30, "sku": "ZAR-MDI-DRS01", "description": "Flowy midi dress in floral print, V-neck, ideal for brunches and casual outings."},
            {"name": "Limelight Printed Palazzo Set", "price": 3299, "original_price": 4200, "stock": 75, "sku": "LML-PLZ-SET01", "description": "Comfortable cotton palazzo set with matching top. Easy to wear all day."},
            {"name": "J. Junaid Jamshed Party Wear", "price": 12999, "original_price": 15999, "stock": 25, "sku": "JJ-PTY-WR001", "description": "Heavy embroidered chiffon suit, perfect for weddings and formal events."},
        ]
    },
    {
        "name": "Footwear",
        "description": "Shoes, sandals, sneakers and boots for men and women",
        "products": [
            {"name": "Nike Air Max 270", "price": 24999, "original_price": 29999, "stock": 40, "sku": "NIK-AM270-WHT", "description": "Lifestyle shoe with large Air unit for all-day comfort. Mesh upper for breathability."},
            {"name": "Adidas Ultraboost 22", "price": 27999, "original_price": 32999, "stock": 35, "sku": "ADI-UB22-BLK", "description": "Running shoe with BOOST midsole technology. Primeknit+ upper, supportive fit."},
            {"name": "Bata Formal Oxford Shoes", "price": 5999, "original_price": 7999, "stock": 60, "sku": "BAT-OXF-MN001", "description": "Classic leather oxford for office and formal occasions. Slip-resistant sole."},
            {"name": "Servis Women's Heeled Sandals", "price": 3499, "original_price": 4500, "stock": 70, "sku": "SRV-HEL-SND01", "description": "Block heel sandals with adjustable ankle strap. Comfortable for extended wear."},
            {"name": "Skechers Go Walk Arch Fit", "price": 14999, "original_price": 17999, "stock": 45, "sku": "SKC-GWK-AF001", "description": "Podiatrist-certified arch support, lightweight design, machine washable."},
        ]
    },
    {
        "name": "Home & Kitchen",
        "description": "Appliances, cookware, bedding and home decor",
        "products": [
            {"name": "Dawlance Refrigerator 20 CFT", "price": 119999, "original_price": 134999, "stock": 10, "sku": "DAW-REF-20CFT", "description": "Frost-free refrigerator, twin inverter technology, 5-star energy rating, glass shelves."},
            {"name": "Anex Stand Mixer", "price": 18999, "original_price": 22999, "stock": 20, "sku": "ANX-MXR-600W", "description": "600W motor, 6 speed settings, 5L stainless steel bowl, includes dough hook and whisk."},
            {"name": "Westpoint Microwave Oven 30L", "price": 24999, "original_price": 28999, "stock": 18, "sku": "WP-MWO-30L", "description": "30-litre capacity, 900W power, 10 power levels, child lock, auto-cook menus."},
            {"name": "Royal Comfort Bed Sheet Set", "price": 4999, "original_price": 6500, "stock": 50, "sku": "RC-BSS-KING01", "description": "1000 thread count Egyptian cotton, King size, includes 2 pillowcases and fitted sheet."},
            {"name": "Prestige Pressure Cooker 7L", "price": 6999, "original_price": 8999, "stock": 35, "sku": "PRE-PC-7L-SS", "description": "Stainless steel pressure cooker, induction compatible, safety valve, 5-year warranty."},
        ]
    },
    {
        "name": "Beauty & Personal Care",
        "description": "Skincare, makeup, haircare and grooming products",
        "products": [
            {"name": "L'Oreal Paris Revitalift Serum", "price": 3999, "original_price": 4999, "stock": 60, "sku": "LOR-RVL-SRM30", "description": "1.5% pure Hyaluronic Acid + Vitamin C serum, reduces wrinkles, plumps skin in 1 week."},
            {"name": "Maybelline Fit Me Foundation", "price": 1899, "original_price": 2500, "stock": 90, "sku": "MAY-FTM-FND120", "description": "Natural finish foundation, SPF 18, pore-minimizing, available in 20 shades."},
            {"name": "Dove Intensive Repair Shampoo", "price": 899, "original_price": 1100, "stock": 120, "sku": "DOV-SHP-700ML", "description": "700ml, Keratin Repair Actives, reduces breakage by up to 98%, for damaged hair."},
            {"name": "Gillette Mach3 Razor Set", "price": 2499, "original_price": 2999, "stock": 80, "sku": "GIL-M3-SET05", "description": "5 cartridges + handle, 3 Lubrastrip blades, flexible head for close shave."},
            {"name": "Neutrogena Hydro Boost Moisturizer", "price": 2799, "original_price": 3499, "stock": 55, "sku": "NEU-HB-MCR50", "description": "Water gel formula, Hyaluronic Acid, oil-free, non-comedogenic, 50ml."},
        ]
    },
    {
        "name": "Sports & Fitness",
        "description": "Exercise equipment, sportswear and outdoor gear",
        "products": [
            {"name": "PowerMax Treadmill TDM-100", "price": 79999, "original_price": 94999, "stock": 8, "sku": "PMX-TDM100", "description": "3HP motor, 1-12 km/h speed, 12 preset programs, LCD display, max user weight 120kg."},
            {"name": "Decathlon Yoga Mat 10mm", "price": 3999, "original_price": 4999, "stock": 75, "sku": "DCA-YGM-10MM", "description": "Non-slip surface, eco-friendly TPE material, carrying strap included, 183x61cm."},
            {"name": "Wilson Tennis Racket Pro", "price": 12999, "original_price": 15999, "stock": 25, "sku": "WLS-TEN-PRO", "description": "Carbon graphite frame, 100sq inch head, pre-strung, suitable for intermediate players."},
            {"name": "Everlast Boxing Gloves 16oz", "price": 7999, "original_price": 9999, "stock": 30, "sku": "EVL-BOX-16OZ", "description": "Leather palm, foam padding, hook-and-loop wrist closure, suitable for sparring."},
            {"name": "Speedo Swimming Goggles", "price": 2999, "original_price": 3999, "stock": 50, "sku": "SPD-GGL-UV", "description": "UV protection lenses, anti-fog coating, adjustable nose bridge, silicone seals."},
        ]
    },
    {
        "name": "Books & Stationery",
        "description": "Books, notebooks, office supplies and art materials",
        "products": [
            {"name": "Atomic Habits by James Clear", "price": 1499, "original_price": 1999, "stock": 100, "sku": "BK-ATOM-HAB", "description": "Bestselling self-improvement book. Learn to build good habits and break bad ones."},
            {"name": "The Alchemist by Paulo Coelho", "price": 999, "original_price": 1299, "stock": 80, "sku": "BK-ALCH-PCO", "description": "A magical story about following your dreams. Over 65 million copies sold worldwide."},
            {"name": "Moleskine Classic Notebook A5", "price": 2499, "original_price": 2999, "stock": 60, "sku": "MOL-NB-A5-BLK", "description": "Hardcover, 240 pages, elastic closure, ribbon bookmark, pocket at back."},
            {"name": "Staedtler Colour Pencils Set 48", "price": 1999, "original_price": 2499, "stock": 70, "sku": "STD-CP-48SET", "description": "48 vibrant colors, break-resistant core, ideal for sketching and coloring."},
            {"name": "Pilot G2 Gel Pen Pack (12)", "price": 999, "original_price": 1299, "stock": 150, "sku": "PLT-G2-12PK", "description": "Smooth gel ink, 0.7mm tip, retractable, smear and waterproof, 12 assorted colors."},
        ]
    },
    {
        "name": "Toys & Games",
        "description": "Educational toys, board games and outdoor play items",
        "products": [
            {"name": "LEGO City Police Station", "price": 15999, "original_price": 18999, "stock": 20, "sku": "LGO-CPS-60316", "description": "743-piece set, includes police station, truck and 5 minifigures. Age 6+."},
            {"name": "Monopoly Classic Board Game", "price": 4999, "original_price": 6499, "stock": 35, "sku": "MNP-CLS-BRD", "description": "The original property trading game. 2-6 players, ages 8 and up."},
            {"name": "Nerf Elite Disruptor Blaster", "price": 3499, "original_price": 4499, "stock": 45, "sku": "NRF-ELT-DSP", "description": "Fires up to 27m, 6-dart rotating drum, single fire and slam fire modes. Age 8+."},
            {"name": "Fisher-Price Baby Activity Gym", "price": 7999, "original_price": 9999, "stock": 25, "sku": "FP-ACT-GYM01", "description": "5 activity stations, music and lights, tummy-time mirror, detachable toys. Age 0-12m."},
            {"name": "Rubik's Cube 3x3", "price": 1499, "original_price": 1999, "stock": 80, "sku": "RBK-3X3-ORG", "description": "Original Rubik's Cube, smooth mechanism, vibrant colors, over 43 quintillion combinations."},
        ]
    },
    {
        "name": "Automotive",
        "description": "Car accessories, tools and vehicle maintenance products",
        "products": [
            {"name": "Michelin Tyre 185/65 R15", "price": 18999, "original_price": 21999, "stock": 30, "sku": "MCH-TYR-18565", "description": "All-season tyre, rated for 91T speed, fuel efficient, excellent wet grip."},
            {"name": "Meguiar's Car Care Kit", "price": 6999, "original_price": 8999, "stock": 40, "sku": "MEG-CCK-5PC", "description": "5-piece detailing kit: car wash, wax, glass cleaner, interior detailer and microfiber."},
            {"name": "Bosch Car Battery 45Ah", "price": 19999, "original_price": 23999, "stock": 15, "sku": "BSH-BAT-45AH", "description": "Maintenance-free, 12V 45Ah, 400A CCA, suitable for small to mid-size vehicles."},
            {"name": "Garmin DriveSmart GPS Navigator", "price": 34999, "original_price": 39999, "stock": 12, "sku": "GRM-GPS-DS65", "description": "6.95-inch touchscreen, lifetime maps, voice-activated navigation, traffic alerts."},
            {"name": "Universal Car Seat Cover Set", "price": 8999, "original_price": 11999, "stock": 50, "sku": "CSC-UNI-FULL", "description": "Full 9-piece set, premium leatherette, water-resistant, airbag compatible, universal fit."},
        ]
    },
    {
        "name": "Grocery & Food",
        "description": "Dry food, beverages, snacks and everyday grocery items",
        "products": [
            {"name": "Shan Biryani Masala (Pack of 6)", "price": 699, "original_price": 849, "stock": 200, "sku": "SHN-BRY-6PK", "description": "Authentic biryani spice mix, family recipe, 60g each pack. Makes 2kg rice."},
            {"name": "National Basmati Rice 5kg", "price": 1299, "original_price": 1599, "stock": 150, "sku": "NAT-RCE-5KG", "description": "Premium aged basmati, long grain, aromatic, sella variety. 5kg pack."},
            {"name": "Lipton Green Tea (100 bags)", "price": 899, "original_price": 1099, "stock": 180, "sku": "LPT-GTE-100", "description": "100 individually wrapped tea bags, natural antioxidants, light refreshing taste."},
            {"name": "Olpers Full Cream Milk 1.5L", "price": 399, "original_price": 449, "stock": 250, "sku": "OLP-FCM-15L", "description": "UHT full cream milk, 6 months shelf life, rich in calcium and vitamins."},
            {"name": "Sunridge Farm Mixed Nuts 500g", "price": 1999, "original_price": 2499, "stock": 100, "sku": "SRF-MNT-500", "description": "Premium mix of almonds, cashews, pistachios and walnuts. Unsalted, no preservatives."},
        ]
    },
    {
        "name": "Health & Wellness",
        "description": "Vitamins, supplements, medical devices and health monitors",
        "products": [
            {"name": "Centrum Men Multivitamin (60 tabs)", "price": 2999, "original_price": 3499, "stock": 70, "sku": "CTR-MEN-60T", "description": "Complete daily multivitamin for men, 22 essential nutrients, supports energy and immunity."},
            {"name": "Omron Blood Pressure Monitor", "price": 12999, "original_price": 14999, "stock": 25, "sku": "OMR-BPM-HEM", "description": "Upper arm type, clinically validated, stores 60 readings, irregular heartbeat detector."},
            {"name": "Dr. Morepen Glucometer Kit", "price": 4999, "original_price": 5999, "stock": 35, "sku": "DRM-GLU-KIT", "description": "Blood glucose monitor, 25 test strips + lancets included, 5-second results, 250 reading memory."},
            {"name": "Ensure Gold Nutrition Powder 400g", "price": 3499, "original_price": 3999, "stock": 50, "sku": "ENS-GLD-400", "description": "Complete balanced nutrition, 26 vitamins and minerals, high protein, vanilla flavour."},
            {"name": "Himalaya Ashwagandha Tablets (60)", "price": 1299, "original_price": 1599, "stock": 90, "sku": "HIM-ASH-60T", "description": "Pure ashwagandha root extract, reduces stress and anxiety, improves energy and stamina."},
        ]
    },
]

DISCOUNTS = [
    {
        "code": "WELCOME10",
        "type": "percent",
        "value": 10,
        "min_order_amount": 2000,
        "description": "10% off on your first order",
    },
    {
        "code": "SAVE500",
        "type": "fixed",
        "value": 500,
        "min_order_amount": 5000,
        "description": "Rs 500 flat off on orders above Rs 5000",
    },
    {
        "code": "EID25",
        "type": "percent",
        "value": 25,
        "min_order_amount": 10000,
        "description": "25% Eid special discount",
    },
    {
        "code": "FREESHIP",
        "type": "fixed",
        "value": 200,
        "min_order_amount": 1500,
        "description": "Rs 200 off (free shipping equivalent)",
    },
]

CUSTOMERS = [
    {"name": "Ayesha Siddiqui", "email": "ayesha.siddiqui@gmail.com", "phone": "03001234567"},
    {"name": "Muhammad Usman", "email": "m.usman.pk@gmail.com", "phone": "03219876543"},
    {"name": "Fatima Zahra", "email": "fatima.zahra.95@gmail.com", "phone": "03331112222"},
    {"name": "Ahmed Raza", "email": "ahmed.raza.official@gmail.com", "phone": "03114445566"},
    {"name": "Sana Malik", "email": "sana.malik.art@gmail.com", "phone": "03457778899"},
]


class Command(BaseCommand):
    help = 'Seeds the database with realistic ecommerce data — categories, products, customers, orders, discounts.'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Delete existing data before seeding (use with care!)')

    @transaction.atomic
    def handle(self, *args, **options):

      if options['clear']:
        self.stdout.write(self.style.WARNING('Clearing existing data...'))

        # Order data (can still be hard deleted because it has no soft delete)
        OrderItem.objects.all().delete()
        Order.objects.all().delete()
        Customer.objects.all().delete()

        # Soft delete Products
        Product.objects.update(
            is_delete=True,
            is_active=False,
        )

        # Soft delete Categories
        Category.objects.update(
            is_delete=True,
            is_active=False,
        )

        # Soft delete Discounts
        Discount.objects.update(
            is_delete=True,
            is_active=False,
        )

        # Soft delete Users (except superuser)
        User.objects.filter(
            is_superuser=False
        ).update(
            is_delete=True,
            is_active=False,
        )

        self.stdout.write(self.style.SUCCESS('Data cleared.'))

        # ── 1. Store ──────────────────────────────────────────────────────────
        store = Store.objects.first()
        if not store:
            self.stdout.write(self.style.ERROR('No store found! Run "python manage.py seed_store" first.'))
            return
        self.stdout.write(f'Using store: {store.name}')

        # ── 2. Admin user ─────────────────────────────────────────────────────
        admin = User.objects.filter(role='admin').first()
        if not admin:
            self.stdout.write(self.style.ERROR('No admin user found! Run "python manage.py seed_store" first.'))
            return

        # ── 3. Customer Users ─────────────────────────────────────────────────
        self.stdout.write('Creating customer users...')
        user_objs = []
        for idx, c in enumerate(CUSTOMERS):
            user, created = User.objects.get_or_create(
                email=c['email'],
                defaults={
                    'name': c['name'],
                    'phone': c['phone'],
                    'role': 'customer',
                    'is_active': True,
                }
            )
            if created:
                user.set_password('Customer@123')
                user.save()
            user_objs.append(user)
        self.stdout.write(self.style.SUCCESS(f'  {len(user_objs)} customer users ready'))

        # ── 4. Categories ─────────────────────────────────────────────────────
        self.stdout.write('Creating categories...')
        category_objs = {}
        for cat_data in CATEGORIES:
            cat, _ = Category.objects.get_or_create(
                name=cat_data['name'],
                store=store,
                defaults={'description': cat_data['description']}
            )
            category_objs[cat_data['name']] = cat
        self.stdout.write(self.style.SUCCESS(f'  {len(category_objs)} categories ready'))

        # ── 5. Products ───────────────────────────────────────────────────────
        self.stdout.write('Creating products...')
        product_count = 0
        all_products = []

        for cat_data in CATEGORIES:
            cat_obj = category_objs[cat_data['name']]
            for prod_data in cat_data['products']:
                prod, created = Product.objects.get_or_create(
                    sku=prod_data['sku'],
                    defaults={
                        'store': store,
                        'category': cat_obj,
                        'name': prod_data['name'],
                        'description': prod_data['description'],
                        'price': p(prod_data['price']),
                        'original_price': p(prod_data['original_price']),
                        'stock': prod_data['stock'],
                        'low_stock_threshold': 5,
                        'is_active': True,
                    }
                )
                if created:
                    product_count += 1
                all_products.append(prod)

        self.stdout.write(self.style.SUCCESS(f'  {product_count} new products created ({len(all_products)} total)'))

        # ── 6. Discounts ──────────────────────────────────────────────────────
        self.stdout.write('Creating discount codes...')
        now = timezone.now()
        discount_count = 0
        for d in DISCOUNTS:
            _, created = Discount.objects.get_or_create(
                code=d['code'],
                defaults={
                    'store': store,
                    'type': d['type'],
                    'value': p(d['value']),
                    'min_order_amount': p(d['min_order_amount']),
                    'start_date': now - timedelta(days=30),
                    'end_date': now + timedelta(days=180),
                    'is_active': True,
                }
            )
            if created:
                discount_count += 1
        self.stdout.write(self.style.SUCCESS(f'  {discount_count} discount codes ready'))

        # ── 7. Customer Profiles ──────────────────────────────────────────────
        self.stdout.write('Creating customer profiles...')
        customer_objs = []
        addresses = [
            'House 12, Street 4, F-10/2, Islamabad',
            'Flat 3B, DHA Phase 5, Lahore',
            'Shop 7, Saddar Market, Karachi',
            'Plot 45, Gulshan-e-Iqbal Block 13, Karachi',
            'House 88, G-9/3, Islamabad',
        ]
        for idx, user in enumerate(user_objs):
            cdata = CUSTOMERS[idx]
            cust, _ = Customer.objects.get_or_create(
                user=user,
                store=store,
                defaults={
                    'name': cdata['name'],
                    'phone': cdata['phone'],
                    'email': cdata['email'],
                    'address': addresses[idx],
                }
            )
            customer_objs.append(cust)
        self.stdout.write(self.style.SUCCESS(f'  {len(customer_objs)} customer profiles ready'))

        # ── 8. Orders ─────────────────────────────────────────────────────────
        self.stdout.write('Creating sample orders...')

        ORDER_TEMPLATES = [
            {
                'customer_idx': 0,
                'status': 'delivered',
                'payment_status': 'paid',
                'payment_method': 'COD',
                'items': [
                    ('SAM-S24U-BLK', 1),
                    ('SNY-WH1000XM5', 1),
                ],
                'days_ago': 45,
            },
            {
                'customer_idx': 1,
                'status': 'shipped',
                'payment_status': 'paid',
                'payment_method': 'easypaisa',
                'items': [
                    ('DEL-XPS15-2024', 1),
                    ('MOL-NB-A5-BLK', 2),
                ],
                'days_ago': 5,
            },
            {
                'customer_idx': 2,
                'status': 'confirmed',
                'payment_status': 'pending',
                'payment_method': 'COD',
                'items': [
                    ('GA-3PC-SUM001', 2),
                    ('LML-PLZ-SET01', 1),
                    ('DOV-SHP-700ML', 3),
                ],
                'days_ago': 2,
            },
            {
                'customer_idx': 3,
                'status': 'pending',
                'payment_status': 'pending',
                'payment_method': 'card',
                'items': [
                    ('NIK-AM270-WHT', 1),
                    ('ADI-UB22-BLK', 1),
                ],
                'days_ago': 0,
            },
            {
                'customer_idx': 4,
                'status': 'delivered',
                'payment_status': 'paid',
                'payment_method': 'COD',
                'items': [
                    ('OMR-BPM-HEM', 1),
                    ('CTR-MEN-60T', 2),
                    ('HIM-ASH-60T', 1),
                ],
                'days_ago': 30,
            },
            {
                'customer_idx': 0,
                'status': 'cancelled',
                'payment_status': 'refunded',
                'payment_method': 'COD',
                'items': [
                    ('APL-15PM-NTT', 1),
                ],
                'days_ago': 20,
            },
            {
                'customer_idx': 2,
                'status': 'delivered',
                'payment_status': 'paid',
                'payment_method': 'easypaisa',
                'items': [
                    ('BK-ATOM-HAB', 1),
                    ('BK-ALCH-PCO', 1),
                    ('STD-CP-48SET', 2),
                ],
                'days_ago': 15,
            },
            {
                'customer_idx': 1,
                'status': 'confirmed',
                'payment_status': 'paid',
                'payment_method': 'card',
                'items': [
                    ('PMX-TDM100', 1),
                    ('DCA-YGM-10MM', 2),
                ],
                'days_ago': 3,
            },
        ]

        product_map = {p.sku: p for p in all_products}
        order_count = 0
        year = timezone.now().year

        for tmpl in ORDER_TEMPLATES:
            cust = customer_objs[tmpl['customer_idx']]

            # Check if order already exists for this customer at this time
            days_ago = tmpl['days_ago']
            created_at = timezone.now() - timedelta(days=days_ago)

            # Generate order number
            order_count += 1
            order_number = f'ORD-{year}-{order_count:05d}'

            if Order.objects.filter(order_number=order_number).exists():
                self.stdout.write(f'  Order {order_number} already exists, skipping.')
                continue

            # Calculate totals
            items_data = []
            total_amount = Decimal('0')
            for sku, qty in tmpl['items']:
                prod = product_map.get(sku)
                if not prod:
                    continue
                line_total = prod.price * qty
                total_amount += line_total
                items_data.append({'product': prod, 'quantity': qty, 'price': prod.price, 'total': line_total})

            if not items_data:
                continue

            order = Order.objects.create(
                store=store,
                customer=cust,
                order_number=order_number,
                total_amount=total_amount,
                discount_amount=Decimal('0'),
                status=tmpl['status'],
                payment_method=tmpl['payment_method'],
                shipping_address=cust.address or 'Pakistan',
            )

            # Backdate the order
            Order.objects.filter(id=order.id).update(created_at=created_at)

            for item in items_data:
                OrderItem.objects.create(
                    order=order,
                    product=item['product'],
                    product_name=item['product'].name,
                    price=item['price'],
                    quantity=item['quantity'],
                    total_price=item['total'],
                )

            Payment.objects.create(
                order=order,
                method=tmpl['payment_method'],
                status=tmpl['payment_status'],
                amount=total_amount,
                paid_at=created_at if tmpl['payment_status'] == 'paid' else None,
            )

        self.stdout.write(self.style.SUCCESS(f'  {order_count} orders processed'))

        # ── 9. Returns ────────────────────────────────────────────────────────
        self.stdout.write('Creating sample returns...')
        delivered_orders = Order.objects.filter(status='delivered')
        if delivered_orders.count() >= 2:
            orders_list = list(delivered_orders)

            Return.objects.get_or_create(
                order=orders_list[0],
                defaults={
                    'customer': orders_list[0].customer,
                    'reason': 'Product received was damaged. Screen had a crack on arrival.',
                    'status': 'approved',
                    'resolved_at': timezone.now() - timedelta(days=2),
                }
            )
            Return.objects.get_or_create(
                order=orders_list[1],
                defaults={
                    'customer': orders_list[1].customer,
                    'reason': 'Wrong size delivered. Ordered L but received M.',
                    'status': 'pending',
                }
            )
        self.stdout.write(self.style.SUCCESS('  Returns created'))

        # ── 10. Complaints ────────────────────────────────────────────────────
        self.stdout.write('Creating sample complaints...')
        if customer_objs:
            Complaint.objects.get_or_create(
                customer=customer_objs[0],
                type='delivery',
                defaults={
                    'message': 'My order was supposed to arrive in 3 days but it has been 7 days and I have not received it yet.',
                    'status': 'in_progress',
                }
            )
            if Order.objects.exists():
                Complaint.objects.get_or_create(
                    customer=customer_objs[2],
                    type='product',
                    defaults={
                        'order': Order.objects.filter(customer=customer_objs[2]).first(),
                        'message': 'The product quality does not match the description on the website.',
                        'status': 'open',
                    }
                )
            Complaint.objects.get_or_create(
                customer=customer_objs[4],
                type='payment',
                defaults={
                    'message': 'I paid via Easypaisa but my order still shows payment pending.',
                    'status': 'resolved',
                    'response': 'We have verified your payment and updated your order. Thank you for your patience.',
                    'resolved_by': admin,
                }
            )
        self.stdout.write(self.style.SUCCESS('  Complaints created'))

        # ── Final Summary ─────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Seeding complete! Summary:'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Store:      {Store.objects.count()}')
        self.stdout.write(f'  Users:      {User.objects.count()} ({User.objects.filter(role="customer").count()} customers)')
        self.stdout.write(f'  Categories: {Category.objects.count()}')
        self.stdout.write(f'  Products:   {Product.objects.count()}')
        self.stdout.write(f'  Discounts:  {Discount.objects.count()}')
        self.stdout.write(f'  Orders:     {Order.objects.count()}')
        self.stdout.write(f'  Returns:    {Return.objects.count()}')
        self.stdout.write(f'  Complaints: {Complaint.objects.count()}')
        self.stdout.write('')
        self.stdout.write('Test credentials:')
        self.stdout.write('  Admin  → admin@store.com / Admin@12345')
        self.stdout.write('  Customer → ayesha.siddiqui@gmail.com / Customer@123')