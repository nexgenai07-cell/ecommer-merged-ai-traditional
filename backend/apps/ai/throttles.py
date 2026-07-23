# PATH: apps/ai/throttles.py
#
# Requirement 11 — REST endpoints ke liye 60 requests/minute per IP/user.
# DRF khud automatically 429 + Retry-After header deta hai jab limit
# exceed ho.

from rest_framework.throttling import UserRateThrottle, AnonRateThrottle


class ChatUserRateThrottle(UserRateThrottle):
    scope = 'chat_user'


class ChatAnonRateThrottle(AnonRateThrottle):
    scope = 'chat_anon'