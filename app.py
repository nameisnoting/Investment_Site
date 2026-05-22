"""
app.py — Flask 웹 백엔드

폼 입력값 → InvestorProfile + AdvisorConfig 변환 → InvestmentAdvisor.run() 실행 → JSON 결과.

자산 수준 + 소득 여부에 따른 분기 정책
─────────────────────────────────────────────
  1) 소득 여부
     - 없음                      → employment_status="unemployed"  (보수 모드 강제)
     - 있음 + 월수입 < 150만원   → "transition" (반-보수)
     - 있음 + 월수입 >= 150만원  → "employed"   (표준)

  2) 자산 규모 (총자산 = 현금+예금+적금+IRP+기존투자금)
     - tier=starter  (< 3천만)   top_n=3, 한국 비중 0%
     - tier=small    (< 1억)     top_n=3, 표준
     - tier=mid      (< 5억)     top_n=4
     - tier=large    (≥ 5억)     top_n=5, 단일종목 상한 25%

  3) 월수입 → 월 생활비 추정 (소득의 60%, 상한 300만), 비상자금 자동 산정
"""

import logging
import os
from dataclasses import replace
from io import StringIO
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

from advisor import InvestmentAdvisor
from config import AdvisorConfig
from models import InvestorProfile, MarketRegime, StockScore

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")

app = Flask(__name__)


# ──────────────────────────────────────────────────────────────
# 입력값 → Profile / Config 매핑
# ──────────────────────────────────────────────────────────────

def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def derive_profile_and_cfg(data: Dict[str, Any]):
    cash               = _to_float(data.get("cash"))
    deposits           = _to_float(data.get("deposits"))
    savings            = _to_float(data.get("savings"))
    monthly_savings    = _to_float(data.get("monthly_savings"))
    retirement         = _to_float(data.get("retirement"))
    monthly_retirement = _to_float(data.get("monthly_retirement"))
    current_invest     = _to_float(data.get("current_invest"))
    has_income         = str(data.get("has_income", "no")).lower() == "yes"
    monthly_income     = _to_float(data.get("monthly_income")) if has_income else 0.0

    # 투자 성향 슬라이더 (1=안정 ... 5=공격), 기본 3=중간
    risk_level = int(data.get("risk_level", 3) or 3)
    risk_level = max(1, min(risk_level, 5))

    # 포트폴리오 전략 (Phase A에선 수신만, Phase B/C/D에서 분기 적용)
    strategy = str(data.get("strategy", "long_term")).lower()
    if strategy not in ("long_term", "surge", "momentum", "mixed"):
        strategy = "long_term"

    # ── 고용 상태 분기 ────────────────────────────────────────
    if not has_income:
        status = "unemployed"
    elif monthly_income < 1_500_000:
        status = "transition"
    else:
        status = "employed"

    # ── 월 생활비 추정 (입력값 없으니 휴리스틱) ──────────────
    if has_income and monthly_income > 0:
        monthly_expense = min(monthly_income * 0.6, 3_000_000)
    else:
        monthly_expense = 2_000_000

    # 슬라이더 → 예금 유지율 / 코어 비중 배율
    # Stage:           1     2     3     4     5
    deposit_keep_table = [1.0,  0.6,  0.3,  0.1,  0.0]
    core_mult_table    = [1.30, 1.15, 1.00, 0.70, 0.40]
    deposit_keep_pct = deposit_keep_table[risk_level - 1]
    core_mult        = core_mult_table[risk_level - 1]
    deposit_keep_amount = deposits * deposit_keep_pct

    profile = InvestorProfile(
        deposits           = deposits,
        checking           = cash,
        purpose_savings    = savings,
        retirement         = retirement,
        monthly_purpose    = monthly_savings,
        monthly_retirement = monthly_retirement,
        monthly_expense    = monthly_expense,
        emergency_months   = 6,
        employment_status  = status,
        usd_krw_rate       = 1380.0,
        deposit_keep_amount = deposit_keep_amount,
    )

    # ── 자산 규모 분기 (기존 투자금 포함) ────────────────────
    # 자산 적을수록 코어 ETF 비중 ↑ (분산 우선)
    total_for_tier = profile.total_assets + current_invest
    cfg = AdvisorConfig()
    # dca_months: 자산 클수록 적립 기간 길게 → 월 부담 균등화
    if total_for_tier < 30_000_000:
        cfg  = replace(cfg, top_n=3, single_stock_cap=0.35,
                       kr_weight=0.0, us_weight=1.0, core_ratio=0.80,
                       dca_months=18)
        tier = "starter"
    elif total_for_tier < 100_000_000:
        cfg  = replace(cfg, top_n=3, core_ratio=0.65, dca_months=24)
        tier = "small"
    elif total_for_tier < 500_000_000:
        cfg  = replace(cfg, top_n=4, core_ratio=0.50, dca_months=36)
        tier = "mid"
    else:
        cfg  = replace(cfg, top_n=5, single_stock_cap=0.25, core_ratio=0.35,
                       dca_months=48)
        tier = "large"

    # 백수면 코어 비중 +10%p (apply_unemployed_mode는 advisor 내부에서 적용)
    if status == "unemployed":
        cfg = replace(cfg, core_ratio=min(cfg.core_ratio + 0.10, 0.90))

    # 슬라이더 배율 적용 — tier/employment 기본값을 사용자 성향으로 조정
    adjusted = max(0.10, min(cfg.core_ratio * core_mult, 0.95))
    cfg = replace(cfg, core_ratio=adjusted)

    return profile, cfg, tier, current_invest, monthly_income, risk_level, deposit_keep_amount, strategy


