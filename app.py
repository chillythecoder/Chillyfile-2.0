
import os
import secrets
from pathlib import Path
from datetime import datetime, timezone

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
    abort
)

from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user
)

from sqlalchemy import Boolean, UniqueConstraint, func, inspect
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

# Logical storage quota: 100 TB per account.
USER_STORAGE_TB = int(os.environ.get("USER_STORAGE_TB", "100"))
USER_STORAGE_LIMIT = USER_STORAGE_TB * (1024 ** 4)

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "dev-only-change-this-before-deploying"
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
        "postgres://",
        "postgresql://",
        1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."


# ==================================================
# DATABASE MODELS
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

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def storage_used(self):
        result = db.session.query(
            func.coalesce(func.sum(StoredFile.size), 0)
        ).filter(
            StoredFile.user_id == self.id
        ).scalar()

        return int(result or 0)

    @property
    def follower_count(self):
        return Follow.query.filter_by(
            followed_id=self.id
        ).count()

    @property
    def following_count(self):
        return Follow.query.filter_by(
            follower_id=self.id
        ).count()


class Folder(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(
        db.String(80),
        nullable=False
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
        backref="folder"
    )


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

    # Public files appear on the owner's profile.
    is_public = db.Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0"
    )


class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    follower_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    followed_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "follower_id",
            "followed_id",
            name="unique_follow"
        ),
    )


# ==================================================
# DATABASE INITIALIZATION
# ==================================================

def initialize_database():
    db.create_all()

    inspector = inspect(db.engine)

    columns = {
        column["name"]
        for column in inspector.get_columns("stored_file")
    }

    with db.engine.begin() as connection:
        if "folder_id" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE stored_file "
                "ADD COLUMN folder_id INTEGER REFERENCES folder(id)"
            )

        if "is_public" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE stored_file "
                "ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT FALSE"
            )


with app.app_context():
    initialize_database()


# ==================================================
# LOGIN MANAGEMENT
# ==================================================

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


# ==================================================
# TEMPLATE GLOBALS
# ==================================================

@app.context_processor
def inject_globals():
    return {
        "site_name": "ChillyFile2.0",
        "storage_limit": USER_STORAGE_LIMIT,
        "storage_limit_tb": USER_STORAGE_TB
    }


# ==================================================
# HELPER FUNCTIONS
# ==================================================

def format_bytes(size):
    size = float(size)

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} TB"


def get_user_files(user_id, query="", folder_id=None):
    files_query = StoredFile.query.filter_by(
        user_id=user_id
    )

    if query:
        files_query = files_query.filter(
            StoredFile.original_name.ilike(f"%{query}%")
        )

    if folder_id is not None:
        files_query = files_query.filter_by(
            folder_id=folder_id
        )

    return files_query.order_by(
        StoredFile.uploaded_at.desc()
    ).all()


def user_is_following(user_id, target_id):
    return Follow.query.filter_by(
        follower_id=user_id,
        followed_id=target_id
    ).first() is not None


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
            "username",
            ""
        ).strip()

        password = request.form.get("password", "")

        if not (3 <= len(username) <= 32):
            flash(
                "Username must be between 3 and 32 characters.",
                "error"
            )

        elif not username.replace("_", "").isalnum():
            flash(
                "Use only letters, numbers, and underscores.",
                "error"
            )

        elif len(password) < 10:
            flash(
                "Use a password with at least 10 characters.",
                "error"
            )

        elif User.query.filter_by(username=username).first():
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

            flash(
                "Your account has been created.",
                "success"
            )

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
            "username",
            ""
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
    folder_value = request.args.get("folder", "").strip()

    active_folder = None

    if folder_value:
        try:
            folder_id = int(folder_value)
        except ValueError:
            abort(404)

        active_folder = Folder.query.filter_by(
            id=folder_id,
            user_id=current_user.id
        ).first_or_404()

    files = get_user_files(
        current_user.id,
        query=query,
        folder_id=active_folder.id if active_folder else None
    )

    folders = Folder.query.filter_by(
        user_id=current_user.id
    ).order_by(
        Folder.name.asc()
    ).all()

    followers = User.query.join(
        Follow,
        Follow.follower_id == User.id
    ).filter(
        Follow.followed_id == current_user.id
    ).order_by(
        User.username.asc()
    ).all()

    following = User.query.join(
        Follow,
        Follow.followed_id == User.id
    ).filter(
        Follow.follower_id == current_user.id
    ).order_by(
        User.username.asc()
    ).all()

    storage_used = current_user.storage_used

    return render_template(
        "dashboard.html",
        files=files,
        query=query,
        folders=folders,
        active_folder=active_folder,
        followers=followers,
        following=following,
        follower_count=len(followers),
        following_count=len(following),
        storage_used=storage_used,
        storage_limit=USER_STORAGE_LIMIT,
        storage_used_display=format_bytes(storage_used),
        storage_limit_display=format_bytes(USER_STORAGE_LIMIT),
        storage_percent=min(
            100,
            (storage_used / USER_STORAGE_LIMIT) * 100
        ) if USER_STORAGE_LIMIT else 0
    )


