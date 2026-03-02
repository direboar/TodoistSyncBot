import main
import datetime
from unittest.mock import patch

def test_cases():
    with patch('main.datetime') as mock_dt:
        mock_dt.now.return_value = datetime.datetime(2026, 3, 1)
        cases = [
            ('2026年3月', True),
            ('2026年4月', True),
            ('2026年 3月', True),
            ('26年4月', True),
            ('令和8年3月', True),
            ('公民館-2026年3月', True),
            ('3月', False),
            ('4月', False),
            ('2026年2月', False),
            ('26年1月', False),
            ('2026年5月', False),
            ('2026年6月', False),
            ('2025年3月', False),
            ('2027年4月', False),
            ('令和7年3月', False)
        ]
        for name, expected in cases:
            actual = main.is_target_channel(name)
            if actual != expected:
                print(f"FAIL: '{name}' expected={expected}, got={actual}")

if __name__ == '__main__':
    test_cases()
