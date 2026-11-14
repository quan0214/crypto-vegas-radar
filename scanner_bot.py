from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import pandas as pd
import pandas_ta as ta
import requests

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
TIMEFRAME = "15m"
BINANCE_BASE_URL = "https://data-api.binance.vision/api/v3"


def send_discord_message(title, description, color=15158332):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD_WEBHOOK_URL，略過發送")
        return
    payload = {
        "embeds": [{"title": title, "description": description, "color": color}]
    }
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            data=json.dumps(payload),
            headers=headers,
            timeout=5,
        )
    except Exception as e:
        print(f"❌ Discord 傳送失敗: {e}")


def get_binance_usdt_pairs():
    url = f"{BINANCE_BASE_URL}/exchangeInfo"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        return [
            s["symbol"]
            for s in data["symbols"]
            if s["status"] == "TRADING"
            and s["quoteAsset"] == "USDT"
            and not s["symbol"].endswith(("UPUSDT", "DOWNUSDT"))
        ]
    except Exception as e:
        print(f"❌ 獲取交易對失敗: {e}")
        return []


def check_symbol(symbol):
    try:
        url = f"{BINANCE_BASE_URL}/klines?symbol={symbol}&interval={TIMEFRAME}&limit=750"
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
        df["Close"] = df["Close"].astype(float)
        df["High"] = df["High"].astype(float)
        df["Low"] = df["Low"].astype(float)

        df["EMA144"] = ta.ema(df["Close"], length=144)
        df["EMA169"] = ta.ema(df["Close"], length=169)
        df["EMA576"] = ta.ema(df["Close"], length=576)
        df["EMA676"] = ta.ema(df["Close"], length=676)

        df = df.dropna(subset=["EMA676"])
        if df.empty:
            return None

        latest = df.iloc[-1]
        current_price = float(latest["Close"])
        high_price = float(latest["High"])
        low_price = float(latest["Low"])

        small_max = max(float(latest["EMA144"]), float(latest["EMA169"]))
        small_min = min(float(latest["EMA144"]), float(latest["EMA169"]))
        big_max = max(float(latest["EMA576"]), float(latest["EMA676"]))
        big_min = min(float(latest["EMA576"]), float(latest["EMA676"]))

        long_cond1 = small_min > big_max
        is_touch_small_l = (low_price <= (small_max * 1.005)) and (
            current_price >= small_min
        )
        is_touch_big_l = (low_price <= (big_max * 1.005)) and (
            current_price >= big_min
        )

        if long_cond1 and (is_touch_small_l or is_touch_big_l):
            channel = (
                "小通道 (144/169)" if is_touch_small_l else "大通道 (576/676)"
            )
            return ("LONG", symbol, current_price, low_price, channel)

        short_cond1 = small_max < big_min
        is_touch_small_s = (high_price >= (small_min * 0.995)) and (
            current_price <= small_max
        )
        is_touch_big_s = (high_price >= (big_min * 0.995)) and (
            current_price <= big_max
        )

        if short_cond1 and (is_touch_small_s or is_touch_big_s):
            channel = (
                "小通道 (144/169)" if is_touch_small_s else "大通道 (576/676)"
            )
            return ("SHORT", symbol, current_price, high_price, channel)

    except Exception:
        return None
    return None


if __name__ == "__main__":
    print(f"==== 📈 全市場維加斯雷達啟動 ({TIMEFRAME}) ====")
    all_cryptos = get_binance_usdt_pairs()

    long_hits = []
    short_hits = []

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {
            executor.submit(check_symbol, sym): sym for sym in all_cryptos
        }
        for future in as_completed(futures):
            res = future.result()
            if res:
                if res[0] == "LONG":
                    long_hits.append(res)
                else:
                    short_hits.append(res)

    for _, symbol, price, low_p, channel in long_hits:
        msg = (
            f"**交易對:** {symbol}\n**收盤價:** `{price:.4f}` | **最低價:**"
            f" `{low_p:.4f}`\n🟢 **作多回踩支撐** ({channel})"
        )
        send_discord_message(
            f"📌 [多頭回踩支撐] {symbol} ({TIMEFRAME})", msg, color=3066993
        )

    for _, symbol, price, high_p, channel in short_hits:
        msg = (
            f"**交易對:** {symbol}\n**收盤價:** `{price:.4f}` | **最高價:**"
            f" `{high_p:.4f}`\n🔴 **作空反彈遇阻** ({channel})"
        )
        send_discord_message(
            f"⚠️ [空頭反彈遇阻] {symbol} ({TIMEFRAME})", msg, color=15158332
        )

    print(
        f"🏁 掃描完畢！多頭: {len(long_hits)} 檔 | 空頭: {len(short_hits)} 檔"
    )