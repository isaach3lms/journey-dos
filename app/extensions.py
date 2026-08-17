"""Extension singletons.

Kept in their own module so models can import `db` without importing the
application factory, which would be circular.
"""

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Model(DeclarativeBase):
    """SQLAlchemy 2.x declarative base."""


db = SQLAlchemy(model_class=Model)
migrate = Migrate()
