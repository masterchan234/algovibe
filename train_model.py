import sqlite3
import pandas as pd
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
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
    df['entry_timestamp'] = pd.to_datetime(df['entry_timestamp'])
    df['hour'] = df['entry_timestamp'].dt.hour

    # Cyclical time encoding
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24.0)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24.0)

    # RSI zone features — seberapa oversold/overbought saat entry
    df['rsi_oversold']   = np.maximum(0.0, (30.0 - df['rsi']) / 30.0)
    df['rsi_overbought'] = np.maximum(0.0, (df['rsi'] - 70.0) / 30.0)

    # Magnitude jarak EMA
    df['ema_dist_abs'] = df['ema_dist'].abs()

    # Define Target: 1 if Profit > 0, else 0
    df['target'] = (df['pnl_idr'] > 0).astype(int)
    
    # Feature columns
    features = [
        'ai_prob', 'imbalance', 'volatility', 'rsi', 'ema_dist',
        'ema_dist_abs', 'rsi_oversold', 'rsi_overbought',
        'hour_sin', 'hour_cos',
    ]
    
    X = df[features].fillna(0)
    y = df['target']
    
    # 3. Train/Test Split (stratified)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # 4. Model Training (HistGradientBoosting — lebih akurat, tangani imbalance)
    print("⚙️ Training HistGradientBoostingClassifier...")
    model = HistGradientBoostingClassifier(
        max_iter=300,
        max_depth=4,
        learning_rate=0.05,
        min_samples_leaf=20,
        l2_regularization=0.1,
        class_weight="balanced",
        random_state=42,
    )
    model.fit(X_train, y_train)
    
    # 5. Cross-Validation
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(model, X, y, cv=cv, scoring='accuracy', n_jobs=-1)
    print(f"\n📊 Cross-Validation Accuracy: {cv_scores.mean()*100:.2f}% ± {cv_scores.std()*100:.2f}%")

    # 6. Evaluation on hold-out test set
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    
    print("\n✅ TRAINING COMPLETE")
    print(f"📈 Hold-out Accuracy: {acc*100:.2f}%")
    print("-" * 30)
    print(classification_report(y_test, y_pred))
    
    # 7. Feature Importance (permutation-based, compatible with HistGradientBoosting)
    from sklearn.inspection import permutation_importance
    print("\n🔍 FEATURE IMPORTANCE (permutation):")
    perm = permutation_importance(model, X_test, y_test, n_repeats=10, random_state=42, n_jobs=-1)
    importances = pd.Series(perm.importances_mean, index=features).sort_values(ascending=False)
    print(importances.to_string())
    
    # 8. Save Model
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)
        
    joblib.dump(model, MODEL_PATH)
    print(f"\n💾 Model saved to: {MODEL_PATH}")
    print("="*50)

if __name__ == "__main__":
    train()
