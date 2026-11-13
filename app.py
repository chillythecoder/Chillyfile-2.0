
import os
import secrets
from pathlib import Path
from datetime import datetime, timezone

from flask import (
    Flask, render_template, render_template_string, request,
    redirect, url_for, flash, send_file, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from sqlalchemy import Table, Column, Integer, ForeignKey, UniqueConstraint
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


# ==================================================
# CONFIGURATION
# ==================================================

ROOT = Path(__file__).resolve().parent

UPLOAD_DIR = Path(
    os.environ.get("UPLOAD_DIR", str(ROOT / "uploads"))
).resolve()

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 50 TB quota per user (not pre-purchased storage)
STORAGE_LIMIT = 50 * 1024 ** 4

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "dev-only-change-this"
)

app.config["MAX_CONTENT_LENGTH"] = (
    int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024
)

database_url = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{ROOT / 'chillyfile.db'}"
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."


# ==================================================
# FOLLOWER RELATIONSHIP
# ==================================================

follows = Table(
    "follows",
    db.metadata,
    Column(
        "follower_id",
        Integer,
        ForeignKey("user.id"),
        primary_key=True
    ),
    Column(
        "followed_id",
        Integer,
        ForeignKey("user.id"),
        primary_key=True
    )
)


# ==================================================
# USER MODEL
# ==================================================

