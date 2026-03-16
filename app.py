import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import timedelta

# ==========================================
# 1. 頁面設定與交易邏輯文字展示
# ==========================================
st.set_page_config(page_title="Sweep & Flip Trading Strategy", layout="wide")

st.title("📈 Sweep & Flip Protocol (Smart Money Strategy)")

st.markdown("""
### 🧠 畫面上的交易邏輯 (Trading Logic)
本程式完美復刻影片中的 **Sweep & Flip Protocol** 策略，專注於機構在倫敦開盤時的流動性獵殺。

1. **時間級別**：主要進場點位看 `15分鐘線(15m)`，大趨勢過濾看 `1小時線(1H)`。
2. **亞洲盤區間 (Asian Range)**：記錄 UTC 00:00 - 08:00 的最高點與最低點（流動性池）。
3. **倫敦盤掃蕩 (London Sweep)**：
   * **做多 (Bullish)**：價格在倫敦盤跌破亞洲盤低點，但15m K線收盤又**收回低點之上**。且1H級別為上漲趨勢。
   * **做空 (Bearish)**：價格在倫敦盤突破亞洲盤高點，但15m K線收盤又**收回高點之下**。且1H級別為下跌趨勢。
4. **止損止盈 (SL & TP)**：
   * **進場 (Entry)**：掃蕩K線收盤價。
   * **止損 (SL)**：放在掃蕩K線的最極端(最高或最低點)。
   * **止盈 (TP)**：嚴格執行最低 **1:2 的盈虧比 (R:R Ratio)**。
""")

# ==========================================
# 2. 側邊欄設定
# ==========================================
st.sidebar.header("⚙️ 交易參數設定")

# 選擇交易對
asset_dict = {
    "Bitcoin (BTC/USD)": "BTC-USD",
    "Gold (XAU/USD)": "GC=F",
    "Euro (EUR/USD)": "EURUSD=X"
}
selected_asset = st.sidebar.selectbox("選擇交易商品", list(asset_dict.keys()))
ticker = asset_dict[selected_asset]

# 選擇天數 (yfinance 15m 資料最多只能抓前 60 天)
days_to_fetch = st.sidebar.slider("載入過去天數資料", min_value=1, max_value=59, value=7)

# ==========================================
# 3. 資料抓取與策略運算引擎
# ==========================================
@st.cache_data(ttl=900) # 緩存15分鐘
def get_data_and_signals(ticker, days):
    # 抓取 15m 與 1h 資料
    df_15m = yf.download(ticker, period=f"{days}d", interval="15m", progress=False)
    df_1h = yf.download(ticker, period=f"{days}d", interval="1h", progress=False)
    
    # 處理 MultiIndex 欄位 (yfinance 最新版本的改變)
    if isinstance(df_15m.columns, pd.MultiIndex):
        df_15m.columns = df_15m.columns.get_level_values(0)
        df_1h.columns = df_1h.columns.get_level_values(0)
    
    # 計算 1H 趨勢 (簡單使用 20 EMA)
    df_1h['1H_EMA20'] = df_1h['Close'].ewm(span=20, adjust=False).mean()
    df_1h['Trend'] = 'Neutral'
    df_1h.loc[df_1h['Close'] > df_1h['1H_EMA20'], 'Trend'] = 'Bullish'
    df_1h.loc[df_1h['Close'] < df_1h['1H_EMA20'], 'Trend'] = 'Bearish'
    
    # 將 1H 趨勢映射到 15m (向前填充)
    df_15m['1H_Trend'] = df_15m.index.floor('h').map(df_1h['Trend']).ffill()
    
    # 初始化欄位
    df_15m['Asian_High'] = None
    df_15m['Asian_Low'] = None
    df_15m['Signal'] = None
    df_15m['Entry'] = None
    df_15m['SL'] = None
    df_15m['TP'] = None
    
    # 轉換為 UTC 小時
    df_15m['Hour'] = df_15m.index.hour
    
    # 依日期分組找出亞洲盤區間
    dates = df_15m.index.date
    unique_dates = pd.Series(dates).unique()
    
    signals =[]

    for d in unique_dates:
        # 取出當天的資料
        day_data = df_15m[df_15m.index.date == d]
        
        # 亞洲盤 (00:00 - 07:59)
        asian_session = day_data[day_data['Hour'] < 8]
        if asian_session.empty:
            continue
            
        asian_high = float(asian_session['High'].max())
        asian_low = float(asian_session['Low'].min())
        
        # 標記全天亞洲盤區間
        df_15m.loc[day_data.index, 'Asian_High'] = asian_high
        df_15m.loc[day_data.index, 'Asian_Low'] = asian_low
        
        # 倫敦盤尋找掃蕩 (08:00 - 15:00)
        london_session = day_data[(day_data['Hour'] >= 8) & (day_data['Hour'] <= 15)]
        
        for idx, row in london_session.iterrows():
            # 做空條件 (Bearish Sweep)
            # 1. 最高點突破亞洲高點
            # 2. 收盤價收回亞洲高點之下
            # 3. 1H趨勢偏空
            if float(row['High']) > asian_high and float(row['Close']) < asian_high and row['1H_Trend'] == 'Bearish':
                entry = float(row['Close'])
                sl = float(row['High']) # 設在掃蕩高點
                risk = sl - entry
                if risk <= 0: continue
                tp = entry - (risk * 2) # 1:2 盈虧比
                
                df_15m.at[idx, 'Signal'] = 'Sell'
                df_15m.at[idx, 'Entry'] = entry
                df_15m.at[idx, 'SL'] = sl
                df_15m.at[idx, 'TP'] = tp
                signals.append({'Time': idx, 'Type': 'Sell 🔴', 'Entry': entry, 'SL': sl, 'TP': tp})
                break # 一天只取一個訊號
                
            # 做多條件 (Bullish Sweep)
            # 1. 最低點跌破亞洲低點
            # 2. 收盤價收回亞洲低點之上
            # 3. 1H趨勢偏多
            elif float(row['Low']) < asian_low and float(row['Close']) > asian_low and row['1H_Trend'] == 'Bullish':
                entry = float(row['Close'])
                sl = float(row['Low']) # 設在掃蕩低點
                risk = entry - sl
                if risk <= 0: continue
                tp = entry + (risk * 2) # 1:2 盈虧比
                
                df_15m.at[idx, 'Signal'] = 'Buy'
                df_15m.at[idx, 'Entry'] = entry
                df_15m.at[idx, 'SL'] = sl
                df_15m.at[idx, 'TP'] = tp
                signals.append({'Time': idx, 'Type': 'Buy 🟢', 'Entry': entry, 'SL': sl, 'TP': tp})
                break
                
    return df_15m, signals

