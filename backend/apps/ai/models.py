# PATH: apps/ai/models.py

from django.db import models
from django.conf import settings


class ChatSession(models.Model):
    CHANNEL_CHOICES = [
        ('customer', 'Customer'),
        ('admin', 'Admin'),
    ]

    user        = models.ForeignKey(
                    settings.AUTH_USER_MODEL,
                    on_delete=models.CASCADE,
                    related_name='chat_sessions',
                    null=True,
                    blank=True,
                  )
    store       = models.ForeignKey(
                    'stores.Store',
                    on_delete=models.CASCADE,
                    related_name='chat_sessions',
                  )
    session_key = models.CharField(max_length=100, unique=True)
    channel     = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default='customer')
    started_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    is_deleted  = models.BooleanField(default=False)
    deleted_at  = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'chat_sessions'
        ordering = ['-started_at']

    def __str__(self):
        if self.user:
            return f'Session {self.session_key} ({self.user.email})'
        return f'Session {self.session_key} (anonymous)'


class ChatMessage(models.Model):
    SENDER_CHOICES = [
        ('user', 'User'),
        ('ai',   'AI'),
    ]

    session    = models.ForeignKey(
                   ChatSession,
                   on_delete=models.CASCADE,
                   related_name='messages',
                 )
    sender     = models.CharField(max_length=10, choices=SENDER_CHOICES)
    message    = models.TextField()
    metadata   = models.JSONField(
                   null=True,
                   blank=True,
                   help_text='Structured data: product cards, order info, cart state etc.'
                 )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_messages'
        ordering = ['created_at']

    def __str__(self):
        return f'[{self.sender}] {self.message[:50]}'


class AuditLog(models.Model):
    store      = models.ForeignKey(
                   'stores.Store',
                   on_delete=models.CASCADE,
                   related_name='audit_logs',
                 )
    user       = models.ForeignKey(
                   settings.AUTH_USER_MODEL,
                   on_delete=models.SET_NULL,
                   related_name='audit_logs',
                   null=True,
                   blank=True,
                   help_text='Admin who performed the action'
                 )
    action     = models.CharField(
                   max_length=100,
                   help_text='e.g. create_product, update_order_status, delete_category'
                 )
    entity     = models.CharField(
                   max_length=50,
                   help_text='e.g. product, order, category, inventory'
                 )
    entity_id  = models.IntegerField(
                   null=True,
                   blank=True,
                   help_text='ID of the affected record'
                 )
    old_data   = models.JSONField(null=True, blank=True, help_text='Snapshot BEFORE change')
    new_data   = models.JSONField(null=True, blank=True, help_text='Snapshot AFTER change')
    ip_address = models.CharField(max_length=50, null=True, blank=True)
    source     = models.CharField(max_length=20, default='web')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['store', 'entity', 'created_at']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        actor = self.user.email if self.user else 'system'
        return f'{actor} → {self.action} on {self.entity}:{self.entity_id}'


# NEW — dynamic FAQ/knowledge base documents. Admin uploads PDFs here
# through the Django admin panel (no hardcoded file paths). Each document
# can be independently indexed/removed from the Qdrant search index,
# so this stays fully database-driven instead of static.
class KnowledgeDocument(models.Model):
    title       = models.CharField(max_length=255, help_text='e.g. "Store FAQ", "Return Policy Details"')
    file        = models.FileField(upload_to='knowledge_base/')
    is_active   = models.BooleanField(
                    default=True,
                    help_text='Only active documents are searchable by the Support Agent.'
                  )
    is_indexed  = models.BooleanField(
                    default=False,
                    editable=False,
                    help_text='Whether this document is currently embedded in Qdrant.'
                  )
    chunk_count = models.IntegerField(default=0, editable=False)
    uploaded_by = models.ForeignKey(
                    settings.AUTH_USER_MODEL,
                    on_delete=models.SET_NULL,
                    null=True,
                    blank=True,
                  )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    indexed_at  = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        db_table = 'knowledge_documents'
        ordering = ['-uploaded_at']

    def __str__(self):
        status = 'indexed' if self.is_indexed else 'not indexed'
        return f'{self.title} ({status})'