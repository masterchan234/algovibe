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

import json
import os
from datetime import datetime, timedelta

from config import (
    MAKER_FEE, PPH_RATE, PPN_RATE, FEE_PER_SIDE, FEE_ROUNDTRIP,
    EXIT_LOG, DAILY_REPORT
)
from engines.telegram_bot import send_telegram_message


def _load_exit_log() -> list[dict]:
    """Baca semua entri dari exit log (format NDJSON)."""
    if not os.path.exists(EXIT_LOG):
        return []
    entries = []
    with open(EXIT_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _calc_net_pnl(entry: dict) -> dict | None:
    """
    Hitung PnL bersih satu trade dari exit log entry.

    Exit log wajib mengandung field:
        entry_price   : float  — harga beli
        exit_price    : float  — harga jual (sudah tereksekusi)
        amount_idr    : float  — modal dalam IDR

    Return dict dengan breakdown biaya, atau None jika data tidak lengkap.
    """
    required = {"entry_price", "exit_price", "amount_idr"}
    if not required.issubset(entry.keys()):
        return None

    entry_price  = float(entry["entry_price"])
    exit_price   = float(entry["exit_price"])
    amount_idr   = float(entry["amount_idr"])   # modal IDR sisi beli
    reason       = entry.get("reason", "UNKNOWN")

    # Nilai transaksi sell
    # amount_crypto = amount_idr / entry_price
    amount_crypto = amount_idr / entry_price
    sell_value    = amount_crypto * exit_price

    # Gross PnL
    gross_pnl     = sell_value - amount_idr

    # Biaya
    fee_buy       = amount_idr * FEE_PER_SIDE    # biaya sisi beli
    fee_sell      = sell_value  * FEE_PER_SIDE   # biaya sisi jual
    total_fee     = fee_buy + fee_sell

    # Net PnL
    net_pnl       = gross_pnl - total_fee

    return {
        "timestamp":    entry.get("timestamp"),
        "pair":         entry.get("pair", "UNKNOWN"),
        "reason":       reason,
        "entry_price":  entry_price,
        "exit_price":   exit_price,
        "amount_idr":   amount_idr,
        "gross_pnl":    round(gross_pnl,  2),
        "fee_buy":      round(fee_buy,    2),
        "fee_sell":     round(fee_sell,   2),
        "total_fee":    round(total_fee,  2),
        "net_pnl":      round(net_pnl,    2),
        "net_pnl_pct":  round((net_pnl / amount_idr) * 100, 4),
        "is_win":       net_pnl > 0,
    }


def calculate_24h_performance(reference_ts: float | None = None) -> dict:
    """
    Hitung ringkasan performa 24 jam terakhir dari exit log.

    Args:
        reference_ts: Unix timestamp acuan. Default = sekarang.

    Returns:
        dict berisi ringkasan lengkap performa harian.
    """
    now_ts     = reference_ts or datetime.now().timestamp()
    cutoff_ts  = now_ts - 86400  # 24 jam ke belakang

    all_entries = _load_exit_log()
    window      = [e for e in all_entries
                   if e.get("timestamp", 0) >= cutoff_ts]

    trades      = []
    for entry in window:
        result = _calc_net_pnl(entry)
        if result:
            trades.append(result)

    total_trades     = len(trades)
    winning_trades   = [t for t in trades if t["is_win"]]
    losing_trades    = [t for t in trades if not t["is_win"]]

    total_net_pnl    = sum(t["net_pnl"]   for t in trades)
    total_gross_pnl  = sum(t["gross_pnl"] for t in trades)
    total_fees_paid  = sum(t["total_fee"] for t in trades)
    total_volume     = sum(t["amount_idr"] for t in trades)

    win_rate         = (len(winning_trades) / total_trades * 100) if total_trades else 0.0
    avg_net_pnl      = (total_net_pnl / total_trades) if total_trades else 0.0

    best_trade       = max(trades, key=lambda t: t["net_pnl"], default=None)
    worst_trade      = min(trades, key=lambda t: t["net_pnl"], default=None)

    tp_count         = sum(1 for t in trades if t["reason"] == "TAKE_PROFIT")
    sl_count         = sum(1 for t in trades if t["reason"] == "STOP_LOSS")

    report = {
        "period":           "24h",
        "generated_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_start":     datetime.fromtimestamp(cutoff_ts).strftime("%Y-%m-%d %H:%M:%S"),
        "window_end":       datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S"),

        # ── Ringkasan Utama ──────────────────────────────────────────────────
        "total_trades":     total_trades,
        "tp_hits":          tp_count,
        "sl_hits":          sl_count,
        "win_rate_pct":     round(win_rate,        2),
        "avg_net_pnl_idr":  round(avg_net_pnl,     2),

        # ── P&L ─────────────────────────────────────────────────────────────
        "total_gross_pnl":  round(total_gross_pnl, 2),
        "total_fees_paid":  round(total_fees_paid, 2),
        "total_net_pnl":    round(total_net_pnl,   2),
        "roi_pct":          round((total_net_pnl / total_volume * 100)
                                   if total_volume else 0.0, 4),

        # ── Fee Breakdown ────────────────────────────────────────────────────
        "fee_breakdown": {
            "maker_fee_pct":  f"{MAKER_FEE*100:.2f}%",
            "pph_pct":        f"{PPH_RATE*100:.2f}%",
            "ppn_pct":        f"{PPN_RATE*100:.2f}%",
            "per_side_pct":   f"{FEE_PER_SIDE*100:.2f}%",
            "roundtrip_pct":  f"{FEE_ROUNDTRIP*100:.2f}%",
        },

        # ── Trade Extremes ───────────────────────────────────────────────────
        "best_trade": {
            "pair":        best_trade["pair"]        if best_trade else None,
            "net_pnl_idr": best_trade["net_pnl"]     if best_trade else None,
            "net_pnl_pct": best_trade["net_pnl_pct"] if best_trade else None,
            "reason":      best_trade["reason"]      if best_trade else None,
        },
        "worst_trade": {
            "pair":        worst_trade["pair"]        if worst_trade else None,
            "net_pnl_idr": worst_trade["net_pnl"]     if worst_trade else None,
            "net_pnl_pct": worst_trade["net_pnl_pct"] if worst_trade else None,
            "reason":      worst_trade["reason"]      if worst_trade else None,
        },

        # ── Detail trades (opsional, untuk debugging) ────────────────────────
        "trades": trades,
    }

    return report


def save_daily_report(report: dict) -> None:
    """Simpan laporan ke file JSON, diindeks per tanggal."""
    today = datetime.now().strftime("%Y-%m-%d")

    history = {}
    if os.path.exists(DAILY_REPORT):
        with open(DAILY_REPORT, "r") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = {}

    history[today] = report

    with open(DAILY_REPORT, "w") as f:
        json.dump(history, f, indent=4)


def print_daily_summary(report: dict) -> None:
    """Cetak ringkasan harian ke terminal dalam format yang mudah dibaca."""
    sep  = "=" * 60
    sep2 = "-" * 60

    lines = [
        sep,
        f"  📊 LAPORAN PERFORMA 24 JAM — {report['generated_at']}",
        sep,
        f"  Periode   : {report['window_start']}  →  {report['window_end']}",
        sep2,
        f"  Total Trade    : {report['total_trades']}  "
            f"(TP: {report['tp_hits']} | SL: {report['sl_hits']})",
        f"  Win Rate       : {report['win_rate_pct']:.1f}%",
        f"  Avg Net/Trade  : Rp {report['avg_net_pnl_idr']:,.0f}",
        sep2,
        f"  Gross PnL      : Rp {report['total_gross_pnl']:>12,.2f}",
        f"  Total Fee Paid : Rp {report['total_fees_paid']:>12,.2f}  "
            f"(roundtrip {FEE_ROUNDTRIP*100:.2f}% / trade)",
        f"  ── Net PnL ──►  Rp {report['total_net_pnl']:>12,.2f}  "
            f"(ROI {report['roi_pct']:.2f}%)",
        sep2,
    ]

    if report["best_trade"]["pair"]:
        bt = report["best_trade"]
        wt = report["worst_trade"]
        lines += [
            f"  Best Trade  : {bt['pair']:10}  +Rp {bt['net_pnl_idr']:,.0f}"
                f"  ({bt['net_pnl_pct']:+.2f}%)  [{bt['reason']}]",
            f"  Worst Trade : {wt['pair']:10}   Rp {wt['net_pnl_idr']:,.0f}"
                f"  ({wt['net_pnl_pct']:+.2f}%)  [{wt['reason']}]",
        ]
    else:
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
