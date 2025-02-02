"""ORM signal handlers."""

from django import dispatch
from django.db.models import Model
from django.db.models import signals as model_signals

from resolwe.permissions.models import Permission, PermissionObject

from .models import Observer
from .protocol import ChangeType, post_permission_changed, pre_permission_changed

# Global 'in migrations' flag to ignore signals during migrations.
# Signal handlers that access the database can crash the migration process.
IN_MIGRATIONS = False


@dispatch.receiver(model_signals.pre_migrate)
def model_pre_migrate(*args, **kwargs):
    """Set 'in migrations' flag."""
    global IN_MIGRATIONS
    IN_MIGRATIONS = True


@dispatch.receiver(model_signals.post_migrate)
def model_post_migrate(*args, **kwargs):
    """Clear 'in migrations' flag."""
    global IN_MIGRATIONS
    IN_MIGRATIONS = False


@dispatch.receiver(pre_permission_changed)
def prepare_permission_change(instance, **kwargs):
    """Store old permissions for an object whose permissions are about to change."""
    if not IN_MIGRATIONS:
        instance._old_viewers = instance.users_with_permission(Permission.VIEW)


@dispatch.receiver(post_permission_changed)
def handle_permission_change(instance, **kwargs):
    """Compare permissions for an object whose permissions changed."""
    if not IN_MIGRATIONS:
        new = set(instance.users_with_permission(Permission.VIEW))
        old = set(instance._old_viewers)

        gains = new - old
        losses = old - new
        Observer.observe_permission_changes(instance, gains, losses)


@dispatch.receiver(model_signals.post_save)
def observe_model_modification(
    sender: type, instance: Model, created: bool = False, **kwargs
):
    """Receive model updates."""

    # Create signals will be caught when the PermissionModel is added.
    if created:
        return

    if isinstance(instance, PermissionObject) and not IN_MIGRATIONS:
        Observer.observe_instance_changes(instance, ChangeType.UPDATE)


@dispatch.receiver(model_signals.pre_delete)
def observe_model_deletion(sender: type, instance: Model, **kwargs):
    """Receive model deletions."""
    if isinstance(instance, PermissionObject) and not IN_MIGRATIONS:
        Observer.observe_instance_changes(instance, ChangeType.DELETE)
