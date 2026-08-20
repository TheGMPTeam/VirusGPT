"""
Logging configuration with file rotation support.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Config


def setup_logging(log_level: str = None) -> logging.Logger:
    """
    Configure application logging with console and rotating file handlers.

    Args:
        log_level: Override log level (default: from Config.LOG_LEVEL)

    Returns:
        Configured logger instance
    """
    level = getattr(logging, (log_level or Config.LOG_LEVEL).upper(), logging.INFO)

    # Create logger
    logger = logging.getLogger('PocketTTS')
    logger.setLevel(level)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Console handler - simple format
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler - detailed format with rotation
    try:
        log_dir = Path(Config.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / Config.LOG_FILE

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=Config.LOG_MAX_BYTES,
            backupCount=Config.LOG_BACKUP_COUNT,
            encoding='utf-8',
        )
        file_handler.setLevel(level)
        file_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    except Exception as e:
        logger.warning(f'Could not set up file logging: {e}')

    # Suppress noisy third-party loggers
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    return logger


def get_logger(name: str = None) -> logging.Logger:
    """Get a logger instance, optionally with a child name."""
    base_logger = logging.getLogger('PocketTTS')
    if name:
        return base_logger.getChild(name)
    return base_logger
