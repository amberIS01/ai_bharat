from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        # Wire the Merkle audit-log signal handlers. Importing `signals`
        # is enough — receivers are registered via @receiver decorators with
        # explicit `dispatch_uid` so re-import is a no-op.
        from core.audit import signals  # noqa: F401
