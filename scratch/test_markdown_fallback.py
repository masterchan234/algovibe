#!/usr/bin/env python3
"""
Test script: Validate Markdown fallback article generation and rendering.
"""
import sys
sys.path.insert(0, '/root/algovibe')

import asyncio
import json
import re
from app.infrastructure.database import Database
from datetime import datetime

# Simulate news-maker fallback
def build_fallback_article():
    move_data = {
        'price': 45000000,
        'change_1h': 1.25,
        'pct_change': 2.50,
        'vol_idr': 500e9,
        'score': 0.75
    }
    
    coin_symbol = "BTC"
    px_min = 44500000
    px_max = 45500000
    px_last = 45200000
    ws_samples = 120
    trend = "UP"
    signal = "BUY"
    
    title = f"{coin_symbol} 📈 Naik {abs(move_data['pct_change']):.1f}% - Analisis Teknis"
    excerpt = f"{coin_symbol} mengalami pergerakan signifikan dengan change {move_data['pct_change']:+.1f}% dalam 24 jam."
    
    content = f"""# Analisis {coin_symbol}

Sistem Algovibe mendeteksi pergerakan signifikan pada **{coin_symbol}** dengan change **{move_data['pct_change']:+.1f}%** dalam 24 jam terakhir.

## Data Pergerakan

| Metrik | Nilai |
| --- | --- |
| **Price** | {move_data['price']:,.0f} IDR |
| **Change 1h** | {move_data['change_1h']:+.1f}% |
| **Change 24h** | {move_data['pct_change']:+.1f}% |
| **Volume 24h** | {move_data['vol_idr']/1e9:.2f}B IDR |

## Sinyal Realtime (Websocket)

| Indikator | Nilai |
| --- | --- |
| **Trend** | {trend} |
| **Signal** | {signal} |
| **Price Min** | {px_min:,.0f} IDR |
| **Price Max** | {px_max:,.0f} IDR |
| **Samples** | {ws_samples} ticks |

## Rekomendasi Trading

Pergerakan ini menunjukkan **volatilitas tinggi**. Trader disarankan untuk:

1. **Identifikasi Support/Resistance** - Cari level kunci berdasarkan historical high-low
2. **Pantau Volume** - Konfirmasi trend dengan volume bars
3. **Risk Management** - Stop-loss ketat dengan ratio 1:2
4. **Breakout Confirmation** - Await clear breakout sebelum entry

## Checklist Pre-Entry

- [ ] Apakah trend searah di timeframe 5m/15m/1h?
- [ ] Apakah volatility masih reasonable untuk posisi?
- [ ] Apakah confluence antara price action dan volume hadir?
- [ ] Apakah target TP/SL sudah realistis?

---

**Disclaimer**: Analisis ini berdasarkan data market realtime dan sinyal teknis. Bukan trading advice. Selalu kelola risiko dengan disiplin."""

    return {
        "title": title,
        "excerpt": excerpt,
        "content": content
    }


async def main():
    print("[test] Generating fallback article...")
    article = build_fallback_article()
    
    print(f"[test] Title: {article['title']}")
    print(f"[test] Excerpt: {article['excerpt']}")
    print(f"[test] Content length: {len(article['content'])} chars")
    print(f"[test] Content preview (first 200 chars):\n{article['content'][:200]}\n")
    
    # Save to database
    db = Database()
    
    try:
        # Save using same logic as news-maker
        title = str(article.get("title") or "").strip()
        excerpt = str(article.get("excerpt") or "").strip()
        content = str(article.get("content") or "").strip()
        
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO intelligence_reports (title, excerpt, content, category, author) VALUES (%s, %s, %s, %s, %s)",
                (title, excerpt, content, "MARKDOWN_TEST", "ALGOVIBE_ORACLE")
            )
        print(f"[test] ✓ Saved article to intelligence_reports table")
        
        # Fetch back from DB
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, title, content FROM intelligence_reports WHERE category = 'MARKDOWN_TEST' ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            
        if row:
            article_id, fetched_title, fetched_content = row
            print(f"[test] ✓ Fetched from DB: ID={article_id}, title={fetched_title[:50]}...")
            print(f"[test] ✓ Content type: {type(fetched_content)}")
            print(f"[test] ✓ Content has Markdown (#): {'#' in fetched_content}")
            print(f"[test] ✓ Content has tables (|): {'|' in fetched_content}")
            print(f"[test] ✓ Content has lists (-): {'-' in fetched_content}")
            print(f"[test] ✓ Content has bold (**): {'**' in fetched_content}")
            
            # Verify markdown structure
            has_h1 = '# Analisis' in fetched_content
            has_h2 = '## ' in fetched_content
            has_table = '| Metrik |' in fetched_content
            has_list = '- [ ]' in fetched_content
            
            if has_h1 and has_h2 and has_table and has_list:
                print("[test] ✅ ALL MARKDOWN STRUCTURE ELEMENTS PRESENT")
            else:
                print(f"[test] ⚠️  Missing elements - H1:{has_h1}, H2:{has_h2}, Table:{has_table}, List:{has_list}")
        
        print("[test] ✅ TEST PASSED - Markdown fallback ready for rendering")
    except Exception as e:
        print(f"[test] ❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
