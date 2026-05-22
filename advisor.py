"""
advisor.py — 실시간 투자 추천 엔진 (v3)

개선 사항 (v2 → v3):
  ① 섹터 집중도 제한  : max_per_sector + sector_weight_cap
  ② 데이터 캐싱       : DataCache로 yfinance 중복 호출 차단
  ③ 종목 풀 확대       : config.py에서 관리 (40개+)
  ④ 과거 분기 재무     : score_with_history()로 lookahead 방지 지원
"""

import logging
import time
from typing import List, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from config import AdvisorConfig, apply_unemployed_mode
from models import MarketRegime, StockScore, EntryPlan, InvestorProfile
from cache import DataCache

logger = logging.getLogger(__name__)


# ==============================================================
# 데이터 페처 (캐시 레이어 통합)
# ==============================================================

class DataFetcher:

    def __init__(self, cache: DataCache, cfg: Optional[AdvisorConfig] = None):
        self.cache = cache
        self.cfg = cfg or AdvisorConfig()
        # Finnhub 분당 60회 제한 → 호출 간 1.2초 throttle
        self._last_finnhub_call: float = 0.0

    def history(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        key = self.cache.make_key("hist", ticker, period)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        for attempt in range(3):
            try:
                df = yf.Ticker(ticker).history(period=period)
                if not df.empty:
                    self.cache.set(key, df)
                    return df
                raise ValueError("빈 데이터")
            except Exception as e:
                if attempt == 2:
                    logger.error(f"데이터 다운로드 실패 ({ticker}): {e}")
                    return pd.DataFrame()
                logger.warning(f"재시도 {attempt+1}/3 ({ticker})")
        return pd.DataFrame()

    def info(self, ticker: str) -> dict:
        """
        펀더멘털 정보 (ROE, PBR, PER, FCF 등).
          미국 종목 + FINNHUB_API_KEY 설정 → Finnhub API 사용 (안정)
          한국 종목 또는 키 없음 → yfinance fallback (Render에선 차단 가능)
        반환 dict는 yfinance info와 호환되는 키 사용 (Screener 코드 무수정).
        """
        key = self.cache.make_key("info", ticker)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        is_korean = ticker.endswith(".KS") or ticker.endswith(".KQ")
        use_finnhub = bool(self.cfg.finnhub_key) and not is_korean

        info: dict = {}
        if use_finnhub:
            info = self._info_finnhub(ticker)
            if not info:
                logger.info(f"{ticker}: Finnhub 실패 → yfinance fallback")
                info = self._info_yfinance(ticker)
        else:
            info = self._info_yfinance(ticker)

        # 종목 풀 메타에서 name/sector 보충 (별도 API 호출 절약)
        meta = self.cfg.stock_meta.get(ticker)
        if meta:
            info.setdefault("longName",  meta.get("name", ticker))
            info.setdefault("shortName", meta.get("name", ticker))
            info.setdefault("sector",    meta.get("sector", "Unknown"))

        if info:
            self.cache.set(key, info)
        return info

    def _info_yfinance(self, ticker: str) -> dict:
        try:
            info = yf.Ticker(ticker).info
            return info or {}
        except Exception as e:
            logger.warning(f"yfinance info 실패 ({ticker}): {e}")
            return {}

    def _info_finnhub(self, ticker: str) -> dict:
        """
        Finnhub /stock/metric → yfinance info와 호환되는 dict로 변환.
        무료 60/min throttle 적용 (호출 간 1.2초).

        주의: ROE/마진 등은 percent 단위 (예: 146.69 = 146.69%, ratio가 아님).
              우리 게이트가 ratio 기준이라 100으로 나눠 정규화함.
        """
        # ── throttle ──
        wait = self.cfg.finnhub_throttle_seconds - (time.time() - self._last_finnhub_call)
        if wait > 0:
            time.sleep(wait)
        self._last_finnhub_call = time.time()

        try:
            r = requests.get(
                f"{self.cfg.finnhub_url}/stock/metric",
                params={"symbol": ticker, "metric": "all", "token": self.cfg.finnhub_key},
                timeout=15,
            )
            data = r.json()
        except Exception as e:
            logger.warning(f"Finnhub 호출 실패 ({ticker}): {e}")
            return {}

        if not isinstance(data, dict) or "error" in data:
            err = data.get("error") if isinstance(data, dict) else data
            logger.warning(f"Finnhub 응답 오류 ({ticker}): {err}")
            return {}

        m = data.get("metric", {}) or {}
        if not m:
            logger.warning(f"Finnhub {ticker}: metric 비어있음")
            return {}

        # ── 단위 변환: Finnhub은 percent, yfinance info는 ratio 관행 ──
        def to_ratio(v):
            try:
                return float(v) / 100.0 if v is not None else None
            except (TypeError, ValueError):
                return None

        def to_pct(v):
            """yfinance의 debtToEquity는 percent (예: 79.548 = 79.548%)"""
            try:
                return float(v) * 100.0 if v is not None else None
            except (TypeError, ValueError):
                return None

        def passthrough(v):
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        # Finnhub → yfinance info 키 매핑
        return {
            "returnOnEquity":    to_ratio(m.get("roeTTM")),                        # 146.69 → 1.4669
            "priceToBook":       passthrough(m.get("pbQuarterly") or m.get("pbAnnual")),
            "trailingPE":        passthrough(m.get("peTTM") or m.get("peNormalizedAnnual")),
            "forwardPE":         passthrough(m.get("peInclExtraTTM")),
            "debtToEquity":      to_pct(m.get("totalDebt/totalEquityQuarterly")
                                        or m.get("totalDebt/totalEquityAnnual")),  # 0.79 → 79.0
            "currentRatio":      passthrough(m.get("currentRatioQuarterly") or m.get("currentRatioAnnual")),
            "marketCap":         passthrough(m.get("marketCapitalization")),
            "revenueGrowth":     to_ratio(m.get("revenueGrowthTTMYoy")),           # 12.76 → 0.1276
            "profitMargins":     to_ratio(m.get("netProfitMarginTTM")),
            "operatingMargins":  to_ratio(m.get("operatingMarginTTM")),
            "pegRatio":          passthrough(m.get("pegRatio")),
            "freeCashflow":      passthrough(m.get("freeCashFlowTTM")),            # 종종 None
            "totalRevenue":      passthrough(m.get("revenueTTM")),                 # 종종 None
        }

    def stockholders_equity(self, ticker: str) -> Optional[float]:
        """
        balance_sheet에서 자기자본(Stockholders Equity) 추출.
        한국 종목에서 yfinance가 priceToBook을 None으로 줄 때
        marketCap / equity 로 PBR을 직접 계산하기 위함.
        """
        key = self.cache.make_key("equity", ticker)
        cached = self.cache.get(key)
        if cached is not None:
            return cached if cached != 0 else None
        try:
            bs = yf.Ticker(ticker).balance_sheet
            if bs is None or bs.empty:
                return None
            for row in ("Stockholders Equity",
                        "Common Stock Equity",
                        "Total Equity Gross Minority Interest"):
                if row in bs.index:
                    series = bs.loc[row].dropna()
                    if len(series) > 0:
                        eq = float(series.iloc[0])  # 가장 최근 분기/연도
                        self.cache.set(key, eq)
                        return eq
            return None
        except Exception as e:
            logger.warning(f"balance_sheet 다운로드 실패 ({ticker}): {e}")
            return None

    def vix(self) -> float:
        key = self.cache.make_key("vix", "latest")
        cached = self.cache.get(key)
        if cached is not None:
            return float(cached)
        try:
            val = float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1])
            self.cache.set(key, val)
            return val
        except Exception as e:
            logger.warning(f"VIX 조회 실패 (기본값 25 사용): {e}")
            return 25.0

    def latest_close(self, ticker: str, period: str = "5d") -> Optional[float]:
        """단순 최신 종가 추출 헬퍼 (yield curve / sector ETF용)"""
        try:
            df = self.history(ticker, period=period)
            if df.empty:
                return None
            return float(df["Close"].iloc[-1])
        except Exception:
            return None


# ==============================================================
# 1단계: 매크로 필터
# ==============================================================

