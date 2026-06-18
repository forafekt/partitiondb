import logging
import sys
from django.db import connection

class TenantContextFilter(logging.Filter):
    """
    Add the current ``schema_name``, ``domain_url``, and ``db_alias`` to log records.
    """
    def filter(self, record):
        try:
            record.db_alias = getattr(connection.tenant, 'db_alias', None)
            record.schema_name = getattr(connection.tenant, 'schema_name', None)
            record.domain_url = getattr(connection.tenant, 'domain_url', None)
        except Exception:  # Catch any potential errors with tenant access.
            record.db_alias = None
            record.schema_name = None
            record.domain_url = None
        return True

class TenantContextFormatter(logging.Formatter):
    """
    A custom formatter that adds color to log messages based on their level
    and includes tenant context.
    """
    # ANSI color codes
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    blue = "\x1b[34;20m"
    purple = "\x1b[35;20m"
    reset = "\x1b[0m"

    # Format string with tenant context
    base_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d) - Schema: %(schema_name)s - DB Alias: %(db_alias)s - Domain: %(domain_url)s"
    # base_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'

    FORMATS = {
        logging.DEBUG: purple + base_format + reset,
        logging.INFO: blue + base_format + reset,
        logging.WARNING: yellow + base_format + reset,
        logging.ERROR: red + base_format + reset,
        logging.CRITICAL: bold_red + base_format + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt) # Create a new formatter object.
        #  Important:  Don't modify the class-level 'format' attribute.
        #  Instead, create a *new* formatter instance for each record.
        #  This prevents issues with the format string being overwritten.

        # Add tenant information to the record.  Do this *once* here.
        try:
            record.db_alias = getattr(connection.tenant, 'db_alias', None)
            record.schema_name = getattr(connection.tenant, 'schema_name', None)
            record.domain_url = getattr(connection.tenant, 'domain_url', None)
        except Exception:
            record.db_alias = "N/A"
            record.schema_name = "N/A"
            record.domain_url = "N/A"

        return formatter.format(record) # Use the formatter instance.
        #  The original super().format(record)  would use the *class* formatter.

def setup_logging():
    """
    Sets up colored logging for the application with tenant context.  This
    function should be called once, typically in your Django app's `ready()`
    method in `apps.py`, or in your main `__init__.py`.
    """
    # Create logger (get it by name, so you can configure it once)
    logger = logging.getLogger(__name__)  # Or a specific logger name
    logger.setLevel(logging.DEBUG)  # Set the overall logging level here

    # Check if handler already exists to avoid adding multiple handlers
    if any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        return  # Logging already configured, do nothing

    # Create console handler
    ch = logging.StreamHandler(sys.stdout) #  Use sys.stdout
    ch.setLevel(logging.DEBUG)  # Set the handler's level

    # Create and set the formatter
    formatter = TenantContextFormatter()
    ch.setFormatter(formatter)

    # Add the filter and handler to the logger
    ch.addFilter(TenantContextFilter())
    logger.addHandler(ch)
    return logger # Returns the logger



# Example usage (in views.py, models.py, etc.)
# Ensure setup_colored_logging() is called during Django app initialization.
# logger = logging.getLogger(__name__) # Get the logger.
#
# def some_view(request):
#     logger.debug("This is a debug message in a view.")
#     logger.info("This is an info message.")
#     logger.warning("This is a warning message.")
#     logger.error("This is an error message.")
#     logger.critical("This is a critical message.")
#     # ... your view logic ...
#     return HttpResponse("View executed. Check the console for logs.")

