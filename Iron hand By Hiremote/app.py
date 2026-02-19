from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from openai import OpenAI
from supabase import Client, create_client


APP_ROOT = Path(__file__).resolve().parent
# Vercel functions have a read-only filesystem; use /tmp for runtime storage.
IS_VERCEL = os.environ.get("VERCEL") == "1"
RUNTIME_ROOT = Path("/tmp/hiremote") if IS_VERCEL else APP_ROOT
INSTANCE_PATH = RUNTIME_ROOT / "instance"
DATABASE_PATH = INSTANCE_PATH / "hiremote.db"
UPLOAD_ROOT = RUNTIME_ROOT / "storage" / "uploads"

ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "mp4",
    "mov",
    "avi",
    "pdf",
    "doc",
    "docx",
    "txt",
}

ROLE_EMPLOYEE = "employee"
ROLE_IRONHAND = "ironhand"
ROLE_CLIENT = "client"
PASSWORD_METHOD = "pbkdf2:sha256"
AI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
AI_MAX_CONTEXT_ITEMS = int(os.environ.get("AI_MAX_CONTEXT_ITEMS", "6"))
POS_DAILY_TABLE = os.environ.get("POS_DAILY_TABLE", "pos_daily_sales")
POS_ITEM_TABLE = os.environ.get("POS_ITEM_TABLE", "pos_item_sales")
POS_DATE_COLUMN = os.environ.get("POS_DATE_COLUMN", "business_date")
POS_ITEM_DATE_COLUMN = os.environ.get("POS_ITEM_DATE_COLUMN", "business_date")

_SUPABASE_CLIENT: Optional[Client] = None
_OPENAI_CLIENT: Optional[OpenAI] = None

DEFAULT_USERS = [
    {
        "name": "Alex Employee",
        "email": "employee@hiremote.com",
        "password": "password123",
        "role": ROLE_EMPLOYEE,
        "store_number": "101",
    },
    {
        "name": "Bianca Ironhand",
        "email": "ironhand@hiremote.com",
        "password": "operations123",
        "role": ROLE_IRONHAND,
        "store_number": "H1",
    },
    {
        "name": "Chris Client",
        "email": "client@hiremote.com",
        "password": "clientaccess",
        "role": ROLE_CLIENT,
        "store_number": "101",
    },
]


def create_app() -> Flask:
    app = Flask(
        __name__,
        instance_path=str(INSTANCE_PATH),
        instance_relative_config=True,
        static_folder="static",
        template_folder="templates",
    )
    app.config.update(
        SECRET_KEY=os.environ.get("HIREMOTE_SECRET", "change-me"),
        MAX_CONTENT_LENGTH=512 * 1024 * 1024,  # 512 MB uploads
        PERMANENT_SESSION_LIFETIME=60 * 60 * 10,
        UPLOAD_FOLDER=str(UPLOAD_ROOT),
    )

    INSTANCE_PATH.mkdir(parents=True, exist_ok=True)
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    init_db()
    seed_users()

    register_routes(app)
    return app


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            store_number TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            employee_name TEXT NOT NULL,
            store_number TEXT NOT NULL,
            category TEXT NOT NULL,
            report_type TEXT,
            notes TEXT,
            payload TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )
    conn.commit()
    conn.close()