# ==================================================
# USER PROFILE
# ==================================================

@app.get("/profile/<username>")
def profile(username):
    profile_user = User.query.filter_by(
        username=username
    ).first_or_404()

    is_following = False

    if current_user.is_authenticated:
        is_following = user_is_following(
            current_user.id,
            profile_user.id
        )

    return render_template(
        "profile.html",
        profile_user=profile_user,
        is_following=is_following,
        follower_count=profile_user.follower_count,
        following_count=profile_user.following_count
    )


# ==================================================
# CREATE FOLDER
# ==================================================

@app.post("/folders/create")
@login_required
def create_folder():
    name = request.form.get("name", "").strip()

    if not name:
        flash("Enter a folder name.", "error")
        return redirect(url_for("dashboard"))

    if len(name) > 80:
        flash(
            "Folder names must be 80 characters or fewer.",
            "error"
        )
        return redirect(url_for("dashboard"))

    existing = Folder.query.filter(
        Folder.user_id == current_user.id,
        func.lower(Folder.name) == name.lower()
    ).first()

    if existing:
        flash(
            "A folder with that name already exists.",
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

    return redirect(
        url_for("dashboard", folder=folder.id)
    )


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

    StoredFile.query.filter_by(
        folder_id=folder.id,
        user_id=current_user.id
    ).update(
        {"folder_id": None},
        synchronize_session=False
    )

    db.session.delete(folder)
    db.session.commit()

    flash(
        "Folder deleted. Its files were moved to your main library.",
        "success"
    )

    return redirect(url_for("dashboard"))


# ==================================================
# MOVE FILE
# ==================================================

@app.post("/files/<int:file_id>/move")
@login_required
def move_file(file_id):
    record = StoredFile.query.filter_by(
        id=file_id,
        user_id=current_user.id
    ).first_or_404()

    folder_value = request.form.get(
        "folder_id",
        ""
    ).strip()

    if not folder_value:
        record.folder_id = None

    else:
        try:
            folder_id = int(folder_value)
        except ValueError:
            flash("Invalid folder.", "error")
            return redirect(url_for("dashboard"))

        folder = Folder.query.filter_by(
            id=folder_id,
            user_id=current_user.id
        ).first()

        if folder is None:
            flash("That folder does not exist.", "error")
            return redirect(url_for("dashboard"))

        record.folder_id = folder.id

    db.session.commit()

    flash("File moved.", "success")

    return redirect(url_for("dashboard"))


# ==================================================
# UPLOAD
# ==================================================

@app.post("/upload")
@login_required
def upload():
    incoming = request.files.getlist("files")

    if not incoming or all(not item.filename for item in incoming):
        flash(
            "Choose at least one file to upload.",
            "error"
        )
        return redirect(url_for("dashboard"))

    folder_value = request.form.get(
        "folder_id",
        ""
    ).strip()

    target_folder = None

    if folder_value:
        try:
            target_folder = Folder.query.filter_by(
                id=int(folder_value),
                user_id=current_user.id
            ).first()
        except ValueError:
            target_folder = None

        if target_folder is None:
            flash("The selected folder is invalid.", "error")
            return redirect(url_for("dashboard"))

    used_storage = current_user.storage_used
    saved_files = []
    saved_count = 0
    rejected_count = 0

    try:
        for item in incoming:
            if not item or not item.filename:
                continue

            raw_name = item.filename.replace(
                "\\",
                "/"
            ).split("/")[-1]

            safe_name = secure_filename(raw_name)

            if not safe_name:
                safe_name = "download"

            original_name = raw_name[:255] or "download"

            disk_name = secrets.token_hex(20)
            destination = UPLOAD_DIR / disk_name

            item.save(destination)

            file_size = destination.stat().st_size

            if used_storage + file_size > USER_STORAGE_LIMIT:
                destination.unlink(missing_ok=True)
                rejected_count += 1
                continue

            record = StoredFile(
                original_name=original_name,
                disk_name=disk_name,
                size=file_size,
                share_token=secrets.token_urlsafe(24),
                user_id=current_user.id,
                folder_id=target_folder.id if target_folder else None
            )

            db.session.add(record)

            saved_files.append(destination)
            used_storage += file_size
            saved_count += 1

        db.session.commit()

    except Exception:
        db.session.rollback()

        for path in saved_files:
            path.unlink(missing_ok=True)

        app.logger.exception("Upload failed")

        flash(
            "An upload failed. Please try again.",
            "error"
        )

        return redirect(url_for("dashboard"))

    if saved_count:
        flash(
            f"{saved_count} file{'s' if saved_count != 1 else ''} uploaded.",
            "success"
        )

    if rejected_count:
        flash(
            f"{rejected_count} file{'s' if rejected_count != 1 else ''} "
            "could not be uploaded because your storage quota would be exceeded.",
            "error"
        )

    if not saved_count and not rejected_count:
        flash("No files were uploaded.", "error")

    return redirect(
        url_for(
            "dashboard",
            folder=target_folder.id if target_folder else None
        )
    )


# ==================================================
# CHANGE FILE VISIBILITY
# ==================================================

@app.post("/files/<int:file_id>/visibility")
@login_required
def toggle_file_visibility(file_id):
    record = StoredFile.query.filter_by(
        id=file_id,
        user_id=current_user.id
    ).first_or_404()

    record.is_public = not record.is_public
    db.session.commit()

    if record.is_public:
        flash("File is now visible on your public profile.", "success")
    else:
        flash("File is now hidden from your public profile.", "success")

    return redirect(url_for("dashboard"))


# ==================================================
# FOLLOW USER
# ==================================================

@app.post("/follow/<username>")
@login_required
def follow_user(username):
    target = User.query.filter_by(
        username=username
    ).first_or_404()

    if target.id == current_user.id:
        flash(
            "You cannot follow your own account.",
            "error"
        )
        return redirect(url_for("dashboard"))

    existing = Follow.query.filter_by(
        follower_id=current_user.id,
        followed_id=target.id
    ).first()

    if existing:
        flash(
            "You are already following that user.",
            "error"
        )
        return redirect(url_for("dashboard"))

    relationship = Follow(
        follower_id=current_user.id,
        followed_id=target.id
    )

    db.session.add(relationship)
    db.session.commit()

    flash(
        f"You are now following {target.username}.",
        "success"
    )

    return redirect(url_for("dashboard"))


# ==================================================
# UNFOLLOW USER
# ==================================================

@app.post("/unfollow/<username>")
@login_required
def unfollow_user(username):
    target = User.query.filter_by(
        username=username
    ).first_or_404()

    relationship = Follow.query.filter_by(
        follower_id=current_user.id,
        followed_id=target.id
    ).first()

    if relationship:
        db.session.delete(relationship)
        db.session.commit()

        flash(
            f"You unfollowed {target.username}.",
            "success"
        )
    else:
        flash(
            "You are not following that user.",
            "error"
        )

    return redirect(url_for("dashboard"))


# ==================================================
# SEARCH USERS
# ==================================================

@app.get("/users/search")
@login_required
def search_users():
    query = request.args.get("q", "").strip()

    if not query:
        return {"users": []}

    matches = User.query.filter(
        User.username.ilike(f"%{query}%"),
        User.id != current_user.id
    ).order_by(
        User.username.asc()
    ).limit(20).all()

    return {
        "users": [
            {
                "username": user.username,
                "followers": user.follower_count,
                "following": user_is_following(
                    current_user.id,
                    user.id
                )
            }
            for user in matches
        ]
    }


# ==================================================
# SHARE PAGE
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
# DOWNLOAD
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
# STORAGE API
# ==================================================

@app.get("/storage")
@login_required
def storage_information():
    used = current_user.storage_used

    return {
        "used_bytes": used,
        "limit_bytes": USER_STORAGE_LIMIT,
        "used": format_bytes(used),
        "limit": format_bytes(USER_STORAGE_LIMIT),
        "percent": min(
            100,
            (used / USER_STORAGE_LIMIT) * 100
        ) if USER_STORAGE_LIMIT else 0
    }


# ==================================================
# UPLOAD SIZE ERROR
# ==================================================

@app.errorhandler(413)
def too_large(_error):
    flash(
        "That upload is too large. "
        f"The current request limit is "
        f"{app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} MB.",
        "error"
    )

    return redirect(
        url_for(
            "dashboard" if current_user.is_authenticated else "index"
        )
    )


# ==================================================
# RUN
# ==================================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=True
    )
