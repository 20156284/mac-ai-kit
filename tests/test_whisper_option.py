"""仅保留 Qwen-ASR 转写，移除 Whisper 菜单但不删除用户文件。"""
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class WhisperOption(unittest.TestCase):
    def setUp(self):
        self.models={m['id']:m for m in json.loads((ROOT/'catalog.json').read_text())['models']}
    def test_only_transcription_voice_models(self):
        self.assertIn('asr-qwen3-1.7b-f16',self.models)
        self.assertNotIn('whisper-large-v3-turbo-mlx',self.models)
        for name in ('aligner-qwen3-0.6b-f16','tts-qwen3-1.7b-orig','tts-qwen3-1.7b-customvoice-bf16','tts-qwen3-1.7b-voicedesign-bf16'):
            self.assertNotIn(name,self.models)
    def test_verified_qwen_asr_snapshot(self):
        m=self.models['asr-qwen3-1.7b-f16']
        self.assertEqual(m['runtime'],'audio.cpp')
        self.assertEqual(m['subdir'],'audio')
        file='Qwen3-ASR-1.7B-GGUF/qwen3-asr-1.7b-f16.gguf'
        self.assertIn(file,m['include'])
        self.assertEqual(m['expected_files'][file]['bytes'],4087660800)
        self.assertEqual(m['expected_files'][file]['sha256'],'59baf62f2454a1b4d1d9ffed730dbf1d0bce28ed6fec30897de17f9d6d452fc3')
if __name__=='__main__': unittest.main()