# ──────────────────────────────────────────────────────────────
# 결과 직렬화
# ──────────────────────────────────────────────────────────────

def _serialize_stock(s: StockScore) -> dict:
    plan = s.entry_plan
    return {
        "ticker": s.ticker,
        "name": s.name,
        "country": s.country,
        "sector": s.sector,
        "roe": s.roe,
        "pbr": s.pbr,
        "pbr_source": s.pbr_source,
        "per": s.per,
        "per_source": s.per_source,
        "debt_to_equity": s.debt_to_equity,
        "fundamental_score": s.fundamental_score,
        "rsi": s.rsi,
        "trend_score": s.trend_score,
        "momentum_pct": s.momentum_pct,
        "technical_score": s.technical_score,
        "composite_score": s.composite_score,
        "weight_pct": s.weight_pct,
        "invest_amount": s.invest_amount,
        # Phase 2: 신규 펀더멘털
        "fcf_margin": s.fcf_margin,
        "operating_margin": s.operating_margin,
        "peg": s.peg,
        "entry_plan": None if plan is None else {
            "action": plan.action,
            "label": plan.label,
            "rationale": plan.rationale,
            "current_price": plan.current_price,
            "target_levels": [list(t) for t in plan.target_levels],
            "confidence": plan.confidence,
        },
    }


def _serialize_regime(r: MarketRegime) -> dict:
    return {
        "market": r.market,
        "is_investable": r.is_investable,
        "trend_score": r.trend_score,
        "risk_level": r.risk_level,
        "vix": r.vix,
        "price_vs_ma200_pct": r.price_vs_ma200_pct,
        "detail": r.detail,
        # Phase 1: 다차원 신호
        "composite_score": r.composite_score,
        "vix_signal": r.vix_signal,
        "breadth_pct": r.breadth_pct,
        "yield_curve_spread": r.yield_curve_spread,
        "yield_curve_signal": r.yield_curve_signal,
        "sector_rotation": r.sector_rotation,
        # Phase 3: 역발상 매수 시그널
        "fear_score": r.fear_score,
    }


