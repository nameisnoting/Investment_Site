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

    return profile, cfg, tier, current_invest, monthly_income, risk_level, deposit_keep_amount


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
    profile, cfg, tier, current_invest, monthly_income, risk_level, deposit_keep_amount = \
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


if __name__ == "__main__":
    # 로컬 실행 시 기본값. 배포(Render 등)에서는 gunicorn이 Procfile로 띄움.
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
