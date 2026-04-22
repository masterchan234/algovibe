import pandas as pd

def get_prediction(price_history: list) -> float:
    """
    Menghitung probabilitas eksekusi menggunakan indikator teknikal lanjutan.
    Menerima array history harga, mengembalikan float 0.0 - 1.0.
    """
    if not price_history or len(price_history) < 30:
        return 0.0

    try:
        df = pd.DataFrame(price_history, columns=['close'])
        prob = 0.40  # Base Score
        current_price = df['close'].iloc[-1]

        # 1. RSI (Relative Strength Index) - 14
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(span=14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(span=14, adjust=False).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        rsi_val = df['RSI'].iloc[-1]
        if pd.notna(rsi_val):
            if rsi_val > 70:
                prob -= 0.10
            elif rsi_val > 55:
                prob += 0.20

        # 2. MACD (12, 26, 9)
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        
        macd_val = macd_line.iloc[-1]
        sig_val = signal_line.iloc[-1]
        
        if pd.notna(macd_val) and pd.notna(sig_val):
            if macd_val > sig_val:
                prob += 0.20

        # 3. Bollinger Bands (20, 2)
        ma = df['close'].rolling(window=20).mean()
        std = df['close'].rolling(window=20).std()
        upper_band = ma + (std * 2)
        lower_band = ma - (std * 2)
        
        bbl_val = lower_band.iloc[-1]
        bbu_val = upper_band.iloc[-1]
        
        if pd.notna(bbl_val) and pd.notna(bbu_val):
            if current_price <= bbl_val * 1.002 or current_price >= bbu_val * 0.998:
                prob += 0.20

        prob = max(0.0, min(prob, 0.99))
        return prob

    except Exception as e:
        return 0.0
