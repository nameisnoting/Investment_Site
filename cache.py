"""
cache.py — 디스크 기반 캐시 (TTL 지원)

역할:
  yfinance는 동일 요청을 반복할수록 느려지고 레이트리밋에 걸림.
  6시간 TTL 캐시로 장중 재호출을 방지하되,
  다음 날 새 데이터는 자동 갱신.

구조:
  .advisor_cache/
    {key}.pkl     ← 데이터 (pandas DataFrame / dict)
    {key}.meta    ← 저장 시각 (timestamp)
"""

import os
import pickle
import time
import hashlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DataCache:

    def __init__(self, cache_dir: str = ".advisor_cache", ttl_hours: float = 6.0):
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_hours * 3600
        os.makedirs(cache_dir, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        """캐시 HIT 시 데이터 반환, MISS / 만료 시 None"""
        data_path, meta_path = self._paths(key)
        if not os.path.exists(data_path) or not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, "r") as f:
                saved_at = float(f.read().strip())
            if time.time() - saved_at > self.ttl_seconds:
                logger.debug(f"캐시 만료: {key}")
                return None
            with open(data_path, "rb") as f:
                data = pickle.load(f)
            logger.debug(f"캐시 HIT: {key}")
            return data
        except Exception as e:
            logger.warning(f"캐시 읽기 실패 ({key}): {e}")
            return None

    def set(self, key: str, data: Any) -> None:
        """데이터를 캐시에 저장"""
        data_path, meta_path = self._paths(key)
        try:
            with open(data_path, "wb") as f:
                pickle.dump(data, f)
            with open(meta_path, "w") as f:
                f.write(str(time.time()))
            logger.debug(f"캐시 저장: {key}")
        except Exception as e:
            logger.warning(f"캐시 쓰기 실패 ({key}): {e}")

    def invalidate(self, key: str) -> None:
        """특정 키 강제 무효화"""
        for path in self._paths(key):
            if os.path.exists(path):
                os.remove(path)

    def clear_all(self) -> None:
        """전체 캐시 삭제"""
        for fname in os.listdir(self.cache_dir):
            os.remove(os.path.join(self.cache_dir, fname))
        logger.info("캐시 전체 초기화 완료")

    # ── 헬퍼 ──────────────────────────────────────────────────

    @staticmethod
    def make_key(*args) -> str:
        """가변 인자로 고유 캐시 키 생성"""
        raw = "_".join(str(a) for a in args)
        return hashlib.md5(raw.encode()).hexdigest()

    def _paths(self, key: str):
        base = os.path.join(self.cache_dir, key)
        return base + ".pkl", base + ".meta"
