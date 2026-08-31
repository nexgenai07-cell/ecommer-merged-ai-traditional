# PATH: apps/ai/views.py

# FLOW: Ye REST endpoint hai (WebSocket nahi) — frontend WebSocket
# connect karne SE PEHLE isay POST karta hai taake session_key mil sake.
#
# Request kahan se aati hai:
#   core/urls.py → path('api/v1/chat/', include('apps.ai.urls'))
#   apps/ai/urls.py → path('session/start/', StartChatSessionView.as_view())
#   → yahan (ye function)
#
# → Yahan se aage: ChatSession model mein naya row banta hai, us ka
#   session_key hi wo cheez hai jo frontend WebSocket URL mein use
#   karega (Step 2 se connect karne ke liye).

import uuid
from django.db.models import Q
from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.stores.models import Store
from apps.users.permissions import IsAdmin
from .models import ChatSession, ChatMessage, AuditLog
from .serializers import ChatSessionSerializer, ChatSessionHistorySerializer, AuditLogSerializer
from core.pagination import StandardResultsPagination

from apps.ai.mixins import ChatAuthErrorMixin
from apps.ai.throttles import ChatUserRateThrottle

class StartChatSessionView(ChatAuthErrorMixin, APIView):
    """
    POST /api/v1/chat/session/start/

    Starts a new chat session.
    - Anonymous users: a new random session_key is generated (frontend saves it
      in localStorage and sends it back on every later request).
    - Logged in users: session is automatically linked to their account.
    """
    # Anyone can start a chat session, even without logging in
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ChatUserRateThrottle]

    def post(self, request):
        # Generate a unique session key — frontend stores this and reuses it
        session_key = uuid.uuid4().hex

        # Single-store setup — always attach the one store that exists
        store = Store.objects.first()

        session = ChatSession.objects.create(
            session_key=session_key,
            store=store,
            # if the user is logged in, link the session to them right away
            user=request.user if request.user.is_authenticated else None,
            channel='customer',
        )

        # FLOW: response mein session_key jata hai → frontend ise
        # WebSocket URL mein daal kar Step 2 (routing.py) ko trigger karega

        return Response(ChatSessionSerializer(session).data, status=status.HTTP_201_CREATED)
    

class ChatSessionHistoryView(ChatAuthErrorMixin, generics.RetrieveAPIView):
    """
    GET /api/v1/chat/session/{session_key}/history/
    FIX (Requirement 2): soft-deleted sessions ab 404 dete hain —
    queryset khud hi is_deleted=False filter karta hai, isliye
    RetrieveAPIView automatically 404 return kar deta hai agar
    session soft-deleted ho chuki hai.
    """
    serializer_class = ChatSessionHistorySerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ChatUserRateThrottle]
    lookup_field = 'session_key'
    queryset = ChatSession.objects.filter(is_deleted=False).prefetch_related('messages')
    

class ClearChatSessionView(ChatAuthErrorMixin, APIView):
    """
    DELETE /api/v1/chat/session/{session_key}/clear/

    Deletes all messages in a session (used when user clicks "clear chat").
    The session itself stays so the same session_key keeps working.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ChatUserRateThrottle]

    def delete(self, request, session_key):
        try:
            session = ChatSession.objects.get(session_key=session_key)
        except ChatSession.DoesNotExist:
            return Response({'error': 'Session not found.'}, status=status.HTTP_404_NOT_FOUND)

        # remove all messages but keep the session row itself
        session.messages.all().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuditLogListView(generics.ListAPIView):
    """
    GET /api/v1/admin/audit-logs/?page=&entity=&user=&search=

    Lets admin see a history of every action performed (web or WhatsApp).
    Read-only — logs are created internally by the system, not via this API.

    FIX (A6): 'page' was already working via the project's global
    DEFAULT_PAGINATION_CLASS (StandardResultsPagination) — set here
    explicitly now so the shape doesn't silently depend on that global
    setting. 'entity' and 'user' filter server-side; 'search' matches
    the log's action text (the model has no separate "description"
    field — 'action' is it, e.g. "create_product").
    """
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        qs = AuditLog.objects.select_related('user').all()
        params = self.request.query_params

        entity = params.get('entity')
        if entity:
            qs = qs.filter(entity=entity)

        user_id = params.get('user')
        if user_id:
            qs = qs.filter(user_id=user_id)

        search = params.get('search')
        if search:
            qs = qs.filter(Q(action__icontains=search))

        return qs