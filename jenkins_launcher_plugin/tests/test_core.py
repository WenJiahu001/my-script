# -*- coding: utf-8 -*-
import os
import json
import tempfile
import unittest
from unittest.mock import patch

import config
from history import get_job_weight
from jenkins_client import JenkinsClient
from main import JenkinsLauncher


class ConfigAndClientTests(unittest.TestCase):
    def test_environment_variable_reference_is_resolved(self):
        with patch.dict(os.environ, {"JENKINS_TEST_TOKEN": "secret-value"}):
            self.assertEqual(config._resolve_environment_value("${JENKINS_TEST_TOKEN}"), "secret-value")
        self.assertEqual(config._resolve_environment_value("${MISSING_VALUE}"), "")

    def test_cache_allows_stale_snapshot_only_when_requested(self):
        original_dir = config.CURRENT_DIR
        with tempfile.TemporaryDirectory() as tmp_dir:
            config.CURRENT_DIR = tmp_dir
            try:
                self.assertTrue(config.save_cache("dev", [{"name": "job"}], 1))
                self.assertEqual(config.load_cache("dev"), [{"name": "job"}])
                self.assertEqual(config.load_cache("dev", allow_stale=True), [{"name": "job"}])
            finally:
                config.CURRENT_DIR = original_dir

    def test_views_are_cached_independently(self):
        original_dir = config.CURRENT_DIR
        with tempfile.TemporaryDirectory() as tmp_dir:
            config.CURRENT_DIR = tmp_dir
            try:
                views = [{"name": "backend", "url": "https://jenkins/view/backend"}]
                self.assertTrue(config.save_views_cache("beta", views, 60))
                self.assertEqual(config.load_views_cache("beta"), views)
                self.assertTrue(config.clear_cache("beta"))
                self.assertIsNone(config.load_views_cache("beta"))
            finally:
                config.CURRENT_DIR = original_dir

    def test_deep_folder_is_fetched_level_by_level(self):
        client = JenkinsClient.__new__(JenkinsClient)
        client.env_key, client.env_name, client.config = "dev", "DEV", {}
        client._parse_job_status = lambda color: ("就绪", "icon.png")
        responses = {
            "https://jenkins/folder/a/api/json?tree=jobs[name,url,color,description,lastBuild[number,result],_class]": {"jobs": [{"name": "b", "url": "https://jenkins/folder/a/folder/b", "_class": "com.cloudbees.hudson.plugins.folder.Folder"}]},
            "https://jenkins/folder/a/folder/b/api/json?tree=jobs[name,url,color,description,lastBuild[number,result],_class]": {"jobs": [{"name": "deploy", "url": "https://jenkins/job/deploy", "color": "blue"}]},
        }
        client._request = lambda endpoint: (200, responses[endpoint], {})
        jobs = client._collect_jobs([{"name": "a", "url": "https://jenkins/folder/a", "_class": "com.cloudbees.hudson.plugins.folder.Folder"}])
        self.assertEqual(jobs[0]["display_name"], "a/b/deploy")

    def test_required_parameter_stops_unsafe_default_build(self):
        client = JenkinsClient.__new__(JenkinsClient)
        client.env_key, client.base_url = "dev", "https://jenkins"
        client._get_parameter_definitions = lambda url: (True, [{"name": "VERSION"}])
        client.get_crumb = lambda force_refresh=False: None
        with patch("jenkins_client.is_env_configured", return_value=(True, "已配置")):
            success, message = client.trigger_build("https://jenkins/job/demo")
        self.assertFalse(success)
        self.assertIn("VERSION", message)

    def test_history_snapshot_avoids_name_lookup_failure(self):
        self.assertEqual(get_job_weight("", "demo", {"url": {"name": "demo", "count": 2, "last_triggered_at": 1}}), (2, 1.0))

    def test_custom_environment_key_is_parsed_from_configuration(self):
        launcher = JenkinsLauncher.__new__(JenkinsLauncher)
        with patch("main.get_environments", return_value={"local": {}, "beta": {}, "release": {}}):
            self.assertEqual(launcher._parse_query_target("BeTa service"), ("beta", "service"))
            self.assertEqual(launcher._parse_query_target("local"), ("local", ""))

    def test_default_environment_falls_back_to_first_configured_environment(self):
        with patch("config.load_config", return_value={"default_environment": "missing", "environments": {"beta": {}, "local": {}}}):
            self.assertEqual(config.get_default_env_key(), "beta")

    def test_project_configuration_is_the_source_of_truth(self):
        self.assertEqual(os.path.dirname(config.get_config_path()), config.PLUGIN_DIR)
        self.assertTrue(config.get_config_path().endswith("config.json"))

    def test_project_config_is_mirrored_to_user_data(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = os.path.join(tmp_dir, "config.json")
            mirror_path = os.path.join(tmp_dir, "user", "config.json")
            original_config, original_user = config.CONFIG_FILE, config.USER_CONFIG_FILE
            config.CONFIG_FILE, config.USER_CONFIG_FILE = source_path, mirror_path
            try:
                payload = {"default_environment": "beta", "environments": {"beta": {}}}
                self.assertTrue(config.save_config(payload))
                loaded = config.load_config()
                self.assertEqual(loaded["default_environment"], "beta")
                with open(mirror_path, encoding="utf-8") as mirror:
                    self.assertEqual(json.load(mirror), loaded)
            finally:
                config.CONFIG_FILE, config.USER_CONFIG_FILE = original_config, original_user

    def test_parameter_metadata_error_fails_closed(self):
        client = JenkinsClient.__new__(JenkinsClient)
        client.env_key, client.base_url = "dev", "https://jenkins"
        client._get_parameter_definitions = lambda url: (False, [])
        with patch("jenkins_client.is_env_configured", return_value=(True, "已配置")):
            success, message = client.trigger_build("https://jenkins/job/demo")
        self.assertFalse(success)
        self.assertIn("无法读取", message)

    def test_invalid_environment_configuration_is_filtered(self):
        normalized = config._normalize_config({
            "default_environment": "../unsafe",
            "environments": {"../unsafe": {}, "beta": {"timeout": "invalid", "views": "invalid"}},
        })
        self.assertEqual(list(normalized["environments"]), ["beta"])
        self.assertEqual(normalized["environments"]["beta"]["timeout"], 5)
        self.assertEqual(normalized["environments"]["beta"]["views"], [])

    def test_copy_install_pointer_uses_original_project_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = os.path.join(tmp_dir, "project")
            os.makedirs(project_dir)
            open(os.path.join(project_dir, "config.json"), "w", encoding="utf-8").close()
            pointer_file = os.path.join(tmp_dir, "config_source_path.txt")
            with open(pointer_file, "w", encoding="utf-8") as pointer:
                pointer.write(project_dir)
            original_pointer = config.CONFIG_SOURCE_POINTER
            config.CONFIG_SOURCE_POINTER = pointer_file
            try:
                self.assertEqual(config._get_project_config_file(), os.path.join(project_dir, "config.json"))
            finally:
                config.CONFIG_SOURCE_POINTER = original_pointer

    def test_empty_job_result_is_cached(self):
        client = JenkinsClient.__new__(JenkinsClient)
        client.env_key, client.config, client.configured_views = "beta", {"cache_duration_seconds": 60}, []
        client._request = lambda endpoint: (200, {"jobs": []}, {})
        with patch("jenkins_client.save_cache") as save_cache:
            success, jobs = client._fetch_jobs()
        self.assertTrue(success)
        self.assertEqual(jobs, [])
        save_cache.assert_called_once_with("beta", [], 60)

    def test_cached_views_skip_jenkins_request(self):
        client = JenkinsClient.__new__(JenkinsClient)
        client.env_key, client.config = "beta", {"cache_duration_seconds": 60}
        client._request = lambda endpoint: self.fail("命中视图缓存时不应请求 Jenkins")
        cached_views = [{"name": "backend"}]
        with patch("jenkins_client.is_env_configured", return_value=(True, "已配置")), patch("jenkins_client.load_views_cache", return_value=cached_views):
            self.assertEqual(client.get_remote_views(), cached_views)

    def test_cache_miss_starts_background_refresh_without_blocking(self):
        client = JenkinsClient.__new__(JenkinsClient)
        client.env_key, client.env_name = "dev", "DEV"
        with patch("jenkins_client.is_env_configured", return_value=(True, "已配置")), patch("jenkins_client.load_cache", return_value=None), patch("jenkins_client.JenkinsManager.refresh_in_background") as refresh:
            success, message = client.get_jobs()
        self.assertFalse(success)
        self.assertIn("后台同步", message)
        refresh.assert_called_once_with("dev")

    @unittest.skipUnless(os.name == "nt", "仅验证 Windows 打开配置行为")
    def test_open_config_uses_notepad_for_deterministic_file_opening(self):
        launcher = JenkinsLauncher.__new__(JenkinsLauncher)
        with patch("main.get_config_path", return_value=r"C:\\temp\\config.json"), patch("main.os.path.exists", return_value=True), patch("main.subprocess.Popen") as popen:
            launcher.open_config()
        self.assertEqual(popen.call_args.args[0], ["notepad.exe", r"C:\\temp\\config.json"])

    def test_refresh_cache_reports_successful_job_count(self):
        launcher = JenkinsLauncher.__new__(JenkinsLauncher)
        launcher.manager = type("Manager", (), {"get_jobs_for_env": lambda self, env, force_refresh: (True, [{}, {}])})()
        with patch("main.clear_cache", return_value=True), patch("main.get_env_config", return_value={"name": "BETA"}), patch.object(launcher, "_show_message") as notify:
            launcher.refresh_cache("beta")
        notify.assert_called_once_with("Jenkins 缓存刷新成功", "【BETA】已同步，共获取 2 个流水线。")

    def test_refresh_cache_reports_sync_failure(self):
        launcher = JenkinsLauncher.__new__(JenkinsLauncher)
        launcher.manager = type("Manager", (), {"get_all_jobs": lambda self, force_refresh: (False, "网络超时")})()
        with patch("main.clear_cache", return_value=True), patch.object(launcher, "_show_message") as notify:
            launcher.refresh_cache("")
        self.assertEqual(notify.call_args.args[0], "Jenkins 缓存刷新失败")
        self.assertIn("网络超时", notify.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
