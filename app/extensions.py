"""Extension singletons.

Kept in their own module so models can import `db` without importing the
application factory, which would be circular.
"""

from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


# Every constraint gets a predictable name.
#
# SQLite cannot ALTER a constraint, so Flask-Migrate rebuilds the table
# instead (`render_as_batch=True`). That rebuild has to name the constraint it
# is recreating, and an unnamed one fails the migration outright with
# "Constraint must have a name". Postgres is more forgiving at write time and
# less forgiving later: it invents names like `person_church_id_fkey1`, which
# are impossible to drop deliberately.
#
# Naming them here means a constraint is addressable by the same name on both
# databases, from the first migration onward.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Model(DeclarativeBase):
    """SQLAlchemy 2.x declarative base."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


db = SQLAlchemy(model_class=Model)
migrate = Migrate()

# Applied to every POST in the app, not opted into per form.
csrf = CSRFProtect()
