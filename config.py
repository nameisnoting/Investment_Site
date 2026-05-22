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

    # ── 폭등시그널 모드 전용 풀 ─────────────────────────────────
    # 코어: 안전자산(BND/SCHD) 제거, 성장 중심 (VOO + QQQ)
    surge_core_etf_pool: List[Dict] = field(default_factory=lambda: [
        {"ticker": "VOO", "name": "Vanguard S&P 500 ETF", "weight": 0.55},
        {"ticker": "QQQ", "name": "Invesco QQQ Trust",     "weight": 0.45},
    ])

    # 레버리지 ETF (3x): risk_level에 따라 비중 적용
    leverage_etf_pool: List[Dict] = field(default_factory=lambda: [
        {"ticker": "TQQQ", "name": "ProShares UltraPro QQQ (3x Nasdaq)", "weight": 0.40},
        {"ticker": "SOXL", "name": "Direxion Semiconductor Bull 3x",     "weight": 0.30},
        {"ticker": "UPRO", "name": "ProShares UltraPro S&P 500 (3x)",    "weight": 0.30},
    ])

    # 폭등시그널 종목 풀 (24개, Finnhub 무료에서 데이터 OK 검증 완료)
    surge_pool: List[str] = field(default_factory=lambda: [
        # SaaS / 클라우드 보안 (9)
        "NET", "DDOG", "SNOW", "MDB", "CRWD", "ZS", "OKTA", "S", "PATH",
        # 핀테크 / 디지털 금융 (5)
        "SOFI", "AFRM", "COIN", "HOOD", "UPST",
        # AI / 메타버스 / 차세대 컴퓨팅 (6)
        "PLTR", "AI", "IONQ", "RBLX", "U", "BBAI",
        # 신흥 그로스 (4)
        "DUOL", "APP", "CELH", "MNDY",
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

    # 한국 개별주: Render(공유 IP)에선 yfinance 차단으로 비활성
    # 로컬(ngrok 포함)에선 yfinance가 가정 IP라 작동 → 한국 종목 활성
    # 환경변수 RENDER는 Render가 자동 셋팅하는 값. 다른 클라우드도 명시적으로 SET 가능.
    kr_pool: List[str] = field(default_factory=lambda: [] if os.environ.get("RENDER") else [
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
    ])

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
        # 한국 (yfinance fallback 사용 — 로컬/ngrok에서만 작동)
        "005930.KS": {"name": "Samsung Electronics",  "sector": "Technology"},
        "000660.KS": {"name": "SK hynix",             "sector": "Technology"},
        "005490.KS": {"name": "POSCO Holdings",       "sector": "Basic Materials"},
        "035420.KS": {"name": "NAVER Corp.",          "sector": "Communication Services"},
        "005380.KS": {"name": "Hyundai Motor",        "sector": "Consumer Cyclical"},
        "068270.KS": {"name": "Celltrion",            "sector": "Healthcare"},
        "051910.KS": {"name": "LG Chem",              "sector": "Basic Materials"},
        "006400.KS": {"name": "Samsung SDI",          "sector": "Technology"},
        "035720.KS": {"name": "Kakao Corp.",          "sector": "Communication Services"},
        "000270.KS": {"name": "Kia Corp.",            "sector": "Consumer Cyclical"},
        # ── 폭등시그널 풀 (소형~중형 그로스) ─────────────────
        "NET":   {"name": "Cloudflare",                "sector": "Technology"},
        "DDOG":  {"name": "Datadog",                   "sector": "Technology"},
        "SNOW":  {"name": "Snowflake",                 "sector": "Technology"},
        "MDB":   {"name": "MongoDB",                   "sector": "Technology"},
        "CRWD":  {"name": "CrowdStrike",               "sector": "Technology"},
        "ZS":    {"name": "Zscaler",                   "sector": "Technology"},
        "OKTA":  {"name": "Okta Inc.",                 "sector": "Technology"},
        "S":     {"name": "SentinelOne",               "sector": "Technology"},
        "PATH":  {"name": "UiPath",                    "sector": "Technology"},
        "SOFI":  {"name": "SoFi Technologies",         "sector": "Financial Services"},
        "AFRM":  {"name": "Affirm Holdings",           "sector": "Financial Services"},
        "COIN":  {"name": "Coinbase",                  "sector": "Financial Services"},
        "HOOD":  {"name": "Robinhood Markets",         "sector": "Financial Services"},
        "UPST":  {"name": "Upstart Holdings",          "sector": "Financial Services"},
        "PLTR":  {"name": "Palantir Technologies",     "sector": "Technology"},
        "AI":    {"name": "C3.ai",                     "sector": "Technology"},
        "IONQ":  {"name": "IonQ",                      "sector": "Technology"},
        "RBLX":  {"name": "Roblox",                    "sector": "Communication Services"},
        "U":     {"name": "Unity Software",            "sector": "Technology"},
        "BBAI":  {"name": "BigBear.ai",                "sector": "Technology"},
        "DUOL":  {"name": "Duolingo",                  "sector": "Consumer Cyclical"},
        "APP":   {"name": "AppLovin",                  "sector": "Technology"},
        "CELH":  {"name": "Celsius Holdings",          "sector": "Consumer Defensive"},
        "MNDY":  {"name": "monday.com",                "sector": "Technology"},
        # 레버리지 ETF (3x)
        "TQQQ":  {"name": "ProShares UltraPro QQQ",        "sector": "Leverage ETF"},
        "SOXL":  {"name": "Direxion Semiconductor Bull 3x", "sector": "Leverage ETF"},
        "UPRO":  {"name": "ProShares UltraPro S&P 500",     "sector": "Leverage ETF"},
    })