class MacroFilter:
    """
    다차원 시장 국면 분석 (Phase 1).

    5개 신호를 0~1로 정규화 후 가중합 → composite_score (0~1)
      1) trend          : 가격 vs MA200 + 20일 모멘텀 + 골든크로스
      2) vix            : 역발상 — VIX 15↓ 약세, 30↑ 강세
      3) breadth        : us_pool 중 MA200 위 종목 비율
      4) yield_curve    : 10년-3월 spread. 역전 시 페널티
      5) sector_rotation: 공격주 ETF vs 방어주 ETF RS

    composite_score가 곧 equity_ratio의 기반이 됨 (단순 cutoff 탈피)
    fear_score: VIX 30↑ + 시장 -15%↓ → 역발상 매수 트리거
    """

    def __init__(self, fetcher: DataFetcher, cfg: AdvisorConfig):
        self.fetcher = fetcher
        self.cfg     = cfg

    def analyze(self, market_ticker: str, deep: bool = True) -> MarketRegime:
        """
        deep=True (US 시장 분석 시): 5개 신호 전부 계산
        deep=False (KR 시장 등): trend + vix만 계산 (sector ETF는 US 전용)
        """
        try:
            data  = self.fetcher.history(market_ticker, period="1y")
            vix   = self.fetcher.vix()
            close = data["Close"].squeeze()

            current   = float(close.iloc[-1])
            ma50      = float(close.rolling(50).mean().iloc[-1])
            ma200     = float(close.rolling(200).mean().iloc[-1])
            pct_vs200 = (current - ma200) / ma200 * 100
            mom_20d   = (current - float(close.iloc[-20])) / float(close.iloc[-20]) * 100

            golden_cross = ma50 > ma200
            trend_score  = self._trend_score(pct_vs200, mom_20d, golden_cross)
            risk_level   = self._vix_risk(vix)
            vix_signal   = self._vix_signal(vix)

            # deep=True: US 전용 추가 신호
            if deep:
                breadth_pct   = self._compute_breadth(self.cfg.us_pool)
                yc_spread, yc_signal = self._yield_curve_signal()
                sector_rot    = self._sector_rotation_signal()
            else:
                breadth_pct, yc_spread, yc_signal, sector_rot = 0.5, 0.0, 0.5, 0.5

            # composite_score (가중합)
            w = self.cfg.signal_weights
            composite = (
                trend_score   * w["trend"]
              + vix_signal    * w["vix"]
              + breadth_pct   * w["breadth"]
              + yc_signal     * w["yield_curve"]
              + sector_rot    * w["sector_rotation"]
            )
            composite = round(min(max(composite, 0.0), 1.0), 3)

            # 역발상 매수 시그널 (fear_score)
            fear_score = self._fear_score(vix, pct_vs200 / 100)

            # is_investable: composite 너무 낮거나 극단 위험 시 차단
            is_investable = not (
                risk_level == "EXTREME" or
                (composite < 0.25 and fear_score < 0.5)
            )

            detail = (
                f"VIX {vix:.1f}({risk_level}) | MA200 {pct_vs200:+.1f}% | "
                f"모멘텀 {mom_20d:+.1f}% | "
                f"breadth {breadth_pct*100:.0f}% | "
                f"섹터RS {sector_rot:.2f} | "
                f"종합 {composite:.2f}"
                + (f" 🔥공포{fear_score:.2f}" if fear_score > 0.3 else "")
            )

            return MarketRegime(
                market=market_ticker, is_investable=is_investable,
                trend_score=round(trend_score, 3), risk_level=risk_level,
                vix=vix, price_vs_ma200_pct=round(pct_vs200, 2), detail=detail,
                composite_score=composite,
                vix_signal=round(vix_signal, 3),
                breadth_pct=round(breadth_pct, 3),
                yield_curve_spread=round(yc_spread, 2),
                yield_curve_signal=round(yc_signal, 3),
                sector_rotation=round(sector_rot, 3),
                fear_score=round(fear_score, 3),
            )
        except Exception as e:
            logger.error(f"시장 분석 실패 ({market_ticker}): {e}")
            return MarketRegime(
                market=market_ticker, is_investable=False, trend_score=0.0,
                risk_level="UNKNOWN", vix=99.0, price_vs_ma200_pct=0.0,
                detail=f"데이터 오류: {e}",
            )

    # ── 신호 산출 헬퍼 ─────────────────────────────────────────

    @staticmethod
    def _trend_score(pct_vs200, mom_20d, golden) -> float:
        s = 0.0
        if   pct_vs200 >  5: s += 0.40
        elif pct_vs200 >  0: s += 0.20
        elif pct_vs200 > -5: s += 0.08
        if   mom_20d >  3:   s += 0.30
        elif mom_20d >  0:   s += 0.15
        elif mom_20d > -3:   s += 0.05
        s += 0.30 if golden else 0.0
        return round(min(s, 1.0), 3)

    @staticmethod
    def _vix_risk(vix: float) -> str:
        if   vix < 20: return "LOW"
        elif vix < 25: return "MEDIUM"
        elif vix < 30: return "HIGH"
        elif vix < 35: return "VERY_HIGH"
        else:          return "EXTREME"

    @staticmethod
    def _vix_signal(vix: float) -> float:
        """
        역발상 매핑.
          VIX  ≤ 12 → 0.20 (너무 안심 = 약세 시그널, 고점 가능성)
          VIX  ~ 18 → 0.55 (정상)
          VIX  ~ 25 → 0.70 (긴장, 위험-보상 양호)
          VIX  ~ 32 → 0.85 (공포 진입, 매수 기회 ↑)
          VIX  ≥ 40 → 0.70 (극도공포 — 너무 위험해서 감점)
        """
        if   vix <= 12: return 0.20
        elif vix <= 15: return 0.35
        elif vix <= 20: return 0.55
        elif vix <= 25: return 0.70
        elif vix <= 30: return 0.78
        elif vix <= 35: return 0.85
        elif vix <= 40: return 0.78
        else:           return 0.60   # EXTREME: 매수기회지만 위험 ↑

    def _compute_breadth(self, pool: List[str]) -> float:
        """us_pool 중 현재가가 MA200 위에 있는 종목 비율 (0~1)"""
        above = 0
        total = 0
        for t in pool:
            df = self.fetcher.history(t, period="1y")
            if df.empty or len(df) < 200:
                continue
            try:
                close = df["Close"].squeeze()
                ma200 = float(close.rolling(200).mean().iloc[-1])
                cur   = float(close.iloc[-1])
                if cur > ma200:
                    above += 1
                total += 1
            except Exception:
                continue
        if total == 0:
            return 0.5
        return above / total

    def _yield_curve_signal(self) -> tuple:
        """
        (10년 - 3월) spread.
          음수(역전) = 침체 선행 → signal 낮음
          양수 = 정상 → signal 높음
        ^TNX, ^IRX는 yfinance에서 %단위 × 10으로 들어옴 (e.g., 4.5% → 45.0)
        """
        short = self.fetcher.latest_close(self.cfg.short_rate_ticker, "1mo")
        long_ = self.fetcher.latest_close(self.cfg.long_rate_ticker,  "1mo")
        if short is None or long_ is None:
            return 0.0, 0.5
        spread = (long_ - short) / 10.0   # %포인트
        # spread → signal
        if   spread > 1.5:  sig = 0.90
        elif spread > 0.5:  sig = 0.75
        elif spread > 0.0:  sig = 0.55
        elif spread > -0.5: sig = 0.35
        else:               sig = 0.15
        return spread, sig

    def _sector_rotation_signal(self) -> float:
        """
        공격주(XLK/XLY/XLF) 평균 20일 모멘텀 vs 방어주(XLP/XLU/XLV) 평균 20일 모멘텀.
        공격주가 강세 → 시장 위험선호 ↑ (signal 높음)
        """
        def avg_mom(tickers):
            moms = []
            for t in tickers:
                df = self.fetcher.history(t, period="3mo")
                if df.empty or len(df) < 25:
                    continue
                try:
                    close = df["Close"].squeeze()
                    mom = (float(close.iloc[-1]) - float(close.iloc[-20])) / float(close.iloc[-20])
                    moms.append(mom)
                except Exception:
                    continue
            return sum(moms) / len(moms) if moms else 0.0

        off = avg_mom(self.cfg.offensive_sector_etfs)
        dfn = avg_mom(self.cfg.defensive_sector_etfs)
        diff = off - dfn   # 양수면 공격주 우세
        # diff → signal
        if   diff >  0.04: return 0.90
        elif diff >  0.01: return 0.70
        elif diff > -0.01: return 0.50
        elif diff > -0.04: return 0.30
        else:              return 0.15

    def _fear_score(self, vix: float, drawdown: float) -> float:
        """
        역발상 매수 시그널.
          VIX ≥ 30  AND  현재가 vs MA200 ≤ -15%  → 강한 fear_score
          둘 중 하나만 충족하면 약한 신호
        """
        vix_part = 0.0
        if   vix >= 40: vix_part = 1.0
        elif vix >= 30: vix_part = 0.7
        elif vix >= 25: vix_part = 0.35
        dd_part = 0.0
        if   drawdown <= -0.25: dd_part = 1.0
        elif drawdown <= -0.15: dd_part = 0.7
        elif drawdown <= -0.08: dd_part = 0.35
        # 두 신호 곱 (둘 다 충족 시 강화)
        # 한쪽만 있을 때는 가중평균
        if vix_part > 0 and dd_part > 0:
            return min(vix_part * 0.6 + dd_part * 0.4 + 0.15, 1.0)
        return max(vix_part, dd_part) * 0.5


# ==============================================================
# 2단계: 펀더멘털 + 기술적 스크리너
# ==============================================================

