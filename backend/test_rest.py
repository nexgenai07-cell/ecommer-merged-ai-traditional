import time
import requests

# ⚠️ Apna Django REST Framework endpoint yahan write karein
TARGET_URL = "http://127.0.0.1:8000/api/sessions/"

# Agar JWT / Token Auth chahiye toh header mei pass karein
HEADERS = {
    # 'Authorization': 'Bearer YOUR_TOKEN_HERE'
}


def test_rate_limit():
    print("🚀 Sending 65 requests to Django endpoint...")

    for i in range(1, 66):
        response = requests.get(TARGET_URL, headers=HEADERS)

        if response.status_code == 429:
            print(f"\n🚨 Request {i}: Status 429 (Too Many Requests)")
            print(f"📋 Retry-After Header: {response.headers.get('Retry-After')}")
            print(f"📩 Response Body: {response.json()}")
            print("\n✅ TEST PASSED: 429 Status aur Retry-After header mil gaya!")
            break
        else:
            print(f"Request {i}: Status {response.status_code}")


if __name__ == "__main__":
    test_rate_limit()