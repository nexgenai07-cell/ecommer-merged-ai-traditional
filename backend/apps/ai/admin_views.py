# PATH: apps/ai/admin_views.py

# FLOW: core/urls.py → apps/ai/urls.py (admin/session/start/ path) → yahan
# Ye customer wale StartChatSessionView jaisa hi hai, FARQ sirf ye hai:
# permission_classes mein IsAdmin lagi hai — is liye sirf admin JWT chalega.

import uuid
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.stores.models import Store
from apps.users.permissions import IsAdmin      # FLOW: yahi check karta hai role='admin' hai ya nahi
from apps.ai.models import ChatSession
from apps.ai.serializers import ChatSessionSerializer


class StartAdminChatSessionView(APIView):
    """
    POST /api/v1/chat/admin/session/start/

    Customer's StartChatSessionView se alag — sirf admin role wale users
    hi admin chat session bana sakte hain. Ye session_key WebSocket
    (ws/admin-chat/<session_key>/) se connect karne k liye use hoga.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]        # FLOW: yahan hi 403 lag jata hai agar admin nahi hai

    def post(self, request):
        session_key = uuid.uuid4().hex
        store = Store.objects.first()

        session = ChatSession.objects.create(
            session_key=session_key,
            store=store,
            user=request.user,      # FLOW: admin session mein user hamesha set hota hai (never None)
            channel='admin',
        )

        # FLOW: ye session_key admin frontend WebSocket connect karne ke liye use karega
        
        return Response(ChatSessionSerializer(session).data, status=status.HTTP_201_CREATED)