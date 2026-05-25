import logging
import os
import sys
from datetime import datetime


class _TeeStream:
    """sys.stdout를 래핑해 콘솔 출력을 로그 파일에도 기록한다."""

    def __init__(self, original, log_path: str):
        self._original = original
        self._log_file = open(log_path, 'a', encoding='utf-8')
        self._buf = ''

    def write(self, data: str) -> int:
        self._original.write(data)
        self._buf += data
        # 개행 단위로 로그 파일에 기록
        if '\n' in self._buf:
            lines = self._buf.split('\n')
            for line in lines[:-1]:
                ts = datetime.now().strftime('%H:%M:%S')
                self._log_file.write(f'{ts} [출력 ] {line}\n')
            self._buf = lines[-1]
        return len(data)

    def flush(self):
        self._original.flush()
        if self._buf:
            ts = datetime.now().strftime('%H:%M:%S')
            self._log_file.write(f'{ts} [출력 ] {self._buf}\n')
            self._buf = ''
        self._log_file.flush()

    def close(self):
        self.flush()
        self._log_file.close()
        sys.stdout = self._original

    # 파일 객체 호환 속성
    def isatty(self) -> bool:
        return self._original.isatty()

    @property
    def encoding(self):
        return self._original.encoding

    @property
    def errors(self):
        return self._original.errors


_tee: _TeeStream | None = None


def setup(log_dir: str = 'logs', level: str = 'INFO') -> str:
    """파일 로거를 초기화하고 stdout를 로그 파일에 미러링한다. 로그 파일 경로 반환."""
    global _tee

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

    # stdout → TeeStream (콘솔 + 로그 파일 동시 출력)
    if _tee is not None:
        _tee.close()
    _tee = _TeeStream(sys.stdout, log_path)
    sys.stdout = _tee

    logger.info('=== noveltool 로그 시작 (level=%s) ===', level.upper())
    return log_path


def get() -> logging.Logger:
    return logging.getLogger('noveltool')
