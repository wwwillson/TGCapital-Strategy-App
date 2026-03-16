import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as plotly_go

# ==========================================
# 1. 網頁基本設定 & 顯示交易邏輯
# ==========================================
st.set_page_config(page_title="SMC 交易策略儀表板", layout="wide")

st.title("📈 SMC (FVG + BOS) 交易策略訊號儀表板")
st.markdown("""
### 🧠 畫面上交易邏輯 (參考影片策略)：
1. **Step 1: 尋找高勝率區間** 
   - 尋找價格產生 **結構破壞 (BOS)** 的強烈推動。
   - 確認該推動產生了 **失衡區 (FVG / Fair Value Gap)** (第一根K線的高/低點與第三根K線的高/低點不重疊)。
2. **Step 2: 等待回踩與尊重 (Respect)**
   - 等待價格回調進入 FVG 區間。
   - K 線的**實體 (Body)** 不能收盤超過 FVG 的邊界 (代表市場尊重該失衡區)。
3. **Step 3: 進場確認與設定**
   - 等待一根 K 線順勢**收盤於 FVG 之外**作為確認訊號。
   - **止損 (SL)** 設在確認K線的極值或近期高低點。
   - **止盈 (TP)** 設定為 1:2 的盈虧比 (Risk-Reward Ratio)。
""")

# ==========================================
# 2. 側邊欄設定 (資產與時間週期)
# ==========================================
st.sidebar.header("⚙️ 參數設定")
assets = {
    "Bitcoin (BTC/USD)": "BTC-USD",
    "Gold (XAU/USD)": "GC=F",
    "Euro (EUR/USD)": "EURUSD=X"
}
selected_asset = st.sidebar.selectbox("選擇交易商品", list(assets.keys()))
ticker = assets[selected_asset]

timeframes = {"15 分鐘": "15m", "1 小時": "1h", "4 小時": "4h", "1 天": "1d"}
selected_tf = st.sidebar.selectbox("選擇時間週期", list(timeframes.keys()))
interval = timeframes[selected_tf]

# 下載資料 (加上 MultiIndex 修正與週期防呆)
@st.cache_data(ttl=300)
def load_data(ticker, interval):
    # yfinance 對歷史資料有限制：15m 最多 60天 (用 59d 防呆)，1h 最多 730天
    if interval == "15m":
        period = "59d"
    elif interval in ["1h", "4h"]:
        period = "730d"
    else:
        period = "1y"
        
    df = yf.download(ticker, period=period, interval=interval)
    
    if df.empty:
        return df

    # 🟢 核心修復：處理 yfinance 新版本的 MultiIndex 欄位問題
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    df.dropna(inplace=True)
    return df

with st.spinner("載入市場數據中..."):
    df = load_data(ticker, interval)

# 🟢 防呆機制：如果 Yahoo Finance 沒傳回數據，顯示提示並停止執行
if df.empty:
    st.warning(f"⚠️ 無法獲取 {selected_asset} 在 {selected_tf} 週期的數據。請嘗試選擇其他商品或時間週期。")
    st.stop()

# ==========================================
# 3. 核心演算法：尋找 FVG 與模擬進場訊號
# ==========================================
def detect_smc_signals(df):
    df = df.copy()
    df['FVG_Bull'] = False
    df['FVG_Bear'] = False
    df['Signal'] = None
    df['Entry'] = 0.0
    df['SL'] = 0.0
    df['TP'] = 0.0
    
    # 掃描資料尋找 FVG 與進場訊號 (簡化版演算法)
    for i in range(2, len(df) - 2):
        # Bullish FVG (看漲失衡區): K線1的High < K線3的Low
        if df['High'].iloc[i-2] < df['Low'].iloc[i]:
            fvg_top = df['Low'].iloc[i]
            fvg_bottom = df['High'].iloc[i-2]
            
            # 檢查後續幾根K線是否回踩且收盤於FVG外 (Step 2 & 3)
            if df['Low'].iloc[i+1] <= fvg_top and df['Close'].iloc[i+1] > fvg_top:
                # 產生做多訊號
                entry_price = float(df['Close'].iloc[i+1])
                sl_price = float(df['Low'].iloc[i+1] - (entry_price * 0.001)) # 止損設在信號K線低點下方
                risk = entry_price - sl_price
                tp_price = float(entry_price + (risk * 2)) # 1:2 盈虧比
                
                df.at[df.index[i+1], 'Signal'] = 'BUY'
                df.at[df.index[i+1], 'Entry'] = entry_price
                df.at[df.index[i+1], 'SL'] = sl_price
                df.at[df.index[i+1], 'TP'] = tp_price

        # Bearish FVG (看跌失衡區): K線1的Low > K線3的High
        elif df['Low'].iloc[i-2] > df['High'].iloc[i]:
            fvg_top = df['Low'].iloc[i-2]
            fvg_bottom = df['High'].iloc[i]
            
            # 檢查回踩
            if df['High'].iloc[i+1] >= fvg_bottom and df['Close'].iloc[i+1] < fvg_bottom:
                # 產生做空訊號
                entry_price = float(df['Close'].iloc[i+1])
                sl_price = float(df['High'].iloc[i+1] + (entry_price * 0.001)) # 止損設在信號K線高點上方
                risk = sl_price - entry_price
                tp_price = float(entry_price - (risk * 2)) # 1:2 盈虧比
                
                df.at[df.index[i+1], 'Signal'] = 'SELL'
                df.at[df.index[i+1], 'Entry'] = entry_price
                df.at[df.index[i+1], 'SL'] = sl_price
                df.at[df.index[i+1], 'TP'] = tp_price

    return df

