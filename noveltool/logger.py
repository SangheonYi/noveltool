import logging
import os
from datetime import datetime


def setup(log_dir: str = 'logs', level: str = 'INFO') -> str:
    """파일 로거를 초기화하고 로그 파일 경로를 반환한다."""
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(log_dir, f'{timestamp}.log')

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger('noveltool')
    logger.setLevel(numeric_level)
    logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setLevel(numeric_level)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)-5s] %(message)s',
        datefmt='%H:%M:%S',
    ))
    logger.addHandler(fh)
    logger.propagate = False

    logger.info('=== noveltool 로그 시작 (level=%s) ===', level.upper())
    return log_path


def get() -> logging.Logger:
    return logging.getLogger('noveltool')
