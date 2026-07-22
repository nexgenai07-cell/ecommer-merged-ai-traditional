# PATH: apps/ai/admin.py

from django.contrib import admin
from django.contrib import messages
from .models import ChatSession, ChatMessage, AuditLog, KnowledgeDocument
from .faq_indexing import index_document, remove_document_from_index


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ['sender', 'message', 'metadata', 'created_at']
    can_delete = False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'session_key', 'user', 'store', 'started_at', 'updated_at']
    search_fields = ['session_key', 'user__email']
    list_filter = ['store']
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'sender', 'short_message', 'created_at']
    list_filter = ['sender']
    search_fields = ['message']

    def short_message(self, obj):
        return obj.message[:50]
    short_message.short_description = 'Message'


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'action', 'entity', 'entity_id', 'source', 'created_at']
    list_filter = ['action', 'entity', 'source']
    search_fields = ['action', 'entity']


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    """
    Admin yahan se FAQ/knowledge PDFs upload karta hai. Upload karne k baad
    'Index selected documents' action chalayein taake Qdrant mein embed ho
    (Support Agent tabhi is document se jawab de sakega).
    """
    list_display = ['title', 'is_active', 'is_indexed', 'chunk_count', 'uploaded_by', 'uploaded_at', 'indexed_at']
    list_filter = ['is_active', 'is_indexed']
    search_fields = ['title']
    readonly_fields = ['is_indexed', 'chunk_count', 'indexed_at', 'uploaded_by', 'uploaded_at']
    actions = ['index_selected_documents', 'remove_selected_from_index']

    def save_model(self, request, obj, form, change):
        if not obj.uploaded_by_id:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Index selected documents (embed into Qdrant)')
    def index_selected_documents(self, request, queryset):
        for document in queryset:
            success, msg = index_document(document)
            level = messages.SUCCESS if success else messages.ERROR
            self.message_user(request, f'"{document.title}": {msg}', level=level)

    @admin.action(description='Remove selected documents from search index')
    def remove_selected_from_index(self, request, queryset):
        for document in queryset:
            remove_document_from_index(document)
            self.message_user(request, f'"{document.title}" removed from search index.', level=messages.SUCCESS)