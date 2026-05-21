"""
main.py — 실행 진입점

사용법:
  python main.py           → 실시간 추천 (기본)
  python main.py backtest  → 백테스트 실행
  python main.py both      → 실시간 추천 + 백테스트

의존성 설치:
  pip install yfinance pandas numpy
"""

import sys
import logging

logging.basicConfig(
    level=logging.WARNING,      # INFO로 바꾸면 상세 진행상황 출력
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from config import AdvisorConfig
from models import InvestorProfile
from advisor import InvestmentAdvisor
from backtest import BacktestEngine


# ─────────────────────────────────────────────────────────────
# 👤 개인 자산 프로필 — 본인 상태에 맞게 여기만 수정하면 됨
# ─────────────────────────────────────────────────────────────
MY_PROFILE = InvestorProfile(
    deposits          = 65_326_954,   # 정기예탁금 (복리식 정기예탁금)
    checking          =  4_422_677,   # 입출금 계좌
    purpose_savings   =  7_420_000,   # 청년 주택드림 청약통장
    retirement        =  6_413_782,   # IRP (S&P500 위주)

    monthly_purpose   = 250_000,      # 청약 월 적립
    monthly_retirement= 100_000,      # IRP 월 적립

    monthly_expense   = 2_000_000,    # 월 생활비 추정
    emergency_months  = 6,            # 백수 → 6개월치 비상자금

    employment_status = "unemployed", # employed / unemployed / transition
    usd_krw_rate      = 1380.0,       # 환율 (대략값)
)


def run_live(cfg: AdvisorConfig, profile: InvestorProfile):
    print("\n" + "─"*64)
    print("  MODE: 실시간 포트폴리오 추천")
    print("─"*64)
    advisor = InvestmentAdvisor(cfg, profile)
    advisor.run()


def run_backtest(cfg: AdvisorConfig):
    print("\n" + "─"*64)
    print("  MODE: 워크포워드 백테스트")
    print("─"*64)
    engine = BacktestEngine(cfg)
    result = engine.run(cfg.us_pool, cfg.kr_pool)
    return result


if __name__ == "__main__":
    cfg  = AdvisorConfig()
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"

    if mode == "live":
        run_live(cfg, MY_PROFILE)
    elif mode == "backtest":
        run_backtest(cfg)
    elif mode == "both":
        run_live(cfg, MY_PROFILE)
        run_backtest(cfg)
    else:
        print(f"알 수 없는 모드: {mode}")
        print("사용법: python main.py [live|backtest|both]")
