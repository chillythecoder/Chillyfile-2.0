# ==================================================
# DOWNLOAD
# ==================================================

@app.get("/download/<token>")
def download(token):
    record = StoredFile.query.filter_by(share_token=token).first_or_404()

    # Private files can only be downloaded by their owner.
    if not record.is_public:
        if not current_user.is_authenticated or current_user.id != record.user_id:
            abort(404)

    try:
        storage = require_b2()
        signed_url = storage.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": B2_BUCKET_NAME,
                "Key": record.disk_name,
                "ResponseContentDisposition": (
                    f'attachment; filename="{record.original_name.replace(chr(34), "")}"'
                )
            },
            ExpiresIn=300
        )
        return redirect(signed_url)
    except Exception:
        app.logger.exception("Could not generate Backblaze download URL")
        abort(503, description="File storage is temporarily unavailable.")


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

    try:
        storage = require_b2()
        storage.delete_object(
            Bucket=B2_BUCKET_NAME,
            Key=record.disk_name
        )
    except Exception:
        app.logger.exception("Could not delete file from Backblaze")
        flash("The file could not be deleted from storage. Please try again.", "error")
        return redirect(url_for("dashboard"))

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
