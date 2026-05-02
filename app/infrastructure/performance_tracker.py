"""
Performance Tracker — Indodax Scalper Bot
==========================================
Menghitung profit/loss bersih per trade setelah pajak Indodax berdasarkan
regulasi PMK 68/2022 yang berlaku sejak 1 Mei 2022.

Struktur Biaya per Transaksi (Buy atau Sell):
  - Maker Fee    : 0.10% dari nilai transaksi
  - PPh Pasal 22 : 0.10% dari nilai transaksi (pajak penghasilan kripto)
  - PPN          : 0.11% dari nilai transaksi (Pajak Pertambahan Nilai)
  ──────────────────────────────────────────────────────────────
  Total per sisi : 0.31% per transaksi
  Total roundtrip: 0.62% (Buy + Sell)

Target Net Profit per trade (TP +0.8% gross): 0.80% - 0.62% = +0.18%
Stop Loss per trade (SL -0.45% gross)       : -0.45% - 0.62% = -1.07%
"""

import os
from datetime import datetime

from config import (
    MAKER_FEE, PPH_RATE, PPN_RATE, FEE_PER_SIDE, FEE_ROUNDTRIP,
    DAILY_REPORT,
)
from app.interfaces.telegram_bot import send_telegram_message
import config
from app.infrastructure.database import db

def calculate_24h_performance(reference_ts: float | None = None) -> dict:
    """
    Hitung ringkasan performa hari ini dari database trade_history.
    """
    dr_val = 1 if config.DRY_RUN else 0
    stats = db.get_daily_stats(dry_run=dr_val)

    total_trades    = stats['total_trades']
    winning_trades  = stats['win_count']
    losing_trades   = stats['loss_count']
    total_net_pnl   = stats['total_pnl_idr']
    avg_net_pnl     = total_net_pnl / total_trades if total_trades else 0.0
    win_rate        = (winning_trades / total_trades * 100) if total_trades else 0.0

    report = {
        "period":           "24h",
        "generated_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode":             "DRY RUN" if config.DRY_RUN else "LIVE",

        # ── Ringkasan Utama ──────────────────────────────────────────────────
        "total_trades":     total_trades,
        "win_count":        winning_trades,
        "loss_count":       losing_trades,
        "win_rate_pct":     round(win_rate,        2),
        "avg_net_pnl_idr":  round(avg_net_pnl,     2),

        # ── P&L ─────────────────────────────────────────────────────────────
        "total_net_pnl":    round(total_net_pnl,   2),

        # ── Fee Breakdown ────────────────────────────────────────────────────
        "fee_breakdown": {
            "maker_fee_pct":  f"{MAKER_FEE*100:.2f}%",
            "pph_pct":        f"{PPH_RATE*100:.2f}%",
            "ppn_pct":        f"{PPN_RATE*100:.2f}%",
            "per_side_pct":   f"{FEE_PER_SIDE*100:.2f}%",
            "roundtrip_pct":  f"{FEE_ROUNDTRIP*100:.2f}%",
        }
    }

    return report

def save_daily_report(report: dict) -> None:
    # Deprecated for raw JSON, but keeping stub if needed elsewhere
    pass

def print_daily_summary(report: dict) -> None:
    """Cetak ringkasan harian ke terminal dalam format yang mudah dibaca."""
    sep  = "=" * 60
    sep2 = "-" * 60

    lines = [
        sep,
        f"  📊 LAPORAN PERFORMA 24 JAM ({report['mode']}) — {report['generated_at']}",
        sep,
        f"  Total Trade    : {report['total_trades']}  "
            f"(Win: {report['win_count']} | Loss: {report['loss_count']})",
        f"  Win Rate       : {report['win_rate_pct']:.1f}%",
        f"  Avg Net/Trade  : Rp {report['avg_net_pnl_idr']:,.0f}",
        sep2,
        f"  ── Net PnL ──►  Rp {report['total_net_pnl']:>12,.2f}",
        sep2,
    ]

    if report["total_trades"] == 0:
        lines.append("  Belum ada trade selesai dalam 24 jam ini.")
        lines.append(sep)

    for line in lines:
        print(line)
        
    # Kirim ke Telegram (hilangkan karakter '=' dan '-' berlebih agar rapi)
    tg_lines = [line for line in lines if not line.startswith("===") and not line.startswith("---")]
    send_telegram_message("\n".join(tg_lines))


def run_performance_report(log_fn=None) -> dict:
    """
    Entry-point utama — dipanggil oleh main.py saat daily reset.
    Menghitung, menyimpan, dan mencetak laporan 24 jam.

    Args:
        log_fn: opsional, fungsi log_message dari main.py

    Returns:
        dict laporan performa.
    """
    report = calculate_24h_performance()
    save_daily_report(report)
    print_daily_summary(report)
    if log_fn:
        log_fn(
            f"[PERF] Net PnL 24h: Rp {report['total_net_pnl']:,.0f} | "
            f"Trades: {report['total_trades']} | Win: {report['win_rate_pct']:.0f}%"
        )
    return report


if __name__ == "__main__":
    run_performance_report()
