from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="加密貨幣維加斯多空雷達", layout="wide")
st.title("📡 加密貨幣維加斯【多/空】純回踩雷達")

# 讀取 URL 網址參數（例如 ?symbol=SOLUSDT&timeframe=15m）
query_params = st.query_params
url_symbol = query_params.get("symbol", None)
url_timeframe = query_params.get("timeframe", None)

timeframe_options = ["15m", "1h", "4h", "1d"]
default_tf_index = (
    timeframe_options.index(url_timeframe)
    if url_timeframe in timeframe_options
    else 1
)

# 嘗試從 Streamlit Secrets 自動讀取預設 Webhook
default_webhook = st.secrets.get("DISCORD_WEBHOOK_URL", "")

with st.sidebar:
    st.header("⚙️ 掃描參數設定")
    timeframe = st.selectbox(
        "K 線時間級別", timeframe_options, index=default_tf_index
    )
    scan_limit = st.slider(
        "掃描成交量前 N 大幣種", min_value=10, max_value=100, value=30, step=10
    )
    enable_discord = st.checkbox("同時推播結果至 Discord", value=False)
    webhook_url = st.text_input(
        "Discord Webhook URL",
        value=default_webhook,
        type="password",
        help="已設定 Streamlit Secrets 時會自動帶入，無需手動輸入",
    )

BINANCE_BASE_URL = "https://data-api.binance.vision/api/v3"


@st.cache_data(ttl=300)
def get_top_usdt_pairs(limit=30):
    url = f"{BINANCE_BASE_URL}/ticker/24hr"
    res = requests.get(url, timeout=10)
    data = res.json()
    usdt_pairs = [
        item
        for item in data
        if item["symbol"].endswith("USDT")
        and not item["symbol"].endswith(("UPUSDT", "DOWNUSDT", "USDCUSDT"))
    ]
    usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return [item["symbol"] for item in usdt_pairs[:limit]]


@st.cache_data(ttl=60)
def analyze_symbol(symbol, timeframe):
    try:
        url = f"{BINANCE_BASE_URL}/klines?symbol={symbol}&interval={timeframe}&limit=750"
        res = requests.get(url, timeout=5)
        kline_data = res.json()
        if not isinstance(kline_data, list) or len(kline_data) < 676:
            return None

        columns = [
            "timestamp",
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
            "Close_time",
            "Quote_av",
            "Trades",
            "Tb_base_av",
            "Tb_quote_av",
            "Ignore",
        ]
        df = pd.DataFrame(kline_data, columns=columns)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = df[col].astype(float)

        df["EMA144"] = df["Close"].ewm(span=144, adjust=False).mean()
        df["EMA169"] = df["Close"].ewm(span=169, adjust=False).mean()
        df["EMA576"] = df["Close"].ewm(span=576, adjust=False).mean()
        df["EMA676"] = df["Close"].ewm(span=676, adjust=False).mean()

        latest = df.iloc[-1]
        current_price = float(latest["Close"])
        high_price = float(latest["High"])
        low_price = float(latest["Low"])

        small_max = max(float(latest["EMA144"]), float(latest["EMA169"]))
        small_min = min(float(latest["EMA144"]), float(latest["EMA169"]))
        big_max = max(float(latest["EMA576"]), float(latest["EMA676"]))
        big_min = min(float(latest["EMA576"]), float(latest["EMA676"]))

        signal_type = None
        channel_type = None

        long_cond1 = small_min > big_max
        is_touch_small_l = (low_price <= (small_max * 1.005)) and (
            current_price >= small_min
        )
        is_touch_big_l = (low_price <= (big_max * 1.005)) and (
            current_price >= big_min
        )

        short_cond1 = small_max < big_min
        is_touch_small_s = (high_price >= (small_min * 0.995)) and (
            current_price <= small_max
        )
        is_touch_big_s = (high_price >= (big_min * 0.995)) and (
            current_price <= big_max
        )

        if long_cond1 and (is_touch_small_l or is_touch_big_l):
            signal_type = "🟢 作多回踩支撐"
            channel_type = (
                "小通道 (144/169)" if is_touch_small_l else "大通道 (576/676)"
            )
        elif short_cond1 and (is_touch_small_s or is_touch_big_s):
            signal_type = "🔴 作空反彈遇阻"
            channel_type = (
                "小通道 (144/169)" if is_touch_small_s else "大通道 (576/676)"
            )

        return {
            "symbol": symbol,
            "price": current_price,
            "high": high_price,
            "low": low_price,
            "signal": signal_type,
            "channel": channel_type,
            "df": df.tail(150),
        }
    except Exception:
        return None


@st.cache_data(ttl=60)
def run_market_scan(timeframe, limit):
    symbols = get_top_usdt_pairs(limit)
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(analyze_symbol, sym, timeframe): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
    return results


