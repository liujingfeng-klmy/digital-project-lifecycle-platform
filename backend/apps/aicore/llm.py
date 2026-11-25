"""模型接入层。

设计要点：
1. 全部走 OpenAI 兼容协议（/chat/completions），DeepSeek、通义、Moonshot、
   混元、vLLM、政务云常见网关、Ollama 都支持这一套。
2. 切换模型只改 config.json 里的 base_url / model / api_key，业务代码不动。
3. 密钥只落在服务器配置文件，任何接口都不下发明文。
"""
import json
import logging
import os
import threading
import time
from pathlib import Path

import requests
from django.conf import settings

log = logging.getLogger(__name__)

# 配置默认放项目根，但支持 PM_CONFIG 指到项目外（推荐做法）：
#   PM_CONFIG=/etc/pm/config.json
# 密钥与代码、数据库分开存，打包/拷贝/上传网盘时不会连密钥一起带走。
CONFIG_PATH = Path(getattr(settings, 'CONFIG_FILE',
                           Path(settings.PROJECT_ROOT) / 'config.json'))
PROJECT_ROOT = Path(settings.PROJECT_ROOT)

_LOCK = threading.Lock()

PRESETS = [
    {
        'key': 'deepseek', 'label': 'DeepSeek（商用云）',
        'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash',
        'note': 'DeepSeek-V4.1-Flash，长材料友好', 'offline': False,
    },
    {
        'key': 'qwen', 'label': '通义千问（阿里云）',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'qwen-plus',
        'note': '需开通 DashScope 兼容模式', 'offline': False,
    },
    {
        'key': 'moonshot', 'label': 'Moonshot（月之暗面）',
        'base_url': 'https://api.moonshot.cn/v1', 'model': 'moonshot-v1-32k',
        'note': '长上下文，适合整章投喂', 'offline': False,
    },
    {
        'key': 'hunyuan', 'label': '腾讯混元',
        'base_url': 'https://api.hunyuan.cloud.tencent.com/v1', 'model': 'hunyuan-turbos-latest',
        'note': '腾讯云 OpenAI 兼容接口', 'offline': False,
    },
    {
        'key': 'intranet', 'label': '单位内网 / 政务云网关',
        'base_url': '', 'model': '',
        'note': '填内网网关地址，协议须兼容 OpenAI', 'offline': True,
    },
    {
        'key': 'ollama', 'label': '本机 Ollama（完全离线）',
        'base_url': 'http://127.0.0.1:11434/v1', 'model': 'qwen2.5:14b',
        'note': '数据不出机器，需先装 Ollama 并拉取模型', 'offline': True,
    },
]

DEFAULT_CONFIG = {
    'llm': {
        'provider': 'deepseek',
        'base_url': '',
        'model': '',
        'api_key': '',
        'temperature': 0.2,
        'max_tokens': 8000,
        'timeout': 120,
        'chunk_chars': 6000,
        'enabled': False,
    },
    'review': {'pass_score': 85, 'revise_score': 70},
    'flow': {'sign_mode': 'all'},
    'sensitive': {'skip_ai': True},
}


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_WARNED_KEY_LOCATION = False


def _warn_key_location():
    """密钥落在项目目录里时只提示一次，不反复刷日志。"""
    global _WARNED_KEY_LOCATION
    if _WARNED_KEY_LOCATION:
        return
    _WARNED_KEY_LOCATION = True
    try:
        resolved = CONFIG_PATH.resolve()
        if CONFIG_PATH.exists() and PROJECT_ROOT.resolve() in resolved.parents:
            raw = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            if (raw.get('llm') or {}).get('api_key'):
                log.warning('配置内含明文密钥且位于项目目录内（%s）。'
                            '建议移到项目外，用环境变量 PM_CONFIG 指向。', resolved)
    except Exception:
        pass


def load_config() -> dict:
    _warn_key_location()
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))
    return _merge(DEFAULT_CONFIG, raw)


def save_config(patch: dict) -> dict:
    with _LOCK:
        cfg = _merge(load_config(), patch)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        try:
            CONFIG_PATH.chmod(0o600)
        except Exception:
            pass
        return cfg


def get_llm() -> dict:
    llm = dict(load_config()['llm'])
    # 密钥可从环境变量注入，优先于配置文件明文。这样可以把 config.json
    # 移到项目外（PM_CONFIG 指向）后连文件里都不放密钥：
    #   export PM_LLM_API_KEY='sk-...'
    # 配置模板 config.example.json 不含真实密钥，交付包里不会出现明文。
    env_key = (os.environ.get('PM_LLM_API_KEY') or '').strip()
    if env_key:
        llm['api_key'] = env_key
    return llm


def mask_key(key: str) -> str:
    if not key:
        return ''
    if len(key) <= 8:
        return '****'
    return f'{key[:4]}****{key[-4:]}'


def public_config() -> dict:
    """下发给前端的配置：密钥掩码，不返回明文。"""
    cfg = load_config()
    llm = dict(cfg['llm'])
    # 掩码与状态位都按运行时真正生效的密钥判断：密钥可由环境变量
    # PM_LLM_API_KEY 注入（不落盘），否则外置配置方案下界面会误报「未配置」。
    effective_key = get_llm().get('api_key', '')
    llm['api_key'] = mask_key(effective_key)
    llm['api_key_set'] = bool(effective_key)
    cfg['llm'] = llm
    return cfg


