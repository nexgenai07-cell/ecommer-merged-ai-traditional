# PATH: apps/ai/upload_views.py
#
# Requirement 8. File content asal mein image hai ya nahi — sirf
# declared content-type pe trust nahi karte, PIL se actual bytes verify
# karte hain.

from PIL import Image
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser

from apps.ai.mixins import ChatAuthErrorMixin
from apps.ai.throttles import ChatUserRateThrottle, ChatAnonRateThrottle
from apps.ai.models import ChatUpload

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}


class ChatUploadView(ChatAuthErrorMixin, APIView):
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser]
    throttle_classes = [ChatUserRateThrottle, ChatAnonRateThrottle]

    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({'error': 'No file provided.'}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded_file.size > MAX_FILE_SIZE:
            return Response({'error': 'File exceeds the 5 MB limit.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        try:
            img = Image.open(uploaded_file)
            img.verify()
            image_format = img.format
        except Exception:
            return Response({'error': 'Unsupported or invalid file type.'}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        if image_format not in ALLOWED_FORMATS:
            return Response({'error': 'Unsupported file type.'}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        # verify() consumes the file pointer — reset before saving
        uploaded_file.seek(0)

        chat_upload = ChatUpload.objects.create(
            file=uploaded_file,
            uploaded_by=request.user if request.user.is_authenticated else None,
        )

        return Response({
            'file_id': str(chat_upload.id),
            'url': request.build_absolute_uri(chat_upload.file.url),
        }, status=status.HTTP_201_CREATED)