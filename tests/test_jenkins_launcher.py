# -*- coding: utf-8 -*-
"""Jenkins 右键菜单与控制台日志自动化测试"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PLUGIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "jenkins_launcher_plugin")
)
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import config
import history
import main
from jenkins_client import JenkinsClient, JenkinsManager
from main import JenkinsLauncher


class TestContextMenu(unittest.TestCase):
    def setUp(self):
        config.clear_cache()

    def test_context_menu_with_job(self):
        """测试正常流水线项目的右键菜单 (必须包含控制台日志)"""
        plugin = JenkinsLauncher()
        menus = plugin.context_menu(["dev", "http://192.168.1.61:8501/job/qm-spds/", "qm-spds"])
        self.assertTrue(len(menus) >= 4)
        first_item = menus[0]
        self.assertIn("查看控制台构建日志", first_item["Title"])
        self.assertEqual(
            first_item["JsonRPCAction"]["parameters"][0],
            "http://192.168.1.61:8501/job/qm-spds/lastBuild/console",
        )

    def test_context_menu_with_none_or_quick_action(self):
        """测试在快捷设置项或空参数上右键时的兜底菜单 (不应报错且应返回可用菜单)"""
        plugin = JenkinsLauncher()
        menus_none = plugin.context_menu(None)
        self.assertTrue(len(menus_none) >= 2)
        titles = [m["Title"] for m in menus_none]
        self.assertTrue(any("[refresh]" in t for t in titles))
        self.assertTrue(any("[config]" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