class Screener:
    """
    섹터 상대 평가 기반 종합 점수 산출

    점수 구성
    ─────────────────────────────────────────
    펀더멘털 60%
      ├─ ROE            30점
      ├─ PBR (섹터비)   25점
      ├─ PER            20점
      ├─ 매출 성장률    15점
      └─ 순이익률       10점
    기술적 40%
      ├─ RSI(14)        40점
      ├─ MA 추세        40점
      └─ 모멘텀(20일)   20점
    ─────────────────────────────────────────
    """

    # 금융·유틸 등 구조적 고부채 섹터 (부채비율 게이트 면제)
    HIGH_DEBT_SECTORS = {
        "Financial Services", "Banks", "Insurance",
        "Utilities", "Real Estate",
    }

    def __init__(self, fetcher: DataFetcher, cfg: AdvisorConfig):
        self.fetcher = fetcher
        self.cfg     = cfg
        # Phase 3: 시장 fear_score (MacroFilter.analyze 후 외부에서 세팅)
        self.fear_score: float = 0.0

    def score_etf(self, ticker: str, name: str) -> Optional[StockScore]:
        """
        ETF용 간소 평가. 펀더멘털 게이트 + RSI 기반 진입 플랜 모두 우회.
        진입 플랜은 항상 DCA (매월 균등 적립).
          - 가격/RSI/모멘텀은 참고용으로만 계산
          - 비중은 config.core_etf_pool의 weight 그대로 사용 (Portfolio에서 부여)
        """
        try:
            tech = self._technical(ticker)
            cur_price = round(tech.get("last_price", 0.0), 2)

            # 진입 플랜 = DCA 강제
            dca_plan = EntryPlan(
                action="DCA",
                label=f"월 적립 ({self.cfg.dca_months}개월)",
                rationale="코어 ETF는 시장 전체 추종 — 매월 균등 적립으로 변동성 완화",
                current_price=cur_price,
                target_levels=[(cur_price, 100.0)],
                confidence=0.95,
            )

            return StockScore(
                ticker=ticker, name=name, country="US", sector="ETF",
                roe=None, pbr=None, per=None, debt_to_equity=None,
                fundamental_score=0.0,
                rsi=round(tech["rsi"], 1),
                trend_score=round(tech["trend"], 3),
                momentum_pct=round(tech["momentum"] * 100, 2),
                technical_score=round(tech["score"], 1),
                composite_score=round(tech["score"], 1),
                entry_signal=True,       # ETF는 항상 적립 가능
                entry_plan=dca_plan,
                pbr_source="missing",
                per_source="missing",
            )
        except Exception as e:
            logger.warning(f"ETF {ticker} 평가 실패: {e}")
            return None

    # ==============================================================
    # 폭등시그널 평가 (Phase B)
    # ==============================================================

    def _bollinger_squeeze_signal(self, ticker: str) -> dict:
        """
        볼린저 스퀴즈 + 거래량 폭발 + 상단 돌파 감지.
          - squeeze: 20일 표준편차가 60일 평균 표준편차의 70% 이하
          - volume_spike: 오늘 거래량이 20일 평균의 3배 이상
          - breakout: 현재가가 볼린저 상단(MA20+2σ) 위
        총 25점 (squeeze 8 + volume_spike 10 + breakout 7)
        """
        try:
            df = self.fetcher.history(ticker, period="6mo")
            if df.empty or len(df) < 60:
                return {"squeeze": False, "volume_spike": False, "breakout": False,
                        "score": 0, "vol_ratio": 0.0, "std_ratio": 0.0}
            close = df["Close"].squeeze()
            volume = df["Volume"].squeeze() if "Volume" in df.columns else None

            ma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper = ma20 + 2 * std20

            std60_mean = std20.rolling(60).mean()
            cur_std = float(std20.iloc[-1])
            avg_std = float(std60_mean.iloc[-1])
            squeeze = (cur_std / avg_std) < 0.70 if avg_std and avg_std > 0 else False
            std_ratio = (cur_std / avg_std) if avg_std and avg_std > 0 else 0.0

            volume_spike, vol_ratio = False, 0.0
            if volume is not None and len(volume) >= 20:
                avg_vol = float(volume.iloc[-20:].mean())
                cur_vol = float(volume.iloc[-1])
                if avg_vol > 0:
                    vol_ratio = cur_vol / avg_vol
                    volume_spike = vol_ratio >= 3.0

            cur = float(close.iloc[-1])
            breakout = cur > float(upper.iloc[-1])

            score = 0
            if squeeze:      score += 8
            if volume_spike: score += 10
            if breakout:     score += 7

            return {"squeeze": squeeze, "volume_spike": volume_spike, "breakout": breakout,
                    "score": score, "vol_ratio": vol_ratio, "std_ratio": std_ratio}
        except Exception as e:
            logger.warning(f"{ticker} 볼린저 분석 실패: {e}")
            return {"squeeze": False, "volume_spike": False, "breakout": False,
                    "score": 0, "vol_ratio": 0.0, "std_ratio": 0.0}

    def score_surge(self, ticker: str, country: str = "US") -> Optional[StockScore]:
        """
        폭등시그널 평가 (Phase B).
        점수 구성 (총 100점):
          - 매출 성장률 40점 (핵심)
          - FCF 전환    15점
          - 볼린저 스퀴즈+거래량 25점
          - 단기 모멘텀  15점
          - RSI 회복     5점

        게이트 (장기 모드보다 완화):
          - ROE 게이트 제거 (그로스주는 적자 흔함)
          - 부채비율 300% 초과만 탈락
          - 매출 성장 5% 미만 탈락
        """
        try:
            info   = self.fetcher.info(ticker)
            name   = info.get("longName") or info.get("shortName", ticker)
            sector = info.get("sector", "Unknown")

            debt_eq    = info.get("debtToEquity")
            rev_growth = info.get("revenueGrowth")
            fcf_raw    = info.get("freeCashflow")
            revenue    = info.get("totalRevenue")
            roe_raw    = info.get("returnOnEquity")

            # ── 완화 게이트 ──
            if debt_eq is not None and float(debt_eq) > 300:
                logger.info(f"{ticker}: 부채비율 과다 ({debt_eq:.0f}) → surge 스킵")
                return None
            if rev_growth is None or float(rev_growth) < 0.05:
                logger.info(f"{ticker}: 매출 성장 부족 (rev={rev_growth}) → surge 스킵")
                return None

            rev = float(rev_growth)

            # 1) 매출 성장 (40점)
            if   rev >= 0.50: rev_score = 40
            elif rev >= 0.30: rev_score = 30
            elif rev >= 0.20: rev_score = 22
            elif rev >= 0.10: rev_score = 12
            else:             rev_score = 5

            # 2) FCF 전환 (15점)
            fcf_score = 0
            fcf_margin = None
            if fcf_raw is not None and revenue and float(revenue) > 0:
                fcf_margin = float(fcf_raw) / float(revenue)
                if   fcf_margin > 0.10:  fcf_score = 15
                elif fcf_margin > 0.00:  fcf_score = 10   # 전환 성공
                elif fcf_margin > -0.05: fcf_score = 5    # break-even 근처
                else:                    fcf_score = 0

            # 3) 볼린저 스퀴즈 + 거래량 (25점)
            boll = self._bollinger_squeeze_signal(ticker)
            boll_score = boll["score"]

            # 4) 단기 모멘텀 (15점)
            tech = self._technical(ticker)
            mom = tech["momentum"]
            if   mom > 0.20: mom_score = 15
            elif mom > 0.10: mom_score = 10
            elif mom > 0.05: mom_score = 5
            else:            mom_score = 0

            # 5) RSI 회복 (5점)
            rsi = tech["rsi"]
            rsi_score = 5 if 45 <= rsi <= 75 else 0

            total = rev_score + fcf_score + boll_score + mom_score + rsi_score
            entry_plan = self._make_surge_entry_plan(
                cur=tech.get("last_price", 0.0), rsi=rsi, mom=mom, boll=boll,
            )

            return StockScore(
                ticker=ticker, name=name, country=country, sector=sector,
                roe=round(float(roe_raw) * 100, 2) if roe_raw is not None else None,
                pbr=None, per=None,
                debt_to_equity=round(float(debt_eq), 1) if debt_eq is not None else None,
                fundamental_score=round(rev_score + fcf_score, 1),
                rsi=round(rsi, 1),
                trend_score=round(tech["trend"], 3),
                momentum_pct=round(mom * 100, 2),
                technical_score=round(boll_score + mom_score + rsi_score, 1),
                composite_score=round(total, 1),
                entry_signal=boll["squeeze"] or boll["volume_spike"],
                entry_plan=entry_plan,
                pbr_source="missing",
                per_source="missing",
                fcf_margin=round(fcf_margin * 100, 2) if fcf_margin is not None else None,
                operating_margin=None,
                peg=None,
            )
        except Exception as e:
            logger.warning(f"{ticker} surge 평가 실패: {e}")
            return None

    def _make_surge_entry_plan(self, cur: float, rsi: float, mom: float, boll: dict) -> EntryPlan:
        """폭등시그널 종목용 진입 플랜"""
        # 볼린저 돌파 + 거래량 폭발 → 즉시 추세 추종
        if boll["breakout"] and boll["volume_spike"]:
            return EntryPlan(
                action="MOMENTUM_RIDE", label="추세 추종 매수",
                rationale=(f"볼린저 상단 돌파 + 거래량 {boll['vol_ratio']:.1f}배 — 추세 시작"),
                current_price=round(cur, 2),
                target_levels=[(round(cur, 2), 100.0)],
                confidence=0.80,
            )
        # 스퀴즈 상태 → 폭발 임박, 50% 즉시 + 분할
        if boll["squeeze"]:
            return EntryPlan(
                action="SPLIT_BUY", label="스퀴즈 분할",
                rationale=(f"변동성 수축 (std {boll['std_ratio']*100:.0f}%) — 폭발 임박"),
                current_price=round(cur, 2),
                target_levels=[
                    (round(cur, 2),        50.0),
                    (round(cur * 0.97, 2), 25.0),
                    (round(cur * 0.94, 2), 25.0),
                ],
                confidence=0.65,
            )
        # 모멘텀 + RSI 정상 → 추세 추종
        if 50 <= rsi <= 75 and mom > 0.10:
            return EntryPlan(
                action="MOMENTUM_RIDE", label="추세 추종 매수",
                rationale=f"모멘텀 {mom*100:+.1f}% + RSI {rsi:.1f}",
                current_price=round(cur, 2),
                target_levels=[(round(cur, 2), 100.0)],
                confidence=0.70,
            )
        # RSI 과매수 → 보류
        if rsi >= 80:
            return EntryPlan(
                action="AVOID", label="진입 보류",
                rationale=f"극단 과매수 (RSI {rsi:.1f}) — 폭등시그널이라도 위험",
                current_price=round(cur, 2),
                target_levels=[], confidence=0.20,
            )
        # 그 외 → 보수적 3분할
        return EntryPlan(
            action="SPLIT_BUY", label="분할 매수",
            rationale=f"신호 약함 (모멘텀 {mom*100:+.1f}%, RSI {rsi:.1f})",
            current_price=round(cur, 2),
            target_levels=[
                (round(cur,        2), 33.3),
                (round(cur * 0.97, 2), 33.3),
                (round(cur * 0.94, 2), 33.4),
            ],
            confidence=0.50,
        )

    # ==============================================================
    # 모멘텀 추종 평가 (Phase C)
    # ==============================================================

    def score_momentum(self, ticker: str, country: str = "US") -> Optional[StockScore]:
        """
        모멘텀 추종 (Phase C) — 단기 트레이딩 용.
        가격 데이터만 사용 (펀더멘털 무관, info 호출 안 함 → 빠름).

        점수 (총 100):
          - 20일 모멘텀 30점 (핵심)
          - 5일 모멘텀  20점 (초단기)
          - RSI 강도   25점 (60~85가 만점, 과매수도 OK)
          - 거래량 증가 15점 (20일 평균 대비 1.5배+)
          - MA20 위    10점 (단기 추세 라인 위)

        1차 필터: 30일 모멘텀 +15% 미만이면 풀에서 제외
        """
        try:
            df = self.fetcher.history(ticker, period="6mo")
            if df.empty or len(df) < 60:
                return None
            close = df["Close"].squeeze()
            volume = df["Volume"].squeeze() if "Volume" in df.columns else None

            cur = float(close.iloc[-1])
            # 30일 모멘텀 (1차 필터)
            mom30 = (cur - float(close.iloc[-30])) / float(close.iloc[-30])
            if mom30 < 0.15:
                logger.info(f"{ticker}: 30일 모멘텀 {mom30*100:+.1f}% < 15% → momentum 스킵")
                return None

            # 20일 모멘텀
            mom20 = (cur - float(close.iloc[-20])) / float(close.iloc[-20])
            # 5일 모멘텀
            mom5  = (cur - float(close.iloc[-5])) / float(close.iloc[-5])

            # RSI(14)
            delta = close.diff()
            gain  = delta.where(delta > 0, 0.0).rolling(14).mean()
            loss  = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rsi   = float((100 - 100 / (1 + gain / loss.replace(0, np.nan))).iloc[-1])

            # MA20
            ma20 = float(close.rolling(20).mean().iloc[-1])
            above_ma20 = cur > ma20

            # 거래량
            vol_ratio = 0.0
            if volume is not None and len(volume) >= 20:
                avg_vol = float(volume.iloc[-20:].mean())
                cur_vol = float(volume.iloc[-1])
                if avg_vol > 0:
                    vol_ratio = cur_vol / avg_vol

            # ── 점수 ─────────────────────────────────────
            # 20일 모멘텀 (30점)
            if   mom20 > 0.30: m20_s = 30
            elif mom20 > 0.20: m20_s = 25
            elif mom20 > 0.10: m20_s = 18
            elif mom20 > 0.05: m20_s = 10
            else:              m20_s = 3

            # 5일 모멘텀 (20점)
            if   mom5 > 0.10:  m5_s = 20
            elif mom5 > 0.05:  m5_s = 15
            elif mom5 > 0.00:  m5_s = 8
            else:              m5_s = 0

            # RSI 강도 (25점) — 과매수(70+)에 보너스, 단 90+는 감점
            if   rsi >= 90:    rsi_s = 5
            elif rsi >= 80:    rsi_s = 18
            elif rsi >= 70:    rsi_s = 25   # sweet spot
            elif rsi >= 60:    rsi_s = 20
            elif rsi >= 50:    rsi_s = 12
            else:              rsi_s = 0

            # 거래량 (15점)
            if   vol_ratio >= 3.0: vol_s = 15
            elif vol_ratio >= 2.0: vol_s = 12
            elif vol_ratio >= 1.5: vol_s = 8
            elif vol_ratio >= 1.0: vol_s = 3
            else:                  vol_s = 0

            # MA20 위 (10점)
            ma_s = 10 if above_ma20 else 0

            total = m20_s + m5_s + rsi_s + vol_s + ma_s

            entry_plan = self._make_momentum_entry_plan(
                cur=cur, rsi=rsi, mom20=mom20, mom5=mom5, vol_ratio=vol_ratio,
            )

            meta = self.cfg.stock_meta.get(ticker, {})
            return StockScore(
                ticker=ticker,
                name=meta.get("name", ticker),
                country=country,
                sector=meta.get("sector", "Unknown"),
                roe=None, pbr=None, per=None, debt_to_equity=None,
                fundamental_score=0.0,
                rsi=round(rsi, 1),
                trend_score=1.0 if above_ma20 else 0.5,
                momentum_pct=round(mom20 * 100, 2),
                technical_score=round(total, 1),
                composite_score=round(total, 1),
                entry_signal=(mom20 > 0.10 and rsi >= 60),
                entry_plan=entry_plan,
                pbr_source="missing",
                per_source="missing",
                fcf_margin=None, operating_margin=None, peg=None,
            )
        except Exception as e:
            logger.warning(f"{ticker} momentum 평가 실패: {e}")
            return None

    def _make_momentum_entry_plan(self, cur: float, rsi: float, mom20: float,
                                   mom5: float, vol_ratio: float) -> EntryPlan:
        """모멘텀 종목 진입 플랜 — 손절선 명시"""
        # RSI 90+ + 5일 모멘텀 둔화 → 꼭지 가능성
        if rsi >= 90 and mom5 < 0.02:
            return EntryPlan(
                action="AVOID", label="진입 보류 (꼭지 가능)",
                rationale=f"극단 과매수(RSI {rsi:.1f}) + 5일 모멘텀 둔화 — 꼭지 신호",
                current_price=round(cur, 2),
                target_levels=[], confidence=0.30,
            )
        # 거래량 폭발 + 강한 모멘텀 → 즉시 추종
        if vol_ratio >= 2.0 and mom20 > 0.15:
            stop_loss = round(cur * 0.93, 2)  # -7% 손절
            return EntryPlan(
                action="MOMENTUM_RIDE", label="추세 추종 매수",
                rationale=(f"모멘텀 {mom20*100:+.1f}% + 거래량 {vol_ratio:.1f}배 — "
                           f"강한 추세 (손절선 {stop_loss})"),
                current_price=round(cur, 2),
                target_levels=[(round(cur, 2), 100.0)],
                confidence=0.75,
            )
        # 일반 추종
        if mom20 > 0.10 and rsi >= 55:
            stop_loss = round(cur * 0.93, 2)
            return EntryPlan(
                action="MOMENTUM_RIDE", label="추세 추종 매수",
                rationale=(f"모멘텀 {mom20*100:+.1f}%, RSI {rsi:.1f} — "
                           f"추세 진행 중 (손절선 {stop_loss})"),
                current_price=round(cur, 2),
                target_levels=[(round(cur, 2), 100.0)],
                confidence=0.65,
            )
        # 약한 신호 → 분할
        return EntryPlan(
            action="SPLIT_BUY", label="분할 매수",
            rationale=f"신호 약함 (모멘텀 {mom20*100:+.1f}%) — 보수적 분할",
            current_price=round(cur, 2),
            target_levels=[
                (round(cur, 2),        50.0),
                (round(cur * 0.97, 2), 25.0),
                (round(cur * 0.94, 2), 25.0),
            ],
            confidence=0.45,
        )

    def score(self, ticker: str, country: str = "US") -> Optional[StockScore]:
        try:
            info   = self.fetcher.info(ticker)
            name   = info.get("longName") or info.get("shortName", ticker)
            sector = info.get("sector", "Unknown")

            roe           = info.get("returnOnEquity")
            pbr           = info.get("priceToBook")
            per           = info.get("trailingPE")
            forward_pe    = info.get("forwardPE")
            debt_eq       = info.get("debtToEquity")
            market_cap    = info.get("marketCap")
            rev_growth    = info.get("revenueGrowth")   or 0.0
            profit_margin = info.get("profitMargins")   or 0.0
            current_ratio = info.get("currentRatio")    or 1.0

            # Phase 2: 추가 펀더멘털 지표
            fcf_raw       = info.get("freeCashflow")            # 절대 금액
            revenue       = info.get("totalRevenue")
            op_margin     = info.get("operatingMargins")        # 비율 (e.g., 0.25)
            peg_raw       = info.get("pegRatio") or info.get("trailingPegRatio")

            fcf_margin = None
            if fcf_raw is not None and revenue and revenue > 0:
                fcf_margin = float(fcf_raw) / float(revenue)    # 비율 (예: 0.20 = 20%)

            # ── PER 보강: trailingPE 결측 시 forwardPE로 대체 ────
            per_source = "info"
            if per is None and forward_pe is not None:
                per = forward_pe
                per_source = "forward"
            elif per is None:
                per_source = "missing"

            # ── PBR 보강: priceToBook 결측 시 재무제표로 직접 계산 ─
            pbr_source = "info"
            if pbr is None and market_cap:
                equity = self.fetcher.stockholders_equity(ticker)
                if equity and equity > 0:
                    pbr = market_cap / equity
                    pbr_source = "computed"
                    logger.info(f"{ticker}: PBR 직접 계산 → {pbr:.2f}")
                else:
                    pbr_source = "missing"
            elif pbr is None:
                pbr_source = "missing"

            # ── 게이트 ────────────────────────────────────────────
            # ROE와 debtEq는 필수 (게이트 판정에 쓰이므로). 결측 시 스킵.
            if roe is None or debt_eq is None:
                logger.info(f"{ticker}: ROE/부채비율 누락 → 스킵")
                return None

            if not self._quality_gate(float(roe), float(debt_eq),
                                      float(current_ratio), sector, country):
                logger.info(f"{ticker}: 퀄리티 게이트 탈락 "
                            f"(ROE={float(roe):.3f}, D/E={float(debt_eq):.1f}, "
                            f"CR={float(current_ratio):.2f})")
                return None

            # ── 점수 (결측 항목은 가중치 재분배) ──────────────────
            f_score = self._fundamental_score(
                roe=float(roe),
                pbr=float(pbr) if pbr is not None else None,
                per=float(per) if per is not None else None,
                rev_growth=float(rev_growth),
                profit_margin=float(profit_margin),
                fcf_margin=fcf_margin,
                op_margin=float(op_margin) if op_margin is not None else None,
                peg=float(peg_raw) if peg_raw is not None else None,
                sector=sector,
            )
            tech = self._technical(ticker)
            composite = f_score * 0.60 + tech["score"] * 0.40

            return StockScore(
                ticker=ticker, name=name, country=country, sector=sector,
                roe=round(float(roe) * 100, 2),
                pbr=round(float(pbr), 2) if pbr is not None else None,
                per=round(float(per), 2) if per is not None else None,
                debt_to_equity=round(float(debt_eq), 1),
                fundamental_score=round(f_score, 1),
                rsi=round(tech["rsi"], 1),
                trend_score=round(tech["trend"], 3),
                momentum_pct=round(tech["momentum"] * 100, 2),
                technical_score=round(tech["score"], 1),
                composite_score=round(composite, 1),
                entry_signal=tech["entry_signal"],
                entry_plan=tech.get("entry_plan"),
                pbr_source=pbr_source,
                per_source=per_source,
                fcf_margin       = round(fcf_margin * 100, 2)  if fcf_margin is not None else None,
                operating_margin = round(float(op_margin) * 100, 2) if op_margin is not None else None,
                peg              = round(float(peg_raw), 2)    if peg_raw is not None else None,
            )
        except Exception as e:
            logger.warning(f"{ticker} 스크리닝 실패: {e}")
            return None

    # ── 내부 헬퍼 ──────────────────────────────────────────────

    def _quality_gate(self, roe, debt_eq, current_ratio, sector, country) -> bool:
        roe_min = 0.10 if country == "US" else 0.07
        if roe < roe_min:
            return False
        if sector not in self.HIGH_DEBT_SECTORS and debt_eq > 200:
            return False
        if current_ratio < 0.8:
            return False
        return True

    def _fundamental_score(self, roe, pbr, per, rev_growth, profit_margin,
                           fcf_margin, op_margin, peg, sector) -> float:
        """
        펀더멘털 점수 (만점 100). 결측 항목은 가중치에서 빼고 100점 환산.

        가중치 배분 (총 100점)
          ROE              20  (필수)
          PBR (섹터 상대)  15  (선택)
          PER              15  (선택)
          매출 성장률      10  (필수)
          순이익률          5  (필수)
          FCF 마진         15  (선택, Phase 2 신규)
          영업이익률       10  (선택, Phase 2 신규)
          PEG              10  (선택, Phase 2 신규)
        """
        scores: dict = {}

        # ROE (가중치 20)
        if   roe > 0.30: scores["roe"] = (20, 20)
        elif roe > 0.20: scores["roe"] = (15, 20)
        elif roe > 0.15: scores["roe"] = (10, 20)
        elif roe > 0.10: scores["roe"] = ( 6, 20)
        else:            scores["roe"] = ( 0, 20)

        # PBR 섹터 상대값 (가중치 15, None 허용)
        if pbr is not None:
            pbr_norm  = self.cfg.sector_pbr_norm.get(sector, 4.0)
            pbr_ratio = pbr / pbr_norm
            if   pbr_ratio < 0.70: scores["pbr"] = (15, 15)
            elif pbr_ratio < 1.00: scores["pbr"] = (11, 15)
            elif pbr_ratio < 1.30: scores["pbr"] = ( 7, 15)
            elif pbr_ratio < 1.70: scores["pbr"] = ( 3, 15)
            else:                  scores["pbr"] = ( 0, 15)

        # PER (가중치 15, None 허용)
        if per is not None:
            if   per < 0:  scores["per"] = ( 0, 15)
            elif per < 15: scores["per"] = (15, 15)
            elif per < 25: scores["per"] = (10, 15)
            elif per < 35: scores["per"] = ( 5, 15)
            else:          scores["per"] = ( 2, 15)

        # 매출 성장률 (가중치 10)
        if   rev_growth > 0.20: scores["rev"] = (10, 10)
        elif rev_growth > 0.10: scores["rev"] = ( 7, 10)
        elif rev_growth > 0.05: scores["rev"] = ( 4, 10)
        elif rev_growth > 0.00: scores["rev"] = ( 2, 10)
        else:                   scores["rev"] = ( 0, 10)

        # 순이익률 (가중치 5)
        if   profit_margin > 0.20: scores["pm"] = ( 5, 5)
        elif profit_margin > 0.10: scores["pm"] = ( 4, 5)
        elif profit_margin > 0.05: scores["pm"] = ( 2, 5)
        else:                      scores["pm"] = ( 0, 5)

        # FCF 마진 (가중치 15) — "이익은 나는데 현금 안 들어오는" 회사 거름
        if fcf_margin is not None:
            if   fcf_margin > 0.20: scores["fcf"] = (15, 15)
            elif fcf_margin > 0.10: scores["fcf"] = (11, 15)
            elif fcf_margin > 0.05: scores["fcf"] = ( 7, 15)
            elif fcf_margin > 0.00: scores["fcf"] = ( 3, 15)
            else:                   scores["fcf"] = ( 0, 15)   # 마이너스 FCF는 큰 페널티

        # 영업이익률 (가중치 10)
        if op_margin is not None:
            if   op_margin > 0.25: scores["op"] = (10, 10)
            elif op_margin > 0.15: scores["op"] = ( 7, 10)
            elif op_margin > 0.08: scores["op"] = ( 4, 10)
            elif op_margin > 0.00: scores["op"] = ( 1, 10)
            else:                  scores["op"] = ( 0, 10)

        # PEG (가중치 10): 1↓ 저평가, 1~2 적정, 2↑ 고평가
        if peg is not None and peg > 0:
            if   peg < 1.0: scores["peg"] = (10, 10)
            elif peg < 1.5: scores["peg"] = ( 7, 10)
            elif peg < 2.0: scores["peg"] = ( 4, 10)
            elif peg < 3.0: scores["peg"] = ( 1, 10)
            else:           scores["peg"] = ( 0, 10)

        got = sum(v for v, _ in scores.values())
        cap = sum(c for _, c in scores.values())
        return (got / cap) * 100 if cap > 0 else 0.0

    def _technical(self, ticker: str) -> dict:
        """RSI + 추세 + 모멘텀 앙상블 + 진입 플랜 산출"""
        try:
            close = self.fetcher.history(ticker, period="6mo")["Close"].squeeze()
            # RSI(14)
            delta = close.diff()
            gain  = delta.where(delta > 0, 0.0).rolling(14).mean()
            loss  = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rsi   = float((100 - 100 / (1 + gain / loss.replace(0, np.nan))).iloc[-1])
            # MA 추세
            ma20 = float(close.rolling(20).mean().iloc[-1])
            ma60 = float(close.rolling(60).mean().iloc[-1])
            cur  = float(close.iloc[-1])
            if cur > ma20 > ma60:    trend = 1.0
            elif cur > ma60:         trend = 0.5
            else:                    trend = 0.0
            # 20일 모멘텀
            mom = float((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20])
            # 점수
            t = 0.0
            if   45 <= rsi <= 65: t += 40
            elif 35 <= rsi <  45: t += 30
            elif 65 <  rsi <= 75: t += 20
            elif rsi >  75:       t +=  5
            else:                 t += 15   # < 35
            t += trend * 40
            if   mom >  0.05: t += 20
            elif mom >  0.00: t += 12
            elif mom > -0.05: t +=  5

            entry_signal = (35 <= rsi <= 55) and (trend >= 0.5) and (mom > -0.05)
            entry_plan = self._make_entry_plan(cur, rsi, trend, mom, ma20, ma60,
                                                fear_score=self.fear_score)

            return {"rsi": rsi, "trend": trend, "momentum": mom,
                    "last_price": cur,
                    "score": min(t, 100.0),
                    "entry_signal": entry_signal,
                    "entry_plan": entry_plan}
        except Exception as e:
            logger.warning(f"{ticker} 기술적 분석 실패: {e}")
            return {"rsi": 50.0, "trend": 0.5, "momentum": 0.0,
                    "last_price": 0.0,
                    "score": 40.0, "entry_signal": False,
                    "entry_plan": None}

    @staticmethod
    def _make_entry_plan(
        cur: float, rsi: float, trend: float, mom: float,
        ma20: float, ma60: float, fear_score: float = 0.0,
    ) -> EntryPlan:
        """
        5단계 진입 플랜 산출
          ① AGGRESSIVE_BUY: 시장 공포 정점 + 종목 과매도 → 역발상 강매수
          ② AVOID         : 추세 망가짐 또는 극단 과매수
          ③ IMMEDIATE     : 이상적 셋업 (RSI 35~55 + 추세 + 모멘텀 안정)
          ④ WAIT_PULLBACK : RSI 65 초과 → MA20 회귀 시 진입
          ⑤ SPLIT_BUY     : 그 외 (3분할 매수: 현재가 / -3% / -6%)
        """
        # ① 역발상 강매수 — 시장 공포 + 종목 과매도 조합
        # 추세가 망가졌어도 OK (오히려 가격이 빠진 상태가 매수 기회)
        if fear_score >= 0.6 and rsi <= 40:
            # 5분할: 첫 매수 50%, 이후 -3%/-6%/-9%/-12% 각 12.5%
            return EntryPlan(
                action="AGGRESSIVE_BUY", label="공격적 매수 (역발상)",
                rationale=(f"시장 공포 정점(fear {fear_score:.2f}) + "
                           f"종목 과매도(RSI {rsi:.1f}) — 분할 강화"),
                current_price=round(cur, 2),
                target_levels=[
                    (round(cur,        2), 50.0),
                    (round(cur * 0.97, 2), 12.5),
                    (round(cur * 0.94, 2), 12.5),
                    (round(cur * 0.91, 2), 12.5),
                    (round(cur * 0.88, 2), 12.5),
                ],
                confidence=0.75,
            )

        # ② 진입 보류
        if trend == 0.0:
            return EntryPlan(
                action="AVOID", label="진입 보류",
                rationale=f"추세 약화 (현재가 < MA60)",
                current_price=round(cur, 2),
                target_levels=[], confidence=0.20,
            )
        if rsi >= 80:
            return EntryPlan(
                action="AVOID", label="진입 보류",
                rationale=f"극단 과매수 (RSI {rsi:.1f})",
                current_price=round(cur, 2),
                target_levels=[], confidence=0.25,
            )

        # ③ 즉시 진입
        if 35 <= rsi <= 55 and trend >= 0.5 and mom > -0.05:
            return EntryPlan(
                action="IMMEDIATE", label="즉시 진입",
                rationale=f"RSI {rsi:.1f} 정상권 · 추세 양호 · 모멘텀 {mom*100:+.1f}%",
                current_price=round(cur, 2),
                target_levels=[(round(cur, 2), 100.0)],
                confidence=0.85,
            )

        # ④ 눌림목 대기 (과매수)
        if rsi > 65:
            target = round(min(ma20, cur * 0.97), 2)
            return EntryPlan(
                action="WAIT_PULLBACK", label="눌림목 대기",
                rationale=f"단기 과열 (RSI {rsi:.1f}) → MA20 회귀 시 진입",
                current_price=round(cur, 2),
                target_levels=[(target, 100.0)],
                confidence=0.60,
            )

        # ⑤ 분할 매수 (그 외)
        return EntryPlan(
            action="SPLIT_BUY", label="분할 매수",
            rationale=f"신호 혼조 (RSI {rsi:.1f}, 추세 {trend:.1f}) → 3분할",
            current_price=round(cur, 2),
            target_levels=[
                (round(cur,        2), 33.3),
                (round(cur * 0.97, 2), 33.3),
                (round(cur * 0.94, 2), 33.4),
            ],
            confidence=0.50,
        )