# ──────────────────────────────────────────────────────────────
# 라우트
# ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/advise", methods=["POST"])
def advise():
    data = request.get_json(force=True, silent=True) or {}
    profile, cfg, tier, current_invest, monthly_income, risk_level, deposit_keep_amount, strategy = \
        derive_profile_and_cfg(data)

    advisor = InvestmentAdvisor(cfg, profile)

    # advisor.run()은 print를 많이 함 → stdout 일단 캡쳐 (디버깅용)
    import sys
    buf = StringIO()
    orig = sys.stdout
    sys.stdout = buf
    try:
        us_regime = advisor.macro.analyze(advisor.cfg.us_market, deep=True)
        kr_regime = advisor.macro.analyze(advisor.cfg.kr_market, deep=False)

        # Phase 3: 시장 fear_score를 Screener에 전파 → 각 종목 entry_plan에 반영
        advisor.screener.fear_score = us_regime.fear_score

        result_payload: Dict[str, Any] = {
            "profile": {
                "total_assets": profile.total_assets,
                "current_invest": current_invest,
                "grand_total": profile.total_assets + current_invest,
                "liquid_pool": profile.liquid_pool,
                "emergency_reserve": profile.emergency_reserve,
                "investable_capital": profile.investable_capital,
                "investable_usd": profile.investable_usd,
                "employment_status": profile.employment_status,
                "monthly_expense": profile.monthly_expense,
                "monthly_income": monthly_income,
                "tier": tier,
                "usd_krw_rate": profile.usd_krw_rate,
                "risk_level": risk_level,
                "deposit_keep_amount": deposit_keep_amount,
                "strategy": strategy,
            },
            "us_regime": _serialize_regime(us_regime),
            "kr_regime": _serialize_regime(kr_regime),
            "portfolio": None,
            "log": "",
            "ok": True,
        }

        if not us_regime.is_investable:
            result_payload["message"] = (
                f"미국 시장 관망 신호 (VIX {us_regime.vix:.1f}, "
                f"{us_regime.risk_level}). 현금 100% 유지 권장."
            )
            return jsonify(result_payload)

        # ──────────────────────────────────────────────────────
        # 전략 분기: long_term (기존) / surge (Phase B) / momentum (Phase C) / mixed (Phase D)
        # ──────────────────────────────────────────────────────
        def _build_long_term():
            """장기 모드 portfolio dict 구성 (mixed에서 재사용)"""
            us_s = [s for t in advisor.cfg.us_pool if (s := advisor.screener.score(t, "US"))]
            kr_s = [s for t in advisor.cfg.kr_pool if (s := advisor.screener.score(t, "KR"))]
            core_s = [s for e in advisor.cfg.core_etf_pool
                      if (s := advisor.screener.score_etf(e["ticker"], e["name"]))]
            if not us_s and not core_s: return None, (len(advisor.cfg.us_pool), 0, len(advisor.cfg.kr_pool), 0, len(advisor.cfg.core_etf_pool), 0)
            p = advisor.portcons.construct(us_s, kr_s, core_s, us_regime, kr_regime)
            advisor._fill_invest_amounts(p)
            return p, (len(advisor.cfg.us_pool), len(us_s), len(advisor.cfg.kr_pool), len(kr_s),
                       len(advisor.cfg.core_etf_pool), len(core_s))

        def _build_surge():
            surge_s = [s for t in advisor.cfg.surge_pool if (s := advisor.screener.score_surge(t, "US"))]
            core_s  = [s for e in advisor.cfg.surge_core_etf_pool
                       if (s := advisor.screener.score_etf(e["ticker"], e["name"]))]
            lev_s   = [s for e in advisor.cfg.leverage_etf_pool
                       if (s := advisor.screener.score_etf(e["ticker"], e["name"]))]
            if not surge_s and not core_s: return None, (len(advisor.cfg.surge_pool), 0, 0, 0,
                                                          len(advisor.cfg.surge_core_etf_pool), 0, len(advisor.cfg.leverage_etf_pool), 0)
            p = advisor.portcons.construct_surge(surge_s, core_s, lev_s, us_regime, risk_level)
            advisor._fill_invest_amounts(p)
            return p, (len(advisor.cfg.surge_pool), len(surge_s), 0, 0,
                       len(advisor.cfg.surge_core_etf_pool), len(core_s),
                       len(advisor.cfg.leverage_etf_pool), len(lev_s))

        def _build_momentum():
            base = list(set(advisor.cfg.us_pool + advisor.cfg.surge_pool))
            mom_s = [s for t in base if (s := advisor.screener.score_momentum(t, "US"))]
            qqq_meta = next((e for e in advisor.cfg.core_etf_pool if e["ticker"] == "QQQ"), None)
            qqq_s = []
            if qqq_meta:
                qs = advisor.screener.score_etf("QQQ", qqq_meta["name"])
                if qs: qqq_s = [qs]
            if not mom_s: return None, (len(base), 0, 0, 0, 1, 0)
            p = advisor.portcons.construct_momentum(mom_s, qqq_s, us_regime, risk_level)
            advisor._fill_invest_amounts(p)
            return p, (len(base), len(mom_s), 0, 0, 1, len(qqq_s))

        if strategy == "mixed":
            # ── 혼합 모드: 세 모드 각각 실행 후 가중 합성 ────
            long_p, long_sc   = _build_long_term()
            surge_p, surge_sc = _build_surge()
            mom_p, mom_sc     = _build_momentum()

            # 빈 결과는 빈 dict로 대체 (가중치만 적용되니 안전)
            empty = {"equity_ratio": 0.0, "cash_pct": 0.0, "core_etfs": [],
                     "leverage_etfs": [], "us_stocks": [], "kr_stocks": []}
            portfolio = advisor.portcons.merge_mixed(
                long_p or empty, surge_p or empty, mom_p or empty, risk_level
            )

            result_payload["portfolio"] = {
                "equity_ratio":    portfolio["equity_ratio"],
                "cash_pct":        portfolio["cash_pct"],
                "core_budget":     portfolio["core_budget"],
                "leverage_budget": portfolio["leverage_budget"],
                "us_budget":       portfolio["us_budget"],
                "kr_budget":       portfolio["kr_budget"],
                "core_etfs":       [_serialize_stock(s) for s in portfolio["core_etfs"]],
                "leverage_etfs":   [_serialize_stock(s) for s in portfolio["leverage_etfs"]],
                "us_stocks":       [_serialize_stock(s) for s in portfolio["us_stocks"]],
                "kr_stocks":       [_serialize_stock(s) for s in portfolio["kr_stocks"]],
                "core_ratio":      advisor.cfg.core_ratio,
                "dca_months":      advisor.cfg.dca_months,
                "mixed_weights":   portfolio["mixed_weights"],
                "screened": {
                    "us_total":  (long_sc[0] if long_sc else 0) + (surge_sc[0] if surge_sc else 0),
                    "us_passed": (long_sc[1] if long_sc else 0) + (surge_sc[1] if surge_sc else 0) + (mom_sc[1] if mom_sc else 0),
                    "kr_total":  long_sc[2] if long_sc else 0,
                    "kr_passed": long_sc[3] if long_sc else 0,
                    "core_total":  (long_sc[4] if long_sc else 0) + (surge_sc[4] if surge_sc else 0),
                    "core_passed": (long_sc[5] if long_sc else 0) + (surge_sc[5] if surge_sc else 0),
                    "leverage_total":  surge_sc[6] if surge_sc and len(surge_sc) > 6 else 0,
                    "leverage_passed": surge_sc[7] if surge_sc and len(surge_sc) > 7 else 0,
                },
            }
            return jsonify(result_payload)

        if strategy == "momentum":
            # ── 모멘텀 추종 모드 (단기 트레이딩) ────────────
            # 베이스 풀: us_pool + surge_pool 합집합 (43종목)
            base_pool = list(set(advisor.cfg.us_pool + advisor.cfg.surge_pool))
            mom_scored = [s for t in base_pool
                          if (s := advisor.screener.score_momentum(t, "US"))]
            # QQQ만 코어
            qqq_meta = next((e for e in advisor.cfg.core_etf_pool if e["ticker"] == "QQQ"), None)
            qqq_scored = []
            if qqq_meta:
                qs = advisor.screener.score_etf("QQQ", qqq_meta["name"])
                if qs:
                    qqq_scored = [qs]

            if not mom_scored:
                result_payload["message"] = (
                    "모멘텀 통과 종목이 없음 (30일 +15% 이상 필요). "
                    "시장 추세가 약하면 흔한 결과 — 현금 비중 유지 권장."
                )
                return jsonify(result_payload)

            portfolio = advisor.portcons.construct_momentum(
                mom_scored, qqq_scored, us_regime, risk_level
            )
            advisor._fill_invest_amounts(portfolio)

            result_payload["portfolio"] = {
                "equity_ratio":  portfolio["equity_ratio"],
                "cash_pct":      portfolio["cash_pct"],
                "core_budget":   portfolio["core_budget"],
                "leverage_budget": 0.0,
                "us_budget":     portfolio["us_budget"],
                "kr_budget":     0.0,
                "core_etfs":     [_serialize_stock(s) for s in portfolio["core_etfs"]],
                "leverage_etfs": [],
                "us_stocks":     [_serialize_stock(s) for s in portfolio["us_stocks"]],
                "kr_stocks":     [],
                "core_ratio":    0.25,
                "dca_months":    advisor.cfg.dca_months,
                "screened": {
                    "us_total":  len(base_pool),
                    "us_passed": len(mom_scored),
                    "kr_total":  0,
                    "kr_passed": 0,
                    "core_total":  1,
                    "core_passed": len(qqq_scored),
                },
            }
            return jsonify(result_payload)

        if strategy == "surge":
            # ── 폭등시그널 모드 ─────────────────────────────
            surge_scored = [s for t in advisor.cfg.surge_pool
                            if (s := advisor.screener.score_surge(t, "US"))]
            core_scored  = [s for e in advisor.cfg.surge_core_etf_pool
                            if (s := advisor.screener.score_etf(e["ticker"], e["name"]))]
            leverage_scored = [s for e in advisor.cfg.leverage_etf_pool
                               if (s := advisor.screener.score_etf(e["ticker"], e["name"]))]

            if not surge_scored and not core_scored:
                result_payload["message"] = "폭등시그널 통과 종목이 없음."
                return jsonify(result_payload)

            portfolio = advisor.portcons.construct_surge(
                surge_scored, core_scored, leverage_scored, us_regime, risk_level
            )
            advisor._fill_invest_amounts(portfolio)

            result_payload["portfolio"] = {
                "equity_ratio":    portfolio["equity_ratio"],
                "cash_pct":        portfolio["cash_pct"],
                "core_budget":     portfolio["core_budget"],
                "leverage_budget": portfolio["leverage_budget"],
                "us_budget":       portfolio["us_budget"],
                "kr_budget":       portfolio["kr_budget"],
                "core_etfs":       [_serialize_stock(s) for s in portfolio["core_etfs"]],
                "leverage_etfs":   [_serialize_stock(s) for s in portfolio["leverage_etfs"]],
                "us_stocks":       [_serialize_stock(s) for s in portfolio["us_stocks"]],
                "kr_stocks":       [],
                "core_ratio":      advisor.cfg.core_ratio,
                "dca_months":      advisor.cfg.dca_months,
                "screened": {
                    "us_total":      len(advisor.cfg.surge_pool),
                    "us_passed":     len(surge_scored),
                    "kr_total":      0,
                    "kr_passed":     0,
                    "core_total":    len(advisor.cfg.surge_core_etf_pool),
                    "core_passed":   len(core_scored),
                    "leverage_total":  len(advisor.cfg.leverage_etf_pool),
                    "leverage_passed": len(leverage_scored),
                },
            }
            return jsonify(result_payload)

        # ── 기본: long_term 모드 (mixed / momentum도 일단 여기로 → Phase C/D) ──
        us_scored = [s for t in advisor.cfg.us_pool
                     if (s := advisor.screener.score(t, "US"))]
        kr_scored = [s for t in advisor.cfg.kr_pool
                     if (s := advisor.screener.score(t, "KR"))]

        # 코어 ETF 평가 (펀더멘털 게이트 우회)
        core_scored = [s for e in advisor.cfg.core_etf_pool
                       if (s := advisor.screener.score_etf(e["ticker"], e["name"]))]

        if not us_scored and not core_scored:
            result_payload["message"] = (
                "현재 기준을 통과한 종목/ETF가 없음. "
                "VIX 또는 데이터 fetch 상태 점검 필요."
            )
            return jsonify(result_payload)

        portfolio = advisor.portcons.construct(us_scored, kr_scored, core_scored,
                                               us_regime, kr_regime)
        advisor._fill_invest_amounts(portfolio)

        result_payload["portfolio"] = {
            "equity_ratio": portfolio["equity_ratio"],
            "cash_pct":     portfolio["cash_pct"],
            "core_budget":  portfolio["core_budget"],
            "us_budget":    portfolio["us_budget"],
            "kr_budget":    portfolio["kr_budget"],
            "core_etfs":    [_serialize_stock(s) for s in portfolio["core_etfs"]],
            "us_stocks":    [_serialize_stock(s) for s in portfolio["us_stocks"]],
            "kr_stocks":    [_serialize_stock(s) for s in portfolio["kr_stocks"]],
            "core_ratio":   advisor.cfg.core_ratio,
            "dca_months":   advisor.cfg.dca_months,
            "screened": {
                "us_total":  len(advisor.cfg.us_pool),
                "us_passed": len(us_scored),
                "kr_total":  len(advisor.cfg.kr_pool),
                "kr_passed": len(kr_scored),
                "core_total":  len(advisor.cfg.core_etf_pool),
                "core_passed": len(core_scored),
            },
        }
        return jsonify(result_payload)
    except Exception as e:
        logging.exception("advise failed")
        return jsonify({"ok": False, "error": str(e), "log": buf.getvalue()}), 500
    finally:
        sys.stdout = orig


