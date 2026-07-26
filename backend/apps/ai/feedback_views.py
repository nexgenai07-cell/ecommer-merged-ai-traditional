# PATH: apps/ai/feedback_views.py

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.ai.mixins import ChatAuthErrorMixin
from apps.ai.throttles import ChatUserRateThrottle, ChatAnonRateThrottle
from apps.ai.models import ChatMessage


class MessageFeedbackView(ChatAuthErrorMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ChatUserRateThrottle, ChatAnonRateThrottle]

    def post(self, request, message_id):
        rating = request.data.get('rating')
        if rating not in ('up', 'down'):
            return Response({'error': "rating must be 'up' or 'down'."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            message = ChatMessage.objects.get(id=message_id)
        except ChatMessage.DoesNotExist:
            return Response({'error': 'Message not found.'}, status=status.HTTP_404_NOT_FOUND)

        if message.sender != 'ai':
            return Response({'error': 'Feedback can only be given on AI messages.'}, status=status.HTTP_400_BAD_REQUEST)

        message.rating = rating
        message.rated_at = timezone.now()
        message.save(update_fields=['rating', 'rated_at'])

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