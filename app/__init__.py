import os

from dotenv import load_dotenv

# Config 가 import 시점에 환경변수를 읽으므로 그보다 먼저 .env 를 불러온다
load_dotenv()
load_dotenv(".env.auth")  # 로그인 계정 (해시만) — `flask set-login` 으로 만듦

from flask import Flask, jsonify, redirect, request, session, url_for  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from .config import Config  # noqa: E402
from .extensions import csrf  # noqa: E402

# 로그인 없이 접근 가능한 엔드포인트
PUBLIC_ENDPOINTS = {"auth.login", "static"}


# DB 없이 파일로 저장 (docs/storage.md) — DATA_DIR(기본 instance/) 아래 위치들
_DATA_PATHS = {
    "NOTES_DIR": "notes",
    "UPLOAD_FOLDER": "uploads",
    "AI_CHAT_DIR": "ai-chats",
    "DECKS_DIR": "decks",
    "AI_EDITS_DIR": "ai-edits",
    "SETTINGS_FILE": "settings.json",
    "USAGE_FILE": "ai-usage.json",
}


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    os.makedirs(app.instance_path, exist_ok=True)

    data_dir = app.config.get("DATA_DIR") or app.instance_path
    app.config["DATA_DIR"] = data_dir
    for key, name in _DATA_PATHS.items():
        if not app.config.get(key):
            app.config[key] = os.path.join(data_dir, name)
    for key in ("NOTES_DIR", "UPLOAD_FOLDER", "DECKS_DIR", "AI_EDITS_DIR"):
        os.makedirs(app.config[key], exist_ok=True)

    if not os.environ.get("SECRET_KEY"):
        app.logger.warning("SECRET_KEY 미설정: 임시 키를 사용하므로 서버 재시작 시 로그인이 풀립니다.")

    csrf.init_app(app)
    if app.config.get("TRUST_PROXY"):
        # nginx 뒤에서 실제 접속 IP(로그인 잠금용)와 https 를 알 수 있게
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    from .cli import register_cli

    register_cli(app)

    from .routes.ai import bp as ai_bp
    from .routes.api import bp as api_bp
    from .routes.decks import bp as decks_bp
    from .routes.auth import bp as auth_bp
    from .routes.files import bp as files_bp
    from .routes.pages import bp as pages_bp
    from .routes.transfer import bp as transfer_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(transfer_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(decks_bp)

    from .services.decks import decks_with_status
    from .services.page_tree import abs_file_path, active_tree, file_path, safe_name

    app.add_template_filter(safe_name, "safe_name")
    app.add_template_global(file_path, "file_path")
    app.add_template_global(abs_file_path, "abs_file_path")

    @app.context_processor
    def inject_sidebar():
        return {"sidebar_tree": active_tree, "sidebar_decks": lambda: decks_with_status("active")}

    @app.before_request
    def require_login():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        from .routes.auth import session_is_valid

        if not session_is_valid():
            if request.path.startswith("/api/"):
                return jsonify(error="로그인이 필요합니다."), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return None

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        if request.path.startswith("/api/"):
            limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
            message = err.description if err.code != 413 else f"파일이 너무 큽니다. (한 번에 최대 {limit_mb}MB)"
            return jsonify(error=message), err.code
        return err

    return app
