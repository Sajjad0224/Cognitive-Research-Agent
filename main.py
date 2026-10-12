"""Compatibility entry point for the Django development server."""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cra_site.settings")

if __name__ == "__main__":
    from django.core.management import execute_from_command_line

    execute_from_command_line(["manage.py", "runserver", "127.0.0.1:8000"])
