"""验证 Remote 打包可复现；仅操作自建的临时测试文件。"""
import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_remote', ROOT / 'scripts/build_remote.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class RemoteBuildTests(unittest.TestCase):
    def test_committed_launcher_matches_sources(self):
        self.assertEqual(build.render(), (ROOT / 'remote/start-remotion-remote.sh').read_bytes())

    def test_renders_every_component_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'remote/src'
            source.mkdir(parents=True)
            markers = [('RUNTIME', 'runtime.cjs'), ('GUARD', 'bind-guard.cjs'), ('ADAPTER', 'studio-adapter.cjs')]
            (source / 'launcher.sh').write_text('\n'.join(f'{m}_PAYLOAD_PLACEHOLDER' for m, _ in markers))
            for _, name in markers:
                (source / name).write_bytes(('测试 ' + name).encode())
            rendered = build.render(root).decode().splitlines()
            for row, (_, name) in zip(rendered, markers):
                self.assertEqual(base64.b64decode(row), (source / name).read_bytes())
            (source / 'launcher.sh').write_text('缺少占位符')
            with self.assertRaises(ValueError):
                build.render(root)


if __name__ == '__main__':
    unittest.main()
