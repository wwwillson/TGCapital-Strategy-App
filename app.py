import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import datetime

# --- 頁面設定 ---
st.set_page_config(page_title="TG Capital 三叉戟交易策略分析", layout="wide")
st.title("🔱 TG Capital: 倫敦殺戮區 30分K 三叉戟交易策略 (Trident Pattern)")

# --- 側邊欄參數設定 ---
st.sidebar.header("參數設定")

# 1. 改為下拉選單 (Selectbox)
ticker_mapping = {
    "Bitcoin vs US Dollar": "BTC-USD",
    "Gold vs US Dollar": "GC=F",
    "Euro vs US Dollar": "EURUSD=X"
}
selected_asset = st.sidebar.selectbox("選擇商品", options=list(ticker_mapping.keys()))
ticker = ticker_mapping[selected_asset]

days = st.sidebar.slider("抓取天數 (yfinance 30mK線最多60天)", 5, 60, 30)

# --- 策略邏輯說明 ---
st.markdown("""
### 📜 交易邏輯說明 (根據影片整理)
1. **交易時間**：紐約時間 03:00 AM - 06:30 AM (London Kill Zone)。
2. **均線過濾**：EMA 5, 9, 13, 21 必須呈現完美多頭排列 (5 > 9 > 13 > 21)。
3. **價格行為 (Trident Pattern)**：
   * 出現 **十字星 (Doji)** 或長下影線 (模擬回踩 FVG 50% 拒絕)。
   * **確認信號**：十字星的下一根 K 線，其 **收盤價必須高於** 十字星的最高價。
4. **出場設置**：
   * **止損 (SL)**：十字星的最低點下方。
   * **止盈 (TP)**：預設 1:10 盈虧比 (影片中提倡高盈虧比 1:20 甚至 1:50)。
---
""")

