"""
backtest.py — 워크포워드 백테스팅 엔진

설계 원칙:
  ① Lookahead Bias 방지
       - 각 리밸런싱 시점 T에서는 T 이전 데이터만 사용
       - yfinance fundamentals는 점-시간(point-in-time) 보장 X
         → 백테스트에서는 가격 기반 팩터(기술적 시그널)만 사용
         → 현 분기 재무지표는 실시간 추천에서만 활용 (정직한 분리)

  ② 거래비용 반영
       - 매 리밸런싱마다 회전율(turnover)에 비례한 비용 차감
       - US: 10bps, KR: 30bps (수수료 + 슬리피지)

  ③ 시장 국면 필터 연동
       - 각 시점의 실제 VIX + SPY MA200으로 투자 가능 여부 판단
       - 관망 시 현금 보유 (수익률 0, 리스크 0)

  ④ 성과 지표 계산
       - CAGR, 변동성, 샤프, 소르티노, MDD, 칼마, 알파, 베타

  전략 시그널 (Lookahead-free, 가격 기반):
       ─────────────────────────────────────
       모멘텀 팩터   40점  (60일 수익률)
       추세 팩터     40점  (현재가 vs MA60)
       RSI 팩터      20점  (RSI 위치)
       ─────────────────────────────────────
"""

import logging
from dataclasses import asdict
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from config import AdvisorConfig
from models import PerformanceStats, BacktestResult

logger = logging.getLogger(__name__)


# ==============================================================
# 성과 지표 계산기
# ==============================================================

class PerformanceMetrics:

    @staticmethod
    def compute(
        equity_curve: pd.Series,
        benchmark_curve: pd.Series,
        risk_free_rate: float = 0.045,
        name: str = "Strategy",
        periods_per_year: int = 12,
    ) -> PerformanceStats:
        """
        equity_curve    : 포트폴리오 가치 시계열 (기준값 1.0)
        benchmark_curve : 벤치마크 가치 시계열 (기준값 1.0)
        periods_per_year: 시계열 샘플링 주기 (월간=12, 일간=252)

        ※ 과거 버그 메모:
          n_years = len(ret) / 252 로 계산하면 월간 데이터에서
          n_years ≈ 0.28 이 되어 CAGR이 폭발했음.
          → 실제 인덱스 날짜 차이로 계산하도록 수정.
        """
        ret  = equity_curve.pct_change().dropna()
        bret = benchmark_curve.reindex(ret.index).pct_change().dropna()

        # ── 기간 (실제 날짜 기준, 단위 안전) ───────────────────
        if len(equity_curve) >= 2:
            days = (equity_curve.index[-1] - equity_curve.index[0]).days
            n_years = max(days / 365.25, 1e-9)
        else:
            n_years = 1e-9

        # ── CAGR ───────────────────────────────────────────────
        total_return = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1)
        cagr = float((1 + total_return) ** (1 / n_years) - 1) if total_return > -1 else -1.0

        bm_total = float(benchmark_curve.iloc[-1] / benchmark_curve.iloc[0] - 1)
        bm_cagr  = float((1 + bm_total) ** (1 / n_years) - 1) if bm_total > -1 else -1.0

        # 비정상값 경고
        if abs(cagr) > 5 or abs(bm_cagr) > 5:
            logger.warning(
                f"[{name}] 비정상 CAGR 감지: 전략 {cagr:.2%}, 벤치 {bm_cagr:.2%}. "
                f"입력 시계열을 점검하세요."
            )

        # ── 변동성·샤프·소르티노 (periods_per_year 일관 적용) ──
        af = np.sqrt(periods_per_year)
        vol = float(ret.std() * af)

        rf_period = risk_free_rate / periods_per_year
        excess    = ret - rf_period
        sharpe    = float(excess.mean() / excess.std() * af) if excess.std() > 0 else 0.0

        down_ret = ret[ret < rf_period]
        down_std = float(down_ret.std() * af) if len(down_ret) > 1 else 0.0
        sortino  = float((ret.mean() - rf_period) * periods_per_year / down_std) if down_std > 0 else 0.0

        # ── 최대 낙폭 (equity_curve 직접 사용) ─────────────────
        roll_max = equity_curve.cummax()
        dd       = (equity_curve - roll_max) / roll_max
        max_dd   = float(dd.min())

        calmar = float(cagr / abs(max_dd)) if max_dd != 0 else 0.0

        # ── 월간 승률 ──────────────────────────────────────────
        if periods_per_year >= 12:
            monthly = ret  # 이미 월간 이상 빈도이면 그대로
        else:
            monthly = ret.resample("ME").apply(lambda x: (1 + x).prod() - 1)
        win_rate = float((monthly > 0).mean()) if len(monthly) > 0 else 0.0

        # ── 알파·베타 (CAPM) ───────────────────────────────────
        common = pd.concat([ret.rename("r"), bret.rename("b")], axis=1, join="inner").dropna()
        if len(common) > 10 and common["b"].std() > 0:
            r = common["r"].values
            b = common["b"].values
            beta  = float(np.cov(r, b)[0, 1] / np.var(b))
            alpha = float(
                (ret.mean() - rf_period - beta * (bret.mean() - rf_period)) * periods_per_year
            )
        else:
            beta, alpha = 1.0, 0.0

        return PerformanceStats(
            strategy_name=name,
            cagr=cagr, volatility=vol, sharpe=sharpe, sortino=sortino,
            max_drawdown=max_dd, calmar=calmar, total_return=total_return,
            win_rate=win_rate, alpha=alpha, beta=beta,
            benchmark_cagr=bm_cagr,
            equity_curve=equity_curve,
            monthly_returns=monthly,
            drawdown_series=dd,
        )


