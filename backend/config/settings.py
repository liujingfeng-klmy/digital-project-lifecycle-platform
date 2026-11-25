"""Django 配置 —— 信息化项目全生命周期管理平台 v0.5"""
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / 'data'
CONFIG_FILE = Path(os.environ.get('PM_CONFIG') or (PROJECT_ROOT / 'config.json'))


def _load_secret_key():
    """取签名密钥：环境变量 → data/.secret_key（首次自动生成并 600 落盘）。

    不再用硬编码兜底。源码一旦外流，硬编码 key 等于任何人都能伪造 token、
    解密 session；自动生成的文件不进版本库，服务器各自独立。
    """
    env = (os.environ.get('PM_SECRET_KEY') or '').strip()
    if env:
        return env
    path = DATA_DIR / '.secret_key'
    if path.exists():
        cached = path.read_text(encoding='utf-8').strip()
        if cached:
            return cached
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    path.write_text(generated, encoding='utf-8')
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return generated


SECRET_KEY = _load_secret_key()


def _local_org_profile() -> dict:
    """读 config.json 的 org 段：行政区前缀与公文落款单位。

    这三项都是「本地化信息」——单位名归一要靠行政区全称去掉前缀，
    批复与证明的落款要写本地主管部门。写进源码等于把部署地摊开，
    因此统一放配置文件，未配置时用中性占位值，功能不受影响。
    """
    try:
        if CONFIG_FILE.exists():
            raw = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            profile = raw.get('org') or {}
            if isinstance(profile, dict):
                return profile
    except Exception:
        pass
    return {}


ORG_PROFILE = _local_org_profile()

# 行政区前缀：材料里常写「XX市财政局」，名录用规范简称，归一前先剥前缀。
# 例：["XX市", "XX自治区"]。未配置时按通用行政层级兜底。
ORG_PREFIXES = tuple(
    p for p in (ORG_PROFILE.get('prefixes') or ('市', '省', '自治区')) if p
)

# 立项批复里的建设地点（{{site}}）
ORG_SITE = (ORG_PROFILE.get('site') or os.environ.get('PM_SITE') or '示例市').strip()

# 数据归集证明等文书的出具单位（{{issuer}}）
ORG_ISSUER = (
    ORG_PROFILE.get('issuer') or os.environ.get('PM_ISSUER') or '示例市数据管理局'
).strip()

# 文书正文引用的本地管理办法名称（{{policy_name}}）
ORG_POLICY_NAME = (
    ORG_PROFILE.get('policy_name')
    or os.environ.get('PM_POLICY_NAME')
    or '数字化项目管理办法'
).strip()

# 默认关。本地试运行由「启动.command」显式置 PM_DEBUG=1；
# 服务器部署不设该变量，避免调试页把堆栈与配置暴露出去。
DEBUG = os.environ.get('PM_DEBUG', '0') == '1'

# 不把 0.0.0.0 写进白名单：它只在校验 Host 头时有意义，
# 真正决定监听地址的是 runserver/gunicorn 的 bind 参数。
ALLOWED_HOSTS = [h.strip() for h in
                 os.environ.get('PM_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
                 if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    # refresh token 黑名单：登出时把 refresh 作废，配合 token_version 形成
    # 两条吊销路径（改密靠版本号、主动登出靠黑名单）。ROTATE_REFRESH_TOKENS
    # 打开的轮换才会把旧 refresh 加入黑名单，必须安装这个 app 才生效。
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'apps.core',
    'apps.aicore',
    'apps.archive',
    'apps.project',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': PROJECT_ROOT / 'data' / 'db.sqlite3',
        'OPTIONS': {'timeout': 20},
    }
}
if os.environ.get('PM_DB_ENGINE'):
    DATABASES['default'] = {
        'ENGINE': os.environ['PM_DB_ENGINE'],
        'NAME': os.environ.get('PM_DB_NAME', 'pm'),
        'USER': os.environ.get('PM_DB_USER', ''),
        'PASSWORD': os.environ.get('PM_DB_PASSWORD', ''),
        'HOST': os.environ.get('PM_DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('PM_DB_PORT', '5432'),
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = PROJECT_ROOT / 'data' / 'static'

MEDIA_URL = '/media/'
MEDIA_ROOT = PROJECT_ROOT / 'media'

# 测试把文件写到临时目录：否则每跑一次测试，真实 media/ 里就多一批
# 以测试库编号命名的孤儿文件（见 config/test_runner.py）
TEST_RUNNER = 'config.test_runner.MediaIsolatedRunner'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'core.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.core.jwt.VersionedJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # DRF 的 PageNumberPagination 不读 PAGE_SIZE_QUERY_PARAM / MAX_PAGE_SIZE
    # 这两个全局键（类内硬编码 None），前端传的 page_size 一直是白传。
    # 用子类把类属性补齐，资料页 100 条、设置页 200 条才真正生效。
    'DEFAULT_PAGINATION_CLASS': 'config.pagination.ApiPageNumberPagination',
    'PAGE_SIZE': 20,
    'DATETIME_FORMAT': '%Y-%m-%d %H:%M',
    'DATE_FORMAT': '%Y-%m-%d',
    # 登录与改密单独限流：默认 LocMemCache 只在单进程内计数，
    # 多进程部署（gunicorn 多 worker）需把 CACHES 换成 Redis 才全局生效
    'DEFAULT_THROTTLE_RATES': {
        'login': '10/min',
        'password': '5/min',
    },
}

SIMPLE_JWT = {
    # 从 12 小时降到 2 小时：配合 /auth/refresh，到期自动续，不打断填表；
    # token 万一外泄，可用窗口也从半天缩到两小时
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=2),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

CORS_ALLOWED_ORIGINS = [
    'http://127.0.0.1:5173',
    'http://localhost:5173',
]
CORS_ALLOW_CREDENTIALS = True

# 这两个是「内存阈值」，不是「可上传大小上限」：
# FILE_UPLOAD_MAX_MEMORY_SIZE 超过后 Django 自动落临时文件再流式读盘，
# 所以调小不会限制业务（单份材料 200MB 的限制在 parsing/services 里单独判断），
# 只是不再把 200MB 的 PDF 整块读进进程内存。
FILE_UPLOAD_MAX_MEMORY_SIZE = 2560 * 1024        # 2.5MB 以上走临时文件
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024   # 非文件字段上限（比对粘正文够用）

# FileHandler 不会自动建父目录，先建好日志目录（幂等）
(DATA_DIR / 'logs').mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '[{asctime}] {levelname} {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
        # 原来只有 console：终端一关日志全丢，事后无法追是谁改了权限、
        # 谁登录失败。加按天轮转的文件 handler，保留 30 天，落 data/logs/。
        'file': {
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'filename': str(DATA_DIR / 'logs' / 'pm.log'),
            'when': 'midnight', 'backupCount': 30, 'encoding': 'utf-8',
            'formatter': 'simple',
        },
    },
    'root': {'handlers': ['console', 'file'], 'level': 'INFO'},
}
