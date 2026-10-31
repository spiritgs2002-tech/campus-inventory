from django.db import models


class TransactionIDField(models.Field):
    """xid8 stamped by a BEFORE INSERT trigger, omitted from ORM writes.

    Like a GeneratedField, it participates in RETURNING. It is not a PostgreSQL
    generated column: the authoritative SQL defines the xid_stamp trigger.
    """

    generated = True
    db_returning = True

    def __init__(self, *args, **kwargs):
        kwargs["editable"] = False
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        return "xid8"

    def to_python(self, value):
        return None if value is None else str(value)

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)
