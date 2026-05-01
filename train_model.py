import sqlite3
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import joblib
import os

# Configuration
DB_PATH = "data/audit/production_analysis.db"
MODEL_DIR = "models"
MODEL_PATH = os.path.join(MODEL_DIR, "scalper_v2.joblib")

def train():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}")
        return

    print("🧠 STARTING MODEL RE-TRAINING (Production Dataset)")
    print("="*50)
    
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Fetch DNA and Outcome
    # We use trade_analytics which has DNA features and PnL
    query = """
    SELECT 
        ai_prob, whale_boost, sector_sentiment, imbalance, 
        volatility, rsi, ema_dist, entry_timestamp, pnl_idr
    FROM trade_analytics
    WHERE status = 'CLOSED' AND pnl_idr IS NOT NULL
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("📭 No data available for training.")
        return

    print(f"📊 Training Samples: {len(df)}")
    
    # 2. Feature Engineering
    # Extract hour from entry_timestamp
    df['entry_timestamp'] = pd.to_datetime(df['entry_timestamp'])
    df['hour'] = df['entry_timestamp'].dt.hour
    
    # Define Target: 1 if Profit > 0, else 0
    df['target'] = (df['pnl_idr'] > 0).astype(int)
    
    # Feature columns
    features = [
        'ai_prob', 'whale_boost', 'sector_sentiment', 
        'imbalance', 'volatility', 'rsi', 'ema_dist', 'hour'
    ]
    
    X = df[features].fillna(0)
    y = df['target']
    
    # 3. Train/Test Split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # 4. Model Training (Random Forest)
    print("⚙️ Training Random Forest Classifier...")
    model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42)
    model.fit(X_train, y_train)
    
    # 5. Evaluation
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    
    print("\n✅ TRAINING COMPLETE")
    print(f"📈 Accuracy Score: {acc*100:.2f}%")
    print("-" * 30)
    print(classification_report(y_test, y_pred))
    
    # 6. Feature Importance
    print("\n🔍 FEATURE IMPORTANCE:")
    importances = pd.Series(model.feature_importances_, index=features).sort_values(ascending=False)
    print(importances.to_string())
    
    # 7. Save Model
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)
        
    joblib.dump(model, MODEL_PATH)
    print(f"\n💾 Model saved to: {MODEL_PATH}")
    print("="*50)

if __name__ == "__main__":
    train()
