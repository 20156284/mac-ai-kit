"""发行解析与安装离线回归：自造 Mach-O，不执行第三方程序。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

MODULE = Path(__file__).resolve().parents[1] / 'scripts/sd_release.py'
spec = importlib.util.spec_from_file_location('sd_release', MODULE)
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)


def macho(version=(13, 0, 0), cpu=0x100000c, capability=True):
    v = version[0] << 16 | version[1] << 8 | version[2]
    header = struct.pack('<8I', 0xfeedfacf, cpu, 0, 2, 1, 24, 0, 0)
    commands = struct.pack('<6I', 0x32, 24, 1, v, v, 0)
    strings = b'--model-dir\0/sdcpp/v1/models/load\0/sdcpp/v1/models/status\0' if capability else b''
    return header + commands + strings


def release(tag='test-latest', digest='1' * 64):
    name = 'sd-test-bin-Darwin-macOS-99.0-arm64.zip'
    return dict(tag_name=tag, draft=False, prerelease=False, assets=[dict(name=name,
        browser_download_url=sd.DOWNLOAD + tag + '/' + name, digest='sha256:' + digest)])


class ResolverTests(unittest.TestCase):
    def test_official_latest_dynamic_no_numeric_guess(self):
        with mock.patch.object(sd, 'read_json_url', return_value=release('master-9999-new')) as get:
            plan = sd.resolve_latest()
        get.assert_called_once_with(sd.API + 'latest')
        self.assertEqual(plan['tag'], 'master-9999-new')
        self.assertEqual(plan['sha256'], '1' * 64)

    def test_missing_digest_ambiguous_unofficial_prerelease_fail_closed(self):
        for variant in ('missing', 'ambiguous', 'url', 'prerelease', 'arch'):
            data = release()
            if variant == 'missing': data['assets'][0]['digest'] = None
            if variant == 'ambiguous': data['assets'] *= 2
            if variant == 'url': data['assets'][0]['browser_download_url'] = 'https://evil.example/archive.zip'
            if variant == 'prerelease': data['prerelease'] = True
            if variant == 'arch': data['assets'][0]['name'] = 'Darwin-x64.zip'
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                sd.release_plan(data)

    def test_plan_rejects_path_and_digest_injection(self):
        for key, value in [('tag', '../bad'), ('asset_name', '../Darwin-arm64.zip'), ('sha256', 'bad')]:
            plan = sd.release_plan(release())
            plan[key] = value
            with self.assertRaises(ValueError): sd.validate_plan(plan)


class MachoTests(unittest.TestCase):
    def test_thin_and_fat_arm64_minimum(self):
        thin = macho((15, 2, 3))
        self.assertEqual(sd.macho_minimum(thin), (15, 2, 3))
        header = struct.pack('>II5I', 0xcafebabe, 1, 0x100000c, 0, 28, len(thin), 0)
        self.assertEqual(sd.macho_minimum(header + thin), (15, 2, 3))

    def test_scripts_wrong_arch_truncated_and_missing_commands_rejected(self):
        for data in (b'#!/bin/sh\nexit 0', macho(cpu=0x1000007), macho()[:40], struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 0, 0, 0, 0)):
            with self.assertRaises(ValueError): sd.macho_minimum(data)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "AI with 'quotes"
        self.archive = self.base / 'fixture.zip'
        self.write_archive()
        self.plan = sd.release_plan(release(digest=sd.sha256(self.archive)))
        self.host = mock.patch.object(sd, 'check_host')
        self.stop = mock.patch.object(sd, 'assert_stopped')
        self.host.start(); self.stop.start()
        self.addCleanup(self.host.stop); self.addCleanup(self.stop.stop)
        def fetch(plan, target):
            shutil.copyfile(self.archive, target)
            if sd.sha256(target) != plan['sha256']:
                raise ValueError('SHA256 校验不匹配')
        self.fetch = mock.patch.object(sd, 'download', side_effect=fetch)
        self.download = self.fetch.start(); self.addCleanup(self.fetch.stop)

    def write_archive(self, extras=(), capability=True):
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('bin/sd-server', macho(capability=capability))
            z.writestr('lib/libsd.dylib', macho())
            for name, data in extras: z.writestr(name, data)

    @property
    def target(self): return self.root / 'apps/sd.cpp'

    @property
    def entry(self): return self.root / 'bin/sd-server'

    def install(self): return sd.install(self.root, self.plan)

    def test_full_package_stable_target_and_offline_verified_reuse(self):
        result = self.install()
        self.assertEqual(result['installation_path'], str(self.target))
        self.assertEqual(result['minimum_macos'], '13.0.0')
        self.assertTrue((self.target / 'lib/libsd.dylib').is_file())
        self.assertEqual(self.entry.read_text(), sd.wrapper_text(self.target / 'bin/sd-server'))
        self.install()
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(sd.validate_installed(self.target)['tag'], 'test-latest')

    def test_tampered_or_missing_companion_blocks_reuse_and_update(self):
        for mutation in ('delete', 'edit', 'extra'):
            with self.subTest(mutation=mutation):
                if self.root.exists(): shutil.rmtree(self.root)
                self.install()
                path = self.target / 'lib/libsd.dylib'
                if mutation == 'delete': path.unlink()
                elif mutation == 'edit': path.write_bytes(macho((14, 0, 0)))
                else: (self.target / 'unexpected').write_text('user data')
                with self.assertRaises(ValueError): self.install()

    def test_external_entry_and_broken_symlink_preserved(self):
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text('user entry')
        result = self.install()
        self.assertFalse(result['entry_updated'])
        self.assertEqual(self.entry.read_text(), 'user entry')
        self.entry.unlink(); self.entry.symlink_to(self.base / 'absent')
        self.install()
        self.assertTrue(self.entry.is_symlink())

    def test_upgrade_has_stable_name_and_only_one_managed_backup(self):
        self.install()
        for tag in ('release-two', 'release-three'):
            self.plan = sd.release_plan(release(tag, sd.sha256(self.archive)))
            self.install()
        self.assertEqual(sd.validate_installed(self.target)['tag'], 'release-three')
        self.assertEqual(sd.validate_installed(self.root / 'apps/.sd.cpp.previous')['tag'], 'release-two')
        self.assertEqual(sorted(p.name for p in (self.root / 'apps').iterdir()), ['.sd.cpp.previous', 'sd.cpp'])

    def test_running_service_and_incompatible_host_leave_existing_untouched(self):
        self.install(); before = self.entry.read_bytes()
        self.plan = sd.release_plan(release('new', sd.sha256(self.archive)))
        for method in ('assert_stopped', 'check_host'):
            with mock.patch.object(sd, method, side_effect=ValueError('blocked')):
                with self.assertRaises(ValueError): self.install()
            self.assertEqual(sd.validate_installed(self.target)['tag'], 'test-latest')
            self.assertEqual(self.entry.read_bytes(), before)

    def test_unknown_target_and_symlink_ancestors_rejected(self):
        self.target.mkdir(parents=True)
        (self.target / 'user-data').write_text('keep')
        with self.assertRaises(ValueError): self.install()
        self.assertEqual((self.target / 'user-data').read_text(), 'keep')
        shutil.rmtree(self.root)
        self.root.symlink_to(self.base / 'elsewhere')
        with self.assertRaises(ValueError): self.install()

    def test_entry_changed_during_download_is_not_overwritten(self):
        original = self.download.side_effect
        self.entry.parent.mkdir(parents=True)
        def race(plan, path):
            original(plan, path)
            self.entry.write_text('user created this')
        self.download.side_effect = race
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(self.entry.read_text(), 'user created this')
        self.assertFalse(self.target.exists())

    def test_hash_mismatch_and_missing_model_management_rejected(self):
        self.plan['sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.install()
        self.write_archive(capability=False)
        self.plan = sd.release_plan(release(digest=sd.sha256(self.archive)))
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(self.target.exists())

    def test_unsafe_archives_rejected(self):
        for name in ('../escape', '/absolute', 'bin/../escape', 'bin/sd-server', 'BIN/SD-SERVER', 'bin//double', sd.RECEIPT):
            with self.subTest(name=name):
                self.write_archive([(name, b'bad')])
                self.plan = sd.release_plan(release(digest=sd.sha256(self.archive)))
                with self.assertRaises(ValueError): self.install()
                self.assertFalse(self.target.exists())
        self.write_archive()
        with zipfile.ZipFile(self.archive, 'a') as z:
            symlink = zipfile.ZipInfo('link'); symlink.external_attr = 0o120777 << 16
            z.writestr(symlink, '/outside')
        self.plan = sd.release_plan(release(digest=sd.sha256(self.archive)))
        with self.assertRaises(ValueError): self.install()

    def test_directory_flag_alone_cannot_pass_missing_management_routes(self):
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('sd-server', macho(capability=False) + b'--model-dir')
        self.plan = sd.release_plan(release(digest=sd.sha256(self.archive)))
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(self.target.exists())

    def test_untrusted_legacy_digest_does_not_replace_entry(self):
        old = self.root / 'apps/sd.cpp-old'
        binary = old / 'sd-server'; old.mkdir(parents=True)
        binary.write_bytes(macho()); binary.chmod(0o755)
        old_release = release('old')
        (old / '.lean-ai-release').write_text('0' * 64 + '  ' + old_release['assets'][0]['browser_download_url'] + '\n')
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text(sd.legacy_wrapper_text(binary))
        before = self.entry.read_bytes()
        with mock.patch.object(sd, 'read_json_url', return_value=old_release): self.install()
        self.assertEqual(self.entry.read_bytes(), before)

    def test_rename_failure_rolls_back_previous_runtime(self):
        self.install()
        self.plan = sd.release_plan(release('new', sd.sha256(self.archive)))
        original = Path.rename
        def fail(path, target):
            if path.name == 'payload': raise OSError('simulated publication failure')
            return original(path, target)
        with mock.patch.object(Path, 'rename', fail):
            with self.assertRaises(OSError): self.install()
        self.assertEqual(sd.validate_installed(self.target)['tag'], 'test-latest')

    def test_wrapper_publication_failure_rolls_back_runtime_and_entry(self):
        self.install()
        before = self.entry.read_bytes()
        self.plan = sd.release_plan(release('new', sd.sha256(self.archive)))
        with mock.patch.object(sd.os, 'replace', side_effect=OSError('入口无法发布')):
            with self.assertRaises(OSError): self.install()
        self.assertEqual(sd.validate_installed(self.target)['tag'], 'test-latest')
        self.assertEqual(self.entry.read_bytes(), before)

    def test_loaded_correct_native_label_blocks_without_running_sd(self):
        # 恢复真实检查函数；只模拟系统查询，不执行已安装的程序。
        self.stop.stop()
        with mock.patch.object(sd.platform, 'system', return_value='Darwin'), mock.patch.object(sd.platform, 'machine', return_value='arm64'), mock.patch.object(sd.subprocess, 'run') as run:
            run.return_value.returncode = 0
            with self.assertRaises(ValueError): sd.assert_stopped(self.root)
            args = run.call_args.args[0]
            self.assertEqual(args[:2], ['/bin/launchctl', 'print'])
            suffix = hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
            self.assertEqual(args[2], f'gui/{os.getuid()}/org.mac-ai-kit.native.sd-qwen.{suffix}')
            self.assertEqual(run.call_count, 1)

    def test_macos_build_filename_is_not_minimum(self):
        self.host.stop()
        with mock.patch.object(sd.platform, 'system', return_value='Darwin'), mock.patch.object(sd.platform, 'machine', return_value='arm64'), mock.patch.object(sd.platform, 'mac_ver', return_value=('13.0', ('', '', ''), '')):
            self.install()
            with self.assertRaises(ValueError): sd.check_host('26.0.0')

    def test_exact_legacy_wrapper_requires_official_digest_and_preserves_old_folder(self):
        old = self.root / 'apps/sd.cpp-old'
        binary = old / 'bin/sd-server'; binary.parent.mkdir(parents=True)
        binary.write_bytes(macho()); binary.chmod(0o755)
        old_release = release('old')
        (old / '.lean-ai-release').write_text('1' * 64 + '  ' + old_release['assets'][0]['browser_download_url'] + '\n')
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text(sd.legacy_wrapper_text(binary))
        with mock.patch.object(sd, 'read_json_url', return_value=old_release): self.install()
        self.assertEqual(self.entry.read_text(), sd.wrapper_text(self.target / 'bin/sd-server'))
        self.assertTrue(binary.exists())


if __name__ == '__main__':
    unittest.main()
