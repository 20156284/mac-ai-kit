"""在隔离 uvx 环境使用固定版 benchy；先严格分词，失败不发推理请求。"""
import hashlib
import json
from pathlib import Path
import sys


def strict_tokenizer(name):
    print('阶段：准备并校验同源 tokenizer（不加载模型权重）。', flush=True)
    from tokenizers import Tokenizer
    from llama_benchy.corpus import LightweightTokenizer
    path = Path(name)
    if path.is_absolute():
        source = path / 'tokenizer.json' if path.is_dir() else path
        if not source.is_file():
            raise ValueError('本地 tokenizer.json 不存在或不可读')
    else:
        from huggingface_hub import hf_hub_download
        # 只下载 tokenizer JSON，不下载权重、不加载 Python/custom remote code。
        source = Path(hf_hub_download(repo_id=name, filename='tokenizer.json', token=False))
    tokenizer = Tokenizer.from_file(str(source))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    if not tokenizer.encode('Tokenizer verification', add_special_tokens=False).ids:
        raise ValueError('tokenizer 自检失败')
    record = {'requested': name, 'resolved_file': str(source),
              'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'truncation': False, 'padding': False, 'fallback': False}
    Path('tokenizer.json.audit').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print('阶段：tokenizer 校验完成。', flush=True)
    return LightweightTokenizer(tokenizer)


def guard_prompt_count(original):
    async def checked(self, session, context_text, prompt_text, max_tokens, no_cache,
                      tokenizer=None, progress=None, request_id=None):
        result = await original(self, session, context_text, prompt_text, max_tokens,
                                no_cache, tokenizer, progress, request_id)
        if not result.error and tokenizer is not None and result.prompt_tokens > 0:
            expected = len(tokenizer.encode(context_text, add_special_tokens=False)) + len(
                tokenizer.encode(self._non_empty_user_content(prompt_text), add_special_tokens=False))
            # 小输入允许模板开销；长输入严重不一致不能沿用上游的名义 token 数。
            if expected >= 256 and abs(result.prompt_tokens - expected) >= max(256, expected * 0.2):
                result.error = '服务端输入 token 与同源 tokenizer 严重不一致；可能被截断，停止测速'
                print(result.error)
        return result
    return checked


def main():
    print('阶段：Python 测试执行器已启动，正在导入测试依赖。', flush=True)
    from llama_benchy import __version__
    if __version__ != '0.4.0':
        raise RuntimeError('llama-benchy 版本不匹配')
    from llama_benchy.corpus import TokenizedCorpus
    # 替换固定版唯一语料分词入口，彻底阻断其 GPT2 回退。
    TokenizedCorpus._get_tokenizer = lambda self, model_name, tokenizer_name=None: strict_tokenizer(tokenizer_name or model_name)
    from llama_benchy.client import LLMClient
    LLMClient.run_generation = guard_prompt_count(LLMClient.run_generation)
    from llama_benchy.__main__ import main as benchmark_main
    print('阶段：进入 llama-benchy；准备语料、预热与测速，详细进展如下。首次请求可能等待后端加载，实际结果以响应或错误为准。', flush=True)
    benchmark_main()


if __name__ == '__main__':
    main()