# ==============================================================
# 3단계: 포트폴리오 구성 (섹터 집중도 제한 포함)
# ==============================================================

class PortfolioConstructor:

    def __init__(self, cfg: AdvisorConfig):
        self.cfg = cfg

    def construct(
        self,
        us_candidates: List[StockScore],
        kr_candidates: List[StockScore],
        core_etfs: List[StockScore],
        us_regime: MarketRegime,
        kr_regime: MarketRegime,
    ) -> dict:

        # Phase 1: equity_ratio를 composite_score 기반으로 — VIX 단독 cutoff 탈피
        # VIX 상한선만 안전장치로 유지 (EXTREME 시 강제 cash up)
        equity_ratio = self._equity_ratio_from_composite(
            us_regime.composite_score, us_regime.vix
        )
        cash_pct     = round((1 - equity_ratio) * 100, 1)
        invest_total = 100.0 * equity_ratio  # 주식 투자 가능 비중 (전체 대비 %)

        # ── 코어/위성 분할 ──────────────────────────────────────
        core_ratio       = max(0.0, min(self.cfg.core_ratio, 1.0))
        core_budget      = invest_total * core_ratio
        satellite_budget = invest_total * (1 - core_ratio)

        # ── 코어 ETF 비중 부여 ──────────────────────────────────
        core_final = self._allocate_core(core_etfs, core_budget)

        # ── 위성: 미국/한국 분배 ────────────────────────────────
        if kr_regime.is_investable and kr_candidates:
            sat_us_budget = satellite_budget * self.cfg.us_weight
            sat_kr_budget = satellite_budget * self.cfg.kr_weight
        else:
            sat_us_budget, sat_kr_budget = satellite_budget, 0.0

        us_final = self._select_and_allocate(us_candidates, sat_us_budget)
        kr_final = self._select_and_allocate(kr_candidates, sat_kr_budget)

        # 실제 코어 합계 (배정된 ETF 비중 총합)
        core_total = round(sum(e.weight_pct for e in core_final), 1)

        return {
            "equity_ratio": equity_ratio,
            "cash_pct":     cash_pct,
            "core_budget":  core_total,
            "us_budget":    round(sum(s.weight_pct for s in us_final), 1),
            "kr_budget":    round(sum(s.weight_pct for s in kr_final), 1),
            "core_etfs":    core_final,
            "us_stocks":    us_final,
            "kr_stocks":    kr_final,
        }

    def _allocate_core(self, etfs: List[StockScore], budget: float,
                       pool_override: Optional[List[dict]] = None) -> List[StockScore]:
        """
        코어 ETF는 config의 weight 그대로 사용 (점수 무관, 시장지수 추종이라 분산 자체가 핵심).
        weight 합이 1이 안 되면 정규화.
        pool_override 주면 surge_core_etf_pool 등 다른 풀의 weight 사용.
        """
        if not etfs or budget <= 0:
            return []
        pool = pool_override if pool_override is not None else self.cfg.core_etf_pool
        weight_map = {e["ticker"]: e["weight"] for e in pool}
        total_w = sum(weight_map.get(s.ticker, 0) for s in etfs) or 1.0
        for s in etfs:
            w = weight_map.get(s.ticker, 0) / total_w
            s.weight_pct = round(w * budget, 2)
        return etfs

    # ── 모멘텀 추종 모드 포트폴리오 구성 ──────────────────────
    # 단기 트레이딩이라 현금 비중 강제 ↑ (변동성 노출 제한)
    MOMENTUM_CASH_TABLE = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.22, 5: 0.15}

    def construct_momentum(
        self,
        momentum_candidates: List[StockScore],
        core_etfs: List[StockScore],        # QQQ만
        us_regime: MarketRegime,
        risk_level: int,
    ) -> dict:
        """모멘텀 추종 모드: QQQ 코어 + 모멘텀 통과 종목 (현금 비중 高)"""
        cash_ratio   = self.MOMENTUM_CASH_TABLE.get(risk_level, 0.30)
        equity_ratio = 1.0 - cash_ratio

        # 시장 약세(composite<0.4) 시 현금 비중 +20%p 강제
        if us_regime.composite_score < 0.4:
            cash_ratio = min(cash_ratio + 0.20, 0.80)
            equity_ratio = 1.0 - cash_ratio

        invest_total = 100.0 * equity_ratio

        # 코어 QQQ는 작게 (전체 투자분의 25%), 나머지 75%는 모멘텀 종목
        core_budget      = invest_total * 0.25
        satellite_budget = invest_total * 0.75

        core_final = self._allocate_core(core_etfs, core_budget,
                                          pool_override=[{"ticker": "QQQ", "weight": 1.0}])

        # 위성: 점수 비례, 단일종목 상한
        ranked   = sorted(momentum_candidates, key=lambda x: x.composite_score, reverse=True)
        selected = ranked[:self.cfg.top_n]
        total_score = sum(s.composite_score for s in selected) or 1.0
        for s in selected:
            raw = (s.composite_score / total_score) * satellite_budget
            s.weight_pct = round(min(raw, satellite_budget * self.cfg.single_stock_cap), 2)
        allocated = sum(s.weight_pct for s in selected)
        if allocated < satellite_budget and selected:
            selected[-1].weight_pct = round(selected[-1].weight_pct + (satellite_budget - allocated), 2)

        return {
            "equity_ratio":  equity_ratio,
            "cash_pct":      round(cash_ratio * 100, 1),
            "core_budget":   round(sum(e.weight_pct for e in core_final), 1),
            "leverage_budget": 0.0,
            "us_budget":     round(sum(s.weight_pct for s in selected), 1),
            "kr_budget":     0.0,
            "core_etfs":     core_final,
            "leverage_etfs": [],
            "us_stocks":     selected,
            "kr_stocks":     [],
        }

    # ── 폭등시그널 모드 포트폴리오 구성 ───────────────────────
    LEVERAGE_RATIO_TABLE = {1: 0.0, 2: 0.05, 3: 0.15, 4: 0.25, 5: 0.30}

    def construct_surge(
        self,
        surge_candidates: List[StockScore],
        core_etfs: List[StockScore],            # surge_core_etf_pool (VOO, QQQ)
        leverage_etfs: List[StockScore],        # leverage_etf_pool (TQQQ, SOXL, UPRO)
        us_regime: MarketRegime,
        risk_level: int,
    ) -> dict:
        """폭등시그널 모드: 코어(VOO/QQQ) + 레버리지 ETF + surge_pool 위성"""
        equity_ratio = self._equity_ratio_from_composite(
            us_regime.composite_score, us_regime.vix
        )
        cash_pct     = round((1 - equity_ratio) * 100, 1)
        invest_total = 100.0 * equity_ratio

        # ── 슬라이더 → 레버리지 ETF 비중 ──
        leverage_ratio = self.LEVERAGE_RATIO_TABLE.get(risk_level, 0.15)
        # 시장 약세 시 레버리지 비중 자동 축소 (composite 0.5 미만이면 50% 축소)
        if us_regime.composite_score < 0.5:
            leverage_ratio *= 0.5

        core_ratio = max(0.10, min(self.cfg.core_ratio, 0.90))

        leverage_budget  = invest_total * leverage_ratio
        remaining        = invest_total * (1 - leverage_ratio)
        core_budget      = remaining * core_ratio
        satellite_budget = remaining * (1 - core_ratio)

        core_final     = self._allocate_core(core_etfs, core_budget,
                                              pool_override=self.cfg.surge_core_etf_pool)
        leverage_final = self._allocate_core(leverage_etfs, leverage_budget,
                                              pool_override=self.cfg.leverage_etf_pool)

        # 위성 (surge_pool): 점수 비례 배분, 섹터 필터 안 함 (이미 다 그로스 섹터)
        ranked   = sorted(surge_candidates, key=lambda x: x.composite_score, reverse=True)
        selected = ranked[:self.cfg.top_n]
        total_score = sum(s.composite_score for s in selected) or 1.0
        for s in selected:
            raw = (s.composite_score / total_score) * satellite_budget
            s.weight_pct = round(min(raw, satellite_budget * self.cfg.single_stock_cap), 2)
        # 잔여 재배분
        allocated = sum(s.weight_pct for s in selected)
        if allocated < satellite_budget and selected:
            selected[-1].weight_pct = round(selected[-1].weight_pct + (satellite_budget - allocated), 2)

        return {
            "equity_ratio":  equity_ratio,
            "cash_pct":      cash_pct,
            "core_budget":   round(sum(e.weight_pct for e in core_final), 1),
            "leverage_budget": round(sum(e.weight_pct for e in leverage_final), 1),
            "us_budget":     round(sum(s.weight_pct for s in selected), 1),
            "kr_budget":     0.0,
            "core_etfs":     core_final,
            "leverage_etfs": leverage_final,
            "us_stocks":     selected,
            "kr_stocks":     [],
        }

    # ── 내부 헬퍼 ──────────────────────────────────────────────

    def _select_and_allocate(self, candidates: List[StockScore], budget: float) -> List[StockScore]:
        if not candidates or budget == 0:
            return []
        # 점수 내림차순 정렬 후 섹터 제한 적용
        ranked    = sorted(candidates, key=lambda x: x.composite_score, reverse=True)
        selected  = self._sector_filter(ranked)
        selected  = selected[:self.cfg.top_n]
        # 점수 비례 비중 배분
        total_score = sum(s.composite_score for s in selected) or 1.0
        for s in selected:
            raw = (s.composite_score / total_score) * budget
            # 단일 종목 상한
            s.weight_pct = round(min(raw, budget * self.cfg.single_stock_cap), 2)
        # 상한 컷 후 잔여 재배분 (마지막 종목에 합산 — 단순 처리)
        allocated = sum(s.weight_pct for s in selected)
        if allocated < budget and selected:
            selected[-1].weight_pct = round(selected[-1].weight_pct + (budget - allocated), 2)
        return selected

    def _sector_filter(self, ranked: List[StockScore]) -> List[StockScore]:
        """
        섹터당 최대 max_per_sector 종목만 통과
        """
        sector_count: dict = {}
        result = []
        for s in ranked:
            cnt = sector_count.get(s.sector, 0)
            if cnt < self.cfg.max_per_sector:
                result.append(s)
                sector_count[s.sector] = cnt + 1
        return result

    def _equity_ratio(self, vix: float) -> float:
        """VIX 단독 기반 (legacy, 백테스트 등에서 사용 가능)"""
        for threshold, ratio in self.cfg.vix_equity_table:
            if vix < threshold:
                return ratio
        return 0.0

    def _equity_ratio_from_composite(self, composite: float, vix: float) -> float:
        """
        Phase 1: 5개 신호 종합 점수 → equity_ratio.
        VIX EXTREME(>=40)일 땐 합성 점수 무시하고 강제 축소 (안전장치).

          composite ≥ 0.80  → 0.95  (강세 — 거의 풀투자)
          composite ≥ 0.65  → 0.85
          composite ≥ 0.50  → 0.70  (기본값 근처)
          composite ≥ 0.35  → 0.50
          composite ≥ 0.25  → 0.30
          composite <  0.25 → 0.15  (현금 대기 비중 ↑)
        """
        if vix >= 40:
            # EXTREME — 합성 점수 무시
            return 0.10

        if   composite >= 0.80: ratio = 0.95
        elif composite >= 0.65: ratio = 0.85
        elif composite >= 0.50: ratio = 0.70
        elif composite >= 0.35: ratio = 0.50
        elif composite >= 0.25: ratio = 0.30
        else:                   ratio = 0.15
        return ratio


