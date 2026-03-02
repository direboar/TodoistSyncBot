import pytest
from datetime import datetime
from unittest.mock import patch
from main import is_target_channel

class TestIsTargetChannel:
    
    @patch('main.datetime')
    def test_target_channels(self, mock_datetime):
        # 現在時刻を 2026年3月 に固定
        mock_datetime.now.return_value = datetime(2026, 3, 1)
        
        # ---------------------------
        # Trueになるべきケース (2026年3月, 2026年4月)
        # ---------------------------
        # 当月・翌月 (年あり)
        assert is_target_channel("2026年3月") is True
        assert is_target_channel("2026年4月") is True
        
        # 表記揺れ
        assert is_target_channel("2026年 3月") is True
        assert is_target_channel("26年4月") is True
        assert is_target_channel("令和8年3月") is True
        
        # カテゴリ名などが付与されているケース
        assert is_target_channel("公民館-2026年3月") is True

        # ---------------------------
        # Falseになるべきケース
        # ---------------------------
        # 年省略 (年の指定がない場合は対象外とする)
        assert is_target_channel("3月") is False
        assert is_target_channel("4月") is False
        # 過去の月
        assert is_target_channel("2026年2月") is False
        assert is_target_channel("26年1月") is False
        
        # 未来の月 (2ヶ月以上先)
        assert is_target_channel("2026年5月") is False
        assert is_target_channel("2026年6月") is False
        
        # 異なる年で、月だけ当月・翌月と同じ
        assert is_target_channel("2025年3月") is False
        assert is_target_channel("2027年4月") is False
        assert is_target_channel("令和7年3月") is False

