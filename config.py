"""
config.py — 모든 파라미터를 한 곳에서 관리
코드 수정 없이 config만 바꿔 전략 변경 가능하도록 설계
"""
import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Tuple


def apply_unemployed_mode(cfg: "AdvisorConfig") -> "AdvisorConfig":
    """
    백수/소득 단절 상태에서 적용할 보수적 파라미터 오버라이드.
      - 단일 종목 비중 상한:   40% → 20%
      - 종목 수:               3   → 5  (분산 강화)
      - 섹터 최대 종목 수:     2   → 2  (유지)
      - VIX 임계 테이블:       30 초과 시 즉시 50% 이하 축소
      - 한국 비중:             0.20 → 0.10 (환율·과열 리스크 축소)
      - 코어 ETF 풀:           채권(BND) 비중 ↑, QQQ 비중 ↓
    """
    return replace(
        cfg,
        top_n=5,
        single_stock_cap=0.20,
        kr_weight=0.10,
        us_weight=0.90,
        vix_equity_table=[
            (20, 0.90),
            (25, 0.70),
            (30, 0.50),
            (float("inf"), 0.0),
        ],
        core_etf_pool=[
            {"ticker": "VOO",  "name": "Vanguard S&P 500 ETF",        "weight": 0.30},
            {"ticker": "QQQ",  "name": "Invesco QQQ Trust",            "weight": 0.10},
            {"ticker": "SCHD", "name": "Schwab US Dividend Equity",    "weight": 0.25},
            {"ticker": "BND",  "name": "Vanguard Total Bond Market",   "weight": 0.30},
            {"ticker": "EWY",  "name": "iShares MSCI Korea ETF",       "weight": 0.05},
        ],
    )


@dataclass
class AdvisorConfig:

    # ── 시장 티커 ──────────────────────────────────────────────
    us_market:  str = "^GSPC"
    kr_market:  str = "^KS11"
    vix_ticker: str = "^VIX"
    us_benchmark: str = "SPY"   # 백테스트 벤치마크

    # ── Phase 1: 시장 국면 다차원 신호용 티커/임계값 ───────────
    short_rate_ticker: str = "^IRX"   # 13주 (3개월 단기금리)
    long_rate_ticker:  str = "^TNX"   # 10년 장기금리
    offensive_sector_etfs: List[str] = field(default_factory=lambda: [
        "XLK", "XLY", "XLF",  # 기술, 임의소비재, 금융
    ])
    defensive_sector_etfs: List[str] = field(default_factory=lambda: [
        "XLP", "XLU", "XLV",  # 필수소비재, 유틸, 헬스케어
    ])

    # 5개 신호 가중치 (합 = 1.0)
    signal_weights: Dict[str, float] = field(default_factory=lambda: {
        "trend":           0.25,
        "vix":             0.20,
        "breadth":         0.20,
        "yield_curve":     0.15,
        "sector_rotation": 0.20,
    })

    # ── Phase 3: 역발상 매수 트리거 임계값 ─────────────────────
    fear_vix_threshold:        float = 30.0
    fear_drawdown_threshold:   float = -0.15   # MA200 대비 -15% 이상 하락

    # ── 포트폴리오 비중 ────────────────────────────────────────
    us_weight: float = 0.80     # 미국 기본 비중
    kr_weight: float = 0.20     # 한국 기본 비중
    top_n:     int   = 3        # 최종 선정 종목 수 (국가별)

    # ── 섹터 집중도 제한 ───────────────────────────────────────
    max_per_sector:   int   = 2    # 동일 섹터 최대 종목 수
    sector_weight_cap: float = 0.40  # 단일 섹터 포트폴리오 비중 상한
    single_stock_cap:  float = 0.40  # 단일 종목 비중 상한

    # ── 거래비용 (소수점, 0.001 = 0.1%) ───────────────────────
    us_cost_bps: float = 0.0010   # 미국: 수수료+슬리피지
    kr_cost_bps: float = 0.0030   # 한국: 수수료+슬리피지+세금

    # ── 데이터 캐시 ────────────────────────────────────────────
    cache_dir:      str = ".advisor_cache"
    cache_ttl_hours: int = 6       # 6시간 후 만료 (장중 재다운로드 방지)

    # ── Finnhub (미국 종목 펀더멘털, yfinance + Twelve Data 무료 한계 우회) ──
    # 환경변수 FINNHUB_API_KEY 또는 빈 문자열 (없으면 yfinance fallback)
    # 무료 플랜: 60 calls/min, 미국 주식 전체 지원, 한국 주식은 미지원
    finnhub_key:  str = field(default_factory=lambda: os.environ.get("FINNHUB_API_KEY", ""))
    finnhub_url:  str = "https://finnhub.io/api/v1"
    # 60/min = 1초/호출이면 충분, 안전 마진 1.2초
    finnhub_throttle_seconds: float = 1.2

    # ── 백테스트 ───────────────────────────────────────────────
    backtest_start: str = "2019-01-01"
    backtest_end:   str = "2024-12-31"
    rebalance_freq: str = "ME"      # pandas offset alias (월말)
    backtest_top_n: int = 5         # 백테스트 선정 종목 수 (다양성 확보)
    risk_free_rate: float = 0.045   # 연 무위험수익률 (현재 미국 T-Bill 기준)

    # ── 코어/위성 구조 ────────────────────────────────────────
    # 코어 = 인덱스/안전자산 ETF (펀더멘털 게이트 우회)
    # 위성 = 개별 종목 스크리닝 통과 종목
    core_ratio: float = 0.50    # 투자금 중 코어 비중 (app.py에서 자산 tier별 override)

    core_etf_pool: List[Dict] = field(default_factory=lambda: [
        {"ticker": "VOO",  "name": "Vanguard S&P 500 ETF",         "weight": 0.35},
        {"ticker": "QQQ",  "name": "Invesco QQQ Trust",            "weight": 0.20},
        {"ticker": "SCHD", "name": "Schwab US Dividend Equity",    "weight": 0.20},
        {"ticker": "BND",  "name": "Vanguard Total Bond Market",   "weight": 0.15},
        {"ticker": "EWY",  "name": "iShares MSCI Korea ETF",       "weight": 0.10},
    ])

    # 코어 ETF는 시장 추종 — RSI 기반 진입 보류 부적절.
    # 목표 금액을 이 기간에 걸쳐 매월 균등 적립 (DCA).
    dca_months: int = 12

    # ── VIX → 주식 비중 상한 테이블 ────────────────────────────
    vix_equity_table: List[Tuple[float, float]] = field(default_factory=lambda: [
        (20, 1.00),
        (25, 0.90),
        (30, 0.75),
        (35, 0.55),
        (float("inf"), 0.00),   # EXTREME → 전액 현금
    ])

    # ── 섹터별 정상 PBR (시장 장기 평균 근사값) ──────────────
    sector_pbr_norm: Dict[str, float] = field(default_factory=lambda: {
        "Technology":              8.0,
        "Communication Services":  5.5,
        "Consumer Cyclical":       5.0,
        "Healthcare":              4.5,
        "Consumer Defensive":      4.0,
        "Industrials":             4.0,
        "Financial Services":      1.8,
        "Energy":                  2.5,
        "Basic Materials":         2.0,
        "Utilities":               1.8,
        "Real Estate":             2.0,
    })

    # ── 종목 풀 ────────────────────────────────────────────────
    # Finnhub 무료 60회/분 → 미국 20개 안정 동작
    # 한국은 Finnhub 무료 미지원 → 풀에서 제거, 한국 노출은 코어 ETF의 EWY로 대체
    us_pool: List[str] = field(default_factory=lambda: [
        # 빅테크 / 인터넷 (7)
        "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMZN", "TSLA",
        # 금융 (3)
        "JPM", "V", "MA",
        # 헬스케어 (3)
        "UNH", "JNJ", "LLY",
        # 소비재 (4)
        "COST", "WMT", "PG", "KO",
        # 산업/에너지 (2) — BRK.B는 ticker 형식이 yfinance(BRK-B)/Finnhub(BRK.B)
        # 충돌해서 제외 (향후 ticker 매핑 도입 시 복원 가능)
        "HD", "XOM",
    ])

    # 한국 개별주는 데이터 가용성 이슈로 비활성 (코어 EWY ETF로 한국 노출 유지)
    kr_pool: List[str] = field(default_factory=lambda: [])

    # ── 종목 메타 (Finnhub /profile 호출 절약) ─────────────────
    # ticker → {"name": ..., "sector": ...}
    stock_meta: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "AAPL":  {"name": "Apple Inc.",            "sector": "Technology"},
        "MSFT":  {"name": "Microsoft Corp.",       "sector": "Technology"},
        "GOOGL": {"name": "Alphabet Inc.",         "sector": "Communication Services"},
        "META":  {"name": "Meta Platforms",        "sector": "Communication Services"},
        "NVDA":  {"name": "NVIDIA Corp.",          "sector": "Technology"},
        "AMZN":  {"name": "Amazon.com",            "sector": "Consumer Cyclical"},
        "TSLA":  {"name": "Tesla Inc.",            "sector": "Consumer Cyclical"},
        "JPM":   {"name": "JPMorgan Chase",        "sector": "Financial Services"},
        "V":     {"name": "Visa Inc.",             "sector": "Financial Services"},
        "MA":    {"name": "Mastercard",            "sector": "Financial Services"},
        "UNH":   {"name": "UnitedHealth Group",    "sector": "Healthcare"},
        "JNJ":   {"name": "Johnson & Johnson",     "sector": "Healthcare"},
        "LLY":   {"name": "Eli Lilly",             "sector": "Healthcare"},
        "COST":  {"name": "Costco Wholesale",      "sector": "Consumer Defensive"},
        "WMT":   {"name": "Walmart Inc.",          "sector": "Consumer Defensive"},
        "PG":    {"name": "Procter & Gamble",      "sector": "Consumer Defensive"},
        "KO":    {"name": "Coca-Cola Co.",         "sector": "Consumer Defensive"},
        "HD":    {"name": "Home Depot",            "sector": "Consumer Cyclical"},
        "XOM":   {"name": "Exxon Mobil",           "sector": "Energy"},
    })
