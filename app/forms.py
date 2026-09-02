"""Forms. Labels and helper text come from app/content.py, never from here."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from app.content import AUTH

# check_deliverability does a live DNS lookup on every submit. That makes the
# login form exactly as fast and exactly as available as the resolver, and it
# rejects correctly typed addresses whenever DNS hiccups. Syntax only.
EMAIL_SYNTAX_ONLY = Email(message=AUTH["email_invalid"], check_deliverability=False)


class LoginForm(FlaskForm):
    email = StringField(
        AUTH["email_label"],
        validators=[
            DataRequired(message=AUTH["email_required"]),
            EMAIL_SYNTAX_ONLY,
            Length(max=255),
        ],
        render_kw={
            "autocomplete": "username",
            "autofocus": True,
            "inputmode": "email",
        },
    )
    password = PasswordField(
        AUTH["password_label"],
        validators=[DataRequired(message=AUTH["password_required"])],
        render_kw={"autocomplete": "current-password"},
    )
    remember = BooleanField(AUTH["remember_label"])
    submit = SubmitField(AUTH["submit_label"])


class ForgotPasswordForm(FlaskForm):
    email = StringField(
        AUTH["email_label"],
        validators=[
            DataRequired(message=AUTH["email_required"]),
            EMAIL_SYNTAX_ONLY,
            Length(max=255),
        ],
        render_kw={"autocomplete": "username", "autofocus": True, "inputmode": "email"},
    )
    submit = SubmitField(AUTH["forgot_submit"])


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        AUTH["reset_password"],
        validators=[
            DataRequired(message=AUTH["password_required"]),
            # Length is the only rule that reliably helps. Composition rules
            # mostly produce predictable substitutions.
            Length(min=12, message=AUTH["reset_subtitle"]),
        ],
        render_kw={"autocomplete": "new-password", "autofocus": True},
    )
    confirm = PasswordField(
        AUTH["reset_confirm"],
        validators=[
            DataRequired(message=AUTH["password_required"]),
            EqualTo("password", message=AUTH["reset_mismatch"]),
        ],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField(AUTH["reset_submit"])
