import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas_ta as ta
from datetime import datetime, timedelta
import requests
import json
import time
from groq import Groq

# ══════════════════════════════════════════════════════════════════════════════
# 1. 頁面配置與極致深色 CSS 注入
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="美股即時監控系統 PRO", layout="wide", initial_sidebar_state="expanded")

def apply_custom_style():
    st.markdown("""
    <style>
        /* 全域深色背景 */
        .stApp { background-color: #0e1117; color: #e0e0e0; }
        
        /* Metric 卡片自訂 */
        [data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: 700 !important; color: #00ffcc !important; }
        div[data-testid="metric-container"] {
            background-color: #1a1f2c;
            border: 1px solid #2d343f;
            padding: 15px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }

        /* 趨勢卡片 */
        .trend-card {
            padding: 15px;
            border-radius: 8px;
            border-left: 5px solid #00ffcc;
            background: #161b22;
            margin-bottom: 10px;
        }
        
        /* AI 面板 */
        .ai-panel {
            background: linear-gradient(145deg, #1e1e2f, #11111d);
            border: 1px solid #3d3d5c;
            padding: 20px;
            border-radius: 15px;
            color: #ffffff;
        }
        
        /* 狀態條 */
        .status-bar {
            display: flex;
            justify-content: space-between;
            padding: 5px 15px;
            background: #252932;
            border-radius: 20px;
            font-size: 0.8rem;
            margin-bottom: 20px;
        }

        /* 隱藏預設元件 */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

apply_custom_style()

# ══════════════════════════════════════════════════════════════════════════════
# 2. 核心數據處理函數
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=60)
def fetch_stock_data(symbol, interval='5m', period='5d'):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty: return None
        return df
    except:
        return None

def calculate_indicators(df):
    # EMA 系列
    for p in [5, 10, 20, 30, 40, 60, 120, 200]:
        df[f'EMA{p}'] = ta.ema(df['Close'], length=p)
    
    # MA 系列
    df['MA5'] = ta.sma(df['Close'], length=5)
    df['MA15'] = ta.sma(df['Close'], length=15)
    
    # MACD
    macd = ta.macd(df['Close'])
    df = pd.concat([df, macd], axis=1)
    
    # Vol MA
    df['VOL_MA5'] = ta.sma(df['Volume'], length=5)
    
    # Pivot Points (Simple)
    df['Pivot'] = (df['High'].shift(1) + df['Low'].shift(1) + df['Close'].shift(1)) / 3
    return df

# ══════════════════════════════════════════════════════════════════════════════
# 3. 警示與通知系統
# ══════════════════════════════════════════════════════════════════════════════

if 'alerts' not in st.session_state:
    st.session_state.alerts = []

def send_telegram(message):
    token = st.secrets.get("TELEGRAM_BOT_TOKEN")
    chat_id = st.secrets.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
        requests.get(url)

def check_alerts(symbol, df):
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]
    
    msg = ""
    # MACD 金叉
    if prev_row['MACDh_12_26_9'] < 0 and last_row['MACDh_12_26_9'] > 0:
        msg = f"🚀 {symbol} MACD 金叉 (Bullish Cross)"
    # EMA 穿越
    if prev_row['EMA5'] < prev_row['EMA20'] and last_row['EMA5'] > last_row['EMA20']:
        msg = f"🔥 {symbol} EMA5 向上穿越 EMA20"
    # 成交量暴增
    if last_row['Volume'] > last_row['VOL_MA5'] * 2.5:
        msg = f"📊 {symbol} 成交量異常放量 (>2.5x)"

    if msg:
        alert_entry = {"time": datetime.now().strftime("%H:%M:%S"), "symbol": symbol, "msg": msg}
        st.session_state.alerts.insert(0, alert_entry)
        send_telegram(f"【美股監控】{symbol}: {msg}")

# ══════════════════════════════════════════════════════════════════════════════
# 4. 側邊欄配置
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("⚙️ 控制面板")
    input_symbols = st.text_area("監控清單 (逗號分隔)", "AAPL, TSLA, NVDA, MSFT").upper()
    symbols = [s.strip() for s in input_symbols.split(",")]
    
    mode = st.radio("監控模式", ["單一週期詳細", "多週期 MTF 監控"])
    
    intervals = ['1m', '5m', '15m', '30m', '1d', '1wk', '1mo']
    if mode == "單一週期詳細":
        selected_interval = st.selectbox("選擇週期", intervals, index=1)
    else:
        mtf_intervals = st.multiselect("選擇 MTF 週期", intervals, default=['5m', '15m', '1d'])
        col_layout = st.selectbox("圖表排列", ["並排2欄", "堆疊全寬"])

    st.markdown("---")
    refresh_auto = st.toggle("自動刷新", value=False)
    refresh_sec = st.slider("刷新秒數", 10, 300, 60)
    max_bars = st.number_input("K線顯示根數", 30, 300, 90)
    
    st.markdown("---")
    show_market = st.checkbox("市場環境面板", True)
    show_ai = st.checkbox("AI 技術分析", True)
    
    if st.button("🗑️ 清除警示記錄"):
        st.session_state.alerts = []
    
    if st.session_state.alerts:
        df_alerts = pd.DataFrame(st.session_state.alerts)
        st.download_button("📥 匯出警示 CSV", df_alerts.to_csv(index=False), "alerts.csv")

# ══════════════════════════════════════════════════════════════════════════════
# 5. 市場環境總覽 (Top Panel)
# ══════════════════════════════════════════════════════════════════════════════

if show_market:
    st.markdown("### 🌍 市場環境總覽")
    m_cols = st.columns(6)
    indices = {"SPY": "標普500", "QQQ": "納指100", "^VIX": "波動率", "GLD": "黃金", "UUP": "美元", "^TNX": "10Y債息"}
    
    for i, (sym, name) in enumerate(indices.items()):
        m_data = fetch_stock_data(sym, '5m', '2d')
        if m_data is not None:
            price = m_data['Close'].iloc[-1]
            change = m_data['Close'].iloc[-1] - m_data['Close'].iloc[-2]
            pct = (change / m_data['Close'].iloc[-2]) * 100
            color = "normal" if sym != "^VIX" else "inverse"
            m_cols[i].metric(name, f"{price:.2f}", f"{pct:+.2f}%", delta_color=color)

    # VIX 壓力計與情緒
    vix_val = fetch_stock_data("^VIX", '1d', '5d')['Close'].iloc[-1]
    sentiment_score = max(0, min(100, 100 - (vix_val * 2))) # 簡易邏輯
    
    st.markdown(f"""
    <div class="status-bar">
        <span>🔥 恐慌指數 VIX: <b>{vix_val:.2f}</b></span>
        <span>🧠 投資人情緒得分: <b>{sentiment_score:.0f}/100</b> ({'貪婪' if sentiment_score > 60 else '恐懼' if sentiment_score < 40 else '中性'})</span>
        <span>🕒 最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span>
    </div>
    """, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# 6. 專業圖表引擎 (Plotly)
# ══════════════════════════════════════════════════════════════════════════════

def create_pro_chart(df, symbol, interval):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                        vertical_spacing=0.03, row_heights=[0.7, 0.3])

    # K線
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        name="K線", increasing_line_color='#00ffbb', decreasing_line_color='#ff3355'
    ), row=1, col=1)

    # EMA 繪製
    ema_colors = {5: '#ffffff', 20: '#ff9900', 60: '#00ccff', 200: '#ff00ff'}
    for p, color in ema_colors.items():
        fig.add_trace(go.Scatter(x=df.index, y=df[f'EMA{p}'], line=dict(color=color, width=1), name=f'EMA{p}'), row=1, col=1)

    # 成交量
    colors = ['#00ffbb' if c >= o else '#ff3355' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name="成交量", opacity=0.5), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['VOL_MA5'], line=dict(color='#ffcc00', width=1), name="VOL MA5"), row=2, col=1)

    # 異常放量標記
    spike = df[df['Volume'] > df['VOL_MA5'] * 2]
    fig.add_trace(go.Scatter(x=spike.index, y=spike['Low'] * 0.998, mode='markers', 
                             marker=dict(symbol='diamond', size=8, color='#ffff00'), name="異常放量"), row=1, col=1)

    fig.update_layout(
        height=600, template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return fig

# ══════════════════════════════════════════════════════════════════════════════
# 7. AI 分析模組
# ══════════════════════════════════════════════════════════════════════════════

def run_ai_analysis(symbol, df, market_context):
    api_key = st.secrets.get("GROQ_API_KEY")
    if not api_key:
        st.error("請在 st.secrets 中配置 GROQ_API_KEY")
        return None
    
    client = Groq(api_key=api_key)
    last = df.iloc[-1]
    
    prompt = f"""
    你是專業美股分析師。請分析 {symbol}。
    當前數據：價格={last['Close']:.2f}, EMA20={last['EMA20']:.2f}, EMA60={last['EMA60']:.2f}, MACD={last['MACDh_12_26_9']:.4f}。
    市場背景：VIX={market_context['vix']:.2f}。
    
    請嚴格以 JSON 格式回傳：
    {{
      "verdict": "做多/做空/觀望",
      "confidence": 0-100,
      "trend_analysis": "...",
      "entry_price": 0.0,
      "take_profit_1": 0.0,
      "stop_loss": 0.0,
      "reasoning": "繁體中文詳細理由"
    }}
    """
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}

# ══════════════════════════════════════════════════════════════════════════════
# 8. 主程式渲染
# ══════════════════════════════════════════════════════════════════════════════

tabs = st.tabs([f"📈 {s}" for s in symbols] + ["🔔 警示中心"])

for i, symbol in enumerate(symbols):
    with tabs[i]:
        if mode == "單一週期詳細":
            df = fetch_stock_data(symbol, selected_interval)
            if df is not None:
                df = calculate_indicators(df)
                check_alerts(symbol, df)
                
                # 頂部個股快速數據
                c1, c2, c3, c4 = st.columns(4)
                last_p = df['Close'].iloc[-1]
                change_p = last_p - df['Close'].iloc[-2]
                c1.metric(symbol, f"${last_p:.2f}", f"{change_p:+.2f}")
                
                # 趨勢判斷
                trend = "多頭" if last_p > df['EMA60'].iloc[-1] else "空頭"
                c2.markdown(f'<div class="trend-card">趨勢: <b>{trend}</b></div>', unsafe_allow_html=True)
                
                # 圖表
                st.plotly_chart(create_pro_chart(df.tail(max_bars), symbol, selected_interval), use_container_width=True)
                
                # AI 分析區
                if show_ai:
                    if st.button(f"🤖 執行 AI 深度分析 ({symbol})", key=f"ai_{symbol}"):
                        with st.spinner("AI 正在分析大數據與技術指標..."):
                            ctx = {"vix": vix_val}
                            result = run_ai_analysis(symbol, df, ctx)
                            if result and "verdict" in result:
                                st.markdown(f"""
                                <div class="ai-panel">
                                    <h3>🤖 AI 交易決策：{result['verdict']} (信心度: {result['confidence']}%)</h3>
                                    <hr>
                                    <p><b>分析邏輯：</b>{result['reasoning']}</p>
                                    <div style="display: flex; gap: 20px;">
                                        <div style="background:#2e7d32; padding:10px; border-radius:5px;">進場: {result['entry_price']}</div>
                                        <div style="background:#c62828; padding:10px; border-radius:5px;">止損: {result['stop_loss']}</div>
                                        <div style="background:#1565c0; padding:10px; border-radius:5px;">止盈: {result['take_profit_1']}</div>
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
        
        else: # MTF 多週期模式
            st.subheader(f"多週期同步監控 - {symbol}")
            rows = st.columns(2) if col_layout == "並排2欄" else [st.container()]
            
            for idx, m_int in enumerate(mtf_intervals):
                m_df = fetch_stock_data(symbol, m_int)
                if m_df is not None:
                    m_df = calculate_indicators(m_df)
                    target_col = rows[idx % 2] if col_layout == "並排2欄" else st
                    with target_col:
                        st.markdown(f"#### ⏱️ 週期: {m_int}")
                        st.plotly_chart(create_pro_chart(m_df.tail(max_bars), symbol, m_int), use_container_width=True)

# 警示中心分頁
with tabs[-1]:
    st.header("🔔 即時警示串流")
    if not st.session_state.alerts:
        st.info("目前尚無觸發警示")
    else:
        for a in st.session_state.alerts:
            st.warning(f"[{a['time']}] **{a['symbol']}**: {a['msg']}")

# ══════════════════════════════════════════════════════════════════════════════
# 9. 自動刷新邏輯
# ══════════════════════════════════════════════════════════════════════════════
if refresh_auto:
    time.sleep(refresh_sec)
    st.rerun()

# 頁尾資訊
st.markdown("---")
st.caption("PRO System v2.0 | Data by yfinance | AI by Groq LLaMA 3.3")