# ==============================================================
# 메인 어드바이저
# ==============================================================

class InvestmentAdvisor:

    def __init__(
        self,
        cfg: Optional[AdvisorConfig] = None,
        profile: Optional[InvestorProfile] = None,
    ):
        base_cfg = cfg or AdvisorConfig()
        self.profile = profile or InvestorProfile()

        # 백수 모드면 보수적 파라미터로 자동 오버라이드
        if self.profile.employment_status == "unemployed":
            base_cfg = apply_unemployed_mode(base_cfg)
            logger.info("백수 모드 적용 — 보수적 파라미터로 전환")

        self.cfg      = base_cfg
        cache         = DataCache(self.cfg.cache_dir, self.cfg.cache_ttl_hours)
        fetcher       = DataFetcher(cache, self.cfg)
        self.macro    = MacroFilter(fetcher, self.cfg)
        self.screener = Screener(fetcher, self.cfg)
        self.portcons = PortfolioConstructor(self.cfg)
        # Phase 3: fear_score를 Screener에 전파하기 위한 슬롯 (analyze 후 set)
        self.current_fear_score: float = 0.0

    def run(self) -> Optional[dict]:
        print("\n" + "="*64)
        print("   INVESTMENT ADVISOR v3.0  ─  장기 우량주 포트폴리오 추천")
        print("="*64)

        # ── 자산 배분 플랜 (사용자가 입력했을 경우만) ─────────
        if self.profile.total_assets > 0:
            self._print_asset_plan()

        print("\n[1단계] 시장 국면 분석 중...\n")
        us_regime = self.macro.analyze(self.cfg.us_market)
        kr_regime = self.macro.analyze(self.cfg.kr_market)
        self._print_regime("🇺🇸 미국 (S&P 500)", us_regime)
        self._print_regime("🇰🇷 한국 (KOSPI)",   kr_regime)

        if not us_regime.is_investable:
            print(f"\n  ⛔  미국 시장 관망 신호 (VIX {us_regime.vix:.1f}). 현금 100% 유지 권장.\n")
            return None

        print("\n[2단계] 종목 스크리닝 중...")
        us_scored = [s for t in self.cfg.us_pool if (s := self.screener.score(t, "US"))]
        kr_scored = [s for t in self.cfg.kr_pool if (s := self.screener.score(t, "KR"))]
        print(f"  미국 통과: {len(us_scored)}개 / {len(self.cfg.us_pool)}개")
        print(f"  한국 통과: {len(kr_scored)}개 / {len(self.cfg.kr_pool)}개")

        if not us_scored:
            print("\n  ⚠️  현재 기준 충족 미국 종목 없음. 풀 확대 또는 기준 완화 필요.\n")
            return None

        print("\n[3단계] 포트폴리오 구성 중...")
        result = self.portcons.construct(us_scored, kr_scored, us_regime, kr_regime)

        # 비중 → 실제 금액 환산 (profile.investable_capital 기준)
        self._fill_invest_amounts(result)

        self._print_portfolio(result, us_regime)
        return result

    # ── 자산 배분 플랜 출력 ────────────────────────────────────

    def _print_asset_plan(self):
        p = self.profile
        status_kor = {
            "employed":   "안정 (재직)",
            "unemployed": "백수 (소득 없음)",
            "transition": "이직 중",
        }.get(p.employment_status, p.employment_status)

        print("\n" + "─"*64)
        print(f"   👤 투자자 프로필  [{status_kor}]")
        print("─"*64)
        print(f"  총 자산              : ₩{p.total_assets:>15,.0f}")
        print(f"   ├─ 정기예탁금        : ₩{p.deposits:>15,.0f}")
        print(f"   ├─ 입출금            : ₩{p.checking:>15,.0f}")
        print(f"   ├─ 목적자금 (청약 등): ₩{p.purpose_savings:>15,.0f}  (월 +{p.monthly_purpose:,.0f})")
        print(f"   └─ 노후자금 (IRP 등) : ₩{p.retirement:>15,.0f}  (월 +{p.monthly_retirement:,.0f})")
        print()
        print(f"  비상자금 (월 {p.monthly_expense:,.0f} × {p.emergency_months}개월 기준)")
        print(f"   → 확보 필요          : ₩{p.emergency_reserve:>15,.0f}")
        print(f"  현금성 자산 (예금+입출금)")
        print(f"   → 보유               : ₩{p.liquid_pool:>15,.0f}")
        print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  💼 자유 투자 가능 자금: ₩{p.investable_capital:>15,.0f}  "
              f"(${p.investable_usd:,.0f} @{p.usd_krw_rate:.0f})")
        if p.employment_status == "unemployed":
            print(f"  ⚠️  백수 모드: 신규 투입 자제, 기존 IRP 자동 적립({p.monthly_retirement:,.0f}원/월)만 유지")
        print("─"*64)

    # ── 비중 → 실제 금액 환산 ─────────────────────────────────

    def _fill_invest_amounts(self, result: dict):
        if self.profile.investable_capital <= 0:
            return
        krw = self.profile.investable_capital
        usd = self.profile.investable_usd
        for s in result.get("core_etfs", []):
            s.invest_amount = round(usd * s.weight_pct / 100, 0)
        for s in result.get("leverage_etfs", []):
            s.invest_amount = round(usd * s.weight_pct / 100, 0)
        for s in result.get("us_stocks", []):
            s.invest_amount = round(usd * s.weight_pct / 100, 0)
        for s in result.get("kr_stocks", []):
            s.invest_amount = round(krw * s.weight_pct / 100, 0)

    @staticmethod
    def _print_regime(label: str, r: MarketRegime):
        status = "✅ 투자 가능" if r.is_investable else "⛔ 관망"
        print(f"  {label}  [{status}]")
        print(f"  {r.detail}")
        print(f"  Trend Score: {r.trend_score:.2f}  |  Risk: {r.risk_level}\n")

    # ── 진입 플랜 표시용 헬퍼 ─────────────────────────────────

    _ACTION_TAG = {
        "IMMEDIATE":     "🟢",
        "SPLIT_BUY":     "🟡",
        "WAIT_PULLBACK": "🟠",
        "AVOID":         "🔴",
    }

    @classmethod
    def _print_portfolio(cls, res: dict, us_regime: MarketRegime):
        print("\n" + "="*64)
        print("   📊 최종 포트폴리오 추천")
        print("="*64)
        print(f"\n  💵 현금 보유       : {res['cash_pct']:.1f}%  (VIX {us_regime.vix:.1f} 기반)")
        print(f"  📈 주식 투자 총계  : {res['equity_ratio']*100:.0f}%")
        print(f"       ├─ 미국       : {res['us_budget']:.1f}%")
        print(f"       └─ 한국       : {res['kr_budget']:.1f}%\n")

        for label, stocks in [("🇺🇸 미국 추천", res["us_stocks"]),
                               ("🇰🇷 한국 추천", res["kr_stocks"])]:
            if not stocks:
                continue
            print(f"  {label}")
            print(f"  {'-'*70}")
            for s in stocks:
                cls._print_stock_card(s)
            print()

        print("  🟢 즉시 진입   🟡 분할 매수   🟠 눌림목 대기   🔴 진입 보류")
        print("  PBR `*` = 재무제표 직접 계산   |   PER `ᶠ` = forward PE 사용")
        print("  ⚠️  본 결과는 참고용이며 투자 책임은 투자자 본인에게 있습니다.")
        print("="*64 + "\n")

    @classmethod
    def _print_stock_card(cls, s):
        plan = s.entry_plan
        tag  = cls._ACTION_TAG.get(plan.action if plan else "", "⚪")
        action_label = plan.label if plan else "—"

        # ── N/A 표시 헬퍼 ────────────────────────────────────
        def fmt_pbr():
            if s.pbr is None:
                return "PBR  N/A "
            mark = "*" if s.pbr_source == "computed" else " "
            return f"PBR {s.pbr:>5.2f}{mark}"

        def fmt_per():
            if s.per is None:
                return "PER  N/A "
            mark = "ᶠ" if s.per_source == "forward" else " "
            return f"PER {s.per:>5.1f}{mark}"

        # 헤더
        print(f"  {tag} [{action_label}]  {s.name}  ({s.ticker})")

        # 비중·금액 라인
        amount_str = ""
        if s.invest_amount > 0:
            if s.country == "KR":
                amount_str = f"  ≈ ₩{s.invest_amount:,.0f}"
            else:
                amount_str = f"  ≈ ${s.invest_amount:,.0f}"
        print(f"     섹터: {s.sector}  |  비중: {s.weight_pct:.1f}%{amount_str}"
              f"  |  종합점수: {s.composite_score:.1f}")

        roe_str = f"ROE {s.roe:>5.1f}%" if s.roe is not None else "ROE   N/A"
        print(f"     {roe_str}  {fmt_pbr()}  {fmt_per()}  "
              f"RSI {s.rsi:>4.1f}  모멘텀 {s.momentum_pct:+5.1f}%")

        if not plan:
            print()
            return

        if plan.action == "IMMEDIATE":
            price = plan.target_levels[0][0]
            print(f"     ▸ 시장가 매수 (현재가 {cls._fmt_price(price, s.country)})")
            print(f"       사유: {plan.rationale}")

        elif plan.action == "SPLIT_BUY":
            print(f"     ▸ 3분할 진입:")
            for i, (price, pct) in enumerate(plan.target_levels, 1):
                tag_i = "현재가" if i == 1 else f"{(price/plan.current_price - 1)*100:+.1f}%"
                print(f"        {i}차  {cls._fmt_price(price, s.country):<12}"
                      f"({pct:.1f}%)  ─  {tag_i}")
            print(f"       사유: {plan.rationale}")

        elif plan.action == "WAIT_PULLBACK":
            target = plan.target_levels[0][0]
            pullback_pct = (target / plan.current_price - 1) * 100
            print(f"     ▸ 진입 대기:  현재 {cls._fmt_price(plan.current_price, s.country)}  →  "
                  f"목표 {cls._fmt_price(target, s.country)} ({pullback_pct:+.1f}%)")
            print(f"       사유: {plan.rationale}")

        elif plan.action == "AVOID":
            print(f"     ▸ 진입 보류")
            print(f"       사유: {plan.rationale}")

        print()

    @staticmethod
    def _fmt_price(price: float, country: str) -> str:
        if country == "KR":
            return f"₩{price:,.0f}"
        return f"${price:,.2f}"
