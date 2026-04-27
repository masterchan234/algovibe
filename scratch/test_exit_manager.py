import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.getcwd())

# Mock engines.database.db before importing ExitManager
import engines.database
mock_db = MagicMock()
engines.database.db = mock_db

from engines.exit_manager import ExitManager

def test_handle_order_update():
    print("Testing handle_order_update...")
    # Mock positions
    mock_db.get_open_positions.return_value = {
        'ethidr': {
            'pair': 'ethidr',
            'entry_order_id': 'ORDER-123',
            'status': 'PENDING_BUY',
            'amount_crypto': 0.1
        }
    }
    
    manager = ExitManager(exchange_client=MagicMock(), dry_run=False)
    
    # Simulate filled order
    order_event = {
        'id': 'ORDER-123',
        'pair': 'ethidr',
        'status': 'filled'
    }
    
    manager.handle_order_update(order_event)
    
    # Check if update_position_status was called
    mock_db.update_position_status.assert_called_with('ethidr', 'OPEN')
    print("✅ handle_order_update test passed.")

def test_reconcile_with_exchange():
    print("\nTesting reconcile_with_exchange...")
    mock_db.get_open_positions.return_value = {
        'ethidr': {
            'pair': 'ethidr',
            'status': 'PENDING_BUY',
            'amount_crypto': 0.1
        },
        'btcidr': {
            'pair': 'btcidr',
            'status': 'OPEN',
            'amount_crypto': 0.01
        }
    }
    
    mock_exchange = MagicMock()
    mock_exchange.fetch_balance.return_value = {
        'total': {
            'ETH': 0.1,  # Matches ethidr PENDING_BUY
            'BTC': 0.005 # Less than 80% of 0.01 (Ghost)
        }
    }
    
    manager = ExitManager(exchange_client=mock_exchange, dry_run=False)
    manager.reconcile_with_exchange()
    
    # ethidr should be promoted to OPEN
    mock_db.update_position_status.assert_any_call('ethidr', 'OPEN')
    
    # btcidr should be deleted (ghost)
    mock_db.delete_position.assert_any_call('btcidr')
    
    print("✅ reconcile_with_exchange test passed.")

if __name__ == "__main__":
    test_handle_order_update()
    test_reconcile_with_exchange()
