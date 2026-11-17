import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from uuid import uuid4
from urllib.parse import urljoin, urlsplit

import click
from dotenv import load_dotenv
from mysql.connector.errors import IntegrityError

load_dotenv()

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash

from config import Config
from database import Database
from mailer import Mailer
from models import Catalog, User

csrf = CSRFProtect()
login_manager = LoginManager()
APPAREL_SIZES = ("S", "M", "L")
PRODUCT_COLORS = ("Black", "White", "Grey")
APPAREL_CATEGORIES = ("T-Shirts", "Hoodies", "Shorts", "Sleeveless Shirt")
CONTACT_ADDRESS = "Block 34 Lot 79, Street 7-brgy Dayap, Dayap, Calauan, 4012 Laguna, Philippines"
APPLE_MAPS_URL = "https://maps.apple/p/6A__CY.C45r4L_"
MAX_GALLERY_IMAGES = 3
login_manager.login_view = "login"
login_manager.login_message = "Mag-sign in muna para buksan ang pahinang ito."
login_manager.login_message_category = "info"


@login_manager.user_loader
def load_user(user_id):
    return User.get(current_app.extensions["database"], user_id)


def product_image_extension(upload):
    extension = Path(upload.filename).suffix.lower()
    header = upload.stream.read(12)
    upload.stream.seek(0)
    valid_image = (
        extension in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff")
    ) or (
        extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")
    ) or (
        extension == ".webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    )
    return extension if valid_image else None


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.setdefault("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)
    app.config.setdefault(
        "PRODUCT_UPLOAD_FOLDER",
        str(Path(app.static_folder) / "images" / "products"),
    )
    if test_config:
        app.config.update(test_config)
    database = app.config.get("DATABASE") or Database(app.config)
    app.extensions["database"] = database
    app.extensions["database_initialized"] = app.config.get("DATABASE") is not None
    app.extensions["mailer"] = app.config.get("MAILER") or Mailer(app.config)
    csrf.init_app(app)
    login_manager.init_app(app)

    
    def ensure_database():
        if not app.extensions["database_initialized"]:
            database.initialize()
            app.extensions["database_initialized"] = True

    @app.before_request
    def initialize_database():
        if not app.config.get("PRODUCTION", False):
            ensure_database()

    @app.before_request
    def keep_authenticated_sessions_in_browser_session():
        if current_user.is_authenticated and session.permanent:
            session.permanent = False

    def db():
        return app.extensions["database"]

    def catalog():
        return Catalog(db())

    def order_notification_status():
        return db().query(
            "SELECT COUNT(CASE WHEN status = %s THEN 1 END) AS pending_count, "
            "COALESCE(MAX(id), 0) AS latest_order_id FROM orders",
            ("pending",),
            one=True,
        )

    def product_rating_summary(product_id):
        summary = db().query(
            "SELECT AVG(rating) AS average_rating, COUNT(*) AS rating_count "
            "FROM product_reviews WHERE product_id = %s",
            (product_id,),
            one=True,
        )
        return summary or {"average_rating": 0, "rating_count": 0}

    def has_completed_purchase(product_id):
        return db().query(
            "SELECT 1 AS verified_purchase FROM orders o "
            "INNER JOIN order_items oi ON oi.order_id = o.id "
            "WHERE o.user_id = %s AND oi.product_id = %s "
            "AND o.status = 'completed' LIMIT 1",
            (current_user.id, product_id),
            one=True,
        ) is not None

    def completed_purchase_products(user_id):
        return db().query(
            "SELECT DISTINCT oi.product_id, oi.product_name, "
            "CASE WHEN pr.id IS NULL THEN 0 ELSE 1 END AS has_review, "
            "pr.rating AS existing_rating, pr.review_text AS existing_review_text "
            "FROM orders o "
            "INNER JOIN order_items oi ON oi.order_id = o.id "
            "LEFT JOIN product_reviews pr "
            "ON pr.product_id = oi.product_id AND pr.user_id = o.user_id "
            "WHERE o.user_id = %s AND o.status = 'completed' "
            "ORDER BY oi.product_name",
            (user_id,),
        )

    def safe_local_url(target):
        if not target:
            return None
        reference = urlsplit(request.host_url)
        candidate = urlsplit(urljoin(request.host_url, target))
        if (
            candidate.scheme in ("http", "https")
            and candidate.netloc == reference.netloc
            and candidate.path.startswith("/")
        ):
            return candidate.geturl()
        return None

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.is_admin:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    def cart_items():
        items = []
        total = Decimal("0.00")
        for cart_key, quantity in session.get("cart", {}).items():
            cart_parts = cart_key.split(":", 2)
            product_id = cart_parts[0]
            size = cart_parts[1] if len(cart_parts) > 1 else ""
            color = cart_parts[2] if len(cart_parts) > 2 else ""
            product = catalog().get(int(product_id))
            if product:
                line_total = Decimal(str(product.price)) * quantity
                total += line_total
                items.append(
                    {
                        "product": product,
                        "quantity": quantity,
                        "size": size or None,
                        "color": color or None,
                        "cart_key": cart_key,
                        "line_total": line_total,
                    }
                )
        return items, total

    def make_cart_key(product_id, size="", color=""):
        if color:
            return f"{product_id}:{size}:{color}"
        return f"{product_id}:{size}" if size else str(product_id)

    def cart_product_quantity(cart, product_id, exclude_key=None):
        return sum(
            quantity
            for key, quantity in cart.items()
            if key != exclude_key and key.partition(":")[0] == str(product_id)
        )

    def otp_digest(email, code):
        secret = str(app.config["SECRET_KEY"]).encode("utf-8")
        return hmac.new(
            secret,
            f"{email}:{code}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def generate_otp():
        return f"{secrets.randbelow(1_000_000):06d}"

    def utc_now():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def send_otp(email, code):
        return app.extensions["mailer"].send(
            email,
            "Your Gameplan Apparel verification code",
            f"Your verification code is {code}.\n\n"
            "It expires in 10 minutes. Do not share this code with anyone.",
        )

    def otp_record(email):
        return db().query(
            "SELECT email, name, password_hash, role, otp_hash, expires_at, "
            "created_at, attempts FROM otp_verifications WHERE email = %s",
            (email,),
            one=True,
        )

    def resend_wait_seconds(record):
        created_at = record["created_at"]
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
        elapsed = (utc_now() - created_at).total_seconds()
        return max(0, 60 - int(elapsed))

    @app.context_processor
    def inject_store_data():
        categories = list(APPAREL_CATEGORIES)
        categories.extend(
            category
            for category in catalog().categories()
            if category not in categories and category.casefold() != "accessories"
        )
        new_order_count = 0
        latest_order_id = 0
        if current_user.is_authenticated and current_user.is_admin:
            order_status = order_notification_status()
            new_order_count = int(order_status["pending_count"] or 0)
            latest_order_id = int(order_status["latest_order_id"] or 0)
        return {
            "cart_count": sum(session.get("cart", {}).values()),
            "categories": categories,
            "new_order_count": new_order_count,
            "latest_order_id": latest_order_id,
        }

    def get_hero_slides(slide_directory_name, include_default=False):
        hero_slide_directory = Path(app.static_folder) / "img" / slide_directory_name
        image_extensions = {".jpg", ".jpeg", ".png", ".webp"}
        hero_slides = ["img/login-background.png"] if include_default else []
        if hero_slide_directory.is_dir():
            hero_slides.extend(
                f"img/{slide_directory_name}/{image.name}"
                for image in sorted(
                    hero_slide_directory.iterdir(),
                    key=lambda item: item.name.lower(),
                )
                if image.is_file() and image.suffix.lower() in image_extensions
            )
        return hero_slides or ["img/login-background.png"]

    @app.get("/")
    def welcome():
        return render_template(
            "welcome.html",
            hero_slides=get_hero_slides("hero-slides", include_default=True),
        )

    @app.get("/home")
    def home():
        return render_template(
            "home.html",
            hero_slides=get_hero_slides("home-slides", include_default=True),
        )

    @app.get("/about")
    def about():
        return render_template("about.html")

    @app.route("/contact", methods=["GET", "POST"])
    def contact():
        form_values = {"name": "", "email": "", "phone": "", "message": ""}
        if request.method == "POST":
            form_values = {
                field: request.form.get(field, "").strip()
                for field in form_values
            }
            if not form_values["name"] or len(form_values["name"]) > 100:
                flash("Please enter your name (up to 100 characters).", "error")
            elif (
                len(form_values["email"]) > 254
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", form_values["email"])
            ):
                flash("Please enter a valid email address.", "error")
            elif len(form_values["phone"]) > 30:
                flash("Phone number must be 30 characters or fewer.", "error")
            elif not form_values["message"] or len(form_values["message"]) > 5000:
                flash("Please enter a message of up to 5,000 characters.", "error")
            else:
                recipient = app.config.get("ADMIN_EMAIL", "").strip()
                mailer = app.extensions["mailer"]
                if not recipient:
                    flash("Contact email is not configured. Please use the contact details on this page.", "error")
                elif not mailer.configured:
                    flash("Email service is not configured. Please use the contact details on this page.", "error")
                else:
                    body = (
                        f"Name: {form_values['name']}\n"
                        f"Email: {form_values['email']}\n"
                        f"Phone: {form_values['phone'] or 'Not provided'}\n\n"
                        f"Message:\n{form_values['message']}"
                    )
                    if mailer.send(recipient, "New Gameplan Apparel contact message", body):
                        flash("Thanks for your message! We'll be in touch soon.", "success")
                        form_values = {"name": "", "email": "", "phone": "", "message": ""}
                    else:
                        flash("Your message could not be sent. Please try again or use the contact details on this page.", "error")
        return render_template(
            "contact.html",
            form_values=form_values,
            contact_address=CONTACT_ADDRESS,
        )

    @app.get("/contact/map")
    def contact_map():
        return render_template(
            "contact_map.html",
            contact_address=CONTACT_ADDRESS,
            apple_maps_url=APPLE_MAPS_URL,
        )

    @app.get("/shop")
    @login_required
    def shop():
        category = request.args.get("category", "").strip()
        if category.casefold() == "accessories":
            category = ""
        search = request.args.get("q", "").strip()[:100]
        sort = request.args.get("sort", "newest")
        if sort not in {"newest", "price-low", "price-high"}:
            sort = "newest"
        color = request.args.get("color", "").strip().lower()
        if color not in {"black", "blue"}:
            color = ""
        size = request.args.get("size", "").strip().upper()
        if size not in APPAREL_SIZES:
            size = ""
        try:
            minimum = Decimal(request.args["min_price"]) if request.args.get("min_price") else None
            maximum = Decimal(request.args["max_price"]) if request.args.get("max_price") else None
            if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
                raise InvalidOperation
        except InvalidOperation:
            flash("Hindi wasto ang price filter.", "error")
            minimum, maximum = None, None
        products = catalog().list(category, search, minimum, maximum, sort, color, size)
        completed_product_by_id = {
            item["product_id"]: item
            for item in completed_purchase_products(current_user.id)
        }
        return render_template(
            "shop.html",
            products=products,
            completed_product_by_id=completed_product_by_id,
            selected_category=category,
            search=search,
            selected_sort=sort,
            selected_color=color,
            selected_size=size,
        )

    @app.get("/products/<int:product_id>")
    @login_required
    def product_detail(product_id):
        product = catalog().get(product_id)
        if not product:
            abort(404)
        existing_review = db().query(
            "SELECT id, rating, review_text FROM product_reviews "
            "WHERE product_id = %s AND user_id = %s",
            (product_id, current_user.id),
            one=True,
        )
        can_rate = has_completed_purchase(product_id)
        reviews = db().query(
            "SELECT rating, review_text, created_at FROM product_reviews "
            "WHERE product_id = %s ORDER BY created_at DESC, id DESC",
            (product_id,),
        )
        detail_images = [
            image_url
            for image_url in (product.image_url, *product.image_urls)
            if image_url
        ]
        return render_template(
            "product.html",
            product=product,
            detail_images=detail_images,
            sizes=APPAREL_SIZES if product.category == "T-Shirts" else (),
            colors=product.colors,
            can_rate=can_rate,
            existing_review=existing_review,
            product_reviews=reviews,
        )

    @app.post("/products/<int:product_id>/reviews")
    @login_required
    def submit_product_review(product_id):
        product = catalog().get(product_id)
        if not product:
            abort(404)
        destination = url_for(
            "product_detail",
            product_id=product_id,
            _anchor="product-reviews",
        )
        if not has_completed_purchase(product_id):
            flash("Maaari ka lang mag-rate pagkatapos ma-complete ang order mo.", "error")
            return redirect(destination)

        rating_value = request.form.get("rating", "").strip()
        if not re.fullmatch(r"[1-5]", rating_value):
            flash("Pumili ng rating mula 1 hanggang 5 stars.", "error")
            return redirect(destination)
        review_text = request.form.get("review_text", "").strip()
        if not review_text or len(review_text) > 2000:
            flash("Maglagay ng review na hindi lalampas sa 2,000 character.", "error")
            return redirect(destination)

        db().execute(
            "INSERT INTO product_reviews (product_id, user_id, rating, review_text) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE rating = VALUES(rating), "
            "review_text = VALUES(review_text), updated_at = CURRENT_TIMESTAMP",
            (product_id, current_user.id, int(rating_value), review_text),
        )
        flash("Na-save ang rating at review mo.", "success")
        return redirect(destination)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        admin_email = app.config.get("ADMIN_EMAIL", "").strip().lower()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if (
                not name
                or len(name) > 100
                or len(email) > 254
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            ):
                flash("Ilagay ang wastong pangalan at email address.", "error")
            elif len(password) < 10:
                flash("Dapat hindi bababa sa 10 character ang password.", "error")
            elif User.by_email(db(), email):
                flash("May account na gamit ang email na ito.", "error")
            elif not app.extensions["mailer"].configured:
                flash(
                    "Hindi pa naka-configure ang Gmail SMTP. Ayusin muna ang SMTP settings bago mag-register.",
                    "error",
                )
            else:
                existing_otp = otp_record(email)
                if existing_otp and resend_wait_seconds(existing_otp):
                    session["pending_otp_email"] = email
                    flash(
                        "May pending na verification para sa email na ito. Tingnan ang inbox o spam "
                        "folder mo; maaari kang humiling ulit pagkalipas ng 60 segundo.",
                        "info",
                    )
                    return redirect(url_for("verify_email"))

                code = generate_otp()
                db().execute(
                    "INSERT INTO otp_verifications "
                    "(email, name, password_hash, role, otp_hash, expires_at, attempts, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 0, %s) "
                    "ON DUPLICATE KEY UPDATE name=VALUES(name), "
                    "password_hash=VALUES(password_hash), role=VALUES(role), "
                    "otp_hash=VALUES(otp_hash), expires_at=VALUES(expires_at), "
                    "attempts=0, created_at=VALUES(created_at)",
                    (
                        email,
                        name,
                        generate_password_hash(password),
                        "admin" if email == admin_email else "customer",
                        otp_digest(email, code),
                        utc_now() + timedelta(minutes=10),
                        utc_now(),
                    ),
                )
                session["pending_otp_email"] = email
                if not send_otp(email, code):
                    flash(
                        "Hindi naipadala ang verification code. Suriin ang Gmail SMTP settings "
                        "at subukan muli pagkalipas ng 60 segundo.",
                        "error",
                    )
                else:
                    flash("Nagpadala kami ng 6-digit code sa email mo.", "success")
                return redirect(url_for("verify_email"))
        return render_template("register.html")

    @app.route("/verify-email", methods=["GET", "POST"])
    def verify_email():
        email = session.get("pending_otp_email")
        if not email:
            return redirect(url_for("register"))
        record = otp_record(email)
        if not record:
            session.pop("pending_otp_email", None)
            flash("Walang pending na verification. Mag-register ulit.", "error")
            return redirect(url_for("register"))

        error = None
        if request.method == "POST":
            code = request.form.get("otp", "").strip()
            if not re.fullmatch(r"\d{6}", code):
                error = "Ilagay ang 6-digit verification code."
            elif record["attempts"] >= 5:
                error = "Masyadong maraming maling pagtatangka. Humiling ng bagong code."
            else:
                expires_at = record["expires_at"]
                if expires_at.tzinfo is not None:
                    expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
                if utc_now() >= expires_at:
                    error = "Nag-expire na ang code. Humiling ng bago."
                elif not hmac.compare_digest(record["otp_hash"], otp_digest(email, code)):
                    db().execute(
                        "UPDATE otp_verifications SET attempts = attempts + 1 WHERE email = %s",
                        (email,),
                    )
                    error = "Hindi tama ang code. Suriin ito at subukan muli."
                else:
                    try:
                        with db().transaction() as connection:
                            cursor = connection.cursor()
                            try:
                                cursor.execute(
                                    "INSERT INTO users (name, email, password_hash, role) "
                                    "VALUES (%s, %s, %s, %s)",
                                    (
                                        record["name"],
                                        email,
                                        record["password_hash"],
                                        record["role"],
                                    ),
                                )
                                cursor.execute(
                                    "DELETE FROM otp_verifications WHERE email = %s",
                                    (email,),
                                )
                            finally:
                                cursor.close()
                    except IntegrityError:
                        db().execute(
                            "DELETE FROM otp_verifications WHERE email = %s",
                            (email,),
                        )
                        session.pop("pending_otp_email", None)
                        flash("May account na gamit ang email na ito.", "error")
                        return redirect(url_for("register"))

                    session.pop("pending_otp_email", None)
                    mailer = app.extensions["mailer"]
                    if not mailer.send(
                        email,
                        "Welcome to Gameplan Apparel",
                        f"Hi {record['name']},\n\n"
                        "Your Gameplan Apparel account is verified and ready.",
                    ):
                        flash(
                            "Verified na ang account, pero hindi naipadala ang welcome email.",
                            "warning",
                        )
                    flash("Verified na ang email at nagawa na ang account. Mag-sign in na.", "success")
                    return redirect(url_for("login"))
        return render_template("verify_email.html", email=email, error=error)

    @app.post("/verify-email/resend")
    def resend_verification_email():
        email = session.get("pending_otp_email")
        if not email:
            return redirect(url_for("register"))
        record = otp_record(email)
        if not record:
            session.pop("pending_otp_email", None)
            flash("Walang pending na verification. Mag-register ulit.", "error")
            return redirect(url_for("register"))
        wait_seconds = resend_wait_seconds(record)
        if wait_seconds:
            flash(
                f"Maghintay ng {wait_seconds} segundo bago humiling ng bagong code.",
                "info",
            )
            return redirect(url_for("verify_email"))

        code = generate_otp()
        db().execute(
            "UPDATE otp_verifications SET otp_hash = %s, expires_at = %s, "
            "attempts = 0, created_at = %s WHERE email = %s",
            (
                otp_digest(email, code),
                utc_now() + timedelta(minutes=10),
                utc_now(),
                email,
            ),
        )
        if not send_otp(email, code):
            flash(
                "Hindi naipadala ang bagong code. Suriin ang Gmail SMTP settings at subukan muli.",
                "error",
            )
            return redirect(url_for("verify_email"))
        flash("Nagpadala kami ng bagong verification code.", "success")
        return redirect(url_for("verify_email"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        if request.method == "POST":
            user = User.by_email(db(), request.form.get("email", "").strip().lower())
            password = request.form.get("password", "")
            if user and user.verify_password(password):
                login_user(user)
                session.permanent = False
                return redirect(safe_local_url(request.args.get("next")) or url_for("home"))
            flash("Hindi tugma ang email o password.", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        session.pop("cart", None)
        flash("You have been signed out.", "logout")
        return redirect("/", code=303)

    @app.get("/dashboard")
    @login_required
    def dashboard():
        orders = db().query(
            "SELECT id, total, status, created_at FROM orders "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT 10",
            (current_user.id,),
        )
        return render_template(
            "dashboard.html",
            orders=orders,
        )

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        admin_email = app.config.get("ADMIN_EMAIL", "").strip().lower()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            if (
                not name
                or len(name) > 100
                or len(email) > 254
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            ):
                flash("Ilagay ang wastong pangalan at email address.", "error")
            elif request.form.get("new_password") and len(request.form.get("new_password", "")) < 10:
                flash("Dapat hindi bababa sa 10 character ang bagong password.", "error")
            else:
                existing = User.by_email(db(), email)
                if existing and existing.id != current_user.id:
                    flash("Ginagamit na ng ibang account ang email address na iyan.", "error")
                elif request.form.get("new_password") and not User.get(
                    db(), current_user.id
                ).verify_password(request.form.get("current_password", "")):
                    flash("Hindi tama ang kasalukuyang password.", "error")
                else:
                    new_password = request.form.get("new_password", "")
                    role = "admin" if email == admin_email else "customer"
                    if new_password:
                        db().execute(
                            "UPDATE users SET name = %s, email = %s, password_hash = %s, role = %s WHERE id = %s",
                            (name, email, generate_password_hash(new_password), role, current_user.id),
                        )
                    else:
                        db().execute(
                            "UPDATE users SET name = %s, email = %s, role = %s WHERE id = %s",
                            (name, email, role, current_user.id),
                        )
                    flash("Na-update ang profile.", "success")
                    return redirect(url_for("profile"))
        fresh_user = User.get(db(), current_user.id)
        return render_template("profile.html", profile_user=fresh_user)

    @app.post("/cart/add/<int:product_id>")
    @login_required
    def add_to_cart(product_id):
        product = catalog().get(product_id)
        if not product:
            abort(404)
        size = request.form.get("size", "").strip().upper() if product.category == "T-Shirts" else ""
        if product.category == "T-Shirts" and size not in APPAREL_SIZES:
            flash("Pumili muna ng wastong laki ng T-shirt.", "error")
            return redirect(url_for("product_detail", product_id=product_id))
        color = request.form.get("color", "").strip().title() if product.colors else ""
        if product.colors and color not in product.colors:
            flash("Pumili muna ng wastong kulay ng produkto.", "error")
            return redirect(url_for("product_detail", product_id=product_id))
        try:
            quantity = int(request.form.get("quantity", "1"))
        except ValueError:
            quantity = 0
        cart_key = make_cart_key(product_id, size, color)
        if quantity < 1 or quantity > 10:
            flash("Pumili ng 1 hanggang 10 piraso.", "error")
        elif cart_product_quantity(session.get("cart", {}), product_id) + quantity > product.stock:
            flash("Hindi sapat ang stock para sa dami na pinili.", "error")
        else:
            cart = session.get("cart", {})
            cart[cart_key] = cart.get(cart_key, 0) + quantity
            session["cart"] = cart
            flash(f"Naidagdag sa bag ang {product.name}.", "success")
        return_to = safe_local_url(request.form.get("return_to"))
        if return_to:
            return redirect(return_to)
        return redirect(url_for("shop"))

    @app.get("/cart")
    @login_required
    def cart():
        items, subtotal = cart_items()
        return render_template("cart.html", items=items, subtotal=subtotal)

    @app.post("/cart/update/<int:product_id>")
    @login_required
    def update_cart(product_id):
        product = catalog().get(product_id)
        if not product:
            abort(404)
        size = request.form.get("size", "").strip().upper() if product.category == "T-Shirts" else ""
        if product.category == "T-Shirts" and size not in APPAREL_SIZES:
            size = ""
        color = request.form.get("color", "").strip().title() if product.colors else ""
        if product.colors and color not in product.colors:
            flash("Pumili muna ng wastong kulay ng produkto.", "error")
            return redirect(url_for("product_detail", product_id=product_id))
        cart_key = make_cart_key(product_id, size, color)
        try:
            quantity = int(request.form.get("quantity", "1"))
        except ValueError:
            quantity = 0
        cart = session.get("cart", {})
        if quantity <= 0:
            cart.pop(cart_key, None)
            flash("Inalis ang produkto sa bag.", "success")
        elif (
            quantity > 10
            or cart_product_quantity(cart, product_id, cart_key) + quantity > product.stock
        ):
            flash("Hindi sapat ang stock para sa dami na pinili.", "error")
        else:
            cart[cart_key] = quantity
            flash("Na-update ang dami ng produkto sa bag.", "success")
        session["cart"] = cart
        return redirect(url_for("cart"))

    @app.post("/cart/remove/<int:product_id>")
    @login_required
    def remove_from_cart(product_id):
        cart = session.get("cart", {})
        size = request.form.get("size", "").strip().upper()
        product = catalog().get(product_id)
        color = request.form.get("color", "").strip().title()
        if product and product.colors and color not in product.colors:
            flash("Pumili muna ng wastong kulay ng produkto.", "error")
            return redirect(url_for("cart"))
        cart_key = make_cart_key(
            product_id,
            size if size in APPAREL_SIZES else "",
            color if color in PRODUCT_COLORS else "",
        )
        cart.pop(cart_key, None)
        session["cart"] = cart
        return redirect(url_for("cart"))

    @app.route("/checkout", methods=["GET", "POST"])
    @login_required
    def checkout():
        items, subtotal = cart_items()
        if not items:
            flash("Walang laman ang shopping bag mo.", "info")
            return redirect(url_for("cart"))
        shipping_fee = Decimal("100.00")
        total = subtotal + shipping_fee
        if request.method == "POST":
            fields = {
                key: request.form.get(key, "").strip()
                for key in (
                    "full_name",
                    "phone",
                    "email",
                    "address",
                    "barangay",
                    "city",
                    "province",
                    "postal_code",
                )
            }
            if any(not value or len(value) > 255 for value in fields.values()):
                flash("Punan nang wasto ang lahat ng kinakailangang shipping field.", "error")
            elif (
                len(fields["email"]) > 254
                or len(fields["phone"]) > 30
                or len(fields["barangay"]) > 100
                or len(fields["postal_code"]) > 20
            ):
                flash("May shipping field na lumampas sa pinapayagang haba.", "error")
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fields["email"]):
                flash("Hindi wasto ang email address.", "error")
            elif not re.fullmatch(r"\+?[\d ()-]{7,30}", fields["phone"]) or sum(
                character.isdigit() for character in fields["phone"]
            ) < 7:
                flash("Ilagay ang wastong contact number.", "error")
            elif request.form.get("payment_method") != "cod":
                flash("Pumili ng wastong payment method.", "error")
            else:
                try:
                    with db().transaction() as connection:
                        cursor = connection.cursor()
                        cursor.execute(
                            "INSERT INTO orders (user_id, full_name, phone, email, address, barangay, "
                            "city, province, postal_code, payment_method, subtotal, shipping_fee, total) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (
                                current_user.id,
                                fields["full_name"],
                                fields["phone"],
                                fields["email"],
                                fields["address"],
                                fields["barangay"],
                                fields["city"],
                                fields["province"],
                                fields["postal_code"],
                                "cod",
                                subtotal,
                                shipping_fee,
                                total,
                            ),
                        )
                        order_id = cursor.lastrowid
                        stock_short = False
                        for item in items:
                            cursor.execute(
                                "UPDATE products SET stock = stock - %s "
                                "WHERE id = %s AND stock >= %s AND active = TRUE",
                                (
                                    item["quantity"],
                                    item["product"].id,
                                    item["quantity"],
                                ),
                            )
                            if cursor.rowcount != 1:
                                stock_short = True
                                break
                            cursor.execute(
                                "INSERT INTO order_items (order_id, product_id, product_name, size, "
                                "color, unit_price, quantity) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                (
                                    order_id,
                                    item["product"].id,
                                    item["product"].name,
                                    item["size"],
                                    item["color"],
                                    item["product"].price,
                                    item["quantity"],
                                ),
                            )
                        cursor.close()
                        if stock_short:
                            raise ValueError("Insufficient stock")
                except ValueError:
                    flash("May produktong naubusan na ng stock. Suriin muli ang bag mo.", "error")
                    return redirect(url_for("cart"))
                session["cart"] = {}
                mailer = app.extensions["mailer"]
                sent = mailer.send(
                    fields["email"],
                    f"Gameplan order #{order_id} received",
                    f"Hi {fields['full_name']},\n\nWe received your order #{order_id} "
                    f"for PHP {total:.2f}. Payment method: cod.",
                )
                if not sent:
                    flash("Hindi naipadala ang order confirmation email; nakatala pa rin ang order.", "warning")
                flash(f"Natanggap ang order #{order_id}.", "success")
                return redirect(url_for("dashboard"))
        return render_template(
            "checkout.html",
            items=items,
            subtotal=subtotal,
            shipping_fee=shipping_fee,
            total=total,
        )

    @app.get("/admin/products")
    @admin_required
    def admin_products():
        products = db().query("SELECT * FROM products ORDER BY created_at DESC")
        ratings = db().query(
            "SELECT product_id, AVG(rating) AS average_rating, "
            "COUNT(*) AS rating_count FROM product_reviews GROUP BY product_id"
        )
        ratings_by_product = {rating["product_id"]: rating for rating in ratings}
        for product in products:
            rating = ratings_by_product.get(product["id"], {})
            product["average_rating"] = float(rating.get("average_rating") or 0)
            product["rating_count"] = int(rating.get("rating_count") or 0)
        return render_template("admin_products.html", products=products)

    @app.get("/admin/products/<int:product_id>/ratings")
    @admin_required
    def admin_product_ratings(product_id):
        product = db().query(
            "SELECT id, name FROM products WHERE id = %s",
            (product_id,),
            one=True,
        )
        if not product:
            abort(404)
        ratings = db().query(
            "SELECT r.rating, r.review_text, r.created_at, "
            "u.name AS customer_name, u.email AS customer_email "
            "FROM product_reviews r JOIN users u ON u.id = r.user_id "
            "WHERE r.product_id = %s ORDER BY r.created_at DESC, r.id DESC",
            (product_id,),
        )
        return render_template(
            "admin_product_ratings.html",
            product=product,
            ratings=ratings,
            rating_summary=product_rating_summary(product_id),
        )

    @app.get("/admin/orders")
    @admin_required
    def admin_orders():
        orders = db().query(
            "SELECT o.id, o.full_name, o.email, o.phone, o.payment_method, o.status, "
            "o.total, o.created_at, u.name AS customer_name "
            "FROM orders o LEFT JOIN users u ON u.id = o.user_id "
            "ORDER BY o.created_at DESC"
        )
        pending_count = sum(order["status"] == "pending" for order in orders)
        order_value = sum(
            (Decimal(str(order["total"])) for order in orders),
            Decimal("0.00"),
        )
        return render_template(
            "admin_orders.html",
            orders=orders,
            pending_count=pending_count,
            order_value=order_value,
        )

    @app.get("/admin/order-notifications")
    @admin_required
    def admin_order_notifications():
        order_status = order_notification_status()
        orders = db().query(
            "SELECT id, full_name, status, created_at FROM orders "
            "ORDER BY id DESC LIMIT 10"
        )
        response = jsonify(
            pending_count=int(order_status["pending_count"] or 0),
            latest_order_id=int(order_status["latest_order_id"] or 0),
            orders=[
                {
                    "id": order["id"],
                    "full_name": order["full_name"],
                    "status": order["status"],
                    "created_at": order["created_at"],
                    "manage_url": url_for("admin_orders"),
                }
                for order in orders
            ],
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/admin/orders/<int:order_id>")
    @admin_required
    def admin_order_detail(order_id):
        order = db().query(
            "SELECT o.*, u.name AS customer_name "
            "FROM orders o LEFT JOIN users u ON u.id = o.user_id "
            "WHERE o.id = %s",
            (order_id,),
            one=True,
        )
        if not order:
            abort(404)
        items = db().query(
            "SELECT product_name, size, color, unit_price, quantity "
            "FROM order_items WHERE order_id = %s ORDER BY id",
            (order_id,),
        )
        return render_template("admin_order_detail.html", order=order, items=items)

    @app.post("/admin/orders/<int:order_id>/status")
    @admin_required
    def update_order_status(order_id):
        order = db().query("SELECT id FROM orders WHERE id = %s", (order_id,), one=True)
        if not order:
            abort(404)
        return_to_orders = request.form.get("return_to") == "orders"
        destination = (
            url_for("admin_orders")
            if return_to_orders
            else url_for("admin_order_detail", order_id=order_id)
        )
        status = request.form.get("status", "pending")
        valid_statuses = {"pending", "processing", "shipped", "completed", "cancelled"}
        if status not in valid_statuses:
            flash("Hindi valid ang status ng order.", "error")
            return redirect(destination)
        db().execute("UPDATE orders SET status = %s WHERE id = %s", (status, order_id))
        flash("Na-update ang status ng order.", "success")
        return redirect(destination)

    @app.post("/admin/orders/<int:order_id>/delete")
    @admin_required
    def delete_order(order_id):
        order = db().query("SELECT id FROM orders WHERE id = %s", (order_id,), one=True)
        if not order:
            abort(404)
        with db().transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
                cursor.execute("DELETE FROM orders WHERE id = %s", (order_id,))
            finally:
                cursor.close()
        flash(f"Na-delete ang order #{order_id} at lahat ng item nito.", "success")
        return redirect(url_for("admin_orders"))

    @app.post("/admin/orders/delete-all")
    @admin_required
    def delete_all_orders():
        with db().transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("DELETE FROM order_items")
                cursor.execute("DELETE FROM orders")
                deleted_count = cursor.rowcount
            finally:
                cursor.close()
        flash(f"Na-delete ang {deleted_count} order at lahat ng order item nito.", "success")
        return redirect(url_for("admin_orders"))

    @app.route("/admin/products/new", methods=["GET", "POST"])
    @app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_product(product_id=None):
        product = db().query(
            "SELECT * FROM products WHERE id = %s", (product_id,), one=True
        ) if product_id else None
        if product_id and not product:
            abort(404)
        gallery_images = db().query(
            "SELECT image_url FROM product_images WHERE product_id = %s "
            "ORDER BY position, id",
            (product_id,),
        ) if product_id else []
        saved_colors = db().query(
            "SELECT color FROM product_colors WHERE product_id = %s "
            "ORDER BY FIELD(color, 'Black', 'White', 'Grey')",
            (product_id,),
        ) if product_id else []
        selected_colors = [row["color"] for row in saved_colors]
        if request.method == "POST":
            selected_colors = [
                color for color in PRODUCT_COLORS if color in request.form.getlist("colors")
            ]
            name = request.form.get("name", "").strip()
            slug = request.form.get("slug", "").strip().lower()
            description = request.form.get("description", "").strip()
            category = request.form.get("category", "").strip()
            price_input = request.form.get("price", "").strip()
            stock_input = request.form.get("stock", "").strip()
            uploaded_image = request.files.get("image")
            has_upload = bool(uploaded_image and uploaded_image.filename)
            additional_uploads = [
                upload
                for upload in request.files.getlist("gallery_images")
                if upload.filename
            ]
            image_url = product["image_url"] if product else ""
            error_message = None
            if len(gallery_images) + len(additional_uploads) > MAX_GALLERY_IMAGES:
                error_message = (
                    f"Hanggang {MAX_GALLERY_IMAGES} additional product photos lang "
                    "ang puwede sa bawat produkto."
                )
            try:
                price = Decimal(price_input)
                stock = int(stock_input)
                if not name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
                    raise ValueError
                if price <= 0 or stock < 0 or not category or len(category) > 50:
                    raise ValueError
            except (InvalidOperation, ValueError):
                error_message = "Suriin ang pangalan, slug, category, presyo, at stock."
            if not error_message and any(
                color not in PRODUCT_COLORS for color in request.form.getlist("colors")
            ):
                error_message = "Pumili lamang ng Black, White, o Grey na kulay."

            image_uploads = ([uploaded_image] if has_upload else []) + additional_uploads
            validated_uploads = []
            if not error_message:
                for upload in image_uploads:
                    extension = product_image_extension(upload)
                    if not extension:
                        error_message = "Pumili lamang ng valid na JPG, PNG, o WebP na larawan."
                        break
                    validated_uploads.append((upload, extension))
            if not error_message and not image_url and not has_upload:
                error_message = "Mag-upload ng larawan para sa produktong ito."

            if error_message:
                flash(error_message, "error")
            else:
                upload_folder = Path(app.config["PRODUCT_UPLOAD_FOLDER"])
                if image_uploads:
                    upload_folder.mkdir(parents=True, exist_ok=True)
                gallery_urls = []
                if has_upload:
                    extension = validated_uploads[0][1]
                    filename = f"{uuid4().hex}{extension}"
                    uploaded_image.save(upload_folder / filename)
                    image_url = url_for(
                        "static",
                        filename=f"images/products/{filename}",
                    )
                for upload, extension in validated_uploads[1 if has_upload else 0:]:
                    filename = f"{uuid4().hex}{extension}"
                    upload.save(upload_folder / filename)
                    gallery_urls.append(
                        url_for("static", filename=f"images/products/{filename}")
                    )

                values = (
                    name,
                    slug,
                    description,
                    category,
                    price,
                    image_url,
                    stock,
                    bool(request.form.get("featured")),
                )
                if product_id:
                    db().execute(
                        "UPDATE products SET name=%s, slug=%s, description=%s, category=%s, "
                        "price=%s, image_url=%s, stock=%s, featured=%s, active=TRUE WHERE id=%s",
                        (*values, product_id),
                    )
                    saved_product_id = product_id
                else:
                    saved_product_id, _row_count = db().execute(
                        "INSERT INTO products (name, slug, description, category, price, image_url, "
                        "stock, featured) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        values,
                    )
                if additional_uploads:
                    for position, gallery_url in enumerate(
                        gallery_urls,
                        start=len(gallery_images),
                    ):
                        db().execute(
                            "INSERT INTO product_images (product_id, image_url, position) "
                            "VALUES (%s, %s, %s)",
                            (saved_product_id, gallery_url, position),
                        )
                db().execute(
                    "DELETE FROM product_colors WHERE product_id = %s",
                    (saved_product_id,),
                )
                for color in selected_colors:
                    db().execute(
                        "INSERT INTO product_colors (product_id, color) VALUES (%s, %s)",
                        (saved_product_id, color),
                    )
                flash("Na-save ang produkto.", "success")
                return redirect(url_for("shop"))
        return render_template(
            "product_form.html",
            product=product,
            gallery_images=gallery_images,
            selected_colors=selected_colors,
        )

    @app.post("/admin/products/<int:product_id>/visibility")
    @admin_required
    def toggle_product_visibility(product_id):
        product = db().query(
            "SELECT id, active FROM products WHERE id = %s",
            (product_id,),
            one=True,
        )
        if not product:
            abort(404)
        active = not bool(product["active"])
        db().execute(
            "UPDATE products SET active = %s WHERE id = %s",
            (active, product_id),
        )
        flash(
            "Ipinakita na ang produkto sa shop."
            if active
            else "Itinago na ang produkto sa shop.",
            "success",
        )
        return redirect(url_for("admin_products"))

    @app.post("/admin/products/<int:product_id>/delete")
    @admin_required
    def delete_product(product_id):
        db().execute("UPDATE products SET active = FALSE WHERE id = %s", (product_id,))
        flash("Inalis ang produkto sa catalog.", "success")
        return redirect(url_for("admin_products"))

    @app.cli.command("seed")
    def seed_products():
        """Replace the sample catalog with the three Gameplan T-shirts."""
        ensure_database()
        db().execute("UPDATE products SET active = FALSE WHERE active = TRUE")
        examples = [
            ("Poster Graphic Tee", "poster-graphic-tee", "Black cotton T-shirt with a bold illustrated front graphic. A standout everyday streetwear piece.", "T-Shirts", 600, "/static/img/poster-graphic-tee.png", 24, True),
            ("Gameplan Blue Logo Tee", "gameplan-blue-logo-tee", "Royal blue cotton T-shirt with red GAMEPLAN lettering across the chest. Comfortable and easy to style.", "T-Shirts", 600, "/static/img/blue-logo-tee.png", 24, True),
            ("All In Statement Tee", "all-in-statement-tee", "Black cotton T-shirt with the motivational To be ballin, you gotta be all-in print. Made for the everyday grind.", "T-Shirts", 600, "/static/img/all-in-tee.png", 24, True),
        ]
        for row in examples:
            db().execute(
                "INSERT INTO products (name, slug, description, category, price, image_url, stock, featured) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), "
                "category=VALUES(category), price=VALUES(price), image_url=VALUES(image_url), "
                "stock=VALUES(stock), featured=VALUES(featured), active=TRUE",
                row,
            )
        click.echo("The three Gameplan T-shirts are ready.")

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", code=403, message="Wala kang pahintulot dito."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="Hindi makita ang pahinang hinahanap mo."), 404

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False))
