import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

def setup_logger(name="ftp_backend", log_level=logging.INFO):
    """
    Set up a logger with both file and console handlers.
    
    Args:
        name: Logger name
        log_level: Logging level (default: INFO)
        
    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    logger.setLevel(log_level)
    
    from platform_support import app_support_dir

    log_dir = os.path.join(app_support_dir(), "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(levelname)s - %(message)s'
    )
    
    # File handler with rotation
    log_file = os.path.join(log_dir, f"ftp_backend_{datetime.now().strftime('%Y%m%d')}.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_formatter)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)  # Only warnings and errors to console
    console_handler.setFormatter(console_formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def get_logger(name="ftp_backend"):
    """Get or create a logger instance"""
    return logging.getLogger(name) if logging.getLogger(name).handlers else setup_logger(name)

class QueueLogHandler(logging.Handler):
    """Custom log handler that puts messages into a queue for GUI display"""
    
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue
        self.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    
    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.put(msg)
        except Exception:
            # Don't let logging errors crash the application
            pass

def add_queue_handler(logger, log_queue):
    """Add a queue handler to an existing logger for GUI integration"""
    queue_handler = QueueLogHandler(log_queue)
    queue_handler.setLevel(logging.INFO)
    logger.addHandler(queue_handler)
    return logger