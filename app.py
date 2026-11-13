import os
import secrets
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(ROOT / "uploads"))).resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-this-before-deploying")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024

database_url = os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'chillyfile.db'}")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    files = db.relationship("StoredFile", backref="owner", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class StoredFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    disk_name = db.Column(db.String(80), unique=True, nullable=False)
    size = db.Column(db.BigInteger, nullable=False)
    share_token = db.Column(db.String(64), unique=True, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_globals():
    return {"site_name": "ChillyFile2.0"}


@app.get("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not (3 <= len(username) <= 32) or not username.replace("_", "").isalnum():
            flash("Username must be 3–32 characters and use letters, numbers, or underscores.", "error")
        elif len(password) < 10:
            flash("Use a password with at least 10 characters.", "error")
        elif User.query.filter_by(username=username).first():
            flash("That username is already taken.", "error")
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="Create account")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("auth.html", mode="Sign in")


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


@app.get("/dashboard")
@login_required
def dashboard():
    query = request.args.get("q", "").strip()
    files_query = StoredFile.query.filter_by(user_id=current_user.id)
    if query:
        files_query = files_query.filter(StoredFile.original_name.ilike(f"%{query}%"))
    files = files_query.order_by(StoredFile.uploaded_at.desc()).all()
    return render_template("dashboard.html", files=files, query=query)


@app.post("/upload")
@login_required
def upload():
    incoming = request.files.getlist("files")
    if not incoming or all(not f.filename for f in incoming):
        flash("Choose at least one file to upload.", "error")
        return redirect(url_for("dashboard"))
    saved = 0
    for item in incoming:
        if not item or not item.filename:
            continue
        safe_name = secure_filename(item.filename)
        if not safe_name:
            safe_name = "download"
        disk_name = secrets.token_hex(20)
        destination = UPLOAD_DIR / disk_name
        try:
            item.save(destination)
            record = StoredFile(
                original_name=item.filename[:255],
                disk_name=disk_name,
                size=destination.stat().st_size,
                share_token=secrets.token_urlsafe(24),
                user_id=current_user.id,
            )
            db.session.add(record)
            saved += 1
        except Exception:
            destination.unlink(missing_ok=True)
            db.session.rollback()
            flash("An upload failed. Please try again.", "error")
            return redirect(url_for("dashboard"))
    db.session.commit()
    flash(f"{saved} file{'s' if saved != 1 else ''} uploaded and saved.", "success")
    return redirect(url_for("dashboard"))


@app.get("/share/<token>")
def share(token):
    record = StoredFile.query.filter_by(share_token=token).first_or_404()
    path = UPLOAD_DIR / record.disk_name
    if not path.is_file():
        abort(404)
    return render_template("share.html", file=record)


@app.get("/download/<token>")
def download(token):
    record = StoredFile.query.filter_by(share_token=token).first_or_404()
    path = UPLOAD_DIR / record.disk_name
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=record.original_name)


@app.post("/delete/<int:file_id>")
@login_required
def delete_file(file_id):
    record = StoredFile.query.filter_by(id=file_id, user_id=current_user.id).first_or_404()
    (UPLOAD_DIR / record.disk_name).unlink(missing_ok=True)
    db.session.delete(record)
    db.session.commit()
    flash("File deleted.", "success")
    return redirect(url_for("dashboard"))


@app.errorhandler(413)
def too_large(_error):
    flash(f"That file is too large. The current limit is {app.config['MAX_CONTENT_LENGTH'] // (1024*1024)} MB.", "error")
    return redirect(url_for("dashboard" if current_user.is_authenticated else "index"))


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=True)