@app.route("/api/backtest", methods=["POST"])
def backtest():
    """
    추천 포트폴리오 buy-and-hold 백테스팅 + S&P 500 벤치마크 비교.

    입력 (JSON):
      {
        "holdings": [{"ticker": "AAPL", "weight_pct": 15.5}, ...],
        "years":    5   (옵션, 기본 5)
      }

    출력:
      {
        "ok": true,
        "equity_curve":     [[YYYY-MM-DD, value], ...],
        "benchmark_curve":  [[YYYY-MM-DD, value], ...],
        "stats": {
          "cagr": 0.12, "volatility": 0.18, "sharpe": 0.65,
          "max_drawdown": -0.25, "total_return": 0.85,
          "alpha": 0.02, "beta": 1.05, "benchmark_cagr": 0.10,
        },
        "skipped":  ["TICKER1", "TICKER2"],   // 5년 데이터 없는 종목
        "n_years":  5,
      }
    """
    import yfinance as yf
    import pandas as pd
    import numpy as np

    data = request.get_json(force=True, silent=True) or {}
    holdings = data.get("holdings", [])
    years = int(data.get("years", 5))
    years = max(1, min(years, 10))

    if not holdings:
        return jsonify({"ok": False, "error": "holdings 비어있음"}), 400

    # 비중 정규화 (현금/원하는 외 종목만으로 100% 만들기)
    total_w = sum(float(h.get("weight_pct", 0)) for h in holdings)
    if total_w <= 0:
        return jsonify({"ok": False, "error": "비중 합이 0"}), 400

    end   = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(years=years)
    tickers = [h["ticker"] for h in holdings]

    # 한국 종목은 yfinance에서 받아오기 (Render에선 차단 가능)
    try:
        df = yf.download(tickers, start=start, end=end,
                         progress=False, auto_adjust=True, group_by="ticker")
    except Exception as e:
        return jsonify({"ok": False, "error": f"yfinance 다운로드 실패: {e}"}), 500

    if df.empty:
        return jsonify({"ok": False, "error": "가격 데이터 없음 (Render에서 yfinance 차단 가능)"}), 500

    # 종목별 종가 시계열 추출
    closes = {}
    skipped = []
    for h in holdings:
        t = h["ticker"]
        try:
            if len(tickers) == 1:
                series = df["Close"] if "Close" in df.columns else df
            else:
                series = df[t]["Close"] if t in df.columns.get_level_values(0) else None
            if series is None or series.dropna().empty:
                skipped.append(t); continue
            # 5년 전체 데이터가 있어야 의미 있음 (50% 이상 있으면 통과)
            valid = series.dropna()
            expected_days = years * 250 * 0.5
            if len(valid) < expected_days:
                skipped.append(t); continue
            closes[t] = valid
        except Exception:
            skipped.append(t)

    if not closes:
        return jsonify({"ok": False, "error": "유효한 종목 없음", "skipped": skipped}), 500

    # 비중 재정규화 (skipped 제외)
    valid_weights = {t: float(h["weight_pct"]) for t, h in zip(tickers, holdings) if t in closes}
    sum_w = sum(valid_weights.values())
    if sum_w <= 0:
        return jsonify({"ok": False, "error": "유효 비중 0"}), 500
    norm_weights = {t: w / sum_w for t, w in valid_weights.items()}

    # 공통 날짜 인덱스 정렬 + forward fill
    price_df = pd.DataFrame({t: s for t, s in closes.items()}).ffill().dropna()
    if price_df.empty or len(price_df) < 30:
        return jsonify({"ok": False, "error": "공통 가격 데이터 부족"}), 500

    # 일별 수익률
    rets = price_df.pct_change().fillna(0)
    # 포트폴리오 일별 수익률 = 종목별 수익률 × 비중
    port_rets = sum(rets[t] * norm_weights[t] for t in price_df.columns)
    # 누적 수익률 (기준값 1.0)
    equity_curve = (1 + port_rets).cumprod()

    # 벤치마크 (S&P 500 = SPY)
    try:
        spy = yf.download("SPY", start=start, end=end, progress=False, auto_adjust=True)
        spy_close = spy["Close"].squeeze() if not spy.empty else None
        if spy_close is None or spy_close.dropna().empty:
            bench_curve = pd.Series(dtype=float)
        else:
            spy_close = spy_close.reindex(equity_curve.index).ffill().dropna()
            bench_rets = spy_close.pct_change().fillna(0)
            bench_curve = (1 + bench_rets).cumprod()
            # 공통 인덱스로 다시 정렬
            common = equity_curve.index.intersection(bench_curve.index)
            equity_curve = equity_curve.reindex(common)
            bench_curve  = bench_curve.reindex(common)
    except Exception as e:
        logging.warning(f"SPY 벤치마크 다운로드 실패: {e}")
        bench_curve = pd.Series(dtype=float)

    # ── 통계 ─────────────────────────────────────────
    total_days = len(equity_curve)
    n_years = total_days / 252.0 if total_days > 0 else 1.0
    total_ret = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1)
    cagr = (1 + total_ret) ** (1.0 / n_years) - 1 if n_years > 0 else 0.0

    daily_ret = equity_curve.pct_change().dropna()
    vol_annual = float(daily_ret.std() * np.sqrt(252)) if len(daily_ret) > 1 else 0.0
    rf = 0.045
    sharpe = (cagr - rf) / vol_annual if vol_annual > 0 else 0.0

    # 최대 낙폭 (MDD)
    peak = equity_curve.cummax()
    drawdown = equity_curve / peak - 1
    mdd = float(drawdown.min()) if len(drawdown) > 0 else 0.0

    # 알파/베타 (벤치마크 있을 때만)
    alpha, beta, bench_cagr, bench_total = 0.0, 1.0, 0.0, 0.0
    if not bench_curve.empty and len(bench_curve) > 30:
        bench_daily = bench_curve.pct_change().dropna()
        # 공통 인덱스
        idx = daily_ret.index.intersection(bench_daily.index)
        if len(idx) > 30:
            x = bench_daily.reindex(idx).values
            y = daily_ret.reindex(idx).values
            cov = float(np.cov(y, x)[0, 1])
            var = float(np.var(x))
            beta = cov / var if var > 0 else 1.0
            # 알파 = 포트폴리오 CAGR − (rf + beta × (benchmark_cagr − rf))
            bench_total = float(bench_curve.iloc[-1] / bench_curve.iloc[0] - 1)
            bench_cagr = (1 + bench_total) ** (1.0 / n_years) - 1
            alpha = cagr - (rf + beta * (bench_cagr - rf))

    # 시계열 다운샘플링 (월별, 차트 데이터량 감소)
    def downsample(series):
        if series.empty: return []
        monthly = series.resample("ME").last().dropna()
        return [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in monthly.items()]

    return jsonify({
        "ok": True,
        "equity_curve":    downsample(equity_curve),
        "benchmark_curve": downsample(bench_curve) if not bench_curve.empty else [],
        "stats": {
            "cagr":          round(cagr, 4),
            "volatility":    round(vol_annual, 4),
            "sharpe":        round(sharpe, 3),
            "max_drawdown":  round(mdd, 4),
            "total_return":  round(total_ret, 4),
            "alpha":         round(alpha, 4),
            "beta":          round(beta, 3),
            "benchmark_cagr":  round(bench_cagr, 4),
            "benchmark_total": round(bench_total, 4),
        },
        "skipped": skipped,
        "n_years": round(n_years, 2),
        "n_tickers": len(closes),
    })


if __name__ == "__main__":
    # 로컬 실행 시 기본값. 배포(Render 등)에서는 gunicorn이 Procfile로 띄움.
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