df_signals = detect_smc_signals(df)

# ==========================================
# 4. 最新訊號提示區塊
# ==========================================
recent_signals = df_signals.dropna(subset=['Signal']).tail(1)

st.subheader(f"🚨 最新交易訊號 ({selected_asset})")
if not recent_signals.empty:
    sig_time = recent_signals.index[0]
    sig_type = recent_signals['Signal'].values[0]
    entry = float(recent_signals['Entry'].values[0])
    sl = float(recent_signals['SL'].values[0])
    tp = float(recent_signals['TP'].values[0])
    
    color = "green" if sig_type == 'BUY' else "red"
    st.markdown(f"""
    <div style="background-color:rgba({0 if sig_type=='BUY' else 255}, {255 if sig_type=='BUY' else 0}, 0, 0.1); padding:20px; border-radius:10px; border-left: 5px solid {color};">
        <h3 style="color:{color}; margin-top:0;">{sig_type} 訊號觸發!</h3>
        <b>時間:</b> {sig_time.strftime('%Y-%m-%d %H:%M')}<br>
        <b>進場價位 (Entry):</b> ${entry:.2f}<br>
        <b>止損價位 (SL):</b> ${sl:.2f}<br>
        <b>止盈價位 (TP):</b> ${tp:.2f} (盈虧比 1:2)
    </div>
    """, unsafe_allow_html=True)
else:
    st.info("目前所選週期尚未出現符合 FVG 策略的最新訊號。")

# ==========================================
# 5. 繪製圖表 (包含K線、訊號標示、止損止盈線)
# ==========================================
st.subheader("📊 互動式圖表")

# 為了畫面簡潔，只繪製最後 150 根 K 線
plot_df = df_signals.tail(150)

fig = plotly_go.Figure()

# 繪製 K 線圖
fig.add_trace(plotly_go.Candlestick(
    x=plot_df.index,
    open=plot_df['Open'],
    high=plot_df['High'],
    low=plot_df['Low'],
    close=plot_df['Close'],
    name='K線'
))

# 在圖表上標記訊號、止損、止盈
for idx, row in plot_df.dropna(subset=['Signal']).iterrows():
    if row['Signal'] == 'BUY':
        # 標註 BUY 箭頭
        fig.add_annotation(x=idx, y=row['Low'], text="⬆ BUY", showarrow=True, arrowhead=1, arrowcolor="green", font=dict(color="green", size=14), ay=30)
    else:
        # 標註 SELL 箭頭
        fig.add_annotation(x=idx, y=row['High'], text="⬇ SELL", showarrow=True, arrowhead=1, arrowcolor="red", font=dict(color="red", size=14), ay=-30)
    
    # 畫止損(SL)虛線
    fig.add_shape(type="line", x0=idx, y0=row['SL'], x1=plot_df.index[-1], y1=row['SL'],
                  line=dict(color="red", width=2, dash="dash"))
    fig.add_annotation(x=plot_df.index[-1], y=row['SL'], text=f"SL: {row['SL']:.2f}", showarrow=False, font=dict(color="red", size=12), xanchor="left")

    # 畫止盈(TP)虛線
    fig.add_shape(type="line", x0=idx, y0=row['TP'], x1=plot_df.index[-1], y1=row['TP'],
                  line=dict(color="green", width=2, dash="dash"))
    fig.add_annotation(x=plot_df.index[-1], y=row['TP'], text=f"TP: {row['TP']:.2f}", showarrow=False, font=dict(color="green", size=12), xanchor="left")

# 圖表外觀設定
fig.update_layout(
    xaxis_rangeslider_visible=False,
    template="plotly_dark",
    height=600,
    margin=dict(l=0, r=60, t=30, b=0), # 右側留白給文字
    yaxis_title="價格 (USD)"
)

st.plotly_chart(fig, use_container_width=True)

st.caption("聲明：SMC 策略之結構破壞(BOS)判定具主觀性，本程式採用簡化版 FVG 演算法生成提示，僅供學習參考，不構成投資建議。")
