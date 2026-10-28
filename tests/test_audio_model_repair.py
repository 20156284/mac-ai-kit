"""音频模型增量修复：只用小型夹具，不下载或执行真实模型。"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import download_models as d
import api_inventory as inventory

def spec(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

class AudioRepair(unittest.TestCase):
    def test_current_asr_catalog_and_legacy_pair(self):
        models=json.loads((ROOT/'catalog.json').read_text())['models']
        entry=next(m for m in models if m['id']=='asr-qwen3-1.7b-f16')
        value=next(iter(entry['expected_files'].values()))
        self.assertEqual(value['bytes'],4087660800)
        self.assertEqual(value['compatible_versions'][0]['bytes'],4087653248)
        self.assertEqual(value['compatible_versions'][0]['sha256'],'f12537d4ea56df4e1dcca64a902e0b37fb1111f8ef7fc8e554fd00110be047d0')

    def test_old_verified_file_reused_and_actual_identity_recorded(self):
        old,new=b'old',b'new version'
        value=dict(spec(new),compatible_versions=[spec(old)])
        model={'id':'asr','repo_id':'owner/asr','subdir':'audio/asr','expected_files':{'model.gguf':value}}
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);folder=base/'audio/asr';folder.mkdir(parents=True)
            (folder/'model.gguf').write_bytes(old)
            row=d.download_preflight([(model,folder,[])],[],[model],base=base)[0]
            self.assertEqual(row['network'],0)
            self.assertEqual(inventory.inspect_model(model,base)['state'],'complete')
            with patch.object(d.subprocess,'run') as run,contextlib.redirect_stdout(io.StringIO()):
                result=d.download_verified_bundle('unused',model,folder,1,[],[model])
            run.assert_not_called()
            self.assertEqual(result[0]['sha256'],spec(old)['sha256'])
            self.assertEqual(result[0]['bytes'],len(old))
            d.check_expected_files(model,result,True)

    def test_size_alone_never_accepts_or_mixes_versions(self):
        value=dict(spec(b'new'),compatible_versions=[spec(b'older')])
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'model';file.write_bytes(b'bad')
            self.assertFalse(d.verify_file(file,value))
            mismatched={'bytes':3,'sha256':spec(b'older')['sha256'],'path':'model'}
            with self.assertRaises(ValueError):
                d.check_expected_files({'expected_files':{'model':value}},[mismatched],True)

    def test_yue_sidecars_only_download_keeps_both_weights(self):
        payloads={'yue2-3b-bf16.gguf':b'weight','yue2-vae-f32.gguf':b'vae',
          'sidecars/yue2-model-config.json':b'{}','sidecars/yue2-generation-config.json':b'{"a":1}',
          'sidecars/yue2-qwen.tiktoken':b'tokens','sidecars/yue2-vae-config.json':b'{"b":2}'}
        model={'id':'yue2','repo_id':'audio-cpp/Yue2-3B-GGUF','subdir':'music/Yue2-3B-GGUF',
               'expected_files':{k:spec(v) for k,v in payloads.items()}}
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);folder=base/model['subdir'];folder.mkdir(parents=True)
            for name in list(payloads)[:2]: (folder/name).write_bytes(payloads[name])
            inodes={name:(folder/name).stat().st_ino for name in list(payloads)[:2]}
            row=d.download_preflight([(model,folder,[])],[],[model],base=base)[0]
            self.assertEqual(row['missing'],list(payloads)[2:])
            self.assertEqual(row['network'],sum(len(v) for k,v in payloads.items() if k.startswith('sidecars/')))
            def download(args,check):
                includes=[args[i+1] for i,x in enumerate(args) if x=='--include']
                self.assertEqual(includes,list(payloads)[2:])
                stage=Path(args[args.index('--local-dir')+1])
                for name in includes:
                    (stage/name).parent.mkdir(parents=True,exist_ok=True);(stage/name).write_bytes(payloads[name])
            with patch.object(d.subprocess,'run',side_effect=download),contextlib.redirect_stdout(io.StringIO()):
                d.download_verified_bundle('unused',model,folder,1,[],[model])
            for name,node in inodes.items():self.assertEqual((folder/name).stat().st_ino,node)
            self.assertEqual(inventory.inspect_model(model,base)['state'],'complete')
            self.assertFalse(list(folder.glob('.h3-stage-*')))

if __name__=='__main__':unittest.main()