class LLMError(Exception):
    pass


def validate_base_url(url: str) -> str:
    """校验模型地址，防管理员配置引发 SSRF / 密钥外泄。

    只接受 http(s) 地址；拒绝私网网段、链路本地（含云元数据
    169.254.169.254）与保留地址。回环地址保留给 PRESETS 的本机 Ollama
    （离线、数据不出机器）；确需连内网模型网关时用
    PM_ALLOW_PRIVATE_LLM=1 显式放开，避免默认配置把已存 Key 发到内网。
    """
    from urllib.parse import urlparse
    import ipaddress

    url = (url or '').strip().rstrip('/')
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('模型地址必须是 http(s) 开头的完整 URL')
    host = parsed.hostname or ''
    try:
        ip = ipaddress.ip_address(host)
        # 回环地址留给 PRESETS 的本机 Ollama（完全离线，数据不出机器）；
        # 真正危险的是私网网段、链路本地（云元数据 169.254.x.x）与保留地址。
        private = (ip.is_private or ip.is_link_local or ip.is_reserved
                   or ip.is_multicast)
    except ValueError:
        private = host.lower().endswith('.localhost')
    if private and os.environ.get('PM_ALLOW_PRIVATE_LLM') != '1':
        raise ValueError('不允许把模型地址指向本机或内网；'
                         '如确需连接内网模型，请设置 PM_ALLOW_PRIVATE_LLM=1')
    return url


def chat(messages, temperature=None, max_tokens=None, timeout=None,
         api_key_override=None, base_url_override=None, model_override=None):
    """一次对话调用，返回 (正文, usage)。失败抛 LLMError，消息面向人工可读。"""
    cfg = get_llm()
    base_url = (base_url_override or cfg.get('base_url') or '').strip().rstrip('/')
    model = (model_override or cfg.get('model') or '').strip()
    api_key = api_key_override if api_key_override is not None else cfg.get('api_key', '')

    if not base_url:
        raise LLMError('未配置模型地址（base_url）')
    if not model:
        raise LLMError('未配置模型名称（model）')

    url = base_url + '/chat/completions'
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    body = {
        'model': model,
        'messages': messages,
        'temperature': cfg.get('temperature', 0.2) if temperature is None else temperature,
        'max_tokens': cfg.get('max_tokens', 4000) if max_tokens is None else max_tokens,
        'stream': False,
    }

    try:
        r = requests.post(url, headers=headers, json=body,
                          timeout=timeout or cfg.get('timeout', 120))
    except requests.exceptions.Timeout:
        raise LLMError('连接超时，检查地址是否可达、是否需要内网代理')
    except requests.exceptions.ConnectionError:
        raise LLMError('无法连接该地址，检查 base_url 拼写、端口与网络')
    except Exception as e:
        raise LLMError(f'调用失败：{e}')

    if r.status_code == 401:
        raise LLMError('鉴权失败（401），API Key 不正确或无权限')
    if r.status_code == 404:
        raise LLMError('接口不存在（404），base_url 多写或漏写了 /v1 之类路径')
    if r.status_code == 429:
        raise LLMError('请求超限（429），额度用完或并发过高')
    if r.status_code >= 400:
        snippet = (r.text or '')[:300]
        raise LLMError(f'接口返回 {r.status_code}：{snippet}')

    try:
        data = r.json()
        choice = data['choices'][0]
        content = choice['message'].get('content') or ''
    except Exception:
        raise LLMError('返回格式不是预期的 OpenAI 结构，确认该地址是否兼容')

    usage = dict(data.get('usage') or {})
    # 调用方靠这个判断有没有被 max_tokens 截断：推理型模型会把额度先花在思考上，
    # 那时 content 是空的、finish_reason 是 length，绝不能当成「没有发现问题」
    usage['finish_reason'] = choice.get('finish_reason')
    reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
    if reasoning is not None:
        usage['reasoning_tokens'] = reasoning
    return content, usage


def test_connection(base_url, model, api_key, timeout=30):
    """发一次最小请求验证连通性，不写入配置。

    额度不能给太小：推理型模型（如 deepseek-flash）会先把 token 花在思考上，
    给 16 个 token 时正文是空的，页面上就会显示「连通但回复为空」，看着像故障。
    """
    started = time.time()
    try:
        content, usage = chat(
            [{'role': 'user', 'content': '只回复两个字：连通'}],
            temperature=0, max_tokens=256, timeout=timeout,
            base_url_override=base_url, model_override=model,
            api_key_override=api_key,
        )
        reply = (content or '').strip()[:40]
        if not reply:
            # 有 usage 说明接口其实返回了，只是正文被推理吃掉，仍算连通
            reply = '（模型已响应，本次未返回正文）'
        return {
            'ok': True,
            'latency_ms': int((time.time() - started) * 1000),
            'reply': reply,
            'usage': usage,
        }
    except LLMError as e:
        return {'ok': False, 'latency_ms': int((time.time() - started) * 1000),
                'error': str(e)}
