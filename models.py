"""
models.py — 시스템 전반에서 사용하는 데이터 클래스
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import pandas as pd


# ──────────────────────────────────────────────────────────────
# 투자자 프로필 (자산 분리 + 고용 상태)
# ──────────────────────────────────────────────────────────────

@dataclass
class InvestorProfile:
    """
    개인 자산 현황을 입력받아 '실제 투자 가능 금액'을 산출

    employment_status:
      "employed"   : 안정 — 표준 파라미터 사용
      "unemployed" : 백수 — 보수적 파라미터 자동 적용
                     (단일 종목 상한 ↓, 분산 ↑, VIX 컷오프 ↓, 신규 자금 0)
      "transition" : 면접/이직 진행 중 — 안정과 백수의 중간

    monthly_expense, emergency_months 로 비상자금 자동 산정.
    """
    # 자산 (원화 기준)
    deposits:           float = 0.0   # 정기예탁금·CD 등 안전자산
    checking:           float = 0.0   # 입출금 (즉시 사용 가능)
    purpose_savings:    float = 0.0   # 목적자금 (청약, 주택 등 — 건드리지 않음)
    retirement:         float = 0.0   # IRP 등 노후자금 (건드리지 않음)

    # 월 자동 적립 (참고용)
    monthly_purpose:    float = 0.0
    monthly_retirement: float = 0.0

    # 생활비·비상자금
    monthly_expense:    float = 2_000_000   # 월 생활비
    emergency_months:   int   = 6           # 비상자금 개월 수

    # 고용 상태
    employment_status:  str   = "employed"  # employed / unemployed / transition

    # 환율 (USD 환산용)
    usd_krw_rate:       float = 1380.0

    # 투자 성향 슬라이더로 결정 — '의도적으로 예금에 묶어둘 금액'
    # liquid_pool에서 비상자금과 함께 제외되어 investable_capital이 줄어듦
    deposit_keep_amount: float = 0.0

    # ── 파생 지표 ──────────────────────────────────────────────

    @property
    def total_assets(self) -> float:
        return self.deposits + self.checking + self.purpose_savings + self.retirement

    @property
    def emergency_reserve(self) -> float:
        """비상자금 (필수 보유)"""
        if self.employment_status == "unemployed":
            return self.monthly_expense * self.emergency_months
        if self.employment_status == "transition":
            return self.monthly_expense * max(self.emergency_months - 2, 3)
        return self.monthly_expense * max(self.emergency_months - 3, 3)  # 안정: 3개월

    @property
    def liquid_pool(self) -> float:
        """현금성 자산 (정기예탁금 + 입출금)"""
        return self.deposits + self.checking

    @property
    def investable_capital(self) -> float:
        """advisor 시스템에 적용할 자유 투자 가능 금액 (원)
        = 현금성자산 - 비상자금 - (의도적으로 예금에 묶어둘 금액)
        """
        free = self.liquid_pool - self.emergency_reserve - self.deposit_keep_amount
        return max(free, 0.0)

    @property
    def investable_usd(self) -> float:
        return self.investable_capital / self.usd_krw_rate


# ──────────────────────────────────────────────────────────────
# 진입 신호
# ──────────────────────────────────────────────────────────────

@dataclass
class EntryPlan:
    """
    종목별 진입 시점·가격 가이드

    action 종류
      IMMEDIATE     : 즉시 시장가 진입
      SPLIT_BUY     : 3분할 매수 (현재가 / -3% / -6%)
      WAIT_PULLBACK : 눌림목(MA20) 도달 시 진입
      AVOID         : 진입 보류 (추세 약화/극단 과매수)
    """
    action:         str             # IMMEDIATE / SPLIT_BUY / WAIT_PULLBACK / AVOID
    label:          str             # 한국어 표시
    rationale:      str             # 결정 사유
    current_price:  float
    target_levels:  List[Tuple[float, float]] = field(default_factory=list)
                                    # [(진입가, 비중%), ...]
    confidence:     float = 0.0     # 0.0 ~ 1.0


@dataclass
class MarketRegime:
    market:             str
    is_investable:      bool
    trend_score:        float       # 0.0 ~ 1.0 (가격 vs MA200 + 모멘텀 + 골든크로스)
    risk_level:         str         # LOW / MEDIUM / HIGH / VERY_HIGH / EXTREME
    vix:                float
    price_vs_ma200_pct: float
    detail:             str

    # Phase 1: 다차원 시장 국면 신호 (0.0 ~ 1.0 정규화)
    composite_score:    float = 0.5   # 5개 신호 가중합 → equity_ratio의 기반
    vix_signal:         float = 0.5   # 역발상: VIX 15↓ 약세, 30↑ 강세
    breadth_pct:        float = 0.5   # us_pool 중 MA200 위 종목 비율 (0~1)
    yield_curve_spread: float = 0.0   # 10년-3월(%). 마이너스=역전=침체경고
    yield_curve_signal: float = 0.5   # 위 spread를 0~1로 정규화
    sector_rotation:    float = 0.5   # 공격주(XLK/XLY/XLF) RS vs 방어주(XLP/XLU/XLV)

    # Phase 3: 역발상 매수 트리거
    fear_score:         float = 0.0   # 0~1. VIX 30↑ + 시장 -15%↓일 때 ↑


@dataclass
class StockScore:
    ticker:   str
    name:     str
    country:  str
    sector:   str

    # 펀더멘털 (None = 데이터 없음 → N/A 표기)
    roe:                Optional[float]
    pbr:                Optional[float]
    per:                Optional[float]
    debt_to_equity:     Optional[float]
    fundamental_score:  float       # 0 ~ 100

    # 기술적
    rsi:             float
    trend_score:     float
    momentum_pct:    float
    technical_score: float          # 0 ~ 100

    # 종합
    composite_score: float          # 0 ~ 100
    weight_pct:      float = 0.0    # 최종 포트폴리오 비중 (%)
    invest_amount:   float = 0.0    # 실제 투입 금액 (현지 통화)
    entry_signal:    bool  = False  # 호환용 (즉시 진입 여부)
    entry_plan:      Optional[EntryPlan] = None
    pbr_source:      str   = "info"  # "info" / "computed" / "missing"
    per_source:      str   = "info"  # "info" / "forward" / "missing"

    # Phase 2: 보강 펀더멘털 지표 (None = 데이터 없음)
    fcf_margin:       Optional[float] = None  # FCF / Revenue (%)
    operating_margin: Optional[float] = None  # operatingMargins (%)
    peg:              Optional[float] = None  # 성장률 감안한 PER


@dataclass
class PerformanceStats:
    """백테스트 성과 지표 모음"""
    strategy_name:  str

    cagr:           float   # 연평균 복리 수익률
    volatility:     float   # 연환산 변동성
    sharpe:         float   # 샤프 비율
    sortino:        float   # 소르티노 비율
    max_drawdown:   float   # 최대 낙폭 (음수)
    calmar:         float   # CAGR / |MaxDD|
    total_return:   float   # 전체 기간 수익률
    win_rate:       float   # 월간 양수 수익률 비율

    # 벤치마크 대비
    alpha:          float   # 연환산 알파
    beta:           float   # 시장 베타
    benchmark_cagr: float

    # 시계열
    equity_curve:   pd.Series = field(default_factory=pd.Series)
    monthly_returns: pd.Series = field(default_factory=pd.Series)
    drawdown_series: pd.Series = field(default_factory=pd.Series)

    def summary(self) -> str:
        lines = [
            f"\n{'='*54}",
            f"  📈 {self.strategy_name}",
            f"{'='*54}",
            f"  {'항목':<20} {'전략':>10}  {'벤치마크':>10}",
            f"  {'-'*44}",
            f"  {'CAGR':<20} {self.cagr*100:>9.2f}%  {self.benchmark_cagr*100:>9.2f}%",
            f"  {'연 변동성':<20} {self.volatility*100:>9.2f}%",
            f"  {'샤프 비율':<20} {self.sharpe:>10.2f}",
            f"  {'소르티노 비율':<20} {self.sortino:>10.2f}",
            f"  {'최대 낙폭':<20} {self.max_drawdown*100:>9.2f}%",
            f"  {'칼마 비율':<20} {self.calmar:>10.2f}",
            f"  {'전체 수익률':<20} {self.total_return*100:>9.2f}%",
            f"  {'월간 승률':<20} {self.win_rate*100:>9.2f}%",
            f"  {'알파 (연환산)':<20} {self.alpha*100:>9.2f}%",
            f"  {'베타':<20} {self.beta:>10.2f}",
            f"{'='*54}",
        ]
        return "\n".join(lines)


@dataclass
class BacktestResult:
    """백테스트 전체 결과"""
    config_snapshot:  dict
    stats:            PerformanceStats
    benchmark_stats:  PerformanceStats
    rebalance_log:    List[dict] = field(default_factory=list)  # 매 리밸런싱 기록