class User(UserMixin, db.Model):

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(
        db.String(32),
        unique=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    files = db.relationship(
        "StoredFile",
        backref="owner",
        cascade="all, delete-orphan"
    )

    folders = db.relationship(
        "Folder",
        backref="owner",
        cascade="all, delete-orphan"
    )

    following = db.relationship(
        "User",
        secondary=follows,
        primaryjoin=(id == follows.c.follower_id),
        secondaryjoin=(id == follows.c.followed_id),
        backref=db.backref("followers", lazy="dynamic"),
        lazy="dynamic"
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def storage_used(self):
        total = db.session.query(
            db.func.coalesce(db.func.sum(StoredFile.size), 0)
        ).filter(
            StoredFile.user_id == self.id
        ).scalar()

        return int(total or 0)

    @property
    def storage_remaining(self):
        return max(0, STORAGE_LIMIT - self.storage_used)

    @property
    def follower_count(self):
        return self.followers.count()

    @property
    def following_count(self):
        return self.following.count()

    def is_following(self, user):
        return self.following.filter(
            follows.c.followed_id == user.id
        ).first() is not None


# ==================================================
# FOLDER MODEL
# ==================================================

class Folder(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(
        db.String(100),
        nullable=False
    )

    share_token = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: secrets.token_urlsafe(24)
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    files = db.relationship(
        "StoredFile",
        backref="folder",
        lazy=True
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "name",
            name="unique_user_folder"
        ),
    )


# ==================================================
# FILE MODEL
# ==================================================

class StoredFile(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    original_name = db.Column(
        db.String(255),
        nullable=False
    )

    disk_name = db.Column(
        db.String(80),
        unique=True,
        nullable=False
    )

    size = db.Column(
        db.BigInteger,
        nullable=False
    )

    share_token = db.Column(
        db.String(64),
        unique=True,
        nullable=False
    )

    uploaded_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    folder_id = db.Column(
        db.Integer,
        db.ForeignKey("folder.id"),
        nullable=True
    )


# ==================================================
# LOGIN
# ==================================================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_globals():
    return {
        "site_name": "ChillyFile2.0",
        "storage_limit": STORAGE_LIMIT
    }


# ==================================================
# HOME
# ==================================================

@app.get("/")
def index():

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    return render_template("index.html")


# ==================================================
# REGISTER
# ==================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get("password", "")

        if not (
            3 <= len(username) <= 32
        ) or not username.replace("_", "").isalnum():

            flash(
                "Username must be 3–32 characters and use letters, numbers, or underscores.",
                "error"
            )

        elif len(password) < 10:

            flash(
                "Use a password with at least 10 characters.",
                "error"
            )

        elif User.query.filter_by(
            username=username
        ).first():

            flash(
                "That username is already taken.",
                "error"
            )

        else:

            user = User(username=username)
            user.set_password(password)

            db.session.add(user)
            db.session.commit()

            login_user(user)

            return redirect(url_for("dashboard"))

    return render_template(
        "auth.html",
        mode="Create account"
    )


# ==================================================
# LOGIN
# ==================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get("password", "")

        user = User.query.filter_by(
            username=username
        ).first()

        if user and user.check_password(password):

            login_user(user, remember=True)

            return redirect(url_for("dashboard"))

        flash(
            "Incorrect username or password.",
            "error"
        )

    return render_template(
        "auth.html",
        mode="Sign in"
    )


# ==================================================
# LOGOUT
# ==================================================

@app.post("/logout")
@login_required
def logout():

    logout_user()

    return redirect(url_for("index"))


# ==================================================
# DASHBOARD
# ==================================================

@app.get("/dashboard")
@login_required
def dashboard():

    query = request.args.get("q", "").strip()

    files_query = StoredFile.query.filter_by(
        user_id=current_user.id
    )

    if query:
        files_query = files_query.filter(
            StoredFile.original_name.ilike(
                f"%{query}%"
            )
        )

    files = files_query.order_by(
        StoredFile.uploaded_at.desc()
    ).all()

    folders = Folder.query.filter_by(
        user_id=current_user.id
    ).order_by(
        Folder.created_at.desc()
    ).all()

    return render_template(
        "dashboard.html",
        files=files,
        folders=folders,
        query=query,
        storage_used=current_user.storage_used,
        storage_limit=STORAGE_LIMIT,
        storage_remaining=current_user.storage_remaining
    )


# ==================================================
# UPLOAD FILES
# ==================================================

@app.post("/upload")
@login_required
def upload():

    incoming = request.files.getlist("files")

    if not incoming or all(
        not item.filename for item in incoming
    ):

        flash(
            "Choose at least one file to upload.",
            "error"
        )

        return redirect(url_for("dashboard"))

    saved = 0

    for item in incoming:

        if not item or not item.filename:
            continue

        safe_name = secure_filename(item.filename)

        if not safe_name:
            safe_name = "download"

        # Check available quota before saving
        item.stream.seek(0, os.SEEK_END)
        file_size = item.stream.tell()
        item.stream.seek(0)

        if file_size > current_user.storage_remaining:

            flash(
                "You have reached your available storage quota.",
                "error"
            )

            break

        disk_name = secrets.token_hex(20)

        destination = UPLOAD_DIR / disk_name

        try:

            item.save(destination)

            record = StoredFile(
                original_name=item.filename[:255],
                disk_name=disk_name,
                size=destination.stat().st_size,
                share_token=secrets.token_urlsafe(24),
                user_id=current_user.id
            )

            db.session.add(record)
            db.session.flush()

            saved += 1

        except Exception:

            destination.unlink(missing_ok=True)

            db.session.rollback()

            flash(
                "An upload failed. Please try again.",
                "error"
            )

            return redirect(url_for("dashboard"))

    db.session.commit()

    if saved:
        flash(
            f"{saved} file{'s' if saved != 1 else ''} uploaded.",
            "success"
        )

    return redirect(url_for("dashboard"))


# ==================================================
# CREATE FOLDER
# ==================================================

@app.post("/folders/create")
@login_required
def create_folder():

    name = request.form.get(
        "name", ""
    ).strip()

    if not name or len(name) > 100:

        flash(
            "Enter a folder name between 1 and 100 characters.",
            "error"
        )

        return redirect(url_for("dashboard"))

    existing = Folder.query.filter_by(
        user_id=current_user.id,
        name=name
    ).first()

    if existing:

        flash(
            "You already have a folder with that name.",
            "error"
        )

        return redirect(url_for("dashboard"))

    folder = Folder(
        name=name,
        user_id=current_user.id
    )

    db.session.add(folder)
    db.session.commit()

    flash("Folder created.", "success")

    return redirect(url_for("dashboard"))


# ==================================================
# VIEW FOLDER
# ==================================================

@app.get("/folders/<int:folder_id>")
@login_required
def view_folder(folder_id):

    folder = Folder.query.filter_by(
        id=folder_id,
        user_id=current_user.id
    ).first_or_404()

    files = StoredFile.query.filter_by(
        folder_id=folder.id,
        user_id=current_user.id
    ).order_by(
        StoredFile.uploaded_at.desc()
    ).all()

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{ folder.name }} - ChillyFile2.0</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                background: #080808;
                color: white;
                font-family: Arial, sans-serif;
                max-width: 850px;
                margin: 50px auto;
                padding: 20px;
            }
            a { color: white; }
            .file {
                background: #171717;
                border: 1px solid #333;
                padding: 16px;
                margin: 10px 0;
                border-radius: 10px;
            }
        </style>
    </head>
    <body>
        <a href="{{ url_for('dashboard') }}">Back to dashboard</a>
        <h1>{{ folder.name }}</h1>
        <p>{{ files|length }} files</p>
        {% for file in files %}
        <div class="file">
            {{ file.original_name }}
            <a href="{{ url_for('download', token=file.share_token) }}">Download</a>
        </div>
        {% else %}
        <p>This folder is empty.</p>
        {% endfor %}
    </body>
    </html>
    """, folder=folder, files=files)


# ==================================================
# MOVE FILE INTO FOLDER
# ==================================================

@app.post("/files/<int:file_id>/move")
@login_required
def move_file(file_id):

    record = StoredFile.query.filter_by(
        id=file_id,
        user_id=current_user.id
    ).first_or_404()

    folder_id = request.form.get("folder_id", "").strip()

    if not folder_id:

        record.folder_id = None

    else:

        try:
            folder_id = int(folder_id)
        except ValueError:
            abort(400)

        folder = Folder.query.filter_by(
            id=folder_id,
            user_id=current_user.id
        ).first_or_404()

        record.folder_id = folder.id

    db.session.commit()

    flash("File organization updated.", "success")

    return redirect(url_for("dashboard"))


# ==================================================
# DELETE FOLDER
# ==================================================

@app.post("/folders/<int:folder_id>/delete")
@login_required
def delete_folder(folder_id):

    folder = Folder.query.filter_by(
        id=folder_id,
        user_id=current_user.id
    ).first_or_404()

    # Keep files, but move them out of the deleted folder
    StoredFile.query.filter_by(
        folder_id=folder.id
    ).update({
        "folder_id": None
    })

    db.session.delete(folder)
    db.session.commit()

    flash("Folder deleted.", "success")

    return redirect(url_for("dashboard"))


# ==================================================
# FOLLOW USER
# ==================================================

@app.post("/follow/<username>")
@login_required
def follow_user(username):

    user = User.query.filter_by(
        username=username
    ).first_or_404()

    if user.id == current_user.id:

        flash(
            "You cannot follow yourself.",
            "error"
        )

        return redirect(
            url_for("profile", username=username)
        )

    if not current_user.is_following(user):

        current_user.following.append(user)

        db.session.commit()

        flash("You are now following this user.", "success")

    return redirect(
        url_for("profile", username=username)
    )


# ==================================================
# UNFOLLOW USER
# ==================================================

@app.post("/unfollow/<username>")
@login_required
def unfollow_user(username):

    user = User.query.filter_by(
        username=username
    ).first_or_404()

    if current_user.is_following(user):

        current_user.following.remove(user)

        db.session.commit()

        flash("You unfollowed this user.", "success")

    return redirect(
        url_for("profile", username=username)
    )


# ==================================================
# PUBLIC PROFILE
# ==================================================

@app.get("/profile/<username>")
def profile(username):

    user = User.query.filter_by(
        username=username
    ).first_or_404()

    files = StoredFile.query.filter_by(
        user_id=user.id,
        folder_id=None
    ).order_by(
        StoredFile.uploaded_at.desc()
    ).all()

    folders = Folder.query.filter_by(
        user_id=user.id
    ).order_by(
        Folder.created_at.desc()
    ).all()

    is_following = (
        current_user.is_authenticated
        and current_user.is_following(user)
        and current_user.id != user.id
    )

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{ user.username }} - ChillyFile2.0</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                background: #080808;
                color: white;
                font-family: Arial, sans-serif;
                max-width: 850px;
                margin: 50px auto;
                padding: 20px;
            }
            a { color: white; }
            button {
                background: white;
                color: black;
                padding: 10px 18px;
                border: 0;
                border-radius: 7px;
                cursor: pointer;
            }
            .card {
                background: #171717;
                border: 1px solid #333;
                padding: 16px;
                margin: 10px 0;
                border-radius: 10px;
            }
        </style>
    </head>
    <body>
        <a href="{{ url_for('index') }}">ChillyFile2.0</a>
        <h1>{{ user.username }}</h1>

        <p>{{ user.follower_count }} followers</p>
        <p>{{ user.following_count }} following</p>

        {% if current_user.is_authenticated and current_user.id != user.id %}
            {% if is_following %}
            <form method="POST" action="{{ url_for('unfollow_user', username=user.username) }}">
                <button type="submit">Unfollow</button>
            </form>
            {% else %}
            <form method="POST" action="{{ url_for('follow_user', username=user.username) }}">
                <button type="submit">Follow</button>
            </form>
            {% endif %}
        {% endif %}

        <h2>Public files</h2>
        {% for file in files %}
        <div class="card">
            {{ file.original_name }}
            <a href="{{ url_for('share', token=file.share_token) }}">View</a>
        </div>
        {% else %}
        <p>No public files to display.</p>
        {% endfor %}

        <h2>Folders</h2>
        {% for folder in folders %}
        <div class="card">
            {{ folder.name }}
            <a href="{{ url_for('public_folder', token=folder.share_token) }}">View folder</a>
        </div>
        {% else %}
        <p>No folders to display.</p>
        {% endfor %}
    </body>
    </html>
    """,
    user=user,
    files=files,
    folders=folders,
    is_following=is_following)


