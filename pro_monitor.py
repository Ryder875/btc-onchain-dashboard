import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(page_title="BTC 链上指标仪表板", layout="wide")


@st.cache_data(ttl=86400)
def fetch_onchain_data():
    """从 CoinMetrics 免费 API 获取 MVRV 比率与市值历史数据，反推已实现市值"""
    url = (
        "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        "?assets=btc&metrics=CapMVRVCur,CapMrktCurUSD"
        "&frequency=1d&page_size=10000&start_time=2010-01-01"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    df = pd.DataFrame(r.json()["data"])
    df["date"] = pd.to_datetime(df["time"])
    df["mvrv_ratio"] = pd.to_numeric(df["CapMVRVCur"])
    df["market_cap"] = pd.to_numeric(df["CapMrktCurUSD"])
    df["realized_cap"] = df["market_cap"] / df["mvrv_ratio"]

    return df[["date", "mvrv_ratio", "market_cap", "realized_cap"]], datetime.now()


@st.cache_data(ttl=86400)
def fetch_miners_revenue():
    """从 blockchain.info 获取近 2 年每日矿工收入，用于计算 Puell Multiple"""
    url = "https://api.blockchain.info/charts/miners-revenue?format=json&timespan=2years"
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    df = pd.DataFrame(r.json()["values"]).rename(columns={"y": "miners_revenue"})
    df["date"] = pd.to_datetime(df["x"], unit="s").dt.normalize()

    return df[["date", "miners_revenue"]]


@st.cache_data(ttl=86400)
def get_fear_greed():
    """从 alternative.me 获取恐慌贪婪指数"""
    r = requests.get("https://api.alternative.me/fng/", timeout=10).json()
    return int(r["data"][0]["value"]), r["data"][0]["value_classification"]


def calculate_metrics(df_chain, df_miners):
    """根据原始数据计算四大链上指标"""
    # NUPL = 1 - 1/MVRV  (等价于 (MarketCap - RealizedCap) / MarketCap)
    df_chain["nupl"] = 1 - 1 / df_chain["mvrv_ratio"]

    # MVRV Z-Score = (MarketCap - RealizedCap) / ExpandingStd(MarketCap - RealizedCap)
    df_chain["mvrv_diff"] = df_chain["market_cap"] - df_chain["realized_cap"]
    df_chain["mvrv_z_score"] = (
        df_chain["mvrv_diff"] / df_chain["mvrv_diff"].expanding().std()
    )

    # Puell Multiple = DailyRevenue / 365-day MA(DailyRevenue)
    df_miners["puell_ma"] = df_miners["miners_revenue"].rolling(window=365).mean()
    df_miners["puell_multiple"] = df_miners["miners_revenue"] / df_miners["puell_ma"]

    return df_chain, df_miners


# ======================== UI ========================
st.title("📊 BTC 指標儀表板")

if "last_update" in st.session_state:
    next_update = st.session_state["last_update"] + timedelta(days=1)
    st.sidebar.info(
        f"📅 上次數據抓取: {st.session_state['last_update'].strftime('%Y-%m-%d %H:%M')}"
    )
    st.sidebar.info(f"⏳ 下次更新預計: {next_update.strftime('%Y-%m-%d %H:%M')}")
st.sidebar.markdown("---")
st.sidebar.write(
    "本系統已設置為 **24小時** 自動刷新一次。手動點擊瀏覽器刷新不會重複消耗 API 額度。"
)

try:
    df_chain_raw, last_update_time = fetch_onchain_data()
    df_miners_raw = fetch_miners_revenue()
    fng_val, fng_label = get_fear_greed()

    st.session_state["last_update"] = last_update_time

    df_chain, df_miners = calculate_metrics(
        df_chain_raw.copy(), df_miners_raw.copy()
    )

    chain_latest = df_chain.iloc[-1]
    miners_latest = df_miners.dropna(subset=["puell_multiple"]).iloc[-1]

    # ---- 指标面板 ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MVRV Z-Score", f"{chain_latest['mvrv_z_score']:.3f}")
    c2.metric("NUPL", f"{chain_latest['nupl']:.2%}")
    c3.metric("Puell Multiple", f"{miners_latest['puell_multiple']:.3f}")
    c4.metric("恐慌貪婪指數", f"{fng_val}", fng_label)

    # ---- 底部核对清单 ----
    st.subheader("🛠 週期底部判定 (嚴格模式)")
    cols = st.columns(4)
    conditions = [
        ("MVRV Z-Score < 0.1", chain_latest["mvrv_z_score"] < 0.1),
        ("NUPL < 0 (全網虧損)", chain_latest["nupl"] < 0),
        ("Puell < 0.5 (礦工投降)", miners_latest["puell_multiple"] < 0.5),
        ("恐慌指數 < 20", fng_val < 20),
    ]
    for i, (label, status) in enumerate(conditions):
        if status:
            cols[i].success(f"✅ {label}")
        else:
            cols[i].warning(f"❌ {label}")

    # ---- 趋势图 ----
    st.subheader("週期趨勢圖")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_chain["date"],
            y=df_chain["mvrv_z_score"],
            name="MVRV Z-Score",
            line=dict(color="cyan"),
        )
    )
    fig.add_hline(
        y=0.1, line_dash="dash", line_color="green", annotation_text="底部門檻"
    )
    fig.update_layout(template="plotly_dark", height=500)
    st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"運行出錯: {e}")