# 執行策略
df, trade_signals = get_data_and_signals(ticker, days_to_fetch)

# ==========================================
# 4. 圖表繪製與 UI 展示
# ==========================================
if not df.empty:
    st.subheader(f"📊 {selected_asset} - 15分鐘線圖 (附帶止損止盈標示)")
    
    fig = go.Figure()

    # 1. 畫 K線圖
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'],
        name='Price'
    ))

    # 2. 畫出交易訊號與 SL/TP 標示
    for sig in trade_signals:
        t = sig['Time']
        entry = sig['Entry']
        sl = sig['SL']
        tp = sig['TP']
        
        # 繪製進場箭頭
        if sig['Type'] == 'Buy 🟢':
            fig.add_annotation(x=t, y=df.loc[t, 'Low'], text="⬆ BUY", showarrow=True, arrowhead=1, arrowcolor="green", arrowsize=2, font=dict(color="green", size=14), ay=40)
        else:
            fig.add_annotation(x=t, y=df.loc[t, 'High'], text="⬇ SELL", showarrow=True, arrowhead=1, arrowcolor="red", arrowsize=2, font=dict(color="red", size=14), ay=-40)
            
        # 畫水平線表示 SL 和 TP (向右畫幾根K線的長度)
        end_time = t + pd.Timedelta(hours=4) # 畫長一點方便看
        
        # SL 線 (紅色虛線)
        fig.add_shape(type="line", x0=t, y0=sl, x1=end_time, y1=sl,
                      line=dict(color="red", width=2, dash="dash"))
        fig.add_annotation(x=end_time, y=sl, text=f"SL: {sl:.4f}", showarrow=False, font=dict(color="red"))
        
        # TP 線 (綠色虛線)
        fig.add_shape(type="line", x0=t, y0=tp, x1=end_time, y1=tp,
                      line=dict(color="green", width=2, dash="dash"))
        fig.add_annotation(x=end_time, y=tp, text=f"TP: {tp:.4f}", showarrow=False, font=dict(color="green"))
        
        # Entry 線 (藍色實線)
        fig.add_shape(type="line", x0=t, y0=entry, x1=end_time, y1=entry,
                      line=dict(color="blue", width=1))

    # 隱藏週末/非交易時間的空白 (針對黃金/歐元)
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    
    fig.update_layout(
        height=700,
        template="plotly_dark",
        xaxis_title="Time (UTC)",
        yaxis_title="Price",
        margin=dict(l=0, r=0, t=30, b=0)
    )
    
    # 顯示圖表
    st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # 5. 表格列出近期交易訊號
    # ==========================================
    st.subheader("📋 最近交易訊號紀錄")
    if trade_signals:
        sig_df = pd.DataFrame(trade_signals)
        sig_df['Time'] = sig_df['Time'].dt.strftime('%Y-%m-%d %H:%M')
        # 調整顯示順序
        sig_df = sig_df[['Time', 'Type', 'Entry', 'SL', 'TP']]
        st.dataframe(sig_df.style.format({
            "Entry": "{:.5f}",
            "SL": "{:.5f}",
            "TP": "{:.5f}"
        }), use_container_width=True)
    else:
        st.info("在選定的時間範圍內，沒有偵測到符合 Sweep & Flip Protocol 的交易訊號。")

else:
    st.error("無法抓取資料，請稍後再試。")
