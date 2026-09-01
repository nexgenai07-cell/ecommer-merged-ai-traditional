"""
Quick one-off diagnostic — run with:
    python manage.py shell < diagnose_cloudinary.py

Uploads ONE test image and prints exactly what Cloudinary returns,
so we can see whether resource_type/type/format is the problem.
"""
import cloudinary
import cloudinary.uploader

print("Cloudinary config cloud_name:", cloudinary.config().cloud_name)

try:
    result = cloudinary.uploader.upload(
        "https://picsum.photos/seed/diagnostic-test/600/600",
        folder="products",
        public_id="diagnostic-test",
        overwrite=True,
        unique_filename=False,
    )
    print("\n--- FULL UPLOAD RESULT ---")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("\nTest this URL directly in your browser:")
    print(" ", result.get("secure_url"))
except Exception as e:
    print("UPLOAD FAILED WITH EXCEPTION:")
    print(" ", repr(e))