def render_vegas_chart(target_data, tf):
    """繪製單一幣種維加斯通道互動式 K 線圖"""
    chart_df = target_data["df"]
    status_label = (
        target_data["signal"] if target_data["signal"] else "👀 觀察中"
    )
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=chart_df["timestamp"],
            open=chart_df["Open"],
            high=chart_df["High"],
            low=chart_df["Low"],
            close=chart_df["Close"],
            name="K線",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=chart_df["timestamp"],
            y=chart_df["EMA144"],
            line=dict(color="#00e676", width=1.2),
            name="EMA 144",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=chart_df["timestamp"],
            y=chart_df["EMA169"],
            line=dict(color="#00b0ff", width=1.2),
            name="EMA 169",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=chart_df["timestamp"],
            y=chart_df["EMA576"],
            line=dict(color="#ff9100", width=1.5),
            name="EMA 576",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=chart_df["timestamp"],
            y=chart_df["EMA676"],
            line=dict(color="#ff1744", width=1.5),
            name="EMA 676",
        )
    )
    fig.update_layout(
        title=f"{target_data['symbol']} ({tf}) - {status_label}",
        xaxis_rangeslider_visible=False,
        height=550,
        template="plotly_dark",
    )
    st.plotly_chart(fig, use_container_width=True)


# --- 1. 若網址帶有 ?symbol=XXX，優先直接顯示該幣種專屬圖表 ---
if url_symbol:
    clean_sym = url_symbol.upper().strip()
    col_info, col_clear = st.columns([4, 1])
    with col_info:
        st.subheader(f"🎯 推播直達檢視：{clean_sym} ({timeframe})")
    with col_clear:
        if st.button("❌ 關閉單幣檢視"):
            st.query_params.clear()
            st.rerun()

    direct_data = analyze_symbol(clean_sym, timeframe)
    if direct_data:
        m1, m2, m3 = st.columns(3)
        m1.metric("當前收盤價", f"{direct_data['price']:.4f}")
        m2.metric("當根最高 / 最低", f"{direct_data['high']:.4f} / {direct_data['low']:.4f}")
        m3.metric(
            "當前訊號狀態",
            direct_data["signal"] if direct_data["signal"] else "無觸發",
        )
        render_vegas_chart(direct_data, timeframe)
    else:
        st.warning(f"無法取得 {clean_sym} 的足夠 K 線資料（可能為新上架幣種）。")
    st.divider()

# --- 2. 全市場熱門幣種掃描儀表板 ---
if st.button("🔄 立即重新掃描市場", type="primary"):
    st.cache_data.clear()

with st.spinner(
    f"正在平行掃描前 {scan_limit} 大加密貨幣 ({timeframe} 級別)..."
):
    all_results = run_market_scan(timeframe, scan_limit)

triggered = [r for r in all_results if r["signal"] is not None]
long_list = [r for r in triggered if "作多" in r["signal"]]
short_list = [r for r in triggered if "作空" in r["signal"]]

col1, col2, col3 = st.columns(3)
col1.metric("掃描幣種總數", f"{len(all_results)} 檔")
col2.metric("🟢 多頭回踩支撐", f"{len(long_list)} 檔")
col3.metric("🔴 空頭反彈遇阻", f"{len(short_list)} 檔")

if enable_discord and webhook_url and triggered:
    desc = "\n".join([
        f"**{r['symbol']}** | {r['signal']} | {r['channel']} | 現價: `{r['price']:.4f}`"
        for r in triggered
    ])
    payload = {
        "embeds": [{
            "title": f"📡 網頁雷達掃描結果 ({timeframe})",
            "description": desc,
            "color": 3066993,
        }]
    }
    requests.post(
        webhook_url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    st.toast("✅ 已將符合條件的訊號發送至 Discord！")

st.divider()

if triggered:
    st.subheader("📋 熱門幣種符合維加斯通道回踩清單")
    table_data = [
        {
            "交易對": r["symbol"],
            "訊號方向": r["signal"],
            "觸及通道": r["channel"],
            "當前收盤價": round(r["price"], 4),
            "當根最高價": round(r["high"], 4),
            "當根最低價": round(r["low"], 4),
        }
        for r in triggered
    ]
    st.dataframe(pd.DataFrame(table_data), use_container_width=True)

    selected_sym = st.selectbox(
        "📊 選擇清單內標的檢視維加斯通道圖表：", [r["symbol"] for r in triggered]
    )
    target_data = next(r for r in triggered if r["symbol"] == selected_sym)
    render_vegas_chart(target_data, timeframe)
else:
    st.info(
        f"目前前 {scan_limit} 大幣種在 {timeframe} 級別下，暫無符合維加斯回踩條件的標的。"
    )
