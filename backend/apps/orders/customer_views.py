# PATH: apps/orders/customer_views.py

import re

from rest_framework import generics, permissions
from django.db.models import Q, Count, Sum, Value, DecimalField
from django.db.models.functions import Coalesce, Replace
from .models import Customer
from .customer_serializers import CustomerAdminSerializer
from apps.users.permissions import IsAdmin
from core.pagination import StandardResultsPagination

# Returns a searchable list of all customers for administrators.
class AdminCustomerListView(generics.ListAPIView):
    """
    GET /api/v1/admin/customers/?search=&page=&ordering=

    Admin-only — list all customers with order count and total spent.

    FIX (A2 — supersedes the 11 Jul fix below): the new backend spec
    (v7) makes the paginated {count, next, previous, results} shape
    mandatory here — "never a bare array". The old fix had deliberately
    set pagination_class = None to match an older doc that wanted a
    plain array; that's now reversed. StandardResultsPagination (page
    size 10, override with ?page_size=, max 100) is used explicitly so
    this doesn't silently depend on the global DRF default changing
    later. 'search' and 'page' already worked together before this fix
    and continue to.

    FIX (D1): 'ordering' now accepts created_at / total_orders /
    total_spent (and their '-' descending forms). total_orders and
    total_spent aren't real DB columns — the serializer computes them
    in Python via SerializerMethodField — so they're annotated here
    with the exact same "exclude cancelled + pending_payment" logic
    the serializer uses, purely so the DB can sort by them.
    """
    serializer_class = CustomerAdminSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

    ALLOWED_ORDERING = {
        'created_at': 'created_at',
        '-created_at': '-created_at',
        'total_orders': '_total_orders',
        '-total_orders': '-_total_orders',
        'total_spent': '_total_spent',
        '-total_spent': '-_total_spent',
    }

# Fetches all customers and applies search + order-status filters if provided.
    def get_queryset(self):
        qs = Customer.objects.select_related('user').all()

        # FIX (D1 — bug found in testing): Sum() returns NULL (not 0) for
        # customers with zero matching orders. Postgres' default null
        # ordering puts NULLs FIRST on descending order, so those
        # zero-spend customers were sorting to the top of
        # ?ordering=-total_spent even though the response correctly
        # showed "total_spent": 0 for them (that value is computed
        # separately, in Python, by the serializer). Coalesce forces the
        # NULL to a real 0 before ordering, so 0-spend customers now
        # correctly sort to the bottom on descending / top on ascending.
        excluded_statuses = ['cancelled', 'pending_payment']
        qs = qs.annotate(
            _total_orders=Count(
                'orders',
                filter=~Q(orders__status__in=excluded_statuses),
                distinct=True,
            ),
            _total_spent=Coalesce(
                Sum(
                    'orders__total_amount',
                    filter=~Q(orders__status__in=excluded_statuses),
                ),
                Value(0),
                output_field=DecimalField(),
            ),
        )

        search = self.request.query_params.get('search')
        if search:
            search_filter = Q(name__icontains=search) | Q(email__icontains=search) | Q(phone__icontains=search)

            # NEW (Follow-up v8, item 3): phone search now tolerates
            # formatting differences (spaces, dashes, a leading '+') so a
            # WhatsApp-style plain-digit number (e.g. 923211234567)
            # matches a customer whose phone was stored with punctuation
            # (e.g. "+92 321 1234567"). Both sides are reduced to
            # digits-only before comparing:
            #   - qs is annotated with '_phone_digits', the stored phone
            #     with '+', ' ', '-', '(', ')' stripped out via Django's
            #     Replace(), so the comparison happens in the DB.
            #   - the search term goes through the same strip in Python.
            # Only applied when the search term has enough digits to be a
            # plausible phone search (>=6) — otherwise a short numeric
            # search (e.g. "92") would match almost every phone number's
            # digit string and silently widen unrelated searches.
            search_digits = re.sub(r'[\s\-()+]', '', search)
            if len(re.sub(r'\D', '', search_digits)) >= 6:
                qs = qs.annotate(
                    _phone_digits=Replace(
                        Replace(
                            Replace(
                                Replace(
                                    Replace('phone', Value('+'), Value('')),
                                    Value(' '), Value(''),
                                ),
                                Value('-'), Value(''),
                            ),
                            Value('('), Value(''),
                        ),
                        Value(')'), Value(''),
                    )
                )
                search_filter |= Q(_phone_digits__icontains=search_digits)

            qs = qs.filter(search_filter)

        # FIX (B57): "Pending" filter tha hi nahi is view mein — is liye
        # kaam nahi kar raha tha. Ab ?status=pending_payment (ya koi bhi
        # valid Order status) se customers ko filter kiya ja sakta hai
        # jinke us status waale order hain. .distinct() isliye taake ek
        # customer ke multiple matching orders hone par woh list mein
        # duplicate na aaye.
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(orders__status=status_filter).distinct()

        ordering = self.request.query_params.get('ordering')
        if ordering in self.ALLOWED_ORDERING:
            qs = qs.order_by(self.ALLOWED_ORDERING[ordering])
        else:
            qs = qs.order_by('-created_at')

        return qs

# Returns complete information for a selected customer.
class AdminCustomerDetailView(generics.RetrieveAPIView):
    """GET /api/v1/admin/customers/{id}/"""
    serializer_class = CustomerAdminSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    queryset = Customer.objects.select_related('user').all()