# --- 獲取資料函數 (加入快取時間與錯誤處理) ---
@st.cache_data(ttl=3600) # 快取 1 小時，避免頻繁請求觸發 Rate Limit
def load_data(ticker, days):
    try:
        # 抓取 30 分鐘 K 線
        df = yf.download(ticker, period=f"{days}d", interval="30m")
        
        if df is None or df.empty:
            return None, "找不到該商品的資料，或已達到 Yahoo Finance 請求上限，請稍後再試。"
        
        # 處理新版 yfinance 可能回傳 MultiIndex 欄位的問題
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df.dropna(inplace=True)
        
        # 計算 EMA
        df['EMA_5'] = df['Close'].ewm(span=5, adjust=False).mean()
        df['EMA_9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA_13'] = df['Close'].ewm(span=13, adjust=False).mean()
        df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
        
        # 判斷多頭排列
        df['EMA_Stacked_Bull'] = (df['EMA_5'] > df['EMA_9']) & (df['EMA_9'] > df['EMA_13']) & (df['EMA_13'] > df['EMA_21'])
        
        # 計算 K 線特徵
        df['Body'] = abs(df['Close'] - df['Open'])
        df['Lower_Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
        df['Upper_Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
        
        # 定義十字星 / 長下影線 (下影線大於實體2倍，且大於上影線)
        df['Is_Doji_Pinbar'] = (df['Lower_Wick'] > df['Body'] * 2) & (df['Lower_Wick'] > df['Upper_Wick'])
        
        return df, None
        
    except Exception as e:
        return None, f"Yahoo Finance API 發生錯誤 (可能是請求過於頻繁): {str(e)}"

# --- 產生交易信號 ---
def generate_signals(df):
    signals =[]
    # 為了避免迴圈越界，迴圈到最後第二根
    for i in range(1, len(df) - 1):
        prev_candle = df.iloc[i]
        curr_candle = df.iloc[i+1]
        
        # 1. 確保在前一根K線均線多頭排列
        if not prev_candle['EMA_Stacked_Bull']:
            continue
            
        # 2. 前一根必須是 十字星/長下影線 (Doji)
        if not prev_candle['Is_Doji_Pinbar']:
            continue
            
        # 3. 確認信號：當前K線收盤必須「大於」十字星的最高點
        if curr_candle['Close'] > prev_candle['High']:
            
            entry_price = curr_candle['Close']
            stop_loss = prev_candle['Low'] # 止損放在十字星低點
            risk = entry_price - stop_loss
            
            if risk <= 0: continue
                
            take_profit = entry_price + (risk * 10) # 預設 1:10 盈虧比
            
            signals.append({
                'Time': df.index[i+1],
                'Entry': entry_price,
                'SL': stop_loss,
                'TP': take_profit,
                'Doji_Time': df.index[i]
            })
            
    return signals

# --- 執行與繪圖 ---
with st.spinner('正在從 Yahoo Finance 獲取數據，請稍候...'):
    df, error_msg = load_data(ticker, days)

if error_msg:
    # 顯示錯誤訊息 (避免紅字當機)
    st.error(error_msg)
    st.info("💡 提示：如果你頻繁重整頁面，Yahoo Finance 會暫時封鎖你的 IP。請等待約 5~10 分鐘後再試。")
elif df is not None and not df.empty:
    signals = generate_signals(df)
    
    if len(signals) > 0:
        st.success(f"✅ 在圖表中找到了 {len(signals)} 個潛在的三叉戟做多信號！(圖表上以綠色箭頭標示)")
    else:
        st.warning("⚠️ 根據目前邏輯，在選定期間內沒有找到符合條件的信號。")

    # 建立 Plotly K線圖
    fig = go.Figure(data=[go.Candlestick(x=df.index,
                    open=df['Open'], high=df['High'],
                    low=df['Low'], close=df['Close'],
                    name='K線')])

    # 加入 EMA 線
    fig.add_trace(go.Scatter(x=df.index, y=df['EMA_5'], line=dict(color='blue', width=1), name='EMA 5'))
    fig.add_trace(go.Scatter(x=df.index, y=df['EMA_9'], line=dict(color='green', width=1), name='EMA 9'))
    fig.add_trace(go.Scatter(x=df.index, y=df['EMA_13'], line=dict(color='orange', width=1), name='EMA 13'))
    fig.add_trace(go.Scatter(x=df.index, y=df['EMA_21'], line=dict(color='red', width=1), name='EMA 21'))

    # 標示信號與止損止盈
    for sig in signals:
        # 標示進場點箭頭
        fig.add_annotation(
            x=sig['Time'], y=sig['Entry'],
            text="BUY", showarrow=True, arrowhead=1,
            arrowcolor="green", arrowsize=2, arrowwidth=2,
            font=dict(color="white", size=10), bgcolor="green"
        )
        
        # 繪製 SL 與 TP 的水平線段 (從進場點向右延伸一點點方便觀看)
        time_entry = sig['Time']
        time_end = time_entry + datetime.timedelta(hours=10) # 往右畫10小時的長度
        
        # 進場線 (白色虛線)
        fig.add_shape(type="line", x0=time_entry, x1=time_end, y0=sig['Entry'], y1=sig['Entry'],
                      line=dict(color="white", width=2, dash="dash"))
        
        # 止損線 (紅色實線)
        fig.add_shape(type="line", x0=time_entry, x1=time_end, y0=sig['SL'], y1=sig['SL'],
                      line=dict(color="red", width=2))
        fig.add_annotation(x=time_end, y=sig['SL'], text=f"SL: {sig['SL']:.2f}", showarrow=False, font=dict(color="red"))
        
        # 止盈線 (綠色實線)
        fig.add_shape(type="line", x0=time_entry, x1=time_end, y0=sig['TP'], y1=sig['TP'],
                      line=dict(color="lightgreen", width=2))
        fig.add_annotation(x=time_end, y=sig['TP'], text=f"TP(1:10): {sig['TP']:.2f}", showarrow=False, font=dict(color="lightgreen"))

    # 圖表排版設定
    fig.update_layout(
        title=f"{selected_asset} ({ticker}) 30分鐘圖 - 三叉戟進場點標示",
        yaxis_title="Price",
        xaxis_title="Time",
        template="plotly_dark",
        height=700,
        xaxis_rangeslider_visible=False
    )
    
    st.plotly_chart(fig, use_container_width=True)

    # 顯示信號數據表
    if len(signals) > 0:
        st.markdown("### 📊 詳細信號數據")
        sig_df = pd.DataFrame(signals)
        # 格式化顯示價格 (保留小數點)
        sig_df['Entry'] = sig_df['Entry'].apply(lambda x: f"{x:.4f}")
        sig_df['SL'] = sig_df['SL'].apply(lambda x: f"{x:.4f}")
        sig_df['TP'] = sig_df['TP'].apply(lambda x: f"{x:.4f}")
        st.dataframe(sig_df)
