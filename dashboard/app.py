from __future__ import annotations

import html
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import requests
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.view_models import (
    build_database_revision_key,
    build_latest_signal_context,
    build_overview_frame,
    build_prediction_confidence,
    filter_overview_frame,
    metric_value,
    normalize_pair_value,
    resolve_pair_state,
    resolve_score,
    resolve_selected_pair,
)
from token_meme_monitor.clients.geckoterminal import GeckoTerminalClient, compute_lookback_returns
from token_meme_monitor.config import load_config
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.utils import first_non_missing, json_loads


PAGE_TITLE = "Binance Alpha / BSC 监控面板"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
OVERVIEW_FETCH_LIMIT = 2000
LIST_DISPLAY_LIMIT = 10
PAIR_SELECTOR_WIDGET_KEY = "pair_selector_radio_cards_v2"
PAIR_QUERY_SYNC_KEY = "_last_synced_pair_query"
DASHBOARD_LAST_REFRESH_TS_KEY = "_dashboard_last_refresh_ts"
DASHBOARD_REFRESH_RERUN_PENDING_KEY = "_dashboard_refresh_rerun_pending"
DASHBOARD_STATUS_KEY = "_dashboard_status"
DETAIL_VIEW_WIDGET_KEY = "detail_view_mode"
DETAIL_VIEW_PAIR_SYNC_KEY = "_detail_view_pair_sync"

DETAIL_VIEW_OPTIONS = [
    ("overview", "量价快照"),
    ("prediction", "预测"),
    ("explanation", "结论依据"),
    ("features", "指标备注"),
    ("trend", "走势"),
    ("history", "历史记录"),
]
FILTER_MODES = {
    "discover": ("宽松观察", 0, 500, 5000),
    "balanced": ("平衡跟踪", 0, 1000, 15000),
    "focus": ("重点聚焦", 0, 2000, 30000),
}
LIST_GROUP_ORDER = (
    ("launch", "启动异动"),
    ("risk_momentum", "高风险动量"),
    ("strong", "强确认"),
    ("normal", "普通观察"),
)
LIST_GROUP_ALIASES = {
    "overextended": "normal",
}
SIGNAL_THRESHOLD_OPTIONS = [0, 45, 55, 65, 75, 85]
HOLDER_THRESHOLD_OPTIONS = [0, 500, 1000, 2000, 5000, 10000, 20000]
LIQUIDITY_THRESHOLD_OPTIONS = [0, 5000, 15000, 30000, 50000, 100000, 250000]
ONLY_MARKET_DATA_HELP = "开启后只显示已经拿到实时行情快照的代币，隐藏暂时缺少价格、成交额或流动性数据的候选项；关闭后展示全部已发现候选。"
EXTERNAL_TREND_NETWORK = "bsc"
EXTERNAL_TREND_OHLCV_LIMIT = 48
EXTERNAL_TREND_CACHE_TTL_SECONDS = 300
EXTERNAL_TREND_MAX_CURRENT_LAG_SECONDS = 2 * 3600

STATE_LABELS = {
    "new": "新发现",
    "watching": "观察中",
    "focused": "继续跟踪",
    "alerted": "重点关注",
    "archived": "已归档",
}

PREDICTION_STAGE_LABELS = {
    "early": "早期",
    "acceleration": "加速",
    "late": "偏后段",
    "exhaustion": "过热",
}

REASON_LABELS = {
    "active_dex_boost": ("Dex 推广活跃", "DexScreener 推广状态可能带来短期注意力。"),
    "alpha_hot_score": ("Alpha 热度高", "Binance Alpha 分数较高，说明关注度基础较强。"),
    "alpha_score_support": ("Alpha 分数支撑", "Binance Alpha 分数达到基础支撑区间。"),
    "ample_liquidity": ("流动性充足", "池子深度更高，价格滑点风险相对更低。"),
    "balanced_liquidity_to_fdv": ("流动性/估值平衡", "流动性相对估值没有明显失衡。"),
    "binance_futures_listed": ("币安合约标签", "币安合约标签可能带来额外关注。"),
    "h1_buy_dominance": ("1小时买盘主导", "近 1 小时买入结构更强。"),
    "healthy_liquidity_band": ("流动性健康", "池子深度达到基础交易条件。"),
    "holder_depth": ("持币人数较深", "链上持币人数较多，分布基础更成熟。"),
    "liquidity_healthy": ("流动性健康", "池子深度达到基础交易条件。"),
    "liquidity_deep": ("流动性充足", "池子深度更高，价格滑点风险相对更低。"),
    "m5_buy_dominance": ("5分钟买盘主导", "短线买入结构更强。"),
    "m5_volume_impulse": ("5分钟短时放量", "短窗口成交额明显抬升。"),
    "h1_volume_support": ("1小时成交额达标", "近 1 小时成交额达到策略阈值。"),
    "m5_volume_spike": ("5分钟短时放量", "短窗口成交额明显抬升。"),
    "m5_buy_pressure": ("5分钟买盘主导", "短线买入笔数和买卖比偏强。"),
    "h1_buy_pressure": ("1小时买盘偏强", "近 1 小时买卖结构更偏主动买入。"),
    "volume_to_liquidity_breakout": ("成交额/流动性突破", "成交额相对池子深度放大，说明轮动强度提升。"),
    "volume_to_liquidity_support": ("成交额/流动性支撑", "成交活跃度相对流动性有一定支撑。"),
    "fdv_liquidity_balanced": ("估值与流动性相对平衡", "完全稀释估值和池子流动性的比例没有明显失衡。"),
    "project_metadata_complete": ("项目信息完整", "网站或社媒等公开信息较完整。"),
    "project_metadata_present": ("项目信息可见", "网站或社媒等公开信息可复核。"),
    "partial_metadata_present": ("项目信息部分可见", "至少有部分公开项目信息可复核。"),
    "project_metadata_partial": ("项目信息部分可见", "至少有部分公开项目信息可复核。"),
    "boosted": ("Dex 推广中", "DexScreener 推广状态可能带来短期注意力。"),
    "price_trend_up": ("价格趋势向上", "短期价格变化仍保持正向。"),
    "positive_price_trend": ("价格趋势向上", "短期价格变化仍保持正向。"),
    "speculative_pool_activity": ("池子短线换手强", "成交额相对流动性明显放大，需要结合风险一起看。"),
}

RISK_LABELS = {
    "missing_price": ("缺少价格", "行情源没有返回有效价格。"),
    "low_liquidity": ("流动性偏低", "池子深度偏薄，波动和滑点风险更高。"),
    "liquidity_near_zero": ("流动性接近为零", "池子深度过低，不适合继续判断。"),
    "thin_m5_activity": ("5分钟活跃度不足", "短窗口成交笔数或成交额不足。"),
    "sell_pressure": ("卖压偏强", "短窗口卖出笔数明显高于买入。"),
    "missing_project_metadata": ("项目信息缺失", "缺少网站或社媒信息，复核成本更高。"),
    "fdv_missing": ("完全稀释估值缺失", "缺少估值字段，无法判断估值与流动性关系。"),
    "fdv_liquidity_stretched": ("估值/流动性偏高", "估值相对池子深度偏高，追高风险增加。"),
}

PREDICTION_REASON_LABELS = {
    "prediction_alpha_hot": ("Alpha 热度高", "Binance Alpha 分数较强，说明关注度基础较好。"),
    "prediction_alpha_support": ("Alpha 有支撑", "Alpha 分数提供一定质量支撑。"),
    "prediction_holder_depth": ("持币人数较深", "链上持币人数较多，分布基础更成熟。"),
    "prediction_futures_attention": ("期货注意力", "币安合约标签可能带来额外关注。"),
    "prediction_pool_turnover_hot": ("池子换手强", "1 小时成交额相对流动性明显放大。"),
    "prediction_pool_turnover_support": ("池子换手有支撑", "成交额相对池子流动性有一定支撑。"),
    "prediction_volume_impulse_24h": ("24小时相对放量", "成交额相对过去 24 小时基线抬升。"),
    "prediction_volume_impulse_72h": ("72小时相对放量", "成交额相对过去 72 小时基线抬升。"),
    "prediction_m5_buy_pressure": ("5分钟买压", "短线买盘结构偏强。"),
    "prediction_h1_buy_pressure": ("1小时买压", "近 1 小时买卖结构偏强。"),
    "prediction_price_accelerating": ("价格开始加速", "外部 1 小时涨幅进入启动区间，价格动量开始转强。"),
    "prediction_early_momentum": ("早期动量", "短期涨幅尚未过度拉伸但开始转强。"),
    "prediction_momentum_acceleration": ("动量加速", "趋势和成交进入加速状态。"),
    "prediction_overextended_h1": ("1小时涨幅过热", "短窗口涨幅过大，追高风险上升。"),
    "prediction_h1_overextended": ("1小时涨幅过热", "短窗口涨幅过大，追高风险上升。"),
    "prediction_h4_overextended": ("4小时涨幅过热", "中短线涨幅已经较大，继续追高的回撤风险上升。"),
    "prediction_overextended_24h": ("24小时涨幅过热", "全天涨幅过大，回撤风险上升。"),
    "prediction_h24_overextended": ("24小时涨幅过热", "全天涨幅过大，回撤风险上升。"),
    "prediction_m5_reversal": ("5分钟回落", "短线出现转弱迹象。"),
    "prediction_sell_pressure": ("卖压偏强", "短线卖出压力偏强，后续延续性需要谨慎复核。"),
    "prediction_thin_liquidity": ("流动性偏薄", "池子承接能力不足。"),
    "prediction_stretched_structure": ("估值/流动性偏高", "估值相对池子流动性偏高，追高风险被上调。"),
    "prediction_low_opportunity": ("短线机会偏低", "2小时短线机会分不高，更适合观察。"),
    "prediction_high_opportunity": ("机会分较高", "多项因子共同支撑，适合重点复核。"),
    "prediction_short_momentum_opportunity": ("短线机会较高", "2小时短线动量分较高，适合优先复核量能是否延续。"),
    "prediction_continuation_opportunity": ("延续机会较高", "6小时延续分较高，但仍需结合回撤风险确认。"),
    "prediction_breakout_watch": ("24小时爆发观察", "24小时强爆发观察分较高，但样本仍少，应降低置信度。"),
    "prediction_empirical_calibrated": ("历史命中率校准", "概率已叠加相似历史样本的保守校准。"),
    "prediction_empirical_lowered": ("历史校准下调", "相似样本历史表现较弱，概率被下调。"),
    "prediction_empirical_raised": ("历史校准上调", "相似样本历史表现较强，概率被上调。"),
    "prediction_empirical_sparse": ("历史样本不足", "相似历史样本不足，当前主要采用规则概率。"),
}

FEATURE_LABELS = {
    "age_minutes": ("交易对年龄", "交易对从创建到当前快照的时间。"),
    "boosts_active": ("Dex 推广数量", "DexScreener 当前推广数量。"),
    "price_usd": ("当前单价", "当前美元价格。"),
    "market_cap": ("当前市值", "当前市值，优先使用更可信的数据源。"),
    "market_cap_bucket": ("市值区间", "当前市值所处的策略分档。"),
    "fdv": ("完全稀释估值", "估值规模参考。"),
    "liquidity_usd": ("流动性", "池子美元流动性。"),
    "volume_m5": ("5分钟成交额", "近 5 分钟成交强度。"),
    "volume_h1": ("1小时成交额", "近 1 小时成交强度。"),
    "volume_h24": ("24小时成交额", "近 24 小时成交强度。"),
    "tx_count_m5": ("5分钟交易笔数", "短窗口交易活跃度。"),
    "tx_count_h1": ("1小时交易笔数", "近 1 小时交易活跃度。"),
    "buys_m5": ("5分钟买入笔数", "短窗口买入交易笔数。"),
    "sells_m5": ("5分钟卖出笔数", "短窗口卖出交易笔数。"),
    "buys_h1": ("1小时买入笔数", "近 1 小时买入交易笔数。"),
    "sells_h1": ("1小时卖出笔数", "近 1 小时卖出交易笔数。"),
    "buy_sell_ratio_m5": ("5分钟买卖比", "短窗口买盘相对卖盘强度。"),
    "buy_sell_ratio_h1": ("1小时买卖比", "近 1 小时买盘相对卖盘强度。"),
    "liquidity_to_fdv": ("流动性/完全稀释估值", "流动性相对估值的支撑程度。"),
    "volume_to_liquidity_h1": ("1小时成交额/流动性", "池子换手强度。"),
    "price_change_m5": ("5分钟涨跌幅", "短线价格变化。"),
    "price_change_h1": ("1小时涨跌幅", "近 1 小时价格变化。"),
    "price_change_h24": ("24小时涨跌幅", "近 24 小时价格变化。"),
    "h1_return_live": ("外部1小时涨幅", "外部小时线计算的 1 小时表现。"),
    "h4_return_live": ("外部4小时涨幅", "外部小时线计算的 4 小时表现。"),
    "h24_return_live": ("外部24小时涨幅", "外部小时线计算的 24 小时表现。"),
    "volume_impulse_vs_prev24h": ("24小时相对放量", "当前 1 小时成交额相对过去 24 小时基线。"),
    "volume_impulse_vs_prev72h": ("72小时相对放量", "当前 1 小时成交额相对过去 72 小时基线。"),
    "website_count": ("网站数量", "可复核的网站入口数量。"),
    "social_count": ("社媒数量", "可复核的社媒入口数量。"),
    "risk_flags": ("风险标记", "当前信号命中的风险项。"),
    "candidate_indicator_version": ("指标版本", "当前候选指标计算版本。"),
}