def seed_users() -> None:
    conn = get_db_connection()
    existing_users = {
        row["email"] for row in conn.execute("SELECT email FROM users").fetchall()
    }

    for user in DEFAULT_USERS:
        if user["email"] in existing_users:
            continue
        conn.execute(
            """
            INSERT INTO users (name, email, password_hash, role, store_number)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user["name"],
                user["email"].lower(),
                generate_password_hash(user["password"], method=PASSWORD_METHOD),
                user["role"],
                user["store_number"],
            ),
        )
    conn.commit()
    conn.close()


def login_required(role: Optional[str] = None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if role and user["role"] != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def current_user() -> Optional[sqlite3.Row]:
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def get_openai_client() -> Optional[OpenAI]:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    _OPENAI_CLIENT = OpenAI(api_key=api_key)
    return _OPENAI_CLIENT


def get_supabase_client() -> Optional[Client]:
    global _SUPABASE_CLIENT
    if _SUPABASE_CLIENT is not None:
        return _SUPABASE_CLIENT
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    _SUPABASE_CLIENT = create_client(url, key)
    return _SUPABASE_CLIENT


def _safe_float(value: object) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: object) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def summarize_submissions(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    summary = []
    for row in rows:
        summary.append(
            {
                "category": row["category"],
                "report_type": row["report_type"],
                "employee_name": row["employee_name"],
                "store_number": row["store_number"],
                "notes": (row["notes"] or "")[:200],
                "created_at": row["created_at"],
            }
        )
    return summary


def load_pos_summary(store_id: str) -> Dict[str, Any]:
    supabase = get_supabase_client()
    if not supabase:
        return {"status": "not_configured"}

    start_date = (datetime.utcnow().date() - timedelta(days=30)).isoformat()
    summary: Dict[str, Any] = {"status": "ok", "window_start": start_date}

    try:
        daily_rows = (
            supabase.table(POS_DAILY_TABLE)
            .select("*")
            .eq("store_id", store_id)
            .gte(POS_DATE_COLUMN, start_date)
            .execute()
            .data
            or []
        )
    except Exception:
        return {"status": "unavailable"}

    total_gross = sum(_safe_float(row.get("gross_sales")) for row in daily_rows)
    total_net = sum(_safe_float(row.get("net_sales")) for row in daily_rows)
    total_txn = sum(_safe_int(row.get("transactions")) for row in daily_rows)
    total_items = sum(_safe_int(row.get("items_sold")) for row in daily_rows)

    summary.update(
        {
            "days": len(daily_rows),
            "gross_sales": round(total_gross, 2),
            "net_sales": round(total_net, 2),
            "transactions": total_txn,
            "items_sold": total_items,
        }
    )

    try:
        item_rows = (
            supabase.table(POS_ITEM_TABLE)
            .select("*")
            .eq("store_id", store_id)
            .gte(POS_ITEM_DATE_COLUMN, start_date)
            .execute()
            .data
            or []
        )
    except Exception:
        item_rows = []

    item_totals: Dict[str, Dict[str, Any]] = {}
    for row in item_rows:
        name = row.get("item_name") or row.get("item_sku") or "Unknown item"
        item_totals.setdefault(
            name,
            {
                "item_name": name,
                "quantity": 0,
                "gross_sales": 0.0,
            },
        )
        item_totals[name]["quantity"] += _safe_int(row.get("quantity"))
        item_totals[name]["gross_sales"] += _safe_float(row.get("gross_sales"))

    top_items = sorted(
        item_totals.values(), key=lambda item: item["quantity"], reverse=True
    )[:5]
    if top_items:
        summary["top_items"] = top_items

    return summary


def build_store_context(user: sqlite3.Row) -> Dict[str, Any]:
    store_id = user["store_number"]
    recent_rows = fetch_submissions(store=store_id)[:10]
    activity_counts: Dict[str, int] = {}
    for row in recent_rows:
        activity_counts[row["category"]] = activity_counts.get(row["category"], 0) + 1

    context: Dict[str, Any] = {
        "store_id": store_id,
        "generated_at": datetime.utcnow().isoformat(),
        "recent_activity": summarize_submissions(recent_rows),
        "activity_counts": activity_counts,
    }

    pos_summary = load_pos_summary(store_id)
    context["pos_summary"] = pos_summary
    return context


def extract_output_text(response: object) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text
    try:
        for item in getattr(response, "output", []):
            if getattr(item, "type", "") != "message":
                continue
            for content in getattr(item, "content", []):
                text = getattr(content, "text", None)
                if text:
                    return text
    except Exception:
        return ""
    return ""


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_files(files: Dict[str, object]) -> List[Dict[str, str]]:
    saved_files = []
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    for field_name, file in files.items():
        if not file or not getattr(file, "filename", ""):
            continue
        filename = secure_filename(file.filename)
        if not allowed_file(filename):
            raise ValueError(f"Unsupported file type for {filename}")
        file_dir = UPLOAD_ROOT / timestamp
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / filename
        file.save(file_path)
        saved_files.append(
            {
                "field": field_name,
                "stored_name": f"{timestamp}/{filename}",
                "original_name": filename,
                "mime": file.mimetype,
            }
        )
    return saved_files


def store_submission(
    user: sqlite3.Row,
    category: str,
    report_type: str,
    notes: str,
    payload: Dict[str, object],
) -> None:
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO submissions (
            user_id, employee_name, store_number,
            category, report_type, notes, payload, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            user["name"],
            user["store_number"],
            category,
            report_type,
            notes,
            json.dumps(payload),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def fetch_submissions(
    store: Optional[str] = None,
    category: Optional[str] = None,
    employee: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[sqlite3.Row]:
    query = "SELECT * FROM submissions WHERE 1=1"
    params: List[object] = []
    if store:
        query += " AND store_number = ?"
        params.append(store)
    if category:
        query += " AND category = ?"
        params.append(category)
    if employee:
        query += " AND employee_name = ?"
        params.append(employee)
    if start:
        query += " AND created_at >= ?"
        params.append(start)
    if end:
        query += " AND created_at <= ?"
        params.append(end)
    query += " ORDER BY datetime(created_at) DESC"

    conn = get_db_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def register_routes(app: Flask) -> None:
    @app.template_filter("load_payload")
    def load_payload(payload: Optional[str]):
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}

    @app.context_processor
    def inject_globals():
        return {"current_user": current_user()}

    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").lower()
            password = request.form.get("password", "")
            conn = get_db_connection()
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            conn.close()
            if user and check_password_hash(user["password_hash"], password):
                session["user_id"] = user["id"]
                flash("Welcome back!", "success")
                return redirect(url_for("dashboard"))
            flash("Invalid email or password.", "danger")
        return render_template("login.html", app_name="Hiremote Operations Portal")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Signed out successfully.", "info")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required()
    def dashboard():
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if user["role"] == ROLE_EMPLOYEE:
            submissions = fetch_submissions(
                store=user["store_number"],
                category="shift",
                employee=user["name"],
            )[:5]
            return render_template(
                "dashboard_employee.html",
                submissions=submissions,
            )
        if user["role"] == ROLE_IRONHAND:
            reports = fetch_submissions()
            stores = sorted({row["store_number"] for row in reports})
            return render_template(
                "dashboard_ironhand.html",
                reports=reports,
                stores=stores,
            )
        if user["role"] == ROLE_CLIENT:
            return redirect(url_for("client_reports"))
        abort(403)

    @app.route("/upload/shift", methods=["POST"])
    @login_required(ROLE_EMPLOYEE)
    def upload_shift():
        user = current_user()
        notes = request.form.get("notes", "")
        try:
            saved_files = save_uploaded_files(
                {
                    "scratcher_video": request.files.get("scratcher_video"),
                    "cash_photo": request.files.get("cash_photo"),
                    "sales_photo": request.files.get("sales_photo"),
                }
            )
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("dashboard"))

        if len(saved_files) < 3:
            flash("All three files are required for end-of-shift upload.", "danger")
            return redirect(url_for("dashboard"))

        payload = {
            "files": saved_files,
            "notes": notes,
        }
        store_submission(user, "shift", "shift", notes, payload)
        flash("Shift submitted. Great work!", "success")
        return redirect(url_for("dashboard"))

    @app.route("/upload/report", methods=["POST"])
    @login_required(ROLE_IRONHAND)
    def upload_report():
        user = current_user()
        report_type = request.form.get("report_type", "daily")
        summary = request.form.get("summary", "")
        notes = request.form.get("notes", "")

        file_payload = request.files.get("report_file")
        files: List[Dict[str, str]] = []
        if file_payload and file_payload.filename:
            try:
                files = save_uploaded_files({"report_file": file_payload})
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("dashboard"))

        payload = {
            "summary": summary,
            "files": files,
        }
        store_submission(user, report_type, report_type, notes, payload)
        flash(f"{report_type.title()} report sent!", "success")
        return redirect(url_for("dashboard"))

    @app.route("/reports")
    @login_required()
    def client_reports():
        user = current_user()
        if not user:
            abort(403)
        if user["role"] not in {ROLE_CLIENT, ROLE_IRONHAND}:
            abort(403)

        category = request.args.get("category") or None
        employee = request.args.get("employee") or None
        start = request.args.get("start") or None
        end = request.args.get("end") or None
        store_number = request.args.get("store_number") or None

        if user["role"] == ROLE_CLIENT:
            store_number = user["store_number"]

        submissions = fetch_submissions(
            store=store_number,
            category=category,
            employee=employee,
            start=start,
            end=end,
        )
        return render_template(
            "dashboard_client.html",
            submissions=submissions,
            filters={
                "category": category or "",
                "employee": employee or "",
                "start": start or "",
                "end": end or "",
                "store_number": store_number or "",
            },
        )

    @app.route("/api/assistant", methods=["POST"])
    @login_required()
    def assistant():
        user = current_user()
        if not user:
            abort(403)

        payload = request.get_json(silent=True) or {}
        message = (payload.get("message") or "").strip()
        if not message:
            return jsonify({"error": "Message is required."}), 400

        history = payload.get("history") or []
        if not isinstance(history, list):
            history = []

        context = build_store_context(user)
        client = get_openai_client()
        if not client:
            return (
                jsonify(
                    {
                        "error": "OpenAI API key is not configured for this environment."
                    }
                ),
                500,
            )

        system_prompt = (
            "You are the Iron Hand store assistant. Answer only using the provided "
            "store context. If the answer is not available, say you do not have that "
            "data yet and suggest what data would be needed. Keep responses concise "
            "and action-oriented."
        )

        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Store context JSON:\n{json.dumps(context, default=str)}",
                    }
                ],
            },
        ]

        for item in history[-AI_MAX_CONTEXT_ITEMS:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append(
                {
                    "role": role,
                    "content": [{"type": "input_text", "text": content}],
                }
            )

        messages.append(
            {"role": "user", "content": [{"type": "input_text", "text": message}]}
        )

        try:
            response = client.responses.create(
                model=AI_MODEL,
                input=messages,
                metadata={
                    "store_id": str(user["store_number"]),
                    "user_id": str(user["id"]),
                },
            )
        except Exception:
            return jsonify({"error": "Assistant is temporarily unavailable."}), 502

        reply = extract_output_text(response).strip()
        if not reply:
            reply = "I couldn't generate a response with the current data."

        return jsonify(
            {
                "reply": reply,
                "pos_status": context.get("pos_summary", {}).get("status"),
            }
        )

    @app.route("/files/<path:filename>")
    @login_required()
    def download_file(filename: str):
        safe_path = Path(filename)
        if safe_path.is_absolute() or ".." in safe_path.parts:
            abort(400)
        file_path = UPLOAD_ROOT / safe_path
        if not file_path.exists():
            abort(404)
        return send_from_directory(UPLOAD_ROOT, str(safe_path))


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
PASSWORD_METHOD = "pbkdf2:sha256"
