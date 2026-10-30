import os

from dotenv import load_dotenv

# Config 가 import 시점에 환경변수를 읽으므로 그보다 먼저 .env 를 불러온다
load_dotenv()
load_dotenv(".env.auth")  # 로그인 계정 (해시만) — `flask set-login` 으로 만듦

from flask import Flask, flash, g, jsonify, redirect, request, url_for  # noqa: E402
from flask_wtf.csrf import CSRFError, generate_csrf  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from .config import Config  # noqa: E402
from .extensions import csrf  # noqa: E402

# 로그인 없이 접근 가능한 엔드포인트
PUBLIC_ENDPOINTS = {"auth.login", "static"}


def create_app(config_class=Config, accounts: bool = True):
    """accounts=False: 계정 없이 app.config 의 저장 위치만 쓴다 (AI 도구 MCP 서버 — 띄울 때 그 계정의 위치를 받음)."""
    from .services import userdata

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    os.makedirs(app.instance_path, exist_ok=True)

    # 노트는 DB 없이 파일 (docs/storage.md). 계정마다 <DATA_DIR>/users/<아이디>/ 아래 (userdata.py)
    data_dir = app.config.get("DATA_DIR") or app.instance_path
    app.config["DATA_DIR"] = data_dir
    os.makedirs(data_dir, exist_ok=True)
    app.config["ACCOUNTS_DB"] = app.config.get("ACCOUNTS_DB") or os.path.join(data_dir, "accounts.db")
    for key, name in userdata.KEYS.items():  # 계정 나누기 전 위치 (옮기기 원본) · MCP 서버가 받은 위치
        if not app.config.get(key):
            app.config[key] = os.path.join(data_dir, name)
    if accounts:
        from .services import accounts as account_store

        account_store.init(app)
    else:
        for key in userdata.DIRS:
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
    from .services.page_tree import abs_file_path, file_path, safe_name, tree_json

    app.add_template_filter(safe_name, "safe_name")
    app.add_template_global(file_path, "file_path")
    app.add_template_global(abs_file_path, "abs_file_path")

    @app.context_processor
    def inject_sidebar():
        return {
            "explorer_tree": tree_json,
            "sidebar_decks": lambda: decks_with_status("active"),
            "current_user": g.get("user"),
        }

    @app.before_request
    def require_login():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        from .routes.auth import current_user

        user = current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="로그인이 필요합니다."), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        # 이 요청은 로그인한 계정의 노트·그림·대화만 본다
        g.user = user
        userdata.use(userdata.paths_for(user["username"]))
        return None

    @app.get("/api/csrf")
    def fresh_csrf_token():
        """화면이 CSRF 오류를 받으면 새 토큰을 받아 같은 요청을 한 번 다시 보낸다 (app.js 의 fetch)."""
        return jsonify(token=generate_csrf())

    @app.errorhandler(CSRFError)
    def handle_csrf_error(err):
        # 토큰이 어긋난 경우(다른 탭에서 다시 로그인, 서버 키 변경 등) — 팝업 대신 화면이 새 토큰으로 다시 보냄
        if request.path.startswith("/api/") or request.headers.get("X-CSRFToken"):
            return jsonify(error="보안 토큰이 바뀌었습니다. 다시 시도하세요.", csrf=True), 400
        flash("보안 토큰이 바뀌어 다시 불러왔습니다. 한 번 더 눌러 주세요.", "info")
        return redirect(request.referrer or url_for("pages.index"))

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        if request.path.startswith("/api/"):
            limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
            message = err.description if err.code != 413 else f"파일이 너무 큽니다. (한 번에 최대 {limit_mb}MB)"
            return jsonify(error=message), err.code
        return err

    return app
