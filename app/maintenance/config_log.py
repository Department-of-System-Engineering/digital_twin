import logging.config
import os
import sys
from pathlib import Path


def setup_logging() -> None:
    # Set the log directory directly
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Base filename for the logs
    log_file_path = log_dir / "dt_application.log"

    # Logging configuration dictionary
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            }
        },
        "handlers": {
            "console_handler": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "detailed",
                "level": "INFO"
            },
            "file_handler": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": log_file_path,
                "when": "midnight",
                "interval": 1,
                "backupCount": 30,
                "formatter": "detailed",
                "level": "DEBUG",
                "encoding": "utf-8"
            }
        },
        "root": {
            "level": "DEBUG",
            "handlers": ["console_handler", "file_handler"]
        }
    }

    # Apply configuration
    logging.config.dictConfig(logging_config)

    # Custom exception handler to log uncaught exceptions
    def uncaught_exception_handler(exc_type, exc_value, exc_traceback):
        # Allow KeyboardInterrupt to terminate the program normally
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        # Log the unhandled exception
        logging.getLogger().error(
            "Uncaught exception occurred in Digital Twin",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    # Override the default exception hook
    sys.excepthook = uncaught_exception_handler
