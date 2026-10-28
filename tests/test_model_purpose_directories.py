"""按用途分开音乐与语音目录，不改变上游组织名与文件格式。"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PurposeDirectories(unittest.TestCase):
    def setUp(self):
        self.models = {m['id']: m for m in json.loads((ROOT / 'catalog.json').read_text())['models']}

    def test_every_enabled_model_has_framework_independent_target(self):
        expected = {
            'qwen21-turbo': 'images/Qwen-Image-2.1',
            'qwen21-text': 'images/Qwen-Image-2.1',
            'qwen21-vae': 'images/Qwen-Image-2.1',
            'krea2-turbo': 'images/Krea-2',
            'krea2-text': 'images/Krea-2',
            'wan21-vae': 'images/Krea-2/dependencies/Wan_2.1_ComfyUI_repackaged',
            'asr-qwen3-1.7b-f16': 'audio/Qwen3-ASR-1.7B-GGUF',
            'h3-mlx-8bit': 'video/MiniMax-H3-FL2VA-MLX-Serve-8bit',
            'h3-ref2va-mlx-8bit': 'video/MiniMax-H3-REF2VA-MLX-Serve-8bit',
            'music3-mlx-8bit': 'music/MiniMax-Music3-MLX-Serve-8bit',
            'qwen38-flash-next-8bit': 'llm/Qwen3.8-Flash-Next-MLX-Serve-8bit',
            'qwen38-27b-bf16': 'llm/Qwen3.8-27B-bf16',
            'yue2-audio-cpp-bf16': 'music/Yue2-3B-GGUF',
            'qwen21-license': 'images/Qwen-Image-2.1/licenses/Qwen-Image-2.1-Turbo',
            'qwen38-27b-mtp-bf16': 'llm/Qwen3.8-27B-bf16/mtp',
        }
        enabled = {k: m for k, m in self.models.items() if m.get('enabled') is not False}
        self.assertEqual(set(enabled), set(expected))
        for name, target in expected.items():
            with self.subTest(model=name):
                model = enabled[name]
                self.assertEqual(model.get('bundle_dir') or model['subdir'], target)
                self.assertFalse(set(Path(target).parts) & {'mlx', 'mlx-serve', 'audio.cpp', 'audio.cpp-gguf', 'llama.cpp', 'stable-diffusion.cpp'})
        self.assertFalse(self.models['music3-mlx-bf16']['enabled'])
        self.assertNotIn('subdir', self.models['music3-mlx-bf16'])

    def test_music_paths(self):
        self.assertEqual(self.models['yue2-audio-cpp-bf16']['subdir'], 'music/Yue2-3B-GGUF')
        self.assertEqual(self.models['music3-mlx-8bit']['subdir'], 'music/MiniMax-Music3-MLX-Serve-8bit')

    def test_voice_paths(self):
        for name in ['asr-qwen3-1.7b-f16']:
            self.assertEqual(self.models[name]['subdir'], 'audio')

    def test_upstream_and_video_unchanged(self):
        self.assertEqual(self.models['yue2-audio-cpp-bf16']['repo_id'], 'audio-cpp/Yue2-3B-GGUF')
        self.assertEqual(self.models['h3-mlx-8bit']['subdir'], 'video/MiniMax-H3-FL2VA-MLX-Serve-8bit')
        self.assertFalse(any(m.get('subdir', '').startswith('audio-cpp/') for m in self.models.values()))

if __name__ == '__main__':
    unittest.main()