def open_repository(database_path: str) -> MonitorRepository:
    repo = MonitorRepository(database_path)
    repo.initialize()
    return repo


@st.cache_data(show_spinner=False)
def load_overview_frames(database_path: str, revision: tuple[tuple[str, int, int], ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    del revision
    repo = open_repository(database_path)
    try:
        raw_df = pd.DataFrame(repo.list_pair_overview(limit=OVERVIEW_FETCH_LIMIT))
    finally:
        repo.close()
    return raw_df, build_overview_frame(raw_df)


@st.cache_data(show_spinner=False)
def load_dashboard_status(
    database_path: str,
    revision: tuple[tuple[str, int, int], ...],
    window_minutes: int = 10,
) -> dict[str, Any]:
    del revision
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    repo = open_repository(database_path)
    try:
        return repo.get_dashboard_status(since)
    finally:
        repo.close()


@st.cache_data(show_spinner=False)
def load_recent_signals_frame(
    database_path: str,
    revision: tuple[tuple[str, int, int], ...],
    pair_address: str,
    limit: int = 20,
) -> pd.DataFrame:
    del revision
    if not pair_address:
        return pd.DataFrame()
    repo = open_repository(database_path)
    try:
        return pd.DataFrame(repo.list_recent_signals(pair_address, limit=limit))
    finally:
        repo.close()


@st.cache_data(show_spinner=False)
def load_recent_snapshots_frame(
    database_path: str,
    revision: tuple[tuple[str, int, int], ...],
    pair_address: str,
    limit: int = 200,
) -> pd.DataFrame:
    del revision
    if not pair_address:
        return pd.DataFrame()
    repo = open_repository(database_path)
    try:
        return pd.DataFrame(repo.list_recent_snapshots(pair_address, limit=limit))
    finally:
        repo.close()


def inject_styles() -> None:
    st.markdown(
        """
<style>
:root {
  --bg: #f5f7f4;
  --panel: #fffefa;
  --panel-strong: #ffffff;
  --line: rgba(23, 32, 51, 0.10);
  --line-strong: rgba(15, 118, 110, 0.22);
  --text: #111827;
  --muted: #6b7280;
  --subtle: #8b98a3;
  --accent: #0f766e;
  --accent-strong: #0b5f59;
  --accent-soft: #e6f4f1;
  --accent-wash: rgba(15, 118, 110, 0.08);
  --risk: #b45309;
  --danger: #ef4444;
  --shadow-soft: 0 14px 34px rgba(17, 24, 39, 0.07);
  --shadow-card: 0 1px 2px rgba(17, 24, 39, 0.05), 0 10px 24px rgba(17, 24, 39, 0.04);
}
.stApp {
  background:
    linear-gradient(120deg, rgba(230,244,241,0.72), transparent 34rem),
    linear-gradient(145deg, #f6f8f5 0%, #fbf7ef 54%, #f7f8fb 100%);
  color: var(--text);
}
[data-testid="stHeader"],
[data-testid="stToolbar"] {
  display: none;
}
.block-container {
  max-width: 118rem;
  padding: 0.08rem 3rem 3rem;
}
section[data-testid="stSidebar"] { background: #f3f5f1; }
.top-title-row {
  display: flex;
  align-items: center;
  min-height: 1.9rem;
  transform: translateY(-0.50rem);
}
.top-refresh {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  min-height: 1.9rem;
  transform: translateY(-0.52rem);
}
.toolbar-title {
  color: var(--text);
  font-size: 1.52rem;
  font-weight: 850;
  letter-spacing: 0;
  line-height: 1.2;
  margin: 0;
  white-space: nowrap;
}
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.7rem;
}
.top-actions {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}
.top-status-text {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.74rem;
  color: #4b5563;
  font-size: 0.82rem;
  font-weight: 760;
  font-variant-numeric: tabular-nums;
  line-height: 1.35;
  white-space: nowrap;
  text-align: right;
}
.top-status-text strong {
  color: var(--accent-strong);
  font-weight: 850;
}
.top-status-item {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}
.top-status-countdown {
  justify-content: flex-start;
  min-width: 5.65rem;
  text-align: left;
  color: var(--accent-strong);
  font-weight: 850;
}
div[data-testid="stToggle"] {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  min-height: 1.9rem;
  transform: translate(0.5em, -0.50rem);
}
div[data-testid="stToggle"] label {
  align-items: center;
  gap: 0.52rem;
  min-height: 1.9rem;
}
div[data-testid="stToggle"] p {
  color: #354153;
  font-weight: 760;
  white-space: nowrap;
  margin: 0;
}
div[data-testid="stSegmentedControl"] {
  margin: 0.3rem 0 0.85rem;
}
div[data-testid="stSegmentedControl"] [role="radiogroup"] {
  width: 100%;
  padding: 0.12rem;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(255,255,255,0.82);
  box-shadow: 0 1px 2px rgba(17,24,39,0.04);
}
div[data-testid="stSegmentedControl"] label {
  min-height: 2.34rem;
  border-radius: 8px;
  font-weight: 790;
}
div[data-testid="stExpander"] {
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255,255,255,0.70);
  box-shadow: 0 1px 2px rgba(17,24,39,0.04);
  margin-top: 0.25rem;
}
div[data-testid="stExpander"] summary {
  min-height: 2.75rem;
  font-weight: 830;
}
.advanced-filter-label {
  color: var(--subtle);
  font-size: 0.78rem;
  font-weight: 830;
  margin: 0.1rem 0 0.34rem;
}
div[data-testid="stTextInput"] input,
div[data-testid="stTextInput"] div[data-baseweb="input"],
div[data-testid="stTextInput"] div[data-baseweb="input"] > div,
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
div[data-testid="stNumberInput"] input {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255,255,255,0.92);
  color: var(--text);
  min-height: 3.12rem;
  box-shadow: inset 0 1px 0 rgba(17,24,39,0.02);
  box-sizing: border-box;
}
div[data-testid="stTextInput"],
div[data-testid="stTextInput"] > div,
div[data-testid="stSelectbox"],
div[data-testid="stSelectbox"] > div {
  width: 100%;
}
div[data-testid="stTextInput"] {
  margin-top: 1rem;
}
div[data-testid="stTextInput"] input {
  height: 3.12rem;
  line-height: 1.35;
  padding: 0.55rem 0.9rem;
}
div[data-testid="stTextInput"] input:focus,
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within,
div[data-testid="stNumberInput"] input:focus {
  border-color: var(--line-strong);
  box-shadow: 0 0 0 3px rgba(15,118,110,0.10);
}
.filter-summary-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.38rem 0.66rem;
  min-height: 2rem;
  margin: -0.12rem 0 1.08rem;
  padding: 0.34rem 0.08rem 0.44rem;
  border-bottom: 1px solid rgba(17,24,39,0.08);
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.35;
}
.filter-summary-title {
  color: var(--text);
  font-weight: 850;
  white-space: nowrap;
}
.filter-summary-item {
  display: inline-flex;
  align-items: baseline;
  gap: 0.24rem;
  color: var(--muted);
  white-space: nowrap;
}
.filter-summary-item:before {
  content: "·";
  color: var(--subtle);
  font-weight: 700;
}
.filter-summary-item span {
  color: var(--subtle);
  font-weight: 700;
}
.filter-summary-item strong {
  color: #354153;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}
.section-heading {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-end;
  margin: 0.15rem 0 0.78rem;
}
.section-kicker {
  color: var(--accent-strong);
  font-size: 0.76rem;
  font-weight: 850;
  text-transform: uppercase;
  letter-spacing: 0;
}
.section-title {
  color: var(--text);
  font-size: 1.08rem;
  font-weight: 850;
  line-height: 1.3;
}
.section-copy {
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.45;
  max-width: 34rem;
}
.detail-hero {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(15,118,110,0.14);
  background:
    linear-gradient(100deg, rgba(230,244,241,0.92), rgba(255,255,255,0.82) 38%, rgba(250,246,236,0.84)),
    var(--panel-strong);
  border-radius: 20px;
  padding: 1.38rem 1.48rem;
  margin-bottom: 1rem;
  box-shadow: var(--shadow-soft);
}
.detail-hero:before {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: linear-gradient(90deg, var(--accent), rgba(239,68,68,0.78), rgba(180,83,9,0.55));
}
.detail-hero > * {
  position: relative;
}
.detail-hero-top {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}
.detail-hero h2 {
  margin: 0.12rem 0 0.45rem;
  font-size: 2.05rem;
  line-height: 1.2;
  letter-spacing: 0;
}
.conclusion-panel {
  border: 1px solid rgba(15,118,110,0.13);
  border-left: 3px solid rgba(15,118,110,0.42);
  border-radius: 14px;
  background: rgba(255,255,255,0.84);
  padding: 0.72rem 0.88rem;
  margin: 0.3rem 0 0.86rem;
  box-shadow: 0 1px 2px rgba(17,24,39,0.03);
}
.conclusion-title {
  color: var(--text);
  font-size: 1.02rem;
  font-weight: 850;
  line-height: 1.25;
}
.conclusion-copy {
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.38;
  margin-top: 0.18rem;
}
.score-box {
  min-width: 9.2rem;
  padding: 0.72rem 0.84rem;
  border: 1px solid rgba(15,118,110,0.12);
  border-radius: 16px;
  background: rgba(255,255,255,0.56);
  text-align: right;
}
.meta-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.48rem;
  margin-top: 0.76rem;
}
.meta-chip {
  display: inline-flex;
  gap: 0.32rem;
  align-items: center;
  min-height: 2rem;
  padding: 0.32rem 0.62rem;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  background: rgba(255,255,255,0.78);
  font-size: 0.82rem;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.meta-chip strong { color: var(--text); }
.data-quality-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.46rem;
  margin-top: 0.62rem;
  padding-top: 0.62rem;
  border-top: 1px solid rgba(17,24,39,0.07);
}
.data-quality-item {
  display: inline-flex;
  align-items: center;
  gap: 0.32rem;
  min-height: 1.74rem;
  padding: 0.22rem 0.56rem;
  border: 1px solid rgba(17,24,39,0.08);
  border-radius: 999px;
  background: rgba(255,255,255,0.64);
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 720;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.data-quality-item strong {
  color: var(--text);
  font-weight: 820;
}
.data-quality-item.tone-fresh {
  border-color: rgba(15,118,110,0.16);
  background: rgba(230,244,241,0.70);
  color: var(--accent-strong);
}
.data-quality-item.tone-warn {
  border-color: rgba(180,83,9,0.18);
  background: rgba(255,251,235,0.78);
  color: #92400e;
}
.data-quality-item.tone-missing {
  border-color: rgba(107,114,128,0.18);
  background: rgba(249,250,251,0.82);
}
.link-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.48rem;
  margin-top: 0.72rem;
}
.link-pill {
  color: var(--accent-strong) !important;
  text-decoration: none !important;
  border: 1px solid rgba(15,118,110,0.18);
  background: var(--accent-soft);
  border-radius: 999px;
  padding: 0.34rem 0.66rem;
  font-weight: 750;
  font-size: 0.82rem;
}
.link-pill:hover {
  border-color: rgba(15,118,110,0.34);
  background: #d9efeb;
}
.hero-address-line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.52rem;
  margin-top: 0.72rem;
  padding-top: 0.62rem;
  border-top: 1px solid rgba(17,24,39,0.07);
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.35;
}
.hero-address-label {
  color: var(--subtle);
  font-weight: 820;
}
.hero-address-value {
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 760;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.copy-address-button {
  appearance: none;
  border: 1px solid rgba(15,118,110,0.22);
  border-radius: 999px;
  background: rgba(230,244,241,0.88);
  color: var(--accent-strong);
  cursor: pointer;
  font-size: 0.76rem;
  font-weight: 820;
  line-height: 1;
  padding: 0.34rem 0.62rem;
  min-width: 3.7rem;
  transition: background 150ms ease, border-color 150ms ease, transform 150ms ease;
}
.copy-address-button:focus-visible {
  outline: 3px solid rgba(15,118,110,0.16);
  outline-offset: 2px;
}
.copy-address-button:hover {
  background: rgba(217,239,235,0.96);
  border-color: rgba(15,118,110,0.34);
  transform: translateY(-1px);
}
.copy-address-button.is-copied {
  background: rgba(16,185,129,0.14);
  border-color: rgba(16,185,129,0.34);
  color: #047857;
}
.detail-line-list {
  display: grid;
  gap: 0.54rem;
  margin-top: 0.76rem;
}
.detail-line-list.compact { margin-top: 0.55rem; }
.detail-line-item {
  display: grid;
  grid-template-columns: minmax(8.2rem, 0.8fr) minmax(5.4rem, 0.44fr) minmax(0, 2.2fr);
  gap: 0.66rem;
  align-items: start;
  padding: 0.68rem 0.88rem;
  border: 1px solid var(--line);
  border-left: 3px solid rgba(15, 118, 110, 0.42);
  border-radius: 12px;
  background: rgba(255,255,255,0.84);
  box-shadow: 0 1px 2px rgba(17,24,39,0.03);
}
.detail-line-item.compact {
  grid-template-columns: minmax(7.2rem, 0.7fr) minmax(4.3rem, 0.34fr) minmax(0, 2.3fr);
  gap: 0.52rem;
  padding: 0.48rem 0.72rem;
}
.detail-line-item.tone-risk {
  border-left-color: #d97706;
  background: rgba(255,251,235,0.84);
}
.detail-line-title {
  color: var(--text);
  font-weight: 850;
  line-height: 1.3;
}
.detail-line-meta {
  display: block;
  margin-top: 0.14rem;
  color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
.detail-line-value {
  color: var(--accent-strong);
  font-weight: 850;
  line-height: 1.3;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.detail-line-body {
  color: var(--muted);
  font-size: 0.91rem;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.detail-line-item.compact .detail-line-title,
.detail-line-item.compact .detail-line-value { line-height: 1.22; }
.detail-line-item.compact .detail-line-body { font-size: 0.88rem; line-height: 1.34; }
.feature-line-list {
  display: grid;
  gap: 0.5rem;
  margin-top: 0.72rem;
}
.feature-line-item {
  display: grid;
  grid-template-columns: minmax(9.6rem, 0.95fr) minmax(0, 2.05fr) minmax(10.5rem, 1.05fr);
  column-gap: 1.45rem;
  row-gap: 0.28rem;
  align-items: start;
  padding: 0.5rem 0.72rem;
  border: 1px solid var(--line);
  border-left: 3px solid rgba(15, 118, 110, 0.36);
  border-radius: 12px;
  background: rgba(255,255,255,0.84);
}
.feature-line-title {
  color: var(--text);
  font-weight: 780;
  line-height: 1.28;
  min-width: 0;
  white-space: nowrap;
}
.feature-line-body {
  color: var(--muted);
  font-size: 0.88rem;
  line-height: 1.38;
  min-width: 0;
  overflow-wrap: anywhere;
}
.feature-line-value {
  color: var(--accent-strong);
  font-weight: 800;
  line-height: 1.28;
  text-align: right;
  font-variant-numeric: tabular-nums;
  min-width: 0;
  overflow-wrap: anywhere;
}
.history-list {
  display: grid;
  gap: 0.72rem;
  margin-top: 0.76rem;
}
.history-card {
  border: 1px solid var(--line);
  border-left: 3px solid rgba(15, 118, 110, 0.36);
  border-radius: 14px;
  background: rgba(255,255,255,0.86);
  padding: 0.76rem 0.86rem;
  box-shadow: 0 1px 2px rgba(17,24,39,0.03);
}
.history-card.tone-risk {
  border-left-color: #d97706;
  background: rgba(255,251,235,0.82);
}
.history-top {
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
  align-items: flex-start;
  padding-bottom: 0.55rem;
  border-bottom: 1px solid rgba(17,24,39,0.07);
}
.history-time {
  color: var(--text);
  font-weight: 820;
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
.history-meta {
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.35;
  margin-top: 0.16rem;
}
.history-score-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.38rem;
  min-width: 8rem;
}
.history-score-chip {
  display: inline-flex;
  align-items: center;
  border: 1px solid rgba(15,118,110,0.16);
  border-radius: 999px;
  background: rgba(230,244,241,0.70);
  color: var(--accent-strong);
  font-size: 0.78rem;
  font-weight: 760;
  padding: 0.24rem 0.52rem;
  font-variant-numeric: tabular-nums;
}
.history-body {
  display: grid;
  gap: 0.38rem;
  margin-top: 0.62rem;
}
.history-row {
  display: grid;
  grid-template-columns: 3.6rem minmax(0, 1fr);
  gap: 0.58rem;
  align-items: start;
}
.history-row-label {
  color: var(--subtle);
  font-size: 0.78rem;
  font-weight: 820;
  line-height: 1.35;
}
.history-row-text {
  color: var(--muted);
  font-size: 0.88rem;
  line-height: 1.42;
  overflow-wrap: anywhere;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10.4rem, 1fr));
  gap: 0.82rem;
  margin: 0.8rem 0 1.08rem;
}
.metric-card {
  min-height: 6.05rem;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255,255,255,0.90);
  padding: 0.86rem 0.92rem;
  box-shadow: var(--shadow-card);
}
.metric-card.tone-risk {
  border-color: rgba(180,83,9,0.20);
  background: rgba(255,251,235,0.84);
}
.metric-label {
  color: var(--subtle);
  font-size: 0.78rem;
  font-weight: 820;
  line-height: 1.25;
}
.metric-value {
  color: var(--text);
  font-size: 1.35rem;
  font-weight: 860;
  line-height: 1.2;
  margin-top: 0.36rem;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.metric-note {
  color: var(--muted);
  font-size: 0.78rem;
  line-height: 1.35;
  margin-top: 0.28rem;
}
.prediction-confidence {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.86rem;
  border: 1px solid rgba(15,118,110,0.13);
  border-left: 3px solid rgba(15,118,110,0.42);
  border-radius: 14px;
  background: rgba(255,255,255,0.84);
  padding: 0.72rem 0.86rem;
  margin: 0.78rem 0 0.72rem;
  box-shadow: 0 1px 2px rgba(17,24,39,0.03);
}
.prediction-confidence.tone-warn {
  border-left-color: #d97706;
  background: rgba(255,251,235,0.84);
}
.prediction-confidence.tone-neutral {
  border-left-color: rgba(107,114,128,0.36);
}
.prediction-confidence-title {
  color: var(--text);
  font-size: 0.96rem;
  font-weight: 850;
  line-height: 1.26;
}
.prediction-confidence-body {
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.42;
  margin-top: 0.2rem;
}
.prediction-confidence-chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.36rem;
  min-width: 8.2rem;
}
.prediction-confidence-chip {
  display: inline-flex;
  align-items: center;
  min-height: 1.72rem;
  border: 1px solid rgba(15,118,110,0.16);
  border-radius: 999px;
  background: rgba(230,244,241,0.70);
  color: var(--accent-strong);
  font-size: 0.76rem;
  font-weight: 780;
  line-height: 1;
  padding: 0.24rem 0.52rem;
  white-space: nowrap;
}
.prediction-confidence-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 0.34rem;
  margin-top: 0.48rem;
}
.prediction-confidence-evidence span {
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 720;
  line-height: 1.25;
}
.prediction-confidence-evidence span:before {
  content: "·";
  color: var(--subtle);
  margin-right: 0.28rem;
}
.trend-stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8.7rem, 1fr));
  gap: 0.68rem;
  margin: 0.74rem 0 1.02rem;
  padding: 0.2rem 0 0.42rem;
  border-bottom: 1px solid rgba(17,24,39,0.07);
}
.trend-stat {
  min-width: 0;
  padding: 0.18rem 0.18rem 0.26rem;
}
.trend-stat-label {
  color: var(--subtle);
  font-size: 0.76rem;
  font-weight: 820;
  line-height: 1.25;
}
.trend-stat-value {
  color: var(--text);
  font-size: 1.08rem;
  font-weight: 860;
  line-height: 1.24;
  margin-top: 0.24rem;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.trend-stat-note {
  color: var(--muted);
  font-size: 0.76rem;
  line-height: 1.28;
  margin-top: 0.16rem;
}
.trend-chart-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 0.8rem;
  margin: 0.88rem 0 0.36rem;
}
.trend-chart-title {
  color: var(--text);
  font-size: 0.94rem;
  font-weight: 850;
  line-height: 1.25;
}
.trend-chart-copy {
  color: var(--muted);
  font-size: 0.8rem;
  font-weight: 650;
  line-height: 1.35;
  text-align: right;
}
.list-summary {
  color: var(--muted);
  font-size: 0.84rem;
  margin: 0.32rem 0 0.72rem;
  font-weight: 680;
}
.list-group-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.72rem;
  margin: 0.78rem 0 0.36rem;
  padding: 0.08rem 0.06rem;
  color: var(--subtle);
  font-size: 0.72rem;
  font-weight: 840;
  letter-spacing: 0;
}
.list-group-heading::before {
  content: "";
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 999px;
  background: rgba(15,118,110,0.58);
  box-shadow: 0 0 0 4px rgba(15,118,110,0.08);
  flex: 0 0 auto;
}
.list-group-heading-label {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text);
}
.list-group-heading-count {
  flex: 0 0 auto;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
div[data-testid="element-container"]:has(div[data-testid="stTextInput"]),
div[data-testid="element-container"]:has(div[data-testid="stRadio"]),
div[data-testid="element-container"]:has(div[data-testid="stButton"]) {
  width: 100% !important;
  max-width: 100% !important;
}
div[data-testid="element-container"]:has(div[data-testid="stButton"]) {
  margin-bottom: 0.42rem;
}
div[data-testid="stButton"],
div[data-testid="stButton"] > button,
div[data-testid="stButton"] button {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box;
}
div[data-testid="stButton"] button {
  justify-content: flex-start !important;
  align-items: center;
  min-height: 2.72rem;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 0.34rem 0.72rem;
  background: rgba(255,255,255,0.86);
  color: var(--text) !important;
  box-shadow: var(--shadow-card);
  text-align: left !important;
  transition: border-color 150ms ease, background 150ms ease, transform 150ms ease;
}
div[data-testid="stButton"] button > div,
div[data-testid="stButton"] button [data-testid="stMarkdownContainer"] {
  display: block !important;
  flex: 1 1 auto !important;
  width: 100% !important;
  max-width: 100% !important;
  min-width: 0;
  text-align: left !important;
  margin: 0 !important;
}
div[data-testid="stButton"] button:hover {
  border-color: rgba(15,118,110,0.24);
  background: rgba(255,255,255,0.96);
  transform: translateY(-1px);
}
div[data-testid="stButton"] button[data-testid="stBaseButton-primary"] {
  border-color: rgba(15,118,110,0.38);
  background: linear-gradient(90deg, rgba(230,244,241,0.92), rgba(255,255,255,0.9));
  color: var(--accent-strong) !important;
  box-shadow: 0 0 0 1px rgba(15,118,110,0.10) inset;
}
div[data-testid="stButton"] button[kind="primary"] {
  border-color: rgba(15,118,110,0.38);
  background: linear-gradient(90deg, rgba(230,244,241,0.92), rgba(255,255,255,0.9));
  color: var(--accent-strong) !important;
}
div[data-testid="stButton"] button p,
div[data-testid="stButton"] button span {
  width: 100%;
  max-width: 100%;
  color: var(--text) !important;
  font-size: 0.88rem;
  font-weight: 600;
  text-align: left !important;
  line-height: 1.22;
  margin: 0;
  padding-left: 0.18rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
div[data-testid="stButton"] button[data-testid="stBaseButton-primary"] p,
div[data-testid="stButton"] button[data-testid="stBaseButton-primary"] span,
div[data-testid="stButton"] button[kind="primary"] p,
div[data-testid="stButton"] button[kind="primary"] span {
  color: var(--accent-strong) !important;
  font-weight: 660;
}
div[data-testid="stButton"] button:hover p,
div[data-testid="stButton"] button:hover span {
  white-space: normal;
}
div[data-testid="stRadio"],
div[data-testid="stRadio"] > div,
div[data-testid="stRadio"] [role="radiogroup"],
div[data-testid="stRadio"] [role="radiogroup"] > div,
div[data-testid="stRadio"] [role="radiogroup"] label,
div[data-testid="stRadio"] [data-testid="stMarkdownContainer"],
div[data-testid="stRadio"] [data-testid="stMarkdownContainer"] p,
.stRadio,
.stRadio [role="radiogroup"],
.stRadio [role="radiogroup"] label,
.stRadio [data-testid="stMarkdownContainer"],
.stRadio [data-testid="stMarkdownContainer"] p {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box;
}
div[data-testid="stRadio"] [role="radiogroup"],
.stRadio [role="radiogroup"] {
  display: grid !important;
  grid-template-columns: minmax(0, 1fr);
  gap: 0.64rem;
}
div[data-testid="stRadio"] [role="radiogroup"] label,
.stRadio [role="radiogroup"] label {
  display: flex !important;
  align-items: center;
  min-height: 3.55rem;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 0.56rem 0.82rem;
  background: rgba(255,255,255,0.86);
  box-shadow: var(--shadow-card);
  transition: border-color 150ms ease, background 150ms ease, transform 150ms ease;
}
div[data-testid="stRadio"] [role="radiogroup"] label > div:last-child,
.stRadio [role="radiogroup"] label > div:last-child {
  flex: 1 1 auto;
  min-width: 0;
}
div[data-testid="stRadio"] [role="radiogroup"] label:hover,
.stRadio [role="radiogroup"] label:hover {
  border-color: rgba(15,118,110,0.24);
  background: rgba(255,255,255,0.96);
  transform: translateY(-1px);
}
div[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked),
.stRadio [role="radiogroup"] label:has(input:checked) {
  border-color: rgba(15,118,110,0.38);
  background: linear-gradient(90deg, rgba(230,244,241,0.92), rgba(255,255,255,0.9));
  box-shadow: 0 0 0 1px rgba(15,118,110,0.10) inset;
}
div[data-testid="stRadio"] [role="radiogroup"] label p,
.stRadio [role="radiogroup"] label p {
  color: var(--text);
  font-weight: 760;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
div[data-testid="stRadio"] [role="radiogroup"] label:hover p,
.stRadio [role="radiogroup"] label:hover p {
  white-space: normal;
}
div[data-testid="stAlert"] {
  border-radius: 14px;
  border-color: var(--line);
}
@media (max-width: 900px) {
  .block-container {
    padding: 1rem 1rem 2rem;
  }
  .top-bar,
  .detail-hero-top { flex-direction: column; }
  .top-title-row,
  .top-refresh {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
  .score-box { width: 100%; text-align: left; }
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .trend-chart-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .trend-chart-copy {
    text-align: left;
  }
  .detail-line-item,
  .detail-line-item.compact {
    grid-template-columns: 1fr;
    gap: 0.24rem;
  }
  .feature-line-item,
  .history-row {
    grid-template-columns: 1fr;
    gap: 0.24rem;
  }
  .feature-line-value {
    text-align: left;
  }
  .feature-line-title {
    white-space: normal;
  }
  .history-top {
    flex-direction: column;
  }
  .history-score-row {
    justify-content: flex-start;
  }
  .detail-line-value { white-space: normal; }
  .prediction-confidence {
    flex-direction: column;
  }
  .prediction-confidence-chips {
    justify-content: flex-start;
    min-width: 0;
  }
}
@media (max-width: 560px) {
  .metric-grid {
    grid-template-columns: 1fr;
  }
  .detail-hero h2 {
    font-size: 1.62rem;
  }
}
</style>
        """,
        unsafe_allow_html=True,
    )


def translate_state(value: Any) -> str:
    if value in (None, ""):
        return "未定"
    return STATE_LABELS.get(str(value), "未配置状态")


def list_to_text(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item not in (None, "")]
    if not isinstance(raw, str):
        try:
            if pd.isna(raw):
                return []
        except (TypeError, ValueError):
            return []
        return []
    parsed = json_loads(raw, [])
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def explain_reason(code: str) -> tuple[str, str]:
    return REASON_LABELS.get(code, ("未配置信号规则", "该信号规则暂未配置中文说明。"))


def explain_risk(code: str) -> tuple[str, str]:
    return RISK_LABELS.get(code, ("未配置风险标记", "该风险标记暂未配置中文说明。"))


def explain_prediction_reason(code: str) -> tuple[str, str]:
    return PREDICTION_REASON_LABELS.get(code, ("未配置预测因子", "该预测因子暂未配置中文说明。"))


def format_reason_titles(raw: Any) -> str:
    return "，".join(explain_reason(code)[0] for code in list_to_text(raw)) or "暂无"


def format_risk_titles(raw: Any) -> str:
    return "，".join(explain_risk(code)[0] for code in list_to_text(raw)) or "暂无"


def format_prediction_reason_titles(raw: Any) -> str:
    return "，".join(explain_prediction_reason(code)[0] for code in list_to_text(raw)) or "暂无"


def format_stage(value: Any) -> str:
    if value in (None, ""):
        return "--"
    try:
        if pd.isna(value):
            return "--"
    except (TypeError, ValueError):
        pass
    return PREDICTION_STAGE_LABELS.get(str(value), "未配置阶段")


def format_money(value: Any, decimals: int = 0) -> str:
    if value in (None, ""):
        return "--"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    if pd.isna(numeric):
        return "--"
    if decimals > 0:
        return f"${numeric:,.{decimals}f}"
    if abs(numeric) >= 1_000_000:
        return f"${numeric / 1_000_000:.2f}M"
    if abs(numeric) >= 1_000:
        return f"${numeric / 1_000:.1f}K"
    return f"${numeric:,.0f}"


def decimal_from_value(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        numeric = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not numeric.is_finite():
        return None
    return numeric


def format_price(value: Any) -> str:
    numeric_decimal = decimal_from_value(value)
    if numeric_decimal is None:
        return "--"
    if numeric_decimal == 0:
        return "$0"
    absolute = abs(numeric_decimal)
    if absolute >= Decimal("1"):
        return f"${float(numeric_decimal):,.4f}"
    if absolute >= Decimal("0.01"):
        return f"${float(numeric_decimal):,.6f}"
    return "$" + format(numeric_decimal.normalize(), "f")


def price_axis_format(values: pd.Series) -> str:
    numeric_values = pd.to_numeric(values, errors="coerce").dropna()
    nonzero_values = numeric_values[numeric_values != 0].abs()
    if nonzero_values.empty:
        return "$,.8f"
    minimum = Decimal(str(nonzero_values.min()))
    if minimum >= Decimal("1"):
        return "$,.4f"
    if minimum >= Decimal("0.01"):
        return "$,.6f"
    decimals = max(8, min(18, -minimum.adjusted() + 3))
    return f"$,.{decimals}f"


def format_percent(value: Any) -> str:
    if value in (None, ""):
        return "--"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    if pd.isna(numeric):
        return "--"
    return f"{numeric * 100:.1f}%"


def format_holders(value: Any) -> str:
    if value in (None, "", "待接入"):
        return "待接入"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if pd.isna(numeric):
        return "待接入"
    return f"{int(numeric):,}"


def format_timestamp(value: Any) -> str:
    if value in (None, ""):
        return "--"
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return str(value)
    return timestamp.tz_convert(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def format_age_from_timestamp(value: Any) -> str:
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return "--"
    minutes = max(0, int((pd.Timestamp.utcnow() - timestamp).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}分钟"
    if minutes < 1440:
        return f"{minutes / 60:.1f}小时"
    return f"{minutes / 1440:.1f}天"


def format_relative_age(value: Any, *, now_ts: float | None = None) -> str:
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return "--"
    current_ts = pd.Timestamp.utcnow().timestamp() if now_ts is None else float(now_ts)
    seconds = max(0, int(current_ts - timestamp.timestamp()))
    if seconds < 60:
        return f"{seconds}s前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分钟前"
    if minutes < 1440:
        return f"{minutes / 60:.1f}小时前"
    return f"{minutes / 1440:.1f}天前"


def build_detail_data_quality_items(row: Any, *, now_ts: float | None = None) -> tuple[dict[str, str], ...]:
    snapshot_at = first_non_missing(row.get("snapshot_observed_at"), row.get("last_snapshot_at"))
    signal_at = row.get("last_signal_at")
    return (
        _freshness_item("行情快照", snapshot_at, now_ts=now_ts, fresh_seconds=120, warn_seconds=900, missing_text="缺少快照"),
        _freshness_item("最新信号", signal_at, now_ts=now_ts, fresh_seconds=300, warn_seconds=1800, missing_text="暂未生成"),
        _price_source_item(row),
    )


def _freshness_item(
    label: str,
    value: Any,
    *,
    now_ts: float | None,
    fresh_seconds: int,
    warn_seconds: int,
    missing_text: str,
) -> dict[str, str]:
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return {"label": label, "value": missing_text, "tone": "missing"}
    current_ts = pd.Timestamp.utcnow().timestamp() if now_ts is None else float(now_ts)
    age_seconds = max(0, int(current_ts - timestamp.timestamp()))
    relative = format_relative_age(value, now_ts=now_ts)
    if age_seconds <= fresh_seconds:
        return {"label": label, "value": f"新鲜 {relative}", "tone": "fresh"}
    if age_seconds <= warn_seconds:
        return {"label": label, "value": f"可用 {relative}", "tone": "neutral"}
    return {"label": label, "value": f"偏旧 {relative}", "tone": "warn"}


def _price_source_item(row: Any) -> dict[str, str]:
    if has_visible_value(row.get("price_usd")):
        return {"label": "价格来源", "value": "实时快照", "tone": "fresh"}
    if has_visible_value(row.get("alpha_price")):
        return {"label": "价格来源", "value": "Alpha 兜底", "tone": "warn"}
    return {"label": "价格来源", "value": "缺少价格", "tone": "missing"}


def has_visible_value(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return True
    return not pd.isna(numeric) and numeric != 0


def clean_text(value: Any, default: str = "--") -> str:
    if value in (None, ""):
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text else default


def truthy_metadata(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        stripped = value.strip().lower()
        return stripped in {"1", "true", "yes", "y"} if stripped else False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def token_metadata_from_row(row: pd.Series) -> dict[str, Any]:
    metadata = row.get("token_meta")
    if isinstance(metadata, dict):
        return metadata
    parsed = json_loads(row.get("token_metadata_json"), {})
    return parsed if isinstance(parsed, dict) else {}


def format_binance_labels(metadata: dict[str, Any]) -> str:
    labels = []
    if truthy_metadata(metadata.get("binance_cex_listed")):
        labels.append("现货")
    if truthy_metadata(metadata.get("binance_futures_listed")):
        labels.append("合约")
    if truthy_metadata(metadata.get("is_binance_alpha")):
        labels.insert(0, "Alpha")
    return " / ".join(dict.fromkeys(labels)) if labels else "未发现币安标签"


def format_top10_holder_share(metadata: dict[str, Any]) -> str:
    value = metadata.get("top10_holder_share")
    if value is None:
        return "待补齐"
    if isinstance(value, str) and not value.strip():
        return "待补齐"
    try:
        if pd.isna(value):
            return "待补齐"
    except (TypeError, ValueError):
        pass
    return format_percent(value)


def detail_segment(label: str, value: Any, *, skip_empty: bool = True) -> str | None:
    text = clean_text(value)
    if skip_empty and text in {"--", "暂无"}:
        return None
    return f"{label}{text}"


def join_segments(segments: list[str | None], separator: str = " ｜ ") -> str:
    return separator.join(segment for segment in segments if segment)


def render_detail_lines(items: list[dict[str, str]], *, compact: bool = False) -> None:
    if not items:
        return
    compact_class = " compact" if compact else ""
    line_html: list[str] = []
    for item in items:
        tone = html.escape(item.get("tone") or "accent")
        title = html.escape(clean_text(item.get("title")))
        meta = clean_text(item.get("meta"), "")
        value = clean_text(item.get("value"), "")
        body = clean_text(item.get("body"), "")
        meta_html = f"<span class='detail-line-meta'>{html.escape(meta)}</span>" if meta else ""
        value_html = f"<div class='detail-line-value'>{html.escape(value)}</div>" if value else "<div></div>"
        body_html = f"<div class='detail-line-body'>{html.escape(body)}</div>" if body else "<div></div>"
        line_html.append(
            f"<div class='detail-line-item tone-{tone}{compact_class}'>"
            f"<div><div class='detail-line-title'>{title}</div>{meta_html}</div>"
            f"{value_html}{body_html}</div>"
        )
    st.markdown(f"<div class='detail-line-list{compact_class}'>" + "".join(line_html) + "</div>", unsafe_allow_html=True)


def render_feature_lines(items: list[dict[str, str]]) -> None:
    if not items:
        return
    rows = []
    for item in items:
        title = html.escape(clean_text(item.get("title")))
        body = html.escape(clean_text(item.get("body"), "暂无说明"))
        value = html.escape(clean_text(item.get("value")))
        rows.append(
            "<div class='feature-line-item'>"
            f"<div class='feature-line-title'>{title}</div>"
            f"<div class='feature-line-body'>{body}</div>"
            f"<div class='feature-line-value'>{value}</div>"
            "</div>"
        )
    st.markdown("<div class='feature-line-list'>" + "".join(rows) + "</div>", unsafe_allow_html=True)


def render_section(kicker: str, title: str, copy: str) -> None:
    st.markdown(
        f"<div class='section-heading'><div><div class='section-kicker'>{html.escape(kicker)}</div>"
        f"<div class='section-title'>{html.escape(title)}</div></div>"
        f"<div class='section-copy'>{html.escape(copy)}</div></div>",
        unsafe_allow_html=True,
    )


def render_filter_summary(
    *,
    mode_label: str,
    only_with_market_data: bool,
    min_signal: int,
    min_holders: int,
    min_liquidity: int,
) -> None:
    items = [
        ("模式", mode_label),
        ("行情", "只看行情数据" if only_with_market_data else "全部候选"),
        ("信号", "全部" if min_signal <= 0 else f"{min_signal}+"),
        ("持币", "全部" if min_holders <= 0 else f"{min_holders:,}+"),
        ("流动性", "全部" if min_liquidity <= 0 else f"${min_liquidity:,}+"),
    ]
    st.markdown(
        "<div class='filter-summary-bar'><span class='filter-summary-title'>当前筛选</span>"
        + "".join(
            f"<span class='filter-summary-item'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></span>"
            for label, value in items
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def format_threshold_option(value: int, unit: str = "") -> str:
    if value <= 0:
        return "全部"
    suffix = f" {unit}" if unit else ""
    return f"{value:,}+{suffix}"


def threshold_index(options: list[int], value: int) -> int:
    try:
        return options.index(value)
    except ValueError:
        return 0


def render_metric_cards(items: list[dict[str, str]]) -> None:
    if not items:
        return
    card_html = []
    for item in items:
        tone = html.escape(item.get("tone") or "accent")
        label = html.escape(clean_text(item.get("label")))
        value = html.escape(clean_text(item.get("value")))
        note = clean_text(item.get("note"), "")
        note_html = f"<div class='metric-note'>{html.escape(note)}</div>" if note else ""
        card_html.append(
            f"<div class='metric-card tone-{tone}'>"
            f"<div class='metric-label'>{label}</div>"
            f"<div class='metric-value'>{value}</div>"
            f"{note_html}</div>"
        )
    st.markdown("<div class='metric-grid'>" + "".join(card_html) + "</div>", unsafe_allow_html=True)


def render_data_quality_items(items: tuple[dict[str, str], ...]) -> None:
    if not items:
        return
    st.markdown(
        "<div class='data-quality-row'>"
        + "".join(
            f"<span class='data-quality-item tone-{html.escape(clean_text(item.get('tone'), 'neutral'))}'>"
            f"<strong>{html.escape(clean_text(item.get('label')))}</strong>{html.escape(clean_text(item.get('value')))}</span>"
            for item in items
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def render_prediction_confidence(row: pd.Series) -> None:
    confidence = build_prediction_confidence(row)
    chips_html = "".join(
        f"<span class='prediction-confidence-chip'>{html.escape(chip)}</span>" for chip in confidence.chips
    )
    evidence_html = "".join(f"<span>{html.escape(item)}</span>" for item in confidence.evidence)
    st.markdown(
        f"<div class='prediction-confidence tone-{html.escape(confidence.tone)}'>"
        "<div>"
        f"<div class='prediction-confidence-title'>{html.escape(confidence.title)}</div>"
        f"<div class='prediction-confidence-body'>{html.escape(confidence.body)}</div>"
        f"<div class='prediction-confidence-evidence'>{evidence_html}</div>"
        "</div>"
        f"<div class='prediction-confidence-chips'>{chips_html}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_trend_stats(items: list[dict[str, str]]) -> None:
    if not items:
        return
    st.markdown(
        "<div class='trend-stat-grid'>"
        + "".join(
            "<div class='trend-stat'>"
            f"<div class='trend-stat-label'>{html.escape(clean_text(item.get('label')))}</div>"
            f"<div class='trend-stat-value'>{html.escape(clean_text(item.get('value')))}</div>"
            f"<div class='trend-stat-note'>{html.escape(clean_text(item.get('note')))}</div>"
            "</div>"
            for item in items
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def render_trend_chart_header(title: str, copy: str) -> None:
    st.markdown(
        "<div class='trend-chart-header'>"
        f"<div class='trend-chart-title'>{html.escape(title)}</div>"
        f"<div class='trend-chart-copy'>{html.escape(copy)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def build_trend_chart(
    df: pd.DataFrame,
    *,
    column: str,
    label: str,
    color: str,
    height: int,
    value_format: str,
    value_formatter: Callable[[Any], str] | None = None,
) -> alt.Chart | None:
    if column not in df.columns:
        return None
    chart_data = df[["observed_at", column]].dropna().rename(columns={column: "value"})
    if chart_data.empty:
        return None
    value_tooltip: alt.Tooltip
    if value_formatter is not None:
        chart_data["value_label"] = chart_data["value"].map(value_formatter)
        value_tooltip = alt.Tooltip("value_label:N", title=label)
    else:
        value_tooltip = alt.Tooltip("value:Q", title=label, format=value_format)
    base = alt.Chart(chart_data).encode(
        x=alt.X(
            "observed_at:T",
            axis=alt.Axis(title=None, format="%H:%M", labelColor="#6b7280", tickColor="rgba(17,24,39,0.14)", grid=False),
        ),
        y=alt.Y(
            "value:Q",
            axis=alt.Axis(title=None, labelColor="#6b7280", gridColor="rgba(17,24,39,0.08)", format=value_format),
            scale=alt.Scale(zero=False),
        ),
        tooltip=[
            alt.Tooltip("observed_at:T", title="时间", format="%m-%d %H:%M"),
            value_tooltip,
        ],
    )
    area = base.mark_area(color=color, opacity=0.12, interpolate="monotone")
    line = base.mark_line(color=color, strokeWidth=2.6, interpolate="monotone")
    points = base.mark_circle(color=color, size=30, opacity=0.72)
    return (
        (area + line + points)
        .properties(height=height)
        .configure_axis(labelFontSize=11, labelPadding=8)
        .configure_view(strokeWidth=0)
        .configure(background="transparent")
    )


def format_age_minutes(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return clean_text(value)
    if pd.isna(numeric):
        return "--"
    if numeric < 60:
        return f"{int(numeric)}分钟"
    if numeric < 1440:
        return f"{numeric / 60:.1f}小时"
    return f"{numeric / 1440:.1f}天"


def format_indicator_value(name: str, value: Any) -> str:
    if value in (None, ""):
        return "--"
    if name == "risk_flags":
        return format_risk_titles(value)
    if name == "age_minutes":
        return format_age_minutes(value)
    if name == "price_usd":
        return format_price(value)
    if name in {"market_cap", "fdv", "liquidity_usd", "volume_m5", "volume_h1", "volume_h24"}:
        return format_money(value)
    if name in {"tx_count_m5", "tx_count_h1", "buys_m5", "sells_m5", "buys_h1", "sells_h1", "website_count", "social_count", "boosts_active"}:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        return "--" if pd.isna(numeric) else f"{int(numeric):,}"
    if name.startswith("price_change_"):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        return "--" if pd.isna(numeric) else f"{numeric:.1f}%"
    if name.endswith("_return_live"):
        return format_percent(value)
    if name == "market_cap_bucket":
        bucket_labels = {
            "micro": "微型市值",
            "small": "小市值",
            "mid": "中等市值",
            "large": "大市值",
            "unknown": "未知",
        }
        return bucket_labels.get(str(value), str(value))
    if name in {
        "liquidity_to_fdv",
        "volume_to_liquidity_h1",
        "buy_sell_ratio_m5",
        "buy_sell_ratio_h1",
        "volume_impulse_vs_prev24h",
        "volume_impulse_vs_prev72h",
    }:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        return "--" if pd.isna(numeric) else f"{numeric:.2f}x"
    if isinstance(value, (list, tuple)):
        return "，".join(str(item) for item in value) or "--"
    return str(value)


def build_indicator_rows(features: dict[str, Any]) -> list[dict[str, str]]:
    if not features:
        return []
    ordered_keys = [key for key in FEATURE_LABELS if key in features]
    ordered_keys.extend(key for key in features if key not in FEATURE_LABELS)
    rows = []
    for key in ordered_keys:
        label, note = FEATURE_LABELS.get(key, ("未配置指标", "这个字段还没有配置中文备注，后续需要补充。"))
        rows.append({"title": label, "value": format_indicator_value(key, features.get(key)), "body": note, "tone": "accent"})
    return rows


def external_trend_metrics_complete(metrics: dict[str, Any]) -> bool:
    return metrics.get("external_return_2h") is not None and metrics.get("external_return_24h") is not None


def empty_external_trend_metrics() -> dict[str, None]:
    return {"external_return_2h": None, "external_return_24h": None}


def external_ohlcv_has_fresh_current(rows: list[dict[str, Any]], observed_at: Any) -> bool:
    if not rows:
        return False
    observed_ts = int(observed_at.timestamp())
    current_candidates = [int(row.get("ts") or 0) for row in rows if int(row.get("ts") or 0) <= observed_ts]
    if not current_candidates:
        return False
    return max(current_candidates) >= observed_ts - EXTERNAL_TREND_MAX_CURRENT_LAG_SECONDS


def external_ohlcv_covers_lookback(rows: list[dict[str, Any]], observed_at: Any, hours: int) -> bool:
    if not external_ohlcv_has_fresh_current(rows, observed_at):
        return False
    cutoff_ts = int(observed_at.timestamp()) - hours * 3600
    return any(int(row.get("ts") or 0) <= cutoff_ts for row in rows)


def compute_external_trend_metrics_from_rows(rows: list[dict[str, Any]], *, observed_at: Any) -> dict[str, Any]:
    if not external_ohlcv_has_fresh_current(rows, observed_at):
        return empty_external_trend_metrics()
    return compute_lookback_returns(rows, observed_at=observed_at)


def cache_external_trend_metrics(
    repo: MonitorRepository,
    *,
    pair_address: str,
    observed_hour_key: str,
    metrics: dict[str, Any],
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    repo.upsert_external_trend_metrics(
        pair_address,
        observed_hour_key,
        external_return_2h=metrics.get("external_return_2h"),
        external_return_24h=metrics.get("external_return_24h"),
        raw_payload=raw_payload,
    )
    return repo.get_external_trend_metrics(pair_address, observed_hour_key) or metrics


@st.cache_data(show_spinner=False, ttl=EXTERNAL_TREND_CACHE_TTL_SECONDS)
def get_external_trend_metrics(database_path: str, pair_address: str, observed_at_raw: Any) -> dict[str, Any]:
    if not pair_address:
        return {}
    observed_at = pd.to_datetime(observed_at_raw, utc=True, errors="coerce")
    if pd.isna(observed_at):
        return {}
    observed_hour_key = observed_at.floor("h").isoformat()
    observed_dt = observed_at.to_pydatetime().astimezone(timezone.utc)
    before_timestamp = int(observed_dt.timestamp()) + 3600
    repo = open_repository(database_path)
    try:
        cached_metrics = repo.get_external_trend_metrics(pair_address, observed_hour_key)
        cached_payload = json_loads(cached_metrics.get("raw_json"), {}) if cached_metrics is not None else {}
        if (
            cached_metrics is not None
            and external_trend_metrics_complete(cached_metrics)
            and cached_payload.get("source") != "geckoterminal_stale"
        ):
            return cached_metrics

        cached_rows = repo.list_external_ohlcv(
            network=EXTERNAL_TREND_NETWORK,
            pool_address=pair_address,
            timeframe="hour",
            aggregate=1,
            before_timestamp=before_timestamp,
            limit=EXTERNAL_TREND_OHLCV_LIMIT,
        )
        recorded_count = repo.get_external_ohlcv_fetch_row_count(
            network=EXTERNAL_TREND_NETWORK,
            pool_address=pair_address,
            timeframe="hour",
            aggregate=1,
            limit=EXTERNAL_TREND_OHLCV_LIMIT,
            before_timestamp=before_timestamp,
        )
        cached_row_metrics = compute_external_trend_metrics_from_rows(cached_rows, observed_at=observed_dt)
        if external_trend_metrics_complete(cached_row_metrics) and external_ohlcv_covers_lookback(cached_rows, observed_dt, 24):
            return cache_external_trend_metrics(
                repo,
                pair_address=pair_address,
                observed_hour_key=observed_hour_key,
                metrics=cached_row_metrics,
                raw_payload={"source": "cached_external_ohlcv", "row_count": len(cached_rows), **cached_row_metrics},
            )

        if recorded_count is not None and recorded_count > 0 and len(cached_rows) >= recorded_count:
            rows_for_metrics = cached_rows[-recorded_count:]
            metrics = compute_external_trend_metrics_from_rows(rows_for_metrics, observed_at=observed_dt)
            return cache_external_trend_metrics(
                repo,
                pair_address=pair_address,
                observed_hour_key=observed_hour_key,
                metrics=metrics,
                raw_payload={"source": "cached_external_ohlcv", "row_count": recorded_count, **metrics},
            )

        try:
            fetched_rows = GeckoTerminalClient(network=EXTERNAL_TREND_NETWORK, timeout_seconds=8).fetch_pool_ohlcv(
                pair_address,
                timeframe="hour",
                aggregate=1,
                limit=EXTERNAL_TREND_OHLCV_LIMIT,
                before_timestamp=before_timestamp,
            )
        except (requests.RequestException, ValueError, TypeError):
            return cached_row_metrics if any(value is not None for value in cached_row_metrics.values()) else {}

        repo.upsert_external_ohlcv(
            network=EXTERNAL_TREND_NETWORK,
            pool_address=pair_address,
            timeframe="hour",
            aggregate=1,
            rows=fetched_rows,
        )
        repo.record_external_ohlcv_fetch(
            network=EXTERNAL_TREND_NETWORK,
            pool_address=pair_address,
            timeframe="hour",
            aggregate=1,
            limit=EXTERNAL_TREND_OHLCV_LIMIT,
            before_timestamp=before_timestamp,
            row_count=len(fetched_rows),
        )
        fetched_metrics = compute_external_trend_metrics_from_rows(fetched_rows, observed_at=observed_dt)
        payload_source = "geckoterminal" if external_ohlcv_has_fresh_current(fetched_rows, observed_dt) else "geckoterminal_stale"
        return cache_external_trend_metrics(
            repo,
            pair_address=pair_address,
            observed_hour_key=observed_hour_key,
            metrics=fetched_metrics,
            raw_payload={"source": payload_source, "row_count": len(fetched_rows), **fetched_metrics},
        )
    finally:
        repo.close()


def search_overview(df: pd.DataFrame, term: str) -> pd.DataFrame:
    if not term.strip() or df.empty:
        return df
    needle = term.strip().lower()
    searchable = (
        df.get("token_symbol", "").astype(str)
        + " "
        + df.get("token_name", "").astype(str)
        + " "
        + df.get("token_address", "").astype(str)
        + " "
        + df.get("pair_address", "").astype(str)
    ).str.lower()
    return df[searchable.str.contains(needle, regex=False, na=False)]


def build_list_labels(df: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    options: list[str] = []
    labels: dict[str, str] = {}
    for _, row in df.iterrows():
        pair = str(row.get("pair_address") or "")
        if not pair:
            continue
        symbol = clean_text(row.get("token_symbol"), str(row.get("token_address") or "")[:8])
        tier = clean_text(row.get("display_tier_label"), "")
        state = translate_state(row.get("selection_pair_state") or row.get("state"))
        score = row.get("display_score")
        score_label = "待评分" if pd.isna(score) or float(score) < 0 else f"信号 {int(score)}"
        short_score = row.get("prediction_short_momentum_score")
        if pd.isna(short_score):
            short_score = row.get("prediction_opportunity_score")
        opportunity_label = "" if pd.isna(short_score) else f" · 短线 {int(short_score)}"
        tier_label = f" · {tier}" if tier else ""
        labels[pair] = f"{symbol}{tier_label} · {state} · {score_label}{opportunity_label}"
        options.append(pair)
    return options, labels


def build_list_groups(df: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    if df.empty:
        return []
    group_keys = {key for key, _ in LIST_GROUP_ORDER}
    grouped_df = df.copy()
    raw_tiers = (
        grouped_df["display_tier"]
        if "display_tier" in grouped_df.columns
        else pd.Series(["normal"] * len(grouped_df), index=grouped_df.index)
    )
    normalized_tiers = raw_tiers.fillna("normal").astype(str).map(lambda key: LIST_GROUP_ALIASES.get(key, key))
    grouped_df["_list_group_key"] = normalized_tiers.where(normalized_tiers.isin(group_keys), "normal")

    groups: list[tuple[str, pd.DataFrame]] = []
    for group_key, group_label in LIST_GROUP_ORDER:
        group_df = grouped_df[grouped_df["_list_group_key"] == group_key].drop(columns=["_list_group_key"], errors="ignore")
        if not group_df.empty:
            groups.append((group_label, group_df))
    return groups


def select_pair(pair_address: str) -> None:
    previous_pair = st.session_state.get("selected_pair")
    st.session_state["selected_pair"] = pair_address
    if previous_pair != pair_address:
        st.session_state[DETAIL_VIEW_WIDGET_KEY] = "overview"
        st.session_state[DETAIL_VIEW_PAIR_SYNC_KEY] = pair_address


def sync_detail_view_for_pair(pair_address: str) -> None:
    if st.session_state.get(DETAIL_VIEW_PAIR_SYNC_KEY) != pair_address:
        st.session_state[DETAIL_VIEW_WIDGET_KEY] = "overview"
        st.session_state[DETAIL_VIEW_PAIR_SYNC_KEY] = pair_address


def dashboard_refresh_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, seconds)


def mark_dashboard_data_refreshed() -> None:
    st.session_state[DASHBOARD_LAST_REFRESH_TS_KEY] = time.time()
    st.session_state[DASHBOARD_REFRESH_RERUN_PENDING_KEY] = False


def build_refresh_badge_text(refresh_seconds: int) -> str:
    if refresh_seconds <= 0:
        return "自动刷新关闭"
    last_refresh = st.session_state.get(DASHBOARD_LAST_REFRESH_TS_KEY)
    if last_refresh is None:
        remaining = refresh_seconds
    else:
        try:
            elapsed = max(0, int(time.time() - float(last_refresh)))
        except (TypeError, ValueError):
            elapsed = 0
        remaining = max(0, refresh_seconds - elapsed)
    return f"刷新 {remaining}s" if remaining > 0 else "刷新中"


def build_refresh_status_label(refresh_seconds: int, now_ts: float | None = None, last_refresh_ts: float | None = None) -> str:
    if refresh_seconds <= 0:
        return "自动刷新关闭"
    if last_refresh_ts is None:
        last_refresh_ts = st.session_state.get(DASHBOARD_LAST_REFRESH_TS_KEY)
    if last_refresh_ts is None:
        remaining = refresh_seconds
    else:
        current_ts = time.time() if now_ts is None else float(now_ts)
        try:
            elapsed = max(0, int(current_ts - float(last_refresh_ts)))
        except (TypeError, ValueError):
            elapsed = 0
        remaining = max(0, refresh_seconds - elapsed)
    return f"自动刷新 {remaining}s" if remaining > 0 else "自动刷新中"


def format_dashboard_status_summary(status: dict[str, Any] | None, now_ts: float | None = None) -> str:
    status = status or {}
    latest_snapshot_at = status.get("latest_snapshot_at")
    if latest_snapshot_at in (None, ""):
        age_label = "--"
    else:
        parsed = pd.to_datetime(latest_snapshot_at, utc=True, errors="coerce")
        if pd.isna(parsed):
            age_label = "--"
        else:
            current_ts = time.time() if now_ts is None else float(now_ts)
            age_seconds = max(0, int(current_ts - parsed.timestamp()))
            if age_seconds < 60:
                age_label = f"{age_seconds}s前"
            elif age_seconds < 3600:
                age_label = f"{age_seconds // 60}分钟前"
            else:
                age_label = f"{age_seconds / 3600:.1f}小时前"

    snapshot_writes = int(status.get("recent_snapshot_writes") or 0)
    signal_writes = int(status.get("recent_signal_writes") or 0)
    return f"全局采集 {age_label} · 10m写入 {snapshot_writes}/{signal_writes}"


def build_top_refresh_status_text(
    status: dict[str, Any] | None,
    *,
    refresh_seconds: int,
    now_ts: float | None = None,
    last_refresh_ts: float | None = None,
) -> str:
    return (
        f"{format_dashboard_status_summary(status, now_ts=now_ts)}"
        f" · {build_refresh_status_label(refresh_seconds, now_ts=now_ts, last_refresh_ts=last_refresh_ts)}"
    )


def build_top_refresh_status_items(
    status: dict[str, Any] | None,
    *,
    refresh_seconds: int,
    now_ts: float | None = None,
    last_refresh_ts: float | None = None,
) -> tuple[str, str, str]:
    status_summary = format_dashboard_status_summary(status, now_ts=now_ts)
    snapshot_item, write_item = status_summary.split(" · ", 1)
    return (
        snapshot_item,
        write_item,
        build_refresh_status_label(refresh_seconds, now_ts=now_ts, last_refresh_ts=last_refresh_ts),
    )


def render_refresh_badge(refresh_seconds: int) -> None:
    status_items = build_top_refresh_status_items(
        st.session_state.get(DASHBOARD_STATUS_KEY),
        refresh_seconds=refresh_seconds,
    )
    status_html = "".join(
        f"<span class='top-status-item {klass}'>{html.escape(item)}</span>"
        for item, klass in (
            (status_items[0], "top-status-snapshot"),
            (status_items[1], "top-status-writes"),
            (status_items[2], "top-status-countdown"),
        )
    )
    st.markdown(
        f"<div class='top-refresh'><div class='top-status-text'>{status_html}</div></div>",
        unsafe_allow_html=True,
    )


def should_request_dashboard_refresh(refresh_seconds: int) -> bool:
    if refresh_seconds <= 0:
        return False
    last_refresh = st.session_state.get(DASHBOARD_LAST_REFRESH_TS_KEY)
    if last_refresh is None:
        return False
    try:
        elapsed = time.time() - float(last_refresh)
    except (TypeError, ValueError):
        return False
    return elapsed >= refresh_seconds


@st.fragment(run_every=1)
def render_refresh_badge_countdown(refresh_seconds: int) -> None:
    render_refresh_badge(refresh_seconds)
    refresh_due = should_request_dashboard_refresh(refresh_seconds)
    refresh_pending = st.session_state.get(DASHBOARD_REFRESH_RERUN_PENDING_KEY)
    if refresh_due and not refresh_pending:
        st.session_state[DASHBOARD_REFRESH_RERUN_PENDING_KEY] = True
        st.rerun(scope="app")


def render_overview(row: pd.Series, signal_context: Any, database_path: str, pair_address: str) -> None:
    observed_at_raw = (
        signal_context.observed_at
        if signal_context is not None and signal_context.observed_at not in (None, "")
        else row.get("snapshot_observed_at") or row.get("last_snapshot_at")
    )
    external_metrics = get_external_trend_metrics(database_path, pair_address, observed_at_raw)
    render_section("量价快照", "当前量价与结果指标", "先看规模、流动性和区间表现，再决定是否继续加大关注。")
    render_metric_cards(
        [
            {
                "label": "当前市值",
                "value": format_money(
                    first_non_missing(
                        metric_value(signal_context, row, "market_cap"),
                        row.get("alpha_market_cap"),
                        metric_value(signal_context, row, "fdv"),
                    )
                ),
                "note": "优先使用实时快照",
            },
            {
                "label": "当前单价",
                "value": format_price(first_non_missing(metric_value(signal_context, row, "price_usd"), row.get("alpha_price"))),
                "note": "快照缺失时回退 Alpha",
            },
            {"label": "链上持币人数", "value": format_holders(row.get("holder_count")), "note": "用于判断分布深度"},
            {"label": "1小时成交额", "value": format_money(metric_value(signal_context, row, "volume_h1")), "note": "观察当前换手"},
            {
                "label": "流动性",
                "value": format_money(first_non_missing(metric_value(signal_context, row, "liquidity_usd"), row.get("alpha_liquidity"))),
                "note": "用于判断承接能力",
            },
            {"label": "外部2小时涨幅", "value": format_percent(external_metrics.get("external_return_2h")), "note": "GeckoTerminal 小时线"},
            {"label": "外部24小时涨幅", "value": format_percent(external_metrics.get("external_return_24h")), "note": "已缓存历史区间"},
        ]
    )


def render_prediction(row: pd.Series) -> None:
    render_section("概率预测", "分时机会与追高风险", "优先看 2 小时短线机会；6小时和24小时只作为延续观察，不直接触发正式告警。")
    render_metric_cards(
        [
            {"label": "2小时涨20%概率", "value": format_percent(row.get("prediction_prob_2h_up20")), "note": "短线冲刺概率"},
            {"label": "6小时涨50%概率", "value": format_percent(row.get("prediction_prob_6h_up50")), "note": "持续放量概率"},
            {"label": "24小时翻倍概率", "value": format_percent(row.get("prediction_prob_24h_up100")), "note": "中短线爆发概率"},
            {
                "label": "6小时回撤30%风险",
                "value": format_percent(row.get("prediction_risk_6h_dd30")),
                "note": "追高风险",
                "tone": "risk",
            },
        ]
    )
    short_score = row.get("prediction_short_momentum_score")
    if pd.isna(short_score):
        short_score = row.get("prediction_opportunity_score")
    continuation_score = row.get("prediction_continuation_score")
    breakout_score = row.get("prediction_breakout_score")
    meta = [
        ("2小时短线机会", "--" if pd.isna(short_score) else str(int(short_score))),
        ("6小时延续机会", "--" if pd.isna(continuation_score) else str(int(continuation_score))),
        ("24小时爆发观察", "--" if pd.isna(breakout_score) else str(int(breakout_score))),
        ("阶段判断", format_stage(row.get("prediction_stage"))),
    ]
    st.markdown(
        "<div class='meta-chip-row'>"
        + "".join(f"<span class='meta-chip'><strong>{html.escape(k)}</strong>{html.escape(v)}</span>" for k, v in meta)
        + "</div>",
        unsafe_allow_html=True,
    )
    render_prediction_confidence(row)
    reasons = list_to_text(row.get("prediction_reasons"))
    if reasons:
        render_detail_lines(
            [
                {"title": explain_prediction_reason(code)[0], "value": "预测因子", "body": explain_prediction_reason(code)[1], "tone": "accent"}
                for code in reasons
            ]
        )
    else:
        st.info("当前还没有预测解释因子。")


def render_explanation(signal_context: Any, row: pd.Series) -> None:
    score = resolve_score(signal_context, row)
    title = "继续观察"
    summary = "当前信号还没有达到强确认状态。"
    if score is not None and not pd.isna(score):
        if int(score) >= 78:
            title = "重点复核"
            summary = "信号分进入重点关注区间，需要结合流动性、成交延续和风险项确认。"
        elif int(score) >= 65:
            title = "继续跟踪"
            summary = "信号分达到跟踪区间，适合持续观察后续成交和价格结构。"
    st.markdown(
        "<div class='conclusion-panel'>"
        f"<div class='conclusion-title'>{html.escape(title)}</div>"
        f"<div class='conclusion-copy'>{html.escape(summary)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    render_section("结论依据", "正向依据", "把当前结论拆回具体规则，方便确认这条信号强在哪里。")
    if signal_context is None or not signal_context.reasons:
        st.info("当前暂无正向命中规则。")
    else:
        render_detail_lines(
            [
                {"title": explain_reason(code)[0], "value": "正向命中", "body": explain_reason(code)[1], "tone": "accent"}
                for code in signal_context.reasons
            ],
            compact=True,
        )
    if signal_context is not None and signal_context.risk_flags:
        render_section("风险提示", "风险提示", "风险项用于解释为什么结论没有进一步上调。")
        render_detail_lines(
            [
                {"title": explain_risk(code)[0], "value": "需要复核", "body": explain_risk(code)[1], "tone": "risk"}
                for code in signal_context.risk_flags
            ],
            compact=True,
        )


def render_features(signal_context: Any) -> None:
    render_section("指标备注", "信号指标备注", "按中文指标名展示当前值和含义，减少横向信息和滚动负担。")
    rows = build_indicator_rows(signal_context.features if signal_context is not None else {})
    if rows:
        render_feature_lines(rows)
    else:
        st.info("当前还没有完整信号指标。")


def render_trend(snapshots_df: pd.DataFrame) -> None:
    render_section("走势", "价格与成交走势", "结合价格、流动性和近 1 小时成交额，判断强度是否延续。")
    if snapshots_df.empty:
        st.info("当前还没有该代币的快照数据。")
        return
    chart_df = snapshots_df.copy()
    chart_df["observed_at"] = pd.to_datetime(chart_df["observed_at"], errors="coerce")
    chart_df = chart_df.dropna(subset=["observed_at"]).sort_values("observed_at")
    if chart_df.empty:
        st.info("当前快照缺少有效时间，暂时无法绘制走势。")
        return
    for column in ("price_usd", "liquidity_usd", "volume_m5", "volume_h1"):
        if column in chart_df.columns:
            chart_df[column] = pd.to_numeric(chart_df[column], errors="coerce")
    latest = chart_df.iloc[-1]
    render_trend_stats(
        [
            {"label": "最新价格", "value": format_price(latest.get("price_usd")), "note": format_timestamp(latest.get("observed_at"))},
            {"label": "流动性", "value": format_money(latest.get("liquidity_usd")), "note": "池子深度"},
            {"label": "1小时成交额", "value": format_money(latest.get("volume_h1")), "note": "成交延续"},
            {"label": "5分钟成交额", "value": format_money(latest.get("volume_m5")), "note": "短线活跃"},
            {"label": "交易对年龄", "value": format_age_minutes(latest.get("age_minutes")), "note": f"样本 {len(chart_df)} 条"},
        ]
    )

    price_chart = build_trend_chart(
        chart_df,
        column="price_usd",
        label="价格",
        color="#0f766e",
        height=260,
        value_format=price_axis_format(chart_df["price_usd"]),
        value_formatter=format_price,
    )
    if price_chart is not None:
        render_trend_chart_header("价格走势", "优先看趋势延续性，避免只看最新一根快照。")
        st.altair_chart(price_chart, width="stretch")
    else:
        st.info("当前没有可绘制的价格走势。")

    col1, col2 = st.columns(2)
    with col1:
        liquidity_chart = build_trend_chart(
            chart_df,
            column="liquidity_usd",
            label="流动性",
            color="#2563eb",
            height=200,
            value_format="$,.2s",
            value_formatter=format_money,
        )
        if liquidity_chart is not None:
            render_trend_chart_header("流动性", "池子承接能力是否稳定。")
            st.altair_chart(liquidity_chart, width="stretch")
        else:
            st.info("暂无流动性走势。")
    with col2:
        volume_chart = build_trend_chart(
            chart_df,
            column="volume_h1",
            label="1小时成交额",
            color="#b45309",
            height=200,
            value_format="$,.2s",
            value_formatter=format_money,
        )
        if volume_chart is not None:
            render_trend_chart_header("1小时成交额", "成交是否跟随价格同步放大。")
            st.altair_chart(volume_chart, width="stretch")
        else:
            st.info("暂无成交额走势。")


def render_history(signals_df: pd.DataFrame) -> None:
    render_section("历史记录", "最近信号记录", "对照最近几轮得分、概率、命中原因和风险提示，判断节奏是否稳定。")
    if signals_df.empty:
        st.info("当前还没有最近信号记录。")
        return
    cards = []
    for _, row in signals_df.iterrows():
        probability = join_segments(
            [
                detail_segment("2小时 ", format_percent(row.get("prediction_prob_2h_up20"))),
                detail_segment("6小时 ", format_percent(row.get("prediction_prob_6h_up50"))),
                detail_segment("24小时 ", format_percent(row.get("prediction_prob_24h_up100"))),
                detail_segment("回撤 ", format_percent(row.get("prediction_risk_6h_dd30"))),
            ],
            separator=" / ",
        )
        outcome = join_segments(
            [
                detail_segment("2小时 ", format_percent(row.get("outcome_max_return_2h"))),
                detail_segment("6小时 ", format_percent(row.get("outcome_max_return_6h"))),
                detail_segment("24小时 ", format_percent(row.get("outcome_max_return_24h"))),
                detail_segment("回撤 ", format_percent(row.get("outcome_min_return_6h"))),
            ],
            separator=" / ",
        )
        risks = format_risk_titles(row.get("risk_flags"))
        score = row.get("score")
        short_score = row.get("prediction_short_momentum_score")
        if pd.isna(short_score):
            short_score = row.get("prediction_opportunity_score")
        meta = join_segments(
            [
                detail_segment("状态 ", translate_state(row.get("pair_state"))),
                detail_segment("阶段 ", format_stage(row.get("prediction_stage"))),
                detail_segment("告警 ", "是" if row.get("should_alert") else "否"),
            ],
            separator=" · ",
        )
        score_chips = []
        if not pd.isna(score):
            score_chips.append(f"<span class='history-score-chip'>分数 {int(score)}</span>")
        if not pd.isna(short_score):
            score_chips.append(f"<span class='history-score-chip'>短线 {int(short_score)}</span>")
        rows = [
            ("概率", probability or "暂无"),
            ("实际", outcome or "暂无"),
            ("命中", format_reason_titles(row.get("reasons"))),
            ("预测", format_prediction_reason_titles(row.get("prediction_reasons"))),
        ]
        if risks != "暂无":
            rows.append(("风险", risks))
        cards.append(
            f"<div class='history-card {'tone-risk' if risks != '暂无' else ''}'>"
            "<div class='history-top'>"
            f"<div><div class='history-time'>{html.escape(format_timestamp(row.get('observed_at')))}</div>"
            f"<div class='history-meta'>{html.escape(meta or '暂无状态')}</div></div>"
            f"<div class='history-score-row'>{''.join(score_chips) or '<span class=\"history-score-chip\">待评分</span>'}</div>"
            "</div>"
            "<div class='history-body'>"
            + "".join(
                "<div class='history-row'>"
                f"<div class='history-row-label'>{html.escape(label)}</div>"
                f"<div class='history-row-text'>{html.escape(text)}</div>"
                "</div>"
                for label, text in rows
            )
            + "</div></div>"
        )
    st.markdown("<div class='history-list'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_detail(row: pd.Series, *, database_path: str, revision: tuple[tuple[str, int, int], ...]) -> None:
    signal_context = build_latest_signal_context(row)
    pair_address = str(row.get("pair_address") or "")
    token_address = str(row.get("token_address") or "")
    sync_detail_view_for_pair(pair_address)
    token_address_short = token_address[:8] + "..." + token_address[-6:] if len(token_address) > 16 else token_address
    symbol = clean_text(row.get("token_symbol"), token_address[:8])
    token_name = clean_text(row.get("token_name"), "未命名代币")
    token_metadata = token_metadata_from_row(row)
    score = resolve_score(signal_context, row)
    score_label = "待评分" if score is None or pd.isna(score) else f"{int(score)} 分"
    state = translate_state(resolve_pair_state(signal_context, row))
    risk_count = len(set(list_to_text(row.get("risk_flags")) + (list(signal_context.risk_flags) if signal_context else [])))
    meta_items = [
        ("状态", state),
        ("交易对年龄", format_age_from_timestamp(row.get("pair_created_at"))),
        ("币安标签", format_binance_labels(token_metadata)),
        ("Top10 持仓", format_top10_holder_share(token_metadata)),
        ("风险标记", str(risk_count)),
        ("最近快照", format_timestamp(row.get("snapshot_observed_at") or row.get("last_snapshot_at"))),
        ("最新信号", format_timestamp(signal_context.observed_at) if signal_context else "--"),
    ]
    quality_row = {
        "snapshot_observed_at": row.get("snapshot_observed_at") or row.get("last_snapshot_at"),
        "last_signal_at": signal_context.observed_at if signal_context else row.get("last_signal_at"),
        "price_usd": first_non_missing(metric_value(signal_context, row, "price_usd"), row.get("price_usd")),
        "alpha_price": row.get("alpha_price"),
    }
    quality_items = build_detail_data_quality_items(quality_row)
    quality_html = (
        "<div class='data-quality-row'>"
        + "".join(
            f"<span class='data-quality-item tone-{html.escape(clean_text(item.get('tone'), 'neutral'))}'>"
            f"<strong>{html.escape(clean_text(item.get('label')))}</strong>{html.escape(clean_text(item.get('value')))}</span>"
            for item in quality_items
        )
        + "</div>"
    )
    copy_button_id = "copy-token-address-" + "".join(ch for ch in token_address.lower() if ch.isalnum())[:48]
    st.html(
        "<div class='detail-hero'>"
        "<div class='detail-hero-top'>"
        f"<div><div class='section-kicker'>Alpha 候选标的</div><h2>{html.escape(symbol)}</h2>"
        f"<div class='section-copy'>{html.escape(token_name)} / {html.escape(clean_text(row.get('quote_symbol'), '-'))} · {html.escape(state)}</div></div>"
        f"<div class='score-box'><div class='section-copy'>当前信号强度</div><div style='font-size:1.24rem;font-weight:850'>{html.escape(score_label)}</div></div>"
        "</div>"
        "<div class='meta-chip-row'>"
        + "".join(f"<span class='meta-chip'><strong>{html.escape(k)}</strong>{html.escape(v)}</span>" for k, v in meta_items)
        + "</div>"
        + quality_html
        + "<div class='link-row'>"
        f"<a class='link-pill' href='https://dexscreener.com/bsc/{quote(pair_address)}' target='_blank'>DexScreener</a>"
        f"<a class='link-pill' href='https://bscscan.com/token/{quote(token_address)}' target='_blank'>BscScan 代币</a>"
        f"<a class='link-pill' href='https://bscscan.com/address/{quote(pair_address)}' target='_blank'>BscScan 交易对</a>"
        "</div>"
        f"<div class='hero-address-line'><span class='hero-address-label'>代币地址</span>"
        f"<span class='hero-address-value' title='{html.escape(token_address)}'>{html.escape(token_address_short)}</span>"
        f"<button id='{html.escape(copy_button_id, quote=True)}' class='copy-address-button' type='button' data-copy='{html.escape(token_address, quote=True)}' "
        "aria-label='复制代币地址'>复制</button></div>"
        "</div>"
        f"""
<script>
(() => {{
  const button = document.getElementById("{html.escape(copy_button_id, quote=True)}");
  if (!button) {{
    return;
  }}
  const originalText = "复制";

  const setStatus = (text, copied) => {{
    button.textContent = text;
    button.classList.toggle("is-copied", copied);
    window.clearTimeout(button._copyResetTimer);
    button._copyResetTimer = window.setTimeout(() => {{
      button.textContent = originalText;
      button.classList.remove("is-copied");
    }}, 1200);
  }};

  const fallbackCopy = (text) => {{
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    if (!ok) {{
      throw new Error("copy failed");
    }}
  }};

  button.onclick = async (event) => {{
    event.preventDefault();
    event.stopPropagation();
    const value = button.dataset.copy || "";
    try {{
      if (navigator.clipboard && window.isSecureContext) {{
        try {{
          await navigator.clipboard.writeText(value);
        }} catch (clipboardError) {{
          fallbackCopy(value);
        }}
      }} else {{
        fallbackCopy(value);
      }}
      setStatus("已复制", true);
    }} catch (error) {{
      setStatus("复制失败", false);
    }}
  }};
}})();
</script>
        """,
        width="stretch",
        unsafe_allow_javascript=True,
    )
    view = st.segmented_control(
        "详情视图",
        options=[key for key, _ in DETAIL_VIEW_OPTIONS],
        format_func=lambda key: dict(DETAIL_VIEW_OPTIONS)[key],
        required=True,
        label_visibility="collapsed",
        width="stretch",
        key=DETAIL_VIEW_WIDGET_KEY,
    )
    if view not in {key for key, _ in DETAIL_VIEW_OPTIONS}:
        view = "overview"
        st.session_state[DETAIL_VIEW_WIDGET_KEY] = view
    with st.container(key="detail_view_content"):
        if view == "overview":
            render_overview(row, signal_context, database_path, pair_address)
        elif view == "prediction":
            render_prediction(row)
        elif view == "explanation":
            render_explanation(signal_context, row)
        elif view == "features":
            render_features(signal_context)
        elif view == "trend":
            render_trend(load_recent_snapshots_frame(database_path, revision, pair_address))
        else:
            render_history(load_recent_signals_frame(database_path, revision, pair_address))


def render_dashboard_content(config: Any, only_market: bool) -> None:
    selected_mode = st.segmented_control(
        "筛选模式",
        options=list(FILTER_MODES.keys()),
        format_func=lambda key: FILTER_MODES[key][0],
        default="balanced",
        label_visibility="collapsed",
        width="stretch",
        key="filter_mode",
    )
    mode_label, default_signal, default_holders, default_liquidity = FILTER_MODES.get(selected_mode, FILTER_MODES["balanced"])

    search_term = str(st.session_state.get("token_search_term") or "").strip()
    with st.expander("高级筛选（可选）", expanded=False):
        adv_col1, adv_col2, adv_col3 = st.columns(3)
        with adv_col1:
            st.markdown("<div class='advanced-filter-label'>最低信号/短线机会强度</div>", unsafe_allow_html=True)
            min_signal = st.selectbox(
                "最低信号/短线机会强度",
                SIGNAL_THRESHOLD_OPTIONS,
                index=threshold_index(SIGNAL_THRESHOLD_OPTIONS, default_signal),
                format_func=lambda value: format_threshold_option(value, "分"),
                label_visibility="collapsed",
                key=f"advanced_min_signal_{selected_mode}",
            )
        with adv_col2:
            st.markdown("<div class='advanced-filter-label'>最低持币人数</div>", unsafe_allow_html=True)
            min_holders = st.selectbox(
                "最低持币人数",
                HOLDER_THRESHOLD_OPTIONS,
                index=threshold_index(HOLDER_THRESHOLD_OPTIONS, default_holders),
                format_func=lambda value: format_threshold_option(value, "人"),
                label_visibility="collapsed",
                key=f"advanced_min_holders_{selected_mode}",
            )
        with adv_col3:
            st.markdown("<div class='advanced-filter-label'>最低流动性</div>", unsafe_allow_html=True)
            min_liquidity = st.selectbox(
                "最低流动性",
                LIQUIDITY_THRESHOLD_OPTIONS,
                index=threshold_index(LIQUIDITY_THRESHOLD_OPTIONS, default_liquidity),
                format_func=lambda value: format_threshold_option(value, "美元"),
                label_visibility="collapsed",
                key=f"advanced_min_liquidity_{selected_mode}",
            )

    render_filter_summary(
        mode_label=mode_label,
        only_with_market_data=only_market,
        min_signal=int(min_signal),
        min_holders=int(min_holders),
        min_liquidity=int(min_liquidity),
    )

    revision = build_database_revision_key(config.database_path)
    st.session_state[DASHBOARD_STATUS_KEY] = load_dashboard_status(config.database_path, revision)
    raw_df, overview_df = load_overview_frames(config.database_path, revision)
    if raw_df.empty or overview_df.empty:
        st.info("当前还没有可展示的币对数据，请先运行 worker。")
        mark_dashboard_data_refreshed()
        return

    overview_df, filtered_df = filter_overview_frame(
        overview_df,
        min_signal=min_signal,
        min_holders=int(min_holders),
        min_liquidity=int(min_liquidity),
        only_with_market_data=only_market,
    )
    filtered_df = search_overview(filtered_df, search_term)

    left_col, right_col = st.columns([0.95, 1.55], gap="large")
    with left_col:
        st.markdown("<div class='section-title'>强信号代币列表</div>", unsafe_allow_html=True)
        st.text_input("搜索代币", placeholder="按代币符号、名称、地址筛选", label_visibility="collapsed", key="token_search_term")

    if filtered_df.empty:
        with left_col:
            st.warning("当前搜索和筛选条件下没有匹配结果。")
        with right_col:
            st.info("请调整搜索词或高级筛选条件。")
        mark_dashboard_data_refreshed()
        return

    display_df = filtered_df.head(LIST_DISPLAY_LIMIT).copy()
    pair_ids = display_df["pair_address"].astype(str).tolist()
    query_pair = normalize_pair_value(st.query_params.get("pair"))
    last_query_pair = normalize_pair_value(st.session_state.get(PAIR_QUERY_SYNC_KEY))
    selected_pair = resolve_selected_pair(
        pair_ids,
        widget_selected_pair=st.session_state.get(PAIR_SELECTOR_WIDGET_KEY),
        session_selected_pair=st.session_state.get("selected_pair"),
        query_selected_pair=query_pair,
        query_has_priority=query_pair is not None and query_pair != last_query_pair,
    )
    if selected_pair is None:
        st.warning("当前没有可选币对。")
        mark_dashboard_data_refreshed()
        return
    st.session_state["selected_pair"] = selected_pair
    if st.query_params.get("pair") != selected_pair:
        st.query_params["pair"] = selected_pair
    st.session_state[PAIR_QUERY_SYNC_KEY] = selected_pair

    with left_col:
        if selected_pair not in pair_ids:
            selected_pair = pair_ids[0]
            st.session_state["selected_pair"] = selected_pair
            if st.query_params.get("pair") != selected_pair:
                st.query_params["pair"] = selected_pair
        st.markdown(
            f"<div class='list-summary'>显示前 {len(display_df)} 条 · 搜索结果 {len(filtered_df)} · 候选池 {len(overview_df)}</div>",
            unsafe_allow_html=True,
        )
        options, labels = build_list_labels(display_df)
        grouped_options = {pair for pair in options}
        for group_label, group_df in build_list_groups(display_df):
            visible_pairs = [
                str(pair)
                for pair in group_df.get("pair_address", pd.Series(dtype="object")).tolist()
                if str(pair) in grouped_options
            ]
            if not visible_pairs:
                continue
            st.markdown(
                (
                    "<div class='list-group-heading'>"
                    f"<span class='list-group-heading-label'>{html.escape(group_label)}</span>"
                    f"<span class='list-group-heading-count'>{len(visible_pairs)}</span>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
            for pair in visible_pairs:
                is_selected = pair == selected_pair
                st.button(
                    labels.get(pair, pair),
                    key=f"{PAIR_SELECTOR_WIDGET_KEY}_{pair}",
                    use_container_width=True,
                    type="primary" if is_selected else "secondary",
                    on_click=select_pair,
                    args=(pair,),
                )

    selected_rows = display_df.loc[display_df["pair_address"].astype(str) == selected_pair]
    selected_row = selected_rows.iloc[0] if not selected_rows.empty else display_df.iloc[0]
    with right_col:
        render_detail(selected_row, database_path=config.database_path, revision=revision)
    mark_dashboard_data_refreshed()


def main() -> None:
    config = load_config(str(PROJECT_ROOT / ".env"))
    refresh_seconds = dashboard_refresh_seconds(config.dashboard_auto_refresh_seconds)
    st.set_page_config(page_title=PAGE_TITLE, layout="wide")
    inject_styles()
    header_group, refresh_col = st.columns([0.80, 0.20], gap="small", vertical_alignment="center")
    with header_group:
        title_col, toggle_col, _ = st.columns([0.30, 0.18, 0.52], gap="small", vertical_alignment="center")
        with title_col:
            st.markdown("<div class='top-title-row'><div class='toolbar-title'>Binance Alpha / BSC 监控面板</div></div>", unsafe_allow_html=True)
        with toggle_col:
            only_market = st.toggle("只看行情数据", value=True, help=ONLY_MARKET_DATA_HELP)
    with refresh_col:
        if refresh_seconds > 0:
            render_refresh_badge_countdown(refresh_seconds)
        else:
            render_refresh_badge(refresh_seconds)

    # Keep the detail area out of Streamlit fragments; fragment reruns can retain
    # stale tab output when the selected pair and view change quickly.
    render_dashboard_content(config, only_market)


if __name__ == "__main__":
    main()
