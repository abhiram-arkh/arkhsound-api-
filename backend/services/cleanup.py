import os
import shutil
import logging

logger = logging.getLogger("cleanup")

def cleanup_temp_dir(path: str):
    """Safely removes temporary directory and files after streaming."""
    try:
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"Cleaned up temporary directory: {path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup {path}: {e}")