# ==============================================================
# 백테스팅 엔진
# ==============================================================

class BacktestEngine:
    """
    워크포워드 월간 리밸런싱 백테스트

    사용 예:
        engine = BacktestEngine(cfg)
        result = engine.run(us_pool, kr_pool)
        print(result.stats.summary())
    """

    def __init__(self, cfg: Optional[AdvisorConfig] = None):
        self.cfg = cfg or AdvisorConfig()

    # ── Public ─────────────────────────────────────────────────

    def run(
        self,
        us_pool: Optional[List[str]] = None,
        kr_pool: Optional[List[str]] = None,
    ) -> BacktestResult:

        us_pool = us_pool or self.cfg.us_pool
        kr_pool = kr_pool or self.cfg.kr_pool
        all_tickers = us_pool + kr_pool + [self.cfg.us_benchmark, "^VIX", self.cfg.us_market]

        start = self.cfg.backtest_start
        end   = self.cfg.backtest_end

        print(f"\n[백테스트] {start} ~ {end}")
        print(f"  종목 풀: 미국 {len(us_pool)}개 / 한국 {len(kr_pool)}개")
        print("  가격 데이터 다운로드 중...\n")

        # ① 전체 가격 데이터 일괄 다운로드 (효율 극대화)
        prices = self._bulk_download(all_tickers, start, end)
        vix_series = self._get_series(prices, "^VIX")
        spy_series = self._get_series(prices, self.cfg.us_benchmark)
        market_series = self._get_series(prices, self.cfg.us_market)

        # ② 리밸런싱 날짜 생성 (월말)
        rebalance_dates = pd.date_range(start=start, end=end, freq=self.cfg.rebalance_freq)

        # ③ 시뮬레이션
        portfolio_values = []
        current_holdings: dict = {}   # ticker → weight
        log = []

        portfolio_value = 1.0

        for i, rb_date in enumerate(rebalance_dates[:-1]):
            next_date = rebalance_dates[i + 1]

            # 시장 국면 체크 (T 시점까지의 데이터만)
            regime = self._regime_at(market_series, vix_series, rb_date)
            eq_ratio = self._equity_ratio(regime["vix"])

            if not regime["investable"] or eq_ratio == 0:
                # 관망: 현금 보유 (수익률 = 0)
                period_ret = 0.0
                new_holdings = {}
                log.append({
                    "date": rb_date.strftime("%Y-%m"),
                    "action": "CASH",
                    "holdings": [],
                    "period_return": 0.0,
                    "vix": round(regime["vix"], 1),
                })
            else:
                # 종목 선정 (T 시점까지 데이터)
                us_top = self._select_stocks(prices, us_pool, rb_date,
                                             self.cfg.backtest_top_n, "US")
                kr_top = self._select_stocks(prices, kr_pool, rb_date,
                                             self.cfg.backtest_top_n // 2 or 1, "KR")

                # 예산 배분
                if kr_top:
                    us_budget = eq_ratio * self.cfg.us_weight
                    kr_budget = eq_ratio * self.cfg.kr_weight
                else:
                    us_budget = eq_ratio
                    kr_budget = 0.0

                new_holdings = self._allocate_weights(us_top, us_budget, self.cfg.us_pool)
                new_holdings.update(self._allocate_weights(kr_top, kr_budget, self.cfg.kr_pool))

                # 거래비용 차감 (회전율 기반)
                turnover = self._turnover(current_holdings, new_holdings)
                us_turn = sum(abs(v) for t, v in
                              {k: new_holdings.get(k, 0) - current_holdings.get(k, 0)
                               for k in set(new_holdings) | set(current_holdings)
                               if k in self.cfg.us_pool}.items())
                kr_turn = turnover - us_turn
                cost = us_turn * self.cfg.us_cost_bps + kr_turn * self.cfg.kr_cost_bps

                # 보유 종목 수익률 계산
                period_ret = self._period_return(prices, new_holdings, rb_date, next_date) - cost
                current_holdings = new_holdings.copy()

                log.append({
                    "date": rb_date.strftime("%Y-%m"),
                    "action": "INVEST",
                    "holdings": [(t, round(w * 100, 1)) for t, w in new_holdings.items()],
                    "period_return": round(period_ret * 100, 2),
                    "cost_bps": round(cost * 10000, 1),
                    "vix": round(regime["vix"], 1),
                })

            portfolio_value *= (1 + period_ret)
            portfolio_values.append((next_date, portfolio_value))

        # ④ 성과 지표 계산
        port_curve = pd.Series(
            [v for _, v in portfolio_values],
            index=pd.DatetimeIndex([d for d, _ in portfolio_values]),
            name="Strategy"
        )

        # 벤치마크 (SPY 동일 기간)
        spy_aligned = spy_series.reindex(port_curve.index, method="ffill").dropna()
        spy_curve   = spy_aligned / spy_aligned.iloc[0]

        # 월간 리밸런싱 → periods_per_year=12 명시 (단위 안전)
        stats     = PerformanceMetrics.compute(port_curve, spy_curve,
                                               self.cfg.risk_free_rate, "Advisor v3",
                                               periods_per_year=12)
        bm_stats  = PerformanceMetrics.compute(spy_curve, spy_curve,
                                               self.cfg.risk_free_rate, "SPY (Benchmark)",
                                               periods_per_year=12)

        result = BacktestResult(
            config_snapshot=asdict(self.cfg),
            stats=stats,
            benchmark_stats=bm_stats,
            rebalance_log=log,
        )

        self._print_report(result)
        return result

    # ── 데이터 유틸 ────────────────────────────────────────────

    def _bulk_download(self, tickers: List[str], start: str, end: str) -> pd.DataFrame:
        """yfinance 벌크 다운로드 → 종가 DataFrame"""
        try:
            raw = yf.download(tickers, start=start, end=end,
                              auto_adjust=True, progress=False)
            # MultiIndex 처리
            if isinstance(raw.columns, pd.MultiIndex):
                close = raw["Close"].copy()
            else:
                close = raw[["Close"]].copy()
            close.index = pd.to_datetime(close.index)
            return close.ffill()
        except Exception as e:
            logger.error(f"벌크 다운로드 실패: {e}")
            return pd.DataFrame()

    @staticmethod
    def _get_series(prices: pd.DataFrame, ticker: str) -> pd.Series:
        if ticker in prices.columns:
            return prices[ticker].dropna()
        return pd.Series(dtype=float)

    # ── 시장 국면 (특정 날짜 기준) ────────────────────────────

    @staticmethod
    def _regime_at(market: pd.Series, vix: pd.Series, date: pd.Timestamp) -> dict:
        """date 이전 데이터만 사용하여 국면 판단 (Lookahead-free)"""
        try:
            m = market[market.index <= date]
            v = vix[vix.index <= date]
            if len(m) < 200 or len(v) == 0:
                return {"investable": False, "vix": 25.0}
            cur    = float(m.iloc[-1])
            ma200  = float(m.rolling(200).mean().iloc[-1])
            vix_v  = float(v.iloc[-1])
            above200 = cur > ma200
            investable = above200 and vix_v < 35
            return {"investable": investable, "vix": vix_v}
        except Exception:
            return {"investable": False, "vix": 25.0}

    def _equity_ratio(self, vix: float) -> float:
        for threshold, ratio in self.cfg.vix_equity_table:
            if vix < threshold:
                return ratio
        return 0.0

    # ── 종목 선정 (Lookahead-free 기술적 시그널) ──────────────

    def _select_stocks(
        self,
        prices: pd.DataFrame,
        pool: List[str],
        date: pd.Timestamp,
        top_n: int,
        country: str,
    ) -> List[Tuple[str, float]]:
        """
        T 이전 데이터로 기술적 시그널 계산 → 상위 top_n 선정
        
        시그널 (100점):
          모멘텀(60일)  40점
          MA 추세       40점
          RSI(14)       20점
        """
        scores = {}
        for ticker in pool:
            if ticker not in prices.columns:
                continue
            try:
                hist = prices[ticker][prices.index <= date].dropna()
                if len(hist) < 70:
                    continue

                # 모멘텀(60일): 40점
                mom  = float((hist.iloc[-1] - hist.iloc[-60]) / hist.iloc[-60])
                if   mom > 0.15:   m_score = 40
                elif mom > 0.08:   m_score = 30
                elif mom > 0.02:   m_score = 20
                elif mom > -0.02:  m_score = 10
                else:              m_score =  0

                # MA 추세: 40점
                ma20 = float(hist.rolling(20).mean().iloc[-1])
                ma60 = float(hist.rolling(60).mean().iloc[-1])
                cur  = float(hist.iloc[-1])
                if   cur > ma20 > ma60: t_score = 40
                elif cur > ma60:        t_score = 20
                else:                   t_score =  0

                # RSI(14): 20점 (45~65 이상적)
                delta = hist.diff()
                gain  = delta.where(delta > 0, 0.0).rolling(14).mean()
                loss  = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
                rsi   = float((100 - 100 / (1 + gain / loss.replace(0, np.nan))).iloc[-1])
                if   45 <= rsi <= 65: r_score = 20
                elif 35 <= rsi <  45: r_score = 14
                elif 65 <  rsi <= 75: r_score =  8
                elif rsi >  75:       r_score =  2
                else:                 r_score =  6   # < 35

                scores[ticker] = m_score + t_score + r_score

            except Exception:
                continue

        if not scores:
            return []

        # 섹터 제한 적용 (섹터 정보를 여기서 yfinance로 가져오면 lookahead 없음)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]

    # ── 비중 배분 ─────────────────────────────────────────────

    @staticmethod
    def _allocate_weights(
        ranked: List[Tuple[str, float]],
        budget: float,
        pool: List[str],
    ) -> dict:
        """점수 비례 비중 배분"""
        if not ranked or budget == 0:
            return {}
        total_score = sum(s for _, s in ranked) or 1.0
        return {t: (s / total_score) * budget for t, s in ranked}

    # ── 수익률 계산 ────────────────────────────────────────────

    def _period_return(
        self,
        prices: pd.DataFrame,
        holdings: dict,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> float:
        """보유 종목 가중 합산 수익률"""
        total_ret = 0.0
        for ticker, weight in holdings.items():
            if ticker not in prices.columns:
                continue
            period = prices[ticker][(prices.index > start) & (prices.index <= end)].dropna()
            if len(period) < 2:
                continue
            r = float((period.iloc[-1] - period.iloc[0]) / period.iloc[0])
            total_ret += r * weight
        return total_ret

    # ── 회전율 계산 ────────────────────────────────────────────

    @staticmethod
    def _turnover(old: dict, new: dict) -> float:
        all_tickers = set(old) | set(new)
        return sum(abs(new.get(t, 0) - old.get(t, 0)) for t in all_tickers) / 2

    # ── 결과 출력 ─────────────────────────────────────────────

    @staticmethod
    def _print_report(result: BacktestResult):
        print(result.stats.summary())
        print(f"\n  {'날짜':<10}  {'상태':<8}  {'VIX':>5}  {'수익률':>8}  주요 보유")
        print("  " + "-" * 72)
        for entry in result.rebalance_log[-12:]:   # 최근 12개월만 출력
            action = entry["action"]
            holds  = ", ".join(f"{t}({w}%)" for t, w in entry["holdings"][:3]) \
                     if entry["holdings"] else "현금"
            ret_str = f"{entry['period_return']:>+.2f}%" if entry["period_return"] != 0 else "   0.00%"
            print(f"  {entry['date']:<10}  {action:<8}  {entry['vix']:>5.1f}  {ret_str:>8}  {holds}")
        print()
        print("  ※ 백테스트는 가격 기반 기술적 시그널만 사용 (펀더멘털 Lookahead 방지)")
        print("  ※ 과거 성과는 미래 수익을 보장하지 않습니다.\n")
