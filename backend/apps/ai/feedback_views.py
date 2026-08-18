# PATH: apps/ai/feedback_views.py

import logging   # NEW — diagnostic ke liye
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.ai.mixins import ChatAuthErrorMixin
from apps.ai.throttles import ChatUserRateThrottle, ChatAnonRateThrottle
from apps.ai.models import ChatMessage

logger = logging.getLogger("ai.feedback_views")   # NEW


def _normalize_rating(raw):
    """NEW — CRITICAL FIX: pehle sirf EXACT string 'up'/'down' accept
    hoti thi — agar frontend kisi aur convention mein bhejta hai (jaise
    'like'/'dislike', true/false, 1/-1, "thumbs_up", etc.), to ye hamesha
    400 deta tha aur frontend "Couldn't save your feedback" dikhata tha,
    chahe request bilkul valid intent ke sath aayi ho. Ab common aliases
    ko normalize karte hain taake koi bhi reasonable frontend-convention
    kaam kare."""
    if isinstance(raw, bool):
        return 'up' if raw else 'down'
    if isinstance(raw, (int, float)):
        return 'up' if raw > 0 else 'down'
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in ('up', 'like', 'liked', 'thumbs_up', 'thumbsup', 'thumbs-up', 'positive', '1', 'true', 'yes'):
            return 'up'
        if v in ('down', 'dislike', 'disliked', 'thumbs_down', 'thumbsdown', 'thumbs-down', 'negative', '-1', '0', 'false', 'no'):
            return 'down'
    return None


class MessageFeedbackView(ChatAuthErrorMixin, APIView):
    # NEW — CRITICAL FIX: agar Django REST Framework ki
    # DEFAULT_AUTHENTICATION_CLASSES mein SessionAuthentication shamil hai
    # (bohot common default), to wo AllowAny permission ke bawajood bhi
    # POST/DELETE requests par CSRF token maangti hai — aur ek anonymous
    # ya guest customer ka browser fetch() call generally CSRF header
    # nahi bhejta (khaaskar jab chat widget ka WebSocket flow separate
    # hai aur CSRF cookie kabhi set hi nahi hui). Result: 403 "CSRF
    # Failed", jo frontend ko "Couldn't save your feedback" generic error
    # ke tor pe dikhta hai — chahe rating value bilkul sahi ho. Is view
    # ke liye authentication zaroori nahi hai (feedback anonymous bhi ho
    # sakti hai), is liye authentication_classes ko khali kar ke CSRF
    # check hi bypass kar dete hain.
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ChatUserRateThrottle, ChatAnonRateThrottle]

    def post(self, request, message_id):
        raw_rating = request.data.get('rating')
        rating = _normalize_rating(raw_rating)   # NEW

        if rating not in ('up', 'down'):
            # NEW — DIAGNOSTIC: agar phir bhi fail ho, ye log line exact
            # dikha degi frontend ne kya bheja tha
            logger.warning(
                "[MessageFeedbackView] could not normalize rating value=%r (type=%s) for message_id=%s",
                raw_rating, type(raw_rating).__name__, message_id,
            )
            return Response({'error': "rating must be 'up' or 'down'."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            message = ChatMessage.objects.get(id=message_id)
        except ChatMessage.DoesNotExist:
            logger.warning("[MessageFeedbackView] message_id=%s not found", message_id)   # NEW
            return Response({'error': 'Message not found.'}, status=status.HTTP_404_NOT_FOUND)

        if message.sender != 'ai':
            logger.warning(   # NEW
                "[MessageFeedbackView] feedback attempted on non-AI message_id=%s (sender=%s)",
                message_id, message.sender,
            )
            return Response({'error': 'Feedback can only be given on AI messages.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            message.rating = rating
            message.rated_at = timezone.now()
            message.save(update_fields=['rating', 'rated_at'])
        except Exception:
            # NEW — DIAGNOSTIC: agar save() hi kisi wajah se fail ho
            # (jaise DB constraint), poori traceback log mein aayegi
            logger.exception("[MessageFeedbackView] failed to save rating for message_id=%s", message_id)
            return Response({'error': 'Could not save feedback right now.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'message_id': message.id, 'rating': rating}, status=status.HTTP_200_OK)

    def delete(self, request, message_id):
        try:
            message = ChatMessage.objects.get(id=message_id)
        except ChatMessage.DoesNotExist:
            return Response({'error': 'Message not found.'}, status=status.HTTP_404_NOT_FOUND)

        message.rating = None
        message.rated_at = None
        message.save(update_fields=['rating', 'rated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)