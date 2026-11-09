import json

def generate_multi_replace():
    with open('/home/chan/projects/algovibe/app/infrastructure/database.py', 'r') as f:
        content = f.read()

    target_content = """        tables = [
            'positions',
            'trade_history',
            'trade_analytics',
            'account_balances',
            'ticker_history',
            'order_logs',
            'system_events',
            'sector_history',
            'shadow_orders',
            'order_book',
            'market_depth'
        ]"""
    
    replacement_content = """        tables = [
            'positions',
            'trade_history',
            'trade_analytics',
            'account_balances',
            'ticker_history',
            'order_logs',
            'system_events',
            'sector_history',
            'shadow_orders',
            'order_book',
            'market_depth',
            'equity_performance'
        ]"""

    target_content2 = """                # Reset specific bot_state keys
                cursor.execute(
                    "UPDATE bot_state SET value = '0' WHERE key IN ('consecutive_losses', 'daily_target_hit')"
                )
                cursor.execute(
                    "UPDATE bot_state SET value = 'NORMAL' WHERE key = 'session_mode'"
                )"""

    replacement_content2 = """                # Reset specific bot_state keys
                cursor.execute(
                    "UPDATE bot_state SET value = '0' WHERE key IN ('consecutive_losses', 'daily_target_hit', 'peak_pnl_today', 'daily_profit_target', 'net_zero_target_floor', 'net_pnl_realized')"
                )
                cursor.execute(
                    "UPDATE bot_state SET value = 'NORMAL' WHERE key = 'session_mode'"
                )
                cursor.execute(
                    "UPDATE bot_state SET value = 'true' WHERE key = 'session_buy_enabled'"
                )
                cursor.execute(
                    "UPDATE bot_state SET value = 'false' WHERE key IN ('session_latched', 'target_protection_active', 'net_zero_survival_active')"
                )"""

    print(json.dumps([
        {
            "AllowMultiple": False,
            "TargetContent": target_content,
            "ReplacementContent": replacement_content,
            "StartLine": 1650,
            "EndLine": 1680
        },
        {
            "AllowMultiple": False,
            "TargetContent": target_content2,
            "ReplacementContent": replacement_content2,
            "StartLine": 1675,
            "EndLine": 1695
        }
    ]))

generate_multi_replace()
