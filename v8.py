# =============================================================================
# 美股即時監控系統 - 專業級 Streamlit 儀表板
# 版本: 1.0 (2026-02-28)
# 作者: Grok + Team (Harper, Benjamin, Lucas)
# 依賴: pip install streamlit yfinance plotly pandas numpy requests feedparser groq openai
# 使用方式: streamlit run app.py
# =============================================================================

import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import time
from datetime import datetime
import requests
import feedparser
import json
from groq import Groq
from openai import OpenAI
import os

# ====================== 頁面設定與自訂 CSS ======================
st.set_page_config(
    page_title="美股即時監控系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 極致深色高端金融風格 CSS (TradingView Pro + Bloomberg 暗黑模式)
st.markdown("""
<style>
    /* 全域深色主題 */
    .stApp { background-color: #0a0e17; color: #e0e4ed; }
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    
    /* 卡片風格 */
    .metric-card, .trend-card, .ai-panel, .alert-box {
        background: linear-gradient(145deg, #1a2333, #121a29);
        border-radius: 16px;
        padding: 20px;
        border: 1px solid #2a374f;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        margin-bottom: 1.2rem;
    }
    .metric-card:hover { border-color: #3b82f6; transform: translateY(-2px); transition: all 0.3s; }
    
    /* 標題與分隔 */
    h1, h2, h3 { color: #60a5fa; font-weight: 600; letter-spacing: -0.5px; }
    .divider { border-top: 2px solid #334155; margin: 2rem 0; }
    
    /* Metric 美化 */
    .stMetric { background: #1f2937; border-radius: 12px; padding: 12px; }
    .stMetric label { color: #94a3b8; font-size: 0.9rem; }
    .stMetric .stMetricValue { color: #e0e7ff; font-size: 1.8rem; font-weight: 700; }
    .stMetric .stMetricDelta { font-size: 1rem; }
    
    /* AI 裁決大字 */
    .verdict-bull { color: #22c55e; font-size: 3.2rem; font-weight: 800; text-shadow: 0 0 20px #22c55e; }
    .verdict-bear { color: #ef4444; font-size: 3.2rem; font-weight: 800; text-shadow: 0 0 20px #ef4444; }
    .verdict-neutral { color: #eab308; font-size: 3.2rem; font-weight: 800; }
    
    /* 信心度條 */
    .confidence-bar {
        height: 12px; border-radius: 9999px; background: linear-gradient(90deg, #22c55e, #eab308, #ef4444);
        position: relative; overflow: hidden;
    }
    
    /* 警示框 */
    .alert-box { border-left: 6px solid #f59e0b; }
    
    /* VIX 壓力計 */
    .vix-low { color: #22c55e; }
    .vix-med { color: #eab308; }
    .vix-high { color: #ef4444; }
    
    /* 按鈕美化 */
    .stButton>button {
        background: linear-gradient(90deg, #3b82f6, #1e40af);
        color: white; border-radius: 12px; font-weight: 600;
        border: none; padding: 0.6rem 1.8rem;
    }
    .stButton>button:hover { box-shadow: 0 0 15px #60a5fa; }
</style>
""", unsafe_allow_html=True)

# ====================== Session State 初始化 ======================
if 'alerts' not in st.session_state:
    st.session_state.alerts = []
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = time.time()
if 'ai_cache' not in st.session_state:
    st.session_state.ai_cache = {}

# ====================== 輔助函數 ======================

@st.cache_data(ttl=30)  # 30秒快取即時數據
def get_stock_data(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """抓取 yfinance 數據並清理"""
    try:
        data = yf.download(ticker, interval=interval, period=period, auto_adjust=True, prepost=True)
        if data.empty:
            return pd.DataFrame()
        data = data.dropna()
        # 確保欄位存在
        required = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required:
            if col not in data.columns:
                data[col] = np.nan
        return data
    except Exception as e:
        st.error(f"{ticker} 數據抓取失敗: {e}")
        return pd.DataFrame()

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算所有技術指標"""
    if df.empty:
        return df
    df = df.copy()
    
    # 多條 EMA (彩色)
    ema_periods = [5, 10, 20, 30, 40, 60, 120, 200]
    colors = ['#22c55e', '#eab308', '#3b82f6', '#8b5cf6', '#ec4899', '#f97316', '#ef4444', '#64748b']
    for i, p in enumerate(ema_periods):
        df[f'EMA{p}'] = df['Close'].ewm(span=p, adjust=False).mean()
        df[f'EMA{p}_color'] = colors[i % len(colors)]
    
    # 簡單 MA
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA15'] = df['Close'].rolling(15).mean()
    
    # MACD
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']
    
    # Volume MA
    df['Vol_MA5'] = df['Volume'].rolling(5).mean()
    
    # Pivot Points (基於最近完整日)
    if len(df) > 1:
        last_day = df.iloc[-1]
        prev_day = df.iloc[-2] if len(df) > 1 else last_day
        pp = (prev_day['High'] + prev_day['Low'] + prev_day['Close']) / 3
        df['Pivot'] = pp
        df['R1'] = 2 * pp - prev_day['Low']
        df['S1'] = 2 * pp - prev_day['High']
        df['R2'] = pp + (prev_day['High'] - prev_day['Low'])
        df['S2'] = pp - (prev_day['High'] - prev_day['Low'])
    
    return df

def detect_signals(df: pd.DataFrame, tf: str, ticker: str) -> list:
    """偵測多種警示條件，返回觸發列表"""
    signals = []
    if len(df) < 30:
        return signals
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # MACD 金叉/死叉
    if prev['MACD'] < prev['Signal'] and last['MACD'] > last['Signal']:
        signals.append(f"📈 MACD 金叉 ({tf})")
    if prev['MACD'] > prev['Signal'] and last['MACD'] < last['Signal']:
        signals.append(f"📉 MACD 死叉 ({tf})")
    
    # EMA5 穿越 EMA20
    if 'EMA5' in df.columns and 'EMA20' in df.columns:
        if prev['EMA5'] < prev['EMA20'] and last['EMA5'] > last['EMA20']:
            signals.append(f"🚀 EMA5 上穿 EMA20 ({tf})")
        if prev['EMA5'] > prev['EMA20'] and last['EMA5'] < last['EMA20']:
            signals.append(f"⚠️ EMA5 下穿 EMA20 ({tf})")
    
    # 全 EMA 多頭排列 (5>10>20>30>60)
    emas = [5,10,20,30,60]
    if all(f'EMA{p}' in df.columns for p in emas):
        bull_arrange = all(last[f'EMA{emas[i]}'] > last[f'EMA{emas[i+1]}'] for i in range(len(emas)-1))
        if bull_arrange and not all(prev[f'EMA{emas[i]}'] > prev[f'EMA{emas[i+1]}'] for i in range(len(emas)-1)):
            signals.append(f"🌟 全 EMA 多頭排列 ({tf})")
    
    # 成交量暴增 >2x Vol_MA5
    if last['Volume'] > 2 * last['Vol_MA5']:
        signals.append(f"🔥 成交量暴增 {last['Volume']/1e6:.1f}M ({tf})")
    
    # 價格突破/跌破 Pivot
    if last['Close'] > last['R1'] and prev['Close'] <= prev['R1']:
        signals.append(f"🔺 突破 R1 阻力 ({tf})")
    if last['Close'] < last['S1'] and prev['Close'] >= prev['S1']:
        signals.append(f"🔻 跌破 S1 支撐 ({tf})")
    
    return signals

def send_telegram(token: str, chat_id: str, msg: str):
    """發送 Telegram 警示"""
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"})
    except:
        pass

def plot_professional_chart(df: pd.DataFrame, ticker: str, tf: str, max_bars: int = 90) -> go.Figure:
    """極致專業 Plotly K線圖 (3子圖 + 多EMA + 支撐阻力 + Volume spike + MACD 金叉標註)"""
    df = df.tail(max_bars).copy()
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="無數據", showarrow=False, font_size=30)
        return fig
    
    # 主圖 + Volume + MACD
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.55, 0.20, 0.25],
        subplot_titles=(f"{ticker} {tf} K線圖", "成交量 + Vol MA5", "MACD + Signal")
    )
    
    # 1. K線 + EMA + MA + Pivot
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'], name="OHLC",
        increasing_line_color='#22c55e', decreasing_line_color='#ef4444'
    ), row=1, col=1)
    
    # 多條 EMA
    ema_periods = [5,10,20,30,40,60,120,200]
    colors = ['#22c55e','#eab308','#3b82f6','#8b5cf6','#ec4899','#f97316','#ef4444','#64748b']
    for i, p in enumerate(ema_periods):
        col_name = f'EMA{p}'
        if col_name in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df[col_name],
                mode='lines', name=f'EMA{p}',
                line=dict(color=colors[i], width=1.8 if p in [5,20] else 1.2)
            ), row=1, col=1)
    
    # MA5 / MA15
    fig.add_trace(go.Scatter(x=df.index, y=df['MA5'], mode='lines', name='MA5', line=dict(color='#a5b4fc', dash='dot')), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA15'], mode='lines', name='MA15', line=dict(color='#c4d0ff', dash='dot')), row=1, col=1)
    
    # 動態 Pivot 線 (僅顯示最近合理範圍)
    if 'Pivot' in df.columns:
        fig.add_trace(go.Scatter(x=df.index[-30:], y=df['Pivot'][-30:], mode='lines', name='Pivot', line=dict(color='#facc15', dash='dash', width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index[-30:], y=df['R1'][-30:], mode='lines', name='R1', line=dict(color='#f87171', dash='dot')), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index[-30:], y=df['S1'][-30:], mode='lines', name='S1', line=dict(color='#60a5fa', dash='dot')), row=1, col=1)
    
    # 最高最低價自動標註
    high_idx = df['High'].idxmax()
    low_idx = df['Low'].idxmin()
    fig.add_annotation(x=high_idx, y=df['High'].max(), text=f"高 {df['High'].max():.2f}", showarrow=True, arrowhead=2, arrowcolor="#22c55e", font=dict(color="#22c55e"))
    fig.add_annotation(x=low_idx, y=df['Low'].min(), text=f"低 {df['Low'].min():.2f}", showarrow=True, arrowhead=2, arrowcolor="#ef4444", font=dict(color="#ef4444"))
    
    # 2. 成交量柱狀 + Vol MA5 + 異常放量鑽石標記
    colors_vol = ['#22c55e' if c > o else '#ef4444' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='Volume', marker_color=colors_vol, opacity=0.75), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['Vol_MA5'], mode='lines', name='Vol MA5', line=dict(color='#94a3b8', width=2)), row=2, col=1)
    
    # 異常放量標記
    vol_spike = df[df['Volume'] > 2 * df['Vol_MA5']]
    if not vol_spike.empty:
        fig.add_trace(go.Scatter(
            x=vol_spike.index, y=vol_spike['Volume']*1.05,
            mode='markers', name='放量',
            marker=dict(symbol='diamond', size=12, color='#eab308', line=dict(width=2, color='white'))
        ), row=2, col=1)
    
    # 3. MACD 子圖 + 金叉/死叉智能標註
    fig.add_trace(go.Scatter(x=df.index, y=df['MACD'], mode='lines', name='MACD', line=dict(color='#22c55e')), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['Signal'], mode='lines', name='Signal', line=dict(color='#ef4444')), row=3, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['Hist'], name='Hist', marker_color=np.where(df['Hist']>0, '#22c55e', '#ef4444')), row=3, col=1)
    
    # 智能 MACD 交叉標註 (避免過度擁擠，只標最近 5 次)
    crosses = []
    for i in range(1, len(df)):
        if df.iloc[i-1]['MACD'] < df.iloc[i-1]['Signal'] and df.iloc[i]['MACD'] > df.iloc[i]['Signal']:
            crosses.append((df.index[i], "金叉", "#22c55e"))
        elif df.iloc[i-1]['MACD'] > df.iloc[i-1]['Signal'] and df.iloc[i]['MACD'] < df.iloc[i]['Signal']:
            crosses.append((df.index[i], "死叉", "#ef4444"))
    for idx, text, color in crosses[-5:]:
        fig.add_annotation(x=idx, y=df.loc[idx, 'MACD'], text=text, showarrow=True, arrowhead=1, arrowcolor=color, font=dict(color=color, size=11), row=3, col=1)
    
    # 美化佈局
    fig.update_layout(
        height=820,
        title=f"{ticker} {tf} 專業技術圖表 - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        template="plotly_dark",
        paper_bgcolor="#0a0e17",
        plot_bgcolor="#111827",
        font=dict(color="#e0e7ff"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, bgcolor="#1f2937"),
        margin=dict(l=50, r=50, t=80, b=50),
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(gridcolor="#334155", zerolinecolor="#334155")
    fig.update_yaxes(gridcolor="#334155", zerolinecolor="#334155")
    
    return fig

def get_market_indices() -> dict:
    """抓取大盤指數卡片數據"""
    indices = ["SPY", "QQQ", "DIA", "GLD", "UUP", "^TNX"]
    result = {}
    for idx in indices:
        df = get_stock_data(idx, "1d", "5d")
        if not df.empty:
            price = round(df['Close'].iloc[-1], 2)
            change_pct = round((price - df['Close'].iloc[-2]) / df['Close'].iloc[-2] * 100, 2)
            result[idx] = {"price": price, "change": change_pct}
    return result

def get_vix_data():
    """VIX 數據"""
    df = get_stock_data("^VIX", "1d", "30d")
    if df.empty:
        return 20.0, pd.DataFrame()
    return round(df['Close'].iloc[-1], 1), df.tail(10)

def get_news() -> list:
    """即時財經新聞 + bull/bear 標記"""
    feeds = [
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "https://news.google.com/rss/search?q=US+stock+market+OR+wall+street+OR+earnings&hl=en-US&gl=US&ceid=US:en"
    ]
    news_list = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = entry.title
                link = entry.link
                # 簡單情緒判斷
                lower = title.lower()
                if any(w in lower for w in ['rise', 'gain', 'surge', 'bull', 'up', 'beat', 'rally']):
                    sentiment = "🟢 Bull"
                elif any(w in lower for w in ['fall', 'drop', 'plunge', 'bear', 'down', 'miss', 'crash']):
                    sentiment = "🔴 Bear"
                else:
                    sentiment = "⚪ Neutral"
                news_list.append({"title": title[:90] + "..." if len(title)>90 else title, "link": link, "sent": sentiment})
        except:
            continue
    return news_list[:10]

def get_sentiment_index(vix: float, spy_change: float) -> int:
    """投資人情緒指數 (0-100)"""
    score = 50 + (25 - vix) * 1.8 + spy_change * 1.5
    return max(0, min(100, int(score)))

# ====================== AI 技術分析核心 ======================
def generate_ai_analysis(provider: str, api_key: str, ticker: str, tf_data: dict, market_env: dict) -> dict:
    """打包所有指標給 LLM，返回嚴格 JSON"""
    cache_key = f"{ticker}_{provider}"
    if cache_key in st.session_state.ai_cache:
        return st.session_state.ai_cache[cache_key]
    
    # 整理 prompt 數據
    summary = []
    for tf, df in tf_data.items():
        if df.empty: continue
        last = df.iloc[-1]
        summary.append(f"{tf}: 收盤 {last['Close']:.2f} | 漲跌 {((last['Close']/df.iloc[-2]['Close']-1)*100):+.2f}% | "
                       f"MACD {last['MACD']:+.3f} | EMA5 {last.get('EMA5',0):.2f} EMA20 {last.get('EMA20',0):.2f}")
    
    prompt = f"""你是一位頂級美股量化交易員與技術分析師。請對 {ticker} 進行嚴格專業分析。
當前市場環境: VIX={market_env['vix']}, 情緒指數={market_env['sentiment']}, SPY 1日變動={market_env.get('spy_change',0):+.2f}%

技術摘要:
{" | ".join(summary)}

請以繁體中文嚴格回傳以下 JSON 格式 (不要多餘文字，不要 markdown):
{{
  "verdict": "做多" | "做空" | "觀望",
  "confidence": 0-100,
  "trend_analysis": "簡短趨勢描述",
  "entry_price": 數字,
  "entry_note": "進場理由",
  "take_profit_1": 數字,
  "take_profit_2": 數字,
  "stop_loss": 數字,
  "risk_reward": "1:2.5",
  "key_risks": "主要風險點",
  "reasoning": "詳細繁體中文推理邏輯 (300字以內)"
}}
"""
    try:
        if provider == "Groq (LLaMA 3.3)":
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
        else:  # Grok
            client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
            response = client.chat.completions.create(
                model="grok-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
        
        st.session_state.ai_cache[cache_key] = result
        return result
    except Exception as e:
        st.error(f"AI 分析失敗: {e}")
        return {"verdict": "觀望", "confidence": 50, "trend_analysis": "API 錯誤", "reasoning": str(e)}

# ====================== 主程式 ======================
def main():
    st.title("📈 美股即時監控系統")
    st.caption("專業級深色儀表板 • 即時數據 • AI 決策 • Telegram 警示")

    # ====================== 側邊欄 ======================
    with st.sidebar:
        st.header("⚙️ 系統設定")
        stocks_input = st.text_area("股票代號 (逗號分隔)", value="AAPL, TSLA, NVDA, MSFT, AMZN", height=100)
        stocks = [s.strip().upper() for s in stocks_input.split(",") if s.strip()]
        
        mode = st.radio("📊 監控模式", ["單一週期", "多週期同時監控 (MTF)"], horizontal=True)
        
        if mode == "單一週期":
            timeframe = st.selectbox("時間週期", ["1m", "5m", "15m", "30m", "1d", "1wk", "1mo"], index=4)
            selected_tfs = [timeframe]
        else:
            selected_tfs = st.multiselect("選擇多個時間框架", ["1m", "5m", "15m", "30m", "1d", "1wk", "1mo"], default=["1d", "1wk"])
            layout_mode = st.radio("圖表排列方式", ["並排2欄", "堆疊全寬"])
        
        auto_refresh = st.toggle("自動刷新", value=True)
        refresh_sec = st.slider("刷新間隔 (秒)", 15, 300, 45, disabled=not auto_refresh)
        max_bars = st.slider("K線最大顯示根數", 30, 500, 90)
        
        st.divider()
        show_market_panel = st.toggle("🌍 市場環境總覽", value=True)
        show_alerts_detect = st.toggle("🚨 警示偵測", value=True)
        show_ai_panel = st.toggle("🤖 AI 技術分析", value=True)
        
        st.divider()
        ai_provider = st.selectbox("AI 供應商", ["Groq (LLaMA 3.3)", "Grok (xAI)"])
        ai_key = st.text_input("API Key (或使用 .streamlit/secrets.toml)", type="password", value=os.getenv("GROQ_API_KEY") or "")
        
        telegram_on = st.toggle("📲 Telegram 警示通知")
        if telegram_on:
            tg_token = st.text_input("BOT_TOKEN", type="password", value=os.getenv("TG_BOT_TOKEN") or "")
            tg_chat = st.text_input("CHAT_ID", value=os.getenv("TG_CHAT_ID") or "")
        
        if st.button("🗑️ 清除所有警示記錄"):
            st.session_state.alerts = []
            st.success("已清除")
        
        if st.button("📥 匯出警示 CSV"):
            if st.session_state.alerts:
                df_alert = pd.DataFrame(st.session_state.alerts)
                csv = df_alert.to_csv(index=False).encode()
                st.download_button("下載 CSV", csv, f"alerts_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")
    
    # ====================== 自動刷新邏輯 ======================
    if auto_refresh and time.time() - st.session_state.last_refresh > refresh_sec:
        st.session_state.last_refresh = time.time()
        st.rerun()

    # ====================== 置頂市場環境總覽 ======================
    if show_market_panel:
        st.subheader("🌍 市場環境總覽")
        idx_data = get_market_indices()
        cols = st.columns(6)
        for i, (sym, val) in enumerate(idx_data.items()):
            with cols[i]:
                delta_color = "normal" if val['change'] >= 0 else "inverse"
                st.metric(sym, f"{val['price']:.2f}", f"{val['change']:+.2f}%", delta_color=delta_color)
        
        # VIX + 情緒
        vix_val, vix_df = get_vix_data()
        col_vix1, col_vix2, col_sent = st.columns([2, 3, 2])
        with col_vix1:
            st.metric("VIX 恐慌指數", f"{vix_val:.1f}", delta=None)
        with col_vix2:
            fig_gauge = go.Figure(go.Indicator(
                mode="gauge+number",
                value=vix_val,
                title={"text": "VIX 壓力計"},
                gauge={
                    "axis": {"range": [0, 50]},
                    "bar": {"color": "#64748b"},
                    "steps": [
                        {"range": [0, 15], "color": "#22c55e"},
                        {"range": [15, 25], "color": "#eab308"},
                        {"range": [25, 50], "color": "#ef4444"}
                    ],
                    "threshold": {"line": {"color": "white", "width": 4}, "value": vix_val}
                }
            ))
            fig_gauge.update_layout(height=180, margin=dict(l=20,r=20,t=30,b=10))
            st.plotly_chart(fig_gauge, use_container_width=True)
        
        with col_sent:
            spy_chg = idx_data.get("SPY", {}).get("change", 0)
            sentiment = get_sentiment_index(vix_val, spy_chg)
            color = "#22c55e" if sentiment > 65 else "#eab308" if sentiment > 40 else "#ef4444"
            st.metric("投資人情緒指數", f"{sentiment}", delta=None)
            st.progress(sentiment / 100, text=f"情緒 {sentiment}/100")
        
        # 新聞
        st.subheader("📰 即時財經新聞")
        news_items = get_news()
        news_cols = st.columns(2)
        for i, item in enumerate(news_items[:6]):
            with news_cols[i % 2]:
                st.markdown(f"""
                <div class="metric-card">
                    <small style="color:#94a3b8">{item['sent']}</small><br>
                    <a href="{item['link']}" target="_blank" style="color:#e0e7ff; text-decoration:none;">{item['title']}</a>
                </div>
                """, unsafe_allow_html=True)
    
    st.divider()
    
    # ====================== 個股分析 Tab ======================
    if not stocks:
        st.warning("請在側邊欄輸入股票代號")
        st.stop()
    
    tabs = st.tabs(stocks)
    for tab_idx, ticker in enumerate(stocks):
        with tabs[tab_idx]:
            st.header(f"🔍 {ticker} 即時分析")
            
            # 抓取所有選定時間框架數據
            tf_data = {}
            period_map = {"1m": "7d", "5m": "30d", "15m": "30d", "30m": "60d", "1d": "1y", "1wk": "2y", "1mo": "5y"}
            for tf in selected_tfs:
                interval = tf
                period = period_map.get(tf, "60d")
                raw_df = get_stock_data(ticker, interval, period)
                if not raw_df.empty:
                    df = calculate_indicators(raw_df)
                    tf_data[tf] = df.tail(max_bars)
            
            # 總覽摘要列 (每週期一卡片)
            if tf_data:
                sum_cols = st.columns(len(tf_data))
                for i, (tf, df) in enumerate(tf_data.items()):
                    if df.empty: continue
                    last = df.iloc[-1]
                    chg = (last['Close'] / df.iloc[-2]['Close'] - 1) * 100 if len(df)>1 else 0
                    with sum_cols[i]:
                        st.metric(
                            f"{tf} 收盤",
                            f"{last['Close']:.2f}",
                            f"{chg:+.2f}%",
                            delta_color="normal" if chg >= 0 else "inverse"
                        )
                        st.caption(f"H {last['High']:.2f} / L {last['Low']:.2f} | Vol {last['Volume']/1e6:.1f}M")
            
            # 警示
            if show_alerts_detect:
                all_signals = []
                for tf, df in tf_data.items():
                    sigs = detect_signals(df, tf, ticker)
                    all_signals.extend(sigs)
                    for sig in sigs:
                        if sig not in [a['msg'] for a in st.session_state.alerts if a['ticker']==ticker]:
                            alert_entry = {"time": datetime.now().strftime("%H:%M:%S"), "ticker": ticker, "msg": sig, "tf": tf}
                            st.session_state.alerts.append(alert_entry)
                            if telegram_on and 'tg_token' in locals() and 'tg_chat' in locals():
                                send_telegram(tg_token, tg_chat, f"🚨 {ticker} {sig}")
                
                if all_signals:
                    st.subheader("🚨 即時警示")
                    for s in all_signals[-5:]:
                        st.error(s)
            
            # K線圖
            st.subheader("📊 多週期 K線圖")
            if mode == "多週期同時監控 (MTF)":
                if layout_mode == "並排2欄":
                    chart_cols = st.columns(2)
                    for i, (tf, df) in enumerate(tf_data.items()):
                        with chart_cols[i % 2]:
                            fig = plot_professional_chart(df, ticker, tf, max_bars)
                            st.plotly_chart(fig, use_container_width=True, key=f"mtf_{ticker}_{tf}")
                else:
                    for tf, df in tf_data.items():
                        fig = plot_professional_chart(df, ticker, tf, max_bars)
                        st.plotly_chart(fig, use_container_width=True, key=f"stack_{ticker}_{tf}")
            else:
                # 單一模式詳細圖
                if selected_tfs:
                    tf = selected_tfs[0]
                    df = tf_data.get(tf)
                    if df is not None:
                        fig = plot_professional_chart(df, ticker, tf, max_bars)
                        st.plotly_chart(fig, use_container_width=True)
            
            # AI 技術分析面板
            if show_ai_panel and ai_key and tf_data:
                st.subheader("🤖 AI 專業技術分析報告")
                if st.button(f"🚀 執行 {ticker} AI 分析", type="primary"):
                    with st.spinner("AI 正在深度分析市場結構與多週期共振..."):
                        market_env = {
                            "vix": get_vix_data()[0],
                            "sentiment": get_sentiment_index(get_vix_data()[0], idx_data.get("SPY", {}).get("change", 0)),
                            "spy_change": idx_data.get("SPY", {}).get("change", 0)
                        }
                        ai_result = generate_ai_analysis(ai_provider, ai_key, ticker, tf_data, market_env)
                        
                        # 美觀渲染
                        verdict = ai_result.get("verdict", "觀望")
                        conf = ai_result.get("confidence", 50)
                        col_a, col_b = st.columns([3, 2])
                        with col_a:
                            if verdict == "做多":
                                st.markdown(f'<div class="verdict-bull">✅ {verdict}</div>', unsafe_allow_html=True)
                            elif verdict == "做空":
                                st.markdown(f'<div class="verdict-bear">❌ {verdict}</div>', unsafe_allow_html=True)
                            else:
                                st.markdown(f'<div class="verdict-neutral">⏸️ {verdict}</div>', unsafe_allow_html=True)
                            
                            st.markdown(f"**信心度** {conf}%")
                            st.progress(conf / 100)
                        
                        with col_b:
                            st.metric("建議進場價", f"{ai_result.get('entry_price',0):.2f}", help=ai_result.get('entry_note',''))
                            st.metric("停損", f"{ai_result.get('stop_loss',0):.2f}")
                            st.metric("TP1 / TP2", f"{ai_result.get('take_profit_1',0):.2f} / {ai_result.get('take_profit_2',0):.2f}")
                            st.caption(f"RR 比: {ai_result.get('risk_reward','')}")
                        
                        st.markdown("#### 📝 詳細推理")
                        st.info(ai_result.get("reasoning", "無"))
                        
                        st.markdown("#### ⚠️ 關鍵風險")
                        st.warning(ai_result.get("key_risks", "無"))
            
            st.caption(f"最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
