"""Agent 调度约定的追加与引用路径回归；不启动服务或访问用户电脑。"""
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import agent_workflow as workflow
import api_inventory as api


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent policy ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.toolkit = self.base / "tool kit's;$folder"
        self.root = self.base / "service root's;$folder"
        self.models = self.base / "model root's;$folder"
        self.models.mkdir()

    def append(self, old=''):
        return workflow.append_workflow(old, self.root, self.models, self.toolkit)

    def test_policy_is_user_requested_and_preserves_warm_llm(self):
        text = self.append()
        for phrase in ('用户指定的资源使用策略', '不是各框架天生不能并行', '不关闭或卸载 LLM', 'LLM 已加载并能响应', '不默认启动全部', '不是任务取消授权', 'JSON 中的 unknown'):
            self.assertIn(phrase, text)
        self.assertNotIn('--if-idle', text)
        self.assertNotIn(' stop --service all', text)

    def test_all_commands_roundtrip_absolute_paths_with_shell_metacharacters(self):
        commands = [line for line in self.append().splitlines() if line.startswith('python3 ')]
        self.assertEqual(len(commands), 8)
        for command in commands:
            args = shlex.split(command)
            self.assertEqual(args[:6], ['python3', str(self.toolkit / 'scripts/native_services.py'), '--root', str(self.root), '--models-dir', str(self.models)])
            self.assertIn(args[6], ('status', 'start', 'stop'))

    def test_repeated_policy_preserves_existing_manual_crlf_bytes(self):
        old = '# 手写\r\n保留原文\r\n'
        first = self.append(old)
        self.assertTrue(first.startswith(old))
        self.assertEqual(self.append(first), first)
        self.assertEqual(first.count('MAC-AI-KIT:AGENT-WORKFLOW:'), 1)

    def test_relocated_toolkit_or_roots_append_current_paths_without_rewriting(self):
        first = self.append('manual\n')
        second = workflow.append_workflow(first, self.root, self.models, self.base / 'new toolkit')
        third = workflow.append_workflow(second, self.base / 'new services', self.models, self.base / 'new toolkit')
        self.assertTrue(second.startswith(first))
        self.assertTrue(third.startswith(second))
        self.assertEqual(third.count('MAC-AI-KIT:AGENT-WORKFLOW:'), 3)
        self.assertIn('以最后追加的本节为准', third)

    def test_newline_path_is_rejected(self):
        with self.assertRaises(ValueError):
            workflow.append_workflow('', self.root, self.models, self.base / 'bad\npath')

    def test_return_to_prior_toolkit_appends_current_path_as_latest(self):
        first = self.append('manual\n')
        moved = workflow.append_workflow(first, self.root, self.models, self.base / 'new toolkit')
        returned = self.append(moved)
        self.assertTrue(returned.startswith(moved))
        self.assertEqual(returned.count('MAC-AI-KIT:AGENT-WORKFLOW:'), 3)
        self.assertIn(shlex.quote(str(self.toolkit / 'scripts/native_services.py')), returned[len(moved):])
        self.assertEqual(self.append(returned), returned)

    def test_explicit_api_update_appends_policy_once_outside_replaceable_block(self):
        original = '# 手工说明\r\n'
        target = self.models / 'API.md'
        target.write_bytes(original.encode())
        block = api.BEGIN + '\nfirst\n' + api.END + '\n'
        context = dict(service_root=self.root, models_dir=self.models, toolkit_root=self.toolkit)
        api.update_document(self.models, block, agent_context=context)
        first = target.read_bytes()
        before = target.stat().st_mtime_ns
        self.assertTrue(first.startswith(original.encode()))
        self.assertEqual(api.update_document(self.models, block, agent_context=context), '无变化')
        self.assertEqual(target.stat().st_mtime_ns, before)
        api.update_document(self.models, block.replace('first', 'second'), agent_context=context)
        content = target.read_text()
        self.assertEqual(content.count('MAC-AI-KIT:AGENT-WORKFLOW:'), 1)
        self.assertIn('second', content)
        self.assertGreater(content.index('MAC-AI-KIT:AGENT-WORKFLOW'), content.index(api.END))

    def test_explicit_main_uses_real_toolkit_and_passed_service_root(self):
        catalog = self.base / 'catalog.json'; catalog.write_text('{"models": []}')
        templates = self.base / 'templates.json'; templates.write_text('{"runtimes": []}')
        with patch('builtins.print'):
            result = api.main(['--directory', str(self.models), '--catalog', str(catalog), '--templates', str(templates), '--services-root', str(self.root)])
        self.assertEqual(result, 0)
        content = (self.models / 'API.md').read_text()
        self.assertIn(shlex.quote(str(ROOT / 'scripts/native_services.py')), content)
        self.assertIn(shlex.quote(str(self.root)), content)
        self.assertFalse(self.root.exists())

    def test_if_present_keeps_empty_directory_unchanged(self):
        catalog = self.base / 'catalog.json'; catalog.write_text('{"models": []}')
        templates = self.base / 'templates.json'; templates.write_text('{"runtimes": []}')
        with patch('builtins.print'):
            result = api.main(['--directory', str(self.models), '--catalog', str(catalog), '--templates', str(templates), '--if-present'])
        self.assertEqual(result, 0)
        self.assertFalse((self.models / 'API.md').exists())

    def test_health_and_unload_are_not_claimed_as_atomic_stop_guards(self):
        text = self.append()
        for phrase in ('所有提交方停止新请求', '所有同步与异步任务及队列已完成', '向用户确认当前可停止', '不是全局停机屏障', '退出码非零', 'model_residency=empty/ready 也不是 idle'):
            self.assertIn(phrase, text)


if __name__ == '__main__':
    unittest.main()
