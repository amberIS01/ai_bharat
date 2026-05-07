"""Idempotent demo-user setup.

Replaces the manual `createsuperuser` step in the Day-1 plan. Running this
command once creates a deterministic officer user with a known password so
the demo workflow boots cleanly on a fresh database.

Username:  officer
Password:  praman_demo_2026
Role:      is_staff=True, is_superuser=True

For the actual hackathon demo Sahil should change the password before any
public exposure (the demo runs on a laptop with no inbound network so this
is mostly belt-and-braces).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


DEFAULT_USERNAME = "officer"
DEFAULT_PASSWORD = "praman_demo_2026"
DEFAULT_EMAIL = "officer@praman-demo.local"
DEFAULT_FIRST_NAME = "Demo"
DEFAULT_LAST_NAME = "Officer"


class Command(BaseCommand):
    help = "Idempotently create a default 'officer' superuser for the Praman demo."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=DEFAULT_USERNAME)
        parser.add_argument("--password", default=DEFAULT_PASSWORD)
        parser.add_argument("--reset-password", action="store_true",
                            help="Reset password even if the user already exists.")

    def handle(self, *args, **opts):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=opts["username"],
            defaults={
                "email": DEFAULT_EMAIL,
                "first_name": DEFAULT_FIRST_NAME,
                "last_name": DEFAULT_LAST_NAME,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created or opts["reset_password"]:
            user.set_password(opts["password"])
            user.is_staff = True
            user.is_superuser = True
            user.save()

        action = (
            "Created" if created else
            ("Reset password for" if opts["reset_password"] else "Already present")
        )
        self.stdout.write(self.style.SUCCESS(
            f"{action}: user={user.username!r} email={user.email!r} "
            f"is_staff={user.is_staff} is_superuser={user.is_superuser}"
        ))
        if created or opts["reset_password"]:
            self.stdout.write(self.style.WARNING(
                f"Password set to {opts['password']!r}. Change it for any non-local exposure."
            ))
