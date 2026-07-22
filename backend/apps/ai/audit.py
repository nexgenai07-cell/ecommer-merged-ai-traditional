# PATH: apps/ai/audit.py

# FLOW: registry.py ke confirm_pending_action se, HAR mutating action
# ke execute hone ke turant baad call hota hai. Isay AuditLog model
# (apps/ai/models.py) mein ek row daalni hoti hai.

from apps.ai.models import AuditLog
from apps.stores.models import Store


# tool_name -> (entity_name, payload_key_for_entity_id)
# 'create_*' tools ke liye entity_id pending payload mein nahi hota (naya
# record abhi bana hi nahi tha) — us case mein result se nikalte hain.
ENTITY_MAP = {
    'create_product':   ('product', None),
    'update_product':   ('product', 'product_id'),
    'delete_product':   ('product', 'product_id'),
    'create_category':  ('category', None),
    'update_category':  ('category', 'category_id'),
    'delete_category':  ('category', 'category_id'),
    'update_inventory': ('inventory', 'product_id'),
    'update_order':     ('order', 'order_id'),
    'cancel_order':      ('order', 'order_id'),
}


def log_admin_action(user, tool_name: str, payload: dict, result: dict):

    """
    FLOW: confirm_pending_action() se call hota hai, argument mein
    tool ka naam, jo payload bheja gaya tha, aur asal execute ka result
    milta hai — isi se AuditLog.objects.create() ban jata hai.
    → Yahan se kahin aage nahi jata — ye chain ka AAKHRI step hai
      audit trail ke liye.
    """

    """
    Ek mutating action complete hone ke baad AuditLog entry banata hai.
    Fail-safe hai — agar logging mein hi koi error aa jaye, poori request
    ko crash nahi karne dete (audit trail zaroori hai lekin core feature
    ko block nahi karna chahiye).
    """
    try:
        entity, id_key = ENTITY_MAP.get(tool_name, (tool_name, None))

        entity_id = None
        if id_key and id_key in payload:
            entity_id = payload[id_key]
        elif isinstance(result, dict):
            # create_* tools ke liye — naya record ka id result mein hota hai
            for key in ('product', 'category'):
                if key in result and isinstance(result[key], dict):
                    entity_id = result[key].get('id')
                    break

        store = user.stores.first() if hasattr(user, 'stores') else None
        if store is None:
            store = Store.objects.first()

        # FLOW: yahan asal DB row bantа hai — Django admin panel se
        # /admin/ai/auditlog/ pe ye dekhi ja sakti hai

        AuditLog.objects.create(
            store=store,
            user=user,
            action=tool_name,
            entity=entity,
            entity_id=entity_id,
            old_data=None,  # Day 3 scope mein before/after snapshot nahi bana rahe — sirf action record
            new_data={'payload': payload, 'result': result},
            source='web',
        )
    except Exception:
        # Audit logging fail hone se asal action revert nahi hona chahiye —
        # bas silently skip karte hain (production mein isay proper
        # logging/monitoring se track karna chahiye).
        pass        # FLOW: audit fail hone se asal action revert nahi hota — silently skip