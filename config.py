"""
config.py — 모든 파라미터를 한 곳에서 관리
코드 수정 없이 config만 바꿔 전략 변경 가능하도록 설계
"""
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
            {"ticker": "VOO",  "name": "Vanguard S&P 500 ETF",        "weight": 0.35},
            {"ticker": "QQQ",  "name": "Invesco QQQ Trust",            "weight": 0.10},
            {"ticker": "SCHD", "name": "Schwab US Dividend Equity",    "weight": 0.25},
            {"ticker": "BND",  "name": "Vanguard Total Bond Market",   "weight": 0.30},
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
        {"ticker": "VOO",  "name": "Vanguard S&P 500 ETF",        "weight": 0.40},
        {"ticker": "QQQ",  "name": "Invesco QQQ Trust",            "weight": 0.20},
        {"ticker": "SCHD", "name": "Schwab US Dividend Equity",    "weight": 0.20},
        {"ticker": "BND",  "name": "Vanguard Total Bond Market",   "weight": 0.20},
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

    # ── 종목 풀 (실제 운용: Russell 1000 / KOSPI 200 전종목 권장) ──
    us_pool: List[str] = field(default_factory=lambda: [
        # 빅테크
        "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMZN", "TSLA",
        # 금융
        "JPM", "V", "MA", "BRK-B", "GS", "BAC", "MS",
        # 헬스케어
        "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO",
        # 소비재 (필수)
        "PG", "KO", "COST", "WMT", "MCD", "PEP", "CL",
        # 산업재
        "CAT", "DE", "HON", "LMT", "RTX", "GE", "UPS",
        # 에너지
        "XOM", "CVX", "COP", "SLB",
        # 유틸리티
        "NEE", "DUK", "SO",
        # 리츠
        "AMT", "PLD", "EQIX",
    ])

    kr_pool: List[str] = field(default_factory=lambda: [
        "005930.KS",  # 삼성전자
        "000660.KS",  # SK하이닉스
        "005490.KS",  # POSCO홀딩스
        "035420.KS",  # NAVER
        "005380.KS",  # 현대차
        "068270.KS",  # 셀트리온
        "051910.KS",  # LG화학
        "006400.KS",  # 삼성SDI
        "035720.KS",  # 카카오
        "000270.KS",  # 기아
        "096770.KS",  # SK이노베이션
        "028260.KS",  # 삼성물산
        "017670.KS",  # SK텔레콤
        "030200.KS",  # KT
        "066570.KS",  # LG전자
    ])