# ==================================================
# PUBLIC SHARED FOLDER
# ==================================================

@app.get("/folder/<token>")
def public_folder(token):

    folder = Folder.query.filter_by(
        share_token=token
    ).first_or_404()

    files = StoredFile.query.filter_by(
        folder_id=folder.id,
        user_id=folder.user_id
    ).order_by(
        StoredFile.uploaded_at.desc()
    ).all()

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{ folder.name }} - ChillyFile2.0</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                background: #080808;
                color: white;
                font-family: Arial, sans-serif;
                max-width: 850px;
                margin: 50px auto;
                padding: 20px;
            }
            a { color: white; }
            .file {
                background: #171717;
                border: 1px solid #333;
                padding: 16px;
                margin: 10px 0;
                border-radius: 10px;
            }
        </style>
    </head>
    <body>
        <a href="{{ url_for('index') }}">ChillyFile2.0</a>
        <h1>{{ folder.name }}</h1>
        <p>Shared by {{ folder.owner.username }}</p>

        {% for file in files %}
        <div class="file">
            {{ file.original_name }}
            <a href="{{ url_for('download', token=file.share_token) }}">Download</a>
        </div>
        {% else %}
        <p>This folder is empty.</p>
        {% endfor %}
    </body>
    </html>
    """, folder=folder, files=files)


# ==================================================
# SHARE FILE
# ==================================================

@app.get("/share/<token>")
def share(token):

    record = StoredFile.query.filter_by(
        share_token=token
    ).first_or_404()

    path = UPLOAD_DIR / record.disk_name

    if not path.is_file():
        abort(404)

    return render_template(
        "share.html",
        file=record
    )


# ==================================================
# DOWNLOAD FILE
# ==================================================

@app.get("/download/<token>")
def download(token):

    record = StoredFile.query.filter_by(
        share_token=token
    ).first_or_404()

    path = UPLOAD_DIR / record.disk_name

    if not path.is_file():
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=record.original_name
    )


# ==================================================
# DELETE FILE
# ==================================================

@app.post("/delete/<int:file_id>")
@login_required
def delete_file(file_id):

    record = StoredFile.query.filter_by(
        id=file_id,
        user_id=current_user.id
    ).first_or_404()

    path = UPLOAD_DIR / record.disk_name

    path.unlink(missing_ok=True)

    db.session.delete(record)
    db.session.commit()

    flash("File deleted.", "success")

    return redirect(url_for("dashboard"))


# ==================================================
# STORAGE INFORMATION
# ==================================================

@app.get("/api/storage")
@login_required
def storage_information():

    return {
        "used_bytes": current_user.storage_used,
        "remaining_bytes": current_user.storage_remaining,
        "limit_bytes": STORAGE_LIMIT,
        "limit_tb": 50
    }


# ==================================================
# ERROR HANDLING
# ==================================================

@app.errorhandler(413)
def too_large(_error):

    flash(
        "That file is too large for the current upload limit.",
        "error"
    )

    return redirect(
        url_for(
            "dashboard"
            if current_user.is_authenticated
            else "index"
        )
    )


# ==================================================
# DATABASE INITIALIZATION AND MIGRATION
# ==================================================

with app.app_context():

    db.create_all()

    # Add folder_id to an existing StoredFile table.
    # create_all() does not add columns to existing tables.
    inspector = db.inspect(db.engine)

    if inspector.has_table("stored_file"):

        columns = [
            column["name"]
            for column in inspector.get_columns("stored_file")
        ]

        if "folder_id" not in columns:

            with db.engine.begin() as connection:

                connection.execute(
                    db.text(
                        "ALTER TABLE stored_file "
                        "ADD COLUMN folder_id INTEGER "
                        "REFERENCES folder(id)"
                    )
                )


# ==================================================
# START SERVER
# ==================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False
    )
