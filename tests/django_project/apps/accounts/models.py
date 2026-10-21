from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """An installation-wide identity; organization roles belong to Membership."""
