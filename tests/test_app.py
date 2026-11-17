import unittest
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app
from mailer import Mailer


PRODUCT = {
    "id": 1,
    "name": "GP Essential Tee",
    "slug": "gp-essential-tee",
    "description": "Everyday heavyweight cotton tee.",
    "category": "T-Shirts",
    "price": Decimal("699.00"),
    "image_url": "/static/img/poster-graphic-tee.png",
    "stock": 10,
    "featured": True,
}


class MemoryCursor:
    def __init__(self, database):
        self.database = database
        self.rowcount = 0
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self.lastrowid, self.rowcount = self.database.execute(sql, params)

    def close(self):
        pass


class MemoryConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return MemoryCursor(self.database)


class MemoryMailer:
    configured = True

    def __init__(self):
        self.messages = []
        self.send_success = True

    def send(self, recipient, subject, body):
        self.messages.append((recipient, subject, body))
        return self.send_success


class MemoryDatabase:
    def __init__(self):
        self.queries = []
        self.executions = []
        self.otp_verifications = {}
        self.created_users = {}
        self.product_gallery = []
        self.product_colors = []
        self.product_reviews = []
        self.order_items = [
            {"order_id": 1, "product_id": 1, "product_name": "GP Essential Tee"}
        ]
        self.user = {
            "id": 7,
            "name": "Gameplan Customer",
            "email": "customer@example.com",
            "password_hash": generate_password_hash("secure-password-123"),
            "role": "customer",
        }
        self.order = {
            "id": 1,
            "user_id": 7,
            "full_name": "Gameplan Customer",
            "phone": "+639171234567",
            "email": "customer@example.com",
            "address": "123 Street",
            "barangay": "Barangay 123",
            "city": "Manila",
            "province": "Metro Manila",
            "postal_code": "1000",
            "payment_method": "cod",
            "status": "pending",
            "subtotal": Decimal("699.00"),
            "shipping_fee": Decimal("100.00"),
            "total": Decimal("799.00"),
            "created_at": datetime(2026, 1, 1, 0, 0, 0),
            "customer_name": "Gameplan Customer",
        }
        self.catalog_products = [PRODUCT.copy()]
        self.deleted_order_items = []
        self.deleted_orders = []

    def query(self, sql, params=(), one=False):
        self.queries.append((sql, params))
        if "FROM otp_verifications" in sql:
            row = self.otp_verifications.get(params[0])
            return row.copy() if one and row else None
        if "DISTINCT category" in sql:
            return [
                {"category": category}
                for category in sorted({product["category"] for product in self.catalog_products})
            ]
        if "FROM product_images" in sql:
            if "product_id IN" in sql:
                product_ids = set(params)
                return [
                    image.copy()
                    for image in self.product_gallery
                    if image["product_id"] in product_ids
                ]
            return [
                image.copy()
                for image in self.product_gallery
                if image["product_id"] == params[0]
            ]
        if "FROM product_colors" in sql:
            if "product_id IN" in sql:
                product_ids = set(params)
                return [
                    color.copy()
                    for color in self.product_colors
                    if color["product_id"] in product_ids
                ]
            return [
                color.copy()
                for color in self.product_colors
                if color["product_id"] == params[0]
            ]
        if "FROM product_reviews" in sql:
            if "GROUP BY product_id" in sql:
                product_ids = set(params) if "product_id IN" in sql else None
                reviews = [
                    review
                    for review in self.product_reviews
                    if product_ids is None or review["product_id"] in product_ids
                ]
                grouped = {}
                for review in reviews:
                    summary = grouped.setdefault(
                        review["product_id"],
                        {"product_id": review["product_id"], "rating_total": 0, "rating_count": 0},
                    )
                    summary["rating_total"] += review["rating"]
                    summary["rating_count"] += 1
                return [
                    {
                        "product_id": product_id,
                        "average_rating": summary["rating_total"] / summary["rating_count"],
                        "rating_count": summary["rating_count"],
                    }
                    for product_id, summary in grouped.items()
                ]
            if "JOIN users" in sql:
                return [
                    {
                        **review.copy(),
                        "customer_name": self.user["name"],
                        "customer_email": self.user["email"],
                    }
                    for review in self.product_reviews
                    if review["product_id"] == params[0]
                ]
            if "COUNT(*) AS rating_count" in sql:
                matches = [
                    review
                    for review in self.product_reviews
                    if review["product_id"] == params[0]
                ]
                return {
                    "average_rating": (
                        sum(review["rating"] for review in matches) / len(matches)
                        if matches
                        else 0
                    ),
                    "rating_count": len(matches),
                }
            if one and "user_id = %s" in sql:
                match = next(
                    (
                        review
                        for review in self.product_reviews
                        if review["product_id"] == params[0]
                        and review["user_id"] == params[1]
                    ),
                    None,
                )
                return match.copy() if match else None
            return [
                review.copy()
                for review in self.product_reviews
                if review["product_id"] == params[0]
            ]
        if "FROM products" in sql:
            if "SELECT * FROM products ORDER BY created_at DESC" in sql:
                return [
                    {**product.copy(), "active": True}
                    for product in self.catalog_products
                ]
            if "SELECT id, active FROM products" in sql:
                return {"id": 1, "active": True} if one and params == (1,) else None
            if "WHERE id = %s" in sql:
                return PRODUCT.copy() if one and params == (1,) else None
            return [product.copy() for product in self.catalog_products]
        if "FROM users" in sql:
            if one and params in (("customer@example.com",), (7,), ("7",)):
                return self.user.copy()
            if one and params and params[0] in self.created_users:
                return self.created_users[params[0]].copy()
            return None
        if "FROM orders o" in sql and "JOIN order_items oi" in sql:
            if "SELECT DISTINCT oi.product_id" in sql:
                if self.order["status"] != "completed" or self.order["user_id"] != params[0]:
                    return []
                completed_products = {}
                for item in self.order_items:
                    if item["order_id"] != self.order["id"]:
                        continue
                    review = next(
                        (
                            review
                            for review in self.product_reviews
                            if review["product_id"] == item["product_id"]
                            and review["user_id"] == params[0]
                        ),
                        None,
                    )
                    completed_products[item["product_id"]] = {
                        "product_id": item["product_id"],
                        "product_name": item["product_name"],
                        "has_review": int(review is not None),
                        "existing_rating": review["rating"] if review else None,
                        "existing_review_text": review["review_text"] if review else None,
                    }
                return list(completed_products.values())
            is_completed_purchase = (
                self.order["status"] == "completed"
                and self.order["user_id"] == params[0]
                and params[1] == 1
            )
            return {"verified_purchase": 1} if one and is_completed_purchase else None
        if "FROM orders" in sql:
            if "COUNT(CASE WHEN status = %s THEN 1 END)" in sql:
                return {
                    "pending_count": int(self.order["status"] == params[0]) if self.order else 0,
                    "latest_order_id": self.order["id"] if self.order else 0,
                }
            if "SELECT id, full_name, status, created_at FROM orders" in sql:
                return [self.order.copy()] if self.order and self.order["id"] not in self.deleted_orders and "all" not in self.deleted_orders else []
            if "WHERE o.id = %s" in sql or "WHERE id = %s" in sql:
                return self.order.copy() if one and self.order and params == (1,) and 1 not in self.deleted_orders and "all" not in self.deleted_orders else None
            return [self.order.copy()] if self.order and self.order["id"] not in self.deleted_orders and "all" not in self.deleted_orders else []
        if "FROM order_items" in sql:
            return [{
                "product_name": "GP Essential Tee",
                "size": "M",
                "color": "White",
                "unit_price": Decimal("699.00"),
                "quantity": 1,
            }]
        return [] if not one else None

    def execute(self, sql, params=()):
        self.executions.append((sql, params))
        if "INSERT INTO otp_verifications" in sql:
            email, name, password_hash, role, otp_hash, expires_at, created_at = params
            self.otp_verifications[email] = {
                "email": email,
                "name": name,
                "password_hash": password_hash,
                "role": role,
                "otp_hash": otp_hash,
                "expires_at": expires_at,
                "created_at": created_at,
                "attempts": 0,
            }
        elif "UPDATE otp_verifications SET attempts = attempts + 1" in sql:
            self.otp_verifications[params[0]]["attempts"] += 1
        elif "UPDATE otp_verifications" in sql:
            otp_hash, expires_at, created_at, email = params
            record = self.otp_verifications[email]
            record.update(
                otp_hash=otp_hash,
                expires_at=expires_at,
                attempts=0,
                created_at=created_at,
            )
        elif "DELETE FROM otp_verifications" in sql:
            self.otp_verifications.pop(params[0], None)
        elif "DELETE FROM order_items" in sql:
            self.deleted_order_items.append(params[0] if params else "all")
        elif "DELETE FROM orders" in sql:
            self.deleted_orders.append(params[0] if params else "all")
        elif "DELETE FROM product_colors" in sql:
            self.product_colors = [
                color
                for color in self.product_colors
                if color["product_id"] != params[0]
            ]
        elif "INSERT INTO product_colors" in sql:
            product_id, color = params
            self.product_colors.append({"product_id": product_id, "color": color})
        elif "INSERT INTO product_reviews" in sql:
            product_id, user_id, rating, review_text = params
            existing = next(
                (
                    review
                    for review in self.product_reviews
                    if review["product_id"] == product_id
                    and review["user_id"] == user_id
                ),
                None,
            )
            if existing:
                existing.update(rating=rating, review_text=review_text)
            else:
                self.product_reviews.append(
                    {
                        "id": len(self.product_reviews) + 1,
                        "product_id": product_id,
                        "user_id": user_id,
                        "rating": rating,
                        "review_text": review_text,
                        "created_at": datetime(2026, 1, 1),
                    }
                )
        elif "INSERT INTO products" in sql:
            name, slug, description, category, price, image_url, stock, featured = params
            self.catalog_products.append(
                {
                    "id": len(self.catalog_products) + 1,
                    "name": name,
                    "slug": slug,
                    "description": description,
                    "category": category,
                    "price": price,
                    "image_url": image_url,
                    "stock": stock,
                    "featured": featured,
                }
            )
        elif "INSERT INTO users" in sql:
            name, email, password_hash, role = params
            self.created_users[email] = {
                "id": 8,
                "name": name,
                "email": email,
                "password_hash": password_hash,
                "role": role,
            }
        return 1, 1

    @contextmanager
    def transaction(self):
        yield MemoryConnection(self)


class StoreAppTests(unittest.TestCase):
    def setUp(self):
        self.database = MemoryDatabase()
        self.mailer = MemoryMailer()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": self.database,
                "MAILER": self.mailer,
            }
        )
        self.client = self.app.test_client()

    def test_store_home_displays_navigation_and_benefits_without_product_listings(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/home")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Shop by Category", response.data)
        self.assertNotIn(b"home-editorial", response.data)
        self.assertNotIn(b"MADE FOR", response.data)
        self.assertIn(b"home-services", response.data)
        self.assertIn(b"MAKE YOUR", response.data)
        self.assertIn(b"OWN GAMEPLAN", response.data)
        self.assertNotIn(b"img/blue-logo-tee.png", response.data)
        self.assertNotIn(b"Shop All Products", response.data)
        self.assertIn(b'href="/shop"', response.data)
        self.assertNotIn(b'href="/collections"', response.data)
        self.assertIn(b"NEW", response.data)
        self.assertIn(b"ARRIVALS", response.data)
        self.assertNotIn(b"New Drops", response.data)
        self.assertNotIn(b"GP Essential Tee", response.data)
        self.assertNotIn(b"Welcome back", response.data)

    def test_root_is_a_hero_only_entry_page_with_account_actions(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="entry-page"', response.data)
        self.assertIn(b"MAKE YOUR", response.data)
        self.assertIn(b"OWN GAMEPLAN", response.data)
        self.assertNotIn(b"Shop by Category", response.data)
        self.assertNotIn(b"home-editorial", response.data)
        self.assertIn(b' href="/register"', response.data)
        self.assertIn(b' href="/login"', response.data)
        self.assertNotIn(b"GP Essential Tee", response.data)
        self.assertNotIn(b"Welcome Back!", response.data)

    def test_store_home_is_separate_and_shows_navigation_options(self):
        response = self.client.get("/home")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="home-page"', response.data)
        self.assertIn(b'href="/home" class="active"', response.data)
        self.assertIn(b'href="/shop"', response.data)
        self.assertIn(b'href="/about"', response.data)
        self.assertIn(b'href="/contact"', response.data)
        self.assertIn(b"home-services", response.data)
        self.assertNotIn(b"home-editorial", response.data)

    def test_home_discovers_supported_slideshow_images(self):
        with TemporaryDirectory() as static_directory:
            slide_directory = Path(static_directory) / "img" / "hero-slides"
            slide_directory.mkdir(parents=True)
            (slide_directory / "campaign.webp").write_bytes(b"image")
            (slide_directory / "notes.txt").write_text("not an image")
            self.app.static_folder = static_directory

            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/static/img/hero-slides/campaign.webp", response.data)
        self.assertNotIn(b"notes.txt", response.data)
        self.assertIn(b"data-hero-slide=\"1\"", response.data)
        self.assertIn(b"data-hero-slide=\"0\"", response.data)

    def test_store_home_uses_only_its_own_slideshow_images(self):
        home = self.client.get("/home")
        entry = self.client.get("/")

        self.assertEqual(home.status_code, 200)
        self.assertIn(b"/static/img/login-background.png", home.data)
        self.assertIn(b"/static/img/home-slides/camouflage-collection.png", home.data)
        self.assertIn(b"/static/img/home-slides/gp-basketball-selfie.png", home.data)
        self.assertLess(
            home.data.index(b"/static/img/login-background.png"),
            home.data.index(b"/static/img/home-slides/camouflage-collection.png"),
        )
        self.assertNotIn(b"/static/img/home-slides/", entry.data)

    def test_about_page_shows_brand_story_and_is_public(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ABOUT US", response.data)
        self.assertIn(b"Our Mission", response.data)
        self.assertIn(b"Our Vision", response.data)
        self.assertIn(b"Our Values", response.data)
        self.assertIn(b'href="/about" class="active"', response.data)
        self.assertNotIn(b"What We Offer", response.data)
        self.assertIn(b"comfortable, stylish, and high-quality everyday wear", response.data)
        self.assertIn("Quality · Style · Comfort".encode(), response.data)

    def test_contact_page_renders_full_page_design_and_details(self):
        response = self.client.get("/contact")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="contact-body"', response.data)
        self.assertIn(b"CONTACT US", response.data)
        self.assertIn(b"Contact info", response.data)
        self.assertIn(b"GAMEPLAN PH", response.data)
        self.assertIn(b"0955 415 8813", response.data)
        self.assertIn(b"Gameplan", response.data)
        self.assertIn(b"Address", response.data)
        self.assertIn(b"Block 34 Lot 79, Street 7-brgy Dayap", response.data)
        self.assertIn(b"4012 Laguna, Philippines", response.data)
        self.assertIn(b'href="/contact/map"', response.data)
        self.assertIn(b"Send Us a Message", response.data)
        self.assertNotIn(b"https://maps.apple/p/6A__CY.C45r4L_", response.data)
        self.assertNotIn(b'target="_blank"', response.data)
        self.assertNotIn(b"support@gameplanapparel.com", response.data)
        self.assertIn(b'href="/contact" class="active"', response.data)
        self.assertIn(b'name="csrf_token"', response.data)

    def test_contact_map_page_has_a_return_button_and_apple_maps_link(self):
        response = self.client.get("/contact/map")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="contact-body"', response.data)
        self.assertIn(b"Block 34 Lot 79, Street 7-brgy Dayap", response.data)
        self.assertIn(b'href="/contact">', response.data)
        self.assertIn(b"Back to Contact", response.data)
        self.assertIn(b"https://maps.apple/p/6A__CY.C45r4L_", response.data)
        self.assertIn(b'target="_blank"', response.data)

    def test_admin_contact_page_hides_message_form_but_keeps_contact_info(self):
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        login = self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(login.status_code, 302)

        response = self.client.get("/contact")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Contact info", response.data)
        self.assertIn(b"GAMEPLAN PH", response.data)
        self.assertIn(b"contact-admin-view", response.data)
        self.assertNotIn(b"Send Us a Message", response.data)
        self.assertNotIn(b"contact-form-card", response.data)

    def test_contact_form_validates_and_sends_to_admin_email(self):
        self.app.config["ADMIN_EMAIL"] = "support@example.com"
        response = self.client.post(
            "/contact",
            data={
                "name": "Alex Customer",
                "email": "alex@example.com",
                "phone": "09123456789",
                "message": "I have a question about my order.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Thanks for your message", response.data)
        self.assertEqual(len(self.mailer.messages), 1)
        recipient, subject, body = self.mailer.messages[0]
        self.assertEqual(recipient, "support@example.com")
        self.assertEqual(subject, "New Gameplan Apparel contact message")
        self.assertIn("alex@example.com", body)
        self.assertIn("I have a question about my order.", body)

    def test_contact_form_rejects_invalid_email_without_sending(self):
        self.app.config["ADMIN_EMAIL"] = "support@example.com"
        response = self.client.post(
            "/contact",
            data={"name": "Alex", "email": "not-an-email", "message": "Hello"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please enter a valid email address", response.data)
        self.assertEqual(self.mailer.messages, [])

    def test_collection_page_is_removed(self):
        response = self.client.get("/collections")
        self.assertEqual(response.status_code, 404)

    def test_lookbook_routes_and_navigation_are_removed(self):
        self.assertEqual(self.client.get("/lookbook").status_code, 404)
        self.assertEqual(self.client.get("/admin/lookbook").status_code, 404)
        self.assertNotIn(b'href="/lookbook"', self.client.get("/").data)

    def test_login_from_public_landing_page_redirects_to_home(self):
        login = self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.headers["Location"], "/home")

    def test_login_does_not_issue_a_remember_cookie(self):
        login_page = self.client.get("/login")
        self.assertNotIn(b"Remember Me", login_page.data)
        response = self.client.post(
            "/login",
            data={
                "email": "customer@example.com",
                "password": "secure-password-123",
                "remember": "yes",
            },
        )
        cookies = response.headers.getlist("Set-Cookie")
        session_cookie = next(cookie for cookie in cookies if cookie.startswith("session="))

        self.assertFalse(any(cookie.startswith("remember_token=") for cookie in cookies))
        self.assertNotIn("Expires=", session_cookie)

    def test_admin_can_save_product_with_csrf(self):
        csrf_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": self.database,
                "ADMIN_EMAIL": "customer@example.com",
                "PRODUCT_UPLOAD_FOLDER": self.get_temp_upload_folder(),
                "WTF_CSRF_ENABLED": True,
            }
        )
        csrf_client = csrf_app.test_client()
        login_page = csrf_client.get("/login")
        login_token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            login_page.data,
        ).group(1).decode()
        login = csrf_client.post(
            "/login",
            data={
                "csrf_token": login_token,
                "email": "customer@example.com",
                "password": "secure-password-123",
            },
        )
        self.assertEqual(login.status_code, 302)

        product_page = csrf_client.get("/admin/products/new")
        self.assertEqual(product_page.status_code, 200)
        product_token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            product_page.data,
        ).group(1).decode()
        saved = csrf_client.post(
            "/admin/products/new",
            data={
                "csrf_token": product_token,
                "name": "CSRF Tee",
                "slug": "csrf-tee",
                "description": "A test product",
                "category": "T-Shirts",
                "price": "600",
                "stock": "5",
                "image": (BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4), "tee.png"),
            },
        )

        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved.headers["Location"], "/shop")

    def test_existing_permanent_session_is_converted_to_browser_session(self):
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        self.client.post(
            "/login",
            data={
                "email": "customer@example.com",
                "password": "secure-password-123",
            },
        )
        with self.client.session_transaction() as user_session:
            user_session.permanent = True

        response = self.client.get("/dashboard")

        session_cookie = next(
            cookie
            for cookie in response.headers.getlist("Set-Cookie")
            if cookie.startswith("session=")
        )
        self.assertNotIn("Expires=", session_cookie)

    def test_admin_header_keeps_notifications_but_hides_shopping_bag(self):
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.get("/home")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="icon-link notification-link"', response.data)
        self.assertIn(b'class="notification-count">1</span>', response.data)
        self.assertIn(b'id="order-notification-panel"', response.data)
        self.assertNotIn(b'class="icon-link cart-link"', response.data)
        self.assertNotIn(b'class="header-search"', response.data)

        notification_status = self.client.get("/admin/order-notifications")
        self.assertEqual(notification_status.status_code, 200)
        self.assertEqual(
            notification_status.get_json()["orders"][0]["manage_url"],
            "/admin/orders",
        )

    def test_customer_header_keeps_shopping_bag_without_admin_notifications(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.get("/home")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="icon-link cart-link"', response.data)
        self.assertNotIn(b'class="header-search"', response.data)
        self.assertNotIn(b"notification-link", response.data)

    def test_guidelines_page_is_removed(self):
        response = self.client.get("/guidelines")
        self.assertEqual(response.status_code, 404)

    def test_catalog_and_cart_require_sign_in(self):
        for path in ("/shop", "/products/1", "/cart"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].startswith("/login?next="))

    def test_app_factory_does_not_connect_to_mysql_until_a_request(self):
        with patch("app.Database.initialize") as initialize:
            create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-key",
                    "WTF_CSRF_ENABLED": False,
                }
            )
        initialize.assert_not_called()

    def test_customer_auth_pages_render_their_account_forms(self):
        login = self.client.get("/login")
        register = self.client.get("/register")
        home = self.client.get("/")
        logo_asset = self.client.get("/static/img/gameplan-logo.png")
        background_asset = self.client.get("/static/img/login-background.png")

        self.assertEqual(login.status_code, 200)
        self.assertIn(b'name="email"', login.data)
        self.assertIn(b"Welcome Back!", login.data)
        self.assertNotIn(b"Remember Me", login.data)
        self.assertIn(b"Sign Up", login.data)
        self.assertIn(b"password-toggle", login.data)
        self.assertIn(b'class="auth-page"', login.data)
        self.assertIn(b'img/gameplan-logo.png', login.data)
        self.assertEqual(register.status_code, 200)
        self.assertIn(b'name="name"', register.data)
        self.assertIn(b'At least 10 characters', register.data)
        self.assertIn(b'class="auth-page"', register.data)
        self.assertIn(b'class="auth-wrap auth-register-page"', register.data)
        self.assertIn(b"YEARS IN THE GAME", register.data)
        self.assertIn(b'img/gameplan-logo.png', register.data)
        self.assertIn(b'img/gameplan-logo.png', home.data)
        self.assertEqual(logo_asset.status_code, 200)
        self.assertTrue(logo_asset.content_type.startswith("image/png"))
        self.assertEqual(background_asset.status_code, 200)
        self.assertTrue(background_asset.content_type.startswith("image/png"))
        logo_asset.close()
        background_asset.close()

    def test_registration_requires_email_otp_before_creating_account(self):
        response = self.client.post(
            "/register",
            data={
                "name": "New Customer",
                "email": "new@example.com",
                "password": "secure-password-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-email")
        self.assertNotIn("new@example.com", self.database.created_users)
        pending = self.database.otp_verifications["new@example.com"]
        self.assertEqual(len(pending["otp_hash"]), 64)
        self.assertNotIn("secure-password-123", pending["password_hash"])
        self.assertIn("new@example.com", self.mailer.messages[0][0])

        verify_page = self.client.get("/verify-email")
        self.assertEqual(verify_page.status_code, 200)
        self.assertIn(b'name="otp"', verify_page.data)
        code = re.search(r"code is (\d{6})", self.mailer.messages[0][2]).group(1)
        verified = self.client.post("/verify-email", data={"otp": code})

        self.assertEqual(verified.status_code, 302)
        self.assertEqual(verified.headers["Location"], "/login")
        self.assertIn("new@example.com", self.database.created_users)
        self.assertNotIn("new@example.com", self.database.otp_verifications)
        self.assertEqual(len(self.mailer.messages), 2)

    def test_registration_reports_smtp_failure_without_creating_account(self):
        self.mailer.send_success = False

        response = self.client.post(
            "/register",
            data={
                "name": "New Customer",
                "email": "new@example.com",
                "password": "secure-password-123",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hindi naipadala ang verification code", response.data)
        self.assertNotIn("new@example.com", self.database.created_users)

    def test_registration_requires_gmail_smtp_configuration(self):
        self.mailer.configured = False

        response = self.client.post(
            "/register",
            data={
                "name": "New Customer",
                "email": "new@example.com",
                "password": "secure-password-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hindi pa naka-configure ang Gmail SMTP", response.data)
        self.assertFalse(self.database.otp_verifications)
        self.assertFalse(self.mailer.messages)

    def test_gmail_mailer_uses_starttls_and_a_normalized_app_password(self):
        mailer = Mailer(
            {
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": 587,
                "SMTP_USER": "shop@gmail.com",
                "SMTP_PASSWORD": "abcd efgh ijkl mnop",
                "MAIL_FROM": "shop@gmail.com",
            }
        )
        with patch("mailer.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value

            self.assertTrue(mailer.send("customer@example.com", "OTP", "123456"))

        smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("shop@gmail.com", "abcdefghijklmnop")
        server.send_message.assert_called_once()

    def test_expired_otp_and_five_attempt_limit_are_enforced(self):
        with patch("app.secrets.randbelow", return_value=123456):
            self.client.post(
                "/register",
                data={
                    "name": "New Customer",
                    "email": "new@example.com",
                    "password": "secure-password-123",
                },
            )
            code = re.search(
                r"code is (\d{6})", self.mailer.messages[0][2]
            ).group(1)

            pending = self.database.otp_verifications["new@example.com"]
            pending["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
            expired = self.client.post("/verify-email", data={"otp": code})
            self.assertIn(b"Nag-expire na ang code", expired.data)

            pending["expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=10)
            for _ in range(5):
                self.client.post("/verify-email", data={"otp": "000000"})
            locked = self.client.post("/verify-email", data={"otp": code})

        self.assertIn(b"Masyadong maraming maling pagtatangka", locked.data)
        self.assertEqual(pending["attempts"], 5)
        self.assertNotIn("new@example.com", self.database.created_users)

    def test_registration_otp_resend_is_throttled_and_replaces_the_old_code(self):
        with patch("app.secrets.randbelow", side_effect=[111111, 222222]):
            self.client.post(
                "/register",
                data={
                    "name": "New Customer",
                    "email": "new@example.com",
                    "password": "secure-password-123",
                },
            )
            original_code = re.search(
                r"code is (\d{6})", self.mailer.messages[0][2]
            ).group(1)

            throttled = self.client.post("/verify-email/resend", follow_redirects=True)
            self.assertIn(b"Maghintay ng", throttled.data)
            self.assertEqual(len(self.mailer.messages), 1)

            self.database.otp_verifications["new@example.com"]["created_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=61)
            )
            resent = self.client.post("/verify-email/resend")
            self.assertEqual(resent.status_code, 302)
            self.assertEqual(len(self.mailer.messages), 2)
            new_code = re.search(r"code is (\d{6})", self.mailer.messages[1][2]).group(1)
            self.assertNotEqual(original_code, new_code)

            verified = self.client.post("/verify-email", data={"otp": new_code})
            self.assertEqual(verified.headers["Location"], "/login")
            self.assertIn("new@example.com", self.database.created_users)

    def test_customer_and_admin_logout_return_to_entry_page(self):
        for role, admin_email in (("customer", ""), ("admin", "customer@example.com")):
            with self.subTest(role=role):
                self.app.config["ADMIN_EMAIL"] = admin_email
                self.client.post(
                    "/login",
                    data={"email": "customer@example.com", "password": "secure-password-123"},
                )
                logout_response = self.client.post("/logout")

                self.assertEqual(logout_response.status_code, 303)
                self.assertEqual(logout_response.headers["Location"], "/")

                response = self.client.get(logout_response.headers["Location"])
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'class="entry-page"', response.data)
                self.assertIn(b'href="/register"', response.data)
                self.assertIn(b'href="/login"', response.data)
                self.assertIn(b"You have been signed out.", response.data)
                self.assertIn(b'class="flash flash-logout"', response.data)

    def test_category_filter_uses_a_sql_parameter(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop?category=T-Shirts")
        self.assertEqual(response.status_code, 200)
        query, params = next(
            (query, params)
            for query, params in self.database.queries
            if "category = %s" in query
        )
        self.assertEqual(params[0], "T-Shirts")
        self.assertNotIn("T-Shirts", query)

    def test_shop_renders_collection_controls_without_products(self):
        self.database.catalog_products = []
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SHOP COLLECTION", response.data)
        self.assertIn(b"All Products", response.data)
        self.assertIn(b'<span>T-Shirts</span>', response.data)
        self.assertIn(b'<span>Hoodies</span>', response.data)
        self.assertIn(b'<span>Shorts</span>', response.data)
        self.assertIn(b'<span>Sleeveless Shirt</span>', response.data)
        self.assertNotIn(b'<span>Accessories</span>', response.data)
        self.assertIn(b'name="min_price"', response.data)
        self.assertIn(b'name="max_price"', response.data)
        self.assertIn(b'value="newest"', response.data)
        self.assertIn(b"0 products available", response.data)
        self.assertIn(b"Our shop is getting ready", response.data)
        self.assertNotIn(b'aria-label="Add GP Essential Tee to favorites"', response.data)
        self.assertNotIn(b'aria-label="Choose size for GP Essential Tee"', response.data)
        self.assertNotIn(b"poster-graphic-tee.png", response.data)
        self.assertIn(b'aria-label="Grid view"', response.data)
        self.assertIn(b'aria-label="List view"', response.data)

    def test_accessories_is_removed_from_shop_categories_and_filter(self):
        self.database.catalog_products.append(
            {**PRODUCT, "id": 2, "category": "Accessories", "name": "Accessory item"}
        )
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.get("/shop?category=Accessories")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<h2>All Products</h2>", response.data)
        self.assertNotIn(b'name="category" value="Accessories"', response.data)

    def test_shop_displays_products_from_the_catalog(self):
        self.database.product_gallery = [
            {"product_id": 1, "image_url": "/static/images/products/back.png"},
        ]
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.get("/shop")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"1 product available", response.data)
        self.assertIn(b"GP Essential Tee", response.data)
        self.assertIn(b"poster-graphic-tee.png", response.data)
        self.assertIn(b'class="product-image quick-view-trigger"', response.data)
        self.assertIn(b'id="quick-view-dialog"', response.data)
        self.assertIn(b'data-cart-url="/cart/add/1"', response.data)
        self.assertIn(b"back.png", response.data)
        self.assertIn(b'id="quick-view-buy-now"', response.data)
        self.assertIn(b'id="quick-view-rating"', response.data)
        self.assertIn(b'data-product-can-rate="false"', response.data)
        self.assertNotIn(b'id="quick-view-review-form"', response.data)
        self.assertIn(b'id="quick-view-rating-stars"', response.data)
        self.assertIn(b'id="quick-view-reviews-link"', response.data)
        self.assertIn(b'class="shop-product-grid"', response.data)
        self.assertIn(b'id="product-grid"', response.data)
        self.assertNotIn(b"Our shop is getting ready", response.data)

    def test_admin_shop_and_product_detail_hide_purchase_controls(self):
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        shop = self.client.get("/shop")
        self.assertEqual(shop.status_code, 200)
        self.assertNotIn(b'id="quick-view-form"', shop.data)
        self.assertNotIn(b'id="quick-view-buy-now"', shop.data)
        self.assertNotIn(b"Add to Cart", shop.data)
        self.assertNotIn(b'class="shop-add-form"', shop.data)
        self.assertNotIn(b'class="shop-select-options"', shop.data)

        detail = self.client.get("/products/1")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(b'class="detail-purchase-form"', detail.data)
        self.assertNotIn(b'class="detail-promises"', detail.data)

    def test_quick_view_links_to_reviews_and_keeps_rating_on_product_page(self):
        self.database.order["status"] = "completed"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        shop = self.client.get("/shop")

        self.assertEqual(shop.status_code, 200)
        self.assertIn(b'data-product-review-page-url="/products/1#product-reviews"', shop.data)
        self.assertNotIn(b'id="quick-view-review-form"', shop.data)

        detail = self.client.get("/products/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b'id="review-text"', detail.data)

    def test_product_color_choices_render_only_for_configured_products(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        unconfigured = self.client.get("/products/1")
        self.assertNotIn(b'name="color"', unconfigured.data)
        self.database.product_colors = [
            {"product_id": 1, "color": "Black"},
            {"product_id": 1, "color": "White"},
        ]
        detail = self.client.get("/products/1")
        shop = self.client.get("/shop")

        self.assertIn(b'name="color" value="Black" required', detail.data)
        self.assertIn(b'name="color" value="White" required', detail.data)
        self.assertIn(b'data-product-colors="[&#34;Black&#34;, &#34;White&#34;]"', shop.data)

    def test_product_color_is_required_and_preserved_through_checkout(self):
        self.database.product_colors = [
            {"product_id": 1, "color": "Black"},
            {"product_id": 1, "color": "White"},
        ]
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        missing_color = self.client.post(
            "/cart/add/1",
            data={"size": "M", "quantity": "1"},
        )
        self.assertEqual(missing_color.headers["Location"], "/products/1")
        with self.client.session_transaction() as store:
            self.assertEqual(store.get("cart", {}), {})

        added = self.client.post(
            "/cart/add/1",
            data={"size": "M", "color": "White", "quantity": "1"},
        )
        self.assertEqual(added.status_code, 302)
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:M:White": 1})

        cart = self.client.get("/cart")
        updated_cart = self.client.post(
            "/cart/update/1",
            data={"size": "M", "color": "White", "quantity": "2"},
        )
        self.assertEqual(updated_cart.status_code, 302)
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:M:White": 2})
        checkout = self.client.get("/checkout")
        self.assertIn(b"Color White", cart.data)
        self.assertIn(b"Color White", checkout.data)

        ordered = self.client.post(
            "/checkout",
            data={
                "full_name": "Gameplan Customer",
                "phone": "+639171234567",
                "email": "customer@example.com",
                "address": "123 Street",
                "barangay": "Barangay 123",
                "city": "Manila",
                "province": "Metro Manila",
                "postal_code": "1000",
                "payment_method": "cod",
            },
        )
        self.assertEqual(ordered.status_code, 302)
        order_item = next(
            params
            for query, params in self.database.executions
            if "INSERT INTO order_items" in query
        )
        self.assertEqual(order_item[4], "White")
        self.assertEqual(order_item[6], 2)

    def test_checkout_offers_only_cod_and_rejects_other_payment_methods(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.client.post("/cart/add/1", data={"size": "M", "quantity": "1"})

        checkout = self.client.get("/checkout")
        self.assertIn(b'name="payment_method" value="cod"', checkout.data)
        self.assertNotIn(b'value="gcash"', checkout.data)
        self.assertNotIn(b'value="bank_transfer"', checkout.data)

        order_data = {
            "full_name": "Gameplan Customer",
            "phone": "+639171234567",
            "email": "customer@example.com",
            "address": "123 Street",
            "barangay": "Barangay 123",
            "city": "Manila",
            "province": "Metro Manila",
            "postal_code": "1000",
        }
        for payment_method in ("gcash", "bank_transfer"):
            with self.subTest(payment_method=payment_method):
                response = self.client.post(
                    "/checkout",
                    data={**order_data, "payment_method": payment_method},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Pumili ng wastong payment method.", response.data)

        self.assertFalse(
            any("INSERT INTO orders" in query for query, _params in self.database.executions)
        )

    def test_shop_color_filter_uses_a_sql_parameter(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop?color=blue")
        self.assertEqual(response.status_code, 200)
        query, params = next(
            (query, params)
            for query, params in self.database.queries
            if "name LIKE %s OR description LIKE %s" in query
        )
        self.assertEqual(params[-2:], ("%blue%", "%blue%"))
        self.assertNotIn("%blue%", query)

    def test_seed_replaces_the_sample_catalog_with_three_equal_price_tees(self):
        result = self.app.test_cli_runner().invoke(args=["seed"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("The three Gameplan T-shirts are ready.", result.output)
        self.assertIn(
            ("UPDATE products SET active = FALSE WHERE active = TRUE", ()),
            self.database.executions,
        )
        products = [
            params
            for query, params in self.database.executions
            if "INSERT INTO products" in query
        ]
        self.assertEqual(len(products), 3)
        self.assertEqual(
            {product[1] for product in products},
            {"poster-graphic-tee", "gameplan-blue-logo-tee", "all-in-statement-tee"},
        )
        self.assertEqual({product[4] for product in products}, {600})

    def test_seed_product_image_urls_are_available(self):
        for filename in (
            "poster-graphic-tee.png",
            "blue-logo-tee.png",
            "all-in-tee.png",
        ):
            with self.subTest(filename=filename):
                response = self.client.get(f"/static/img/{filename}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/png")
                response.close()

    def test_tshirt_detail_has_gallery_size_guide_and_quantity_controls(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/products/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"GAMEPLAN APPAREL CO.", response.data)
        self.assertIn(b'name="size"', response.data)
        self.assertIn(b'value="S"', response.data)
        self.assertIn(b'value="L"', response.data)
        self.assertIn(b"Size Guide", response.data)
        self.assertIn(b'data-quantity-step="-1"', response.data)
        self.assertIn(b"Fast Delivery", response.data)
        self.assertIn(b"class=\"product-gallery product-gallery-single\"", response.data)
        self.assertIn(b'src="/static/img/poster-graphic-tee.png"', response.data)

    def test_product_detail_renders_gallery_thumbnails(self):
        self.database.product_gallery = [
            {"product_id": 1, "image_url": "/static/images/products/back.png"},
            {"product_id": 1, "image_url": "/static/images/products/detail.png"},
        ]
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.get("/products/1")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"product-gallery-single", response.data)
        self.assertEqual(response.data.count(b'data-image='), 3)
        self.assertIn(b'data-image="/static/images/products/back.png"', response.data)

    def test_unrated_products_show_star_placeholders_to_customers_and_admin(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        detail = self.client.get("/products/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"No ratings yet (0)", detail.data)
        self.assertIn(b'name="rating-preview"', detail.data)
        self.assertIn(b"account that placed a completed order for this product", detail.data)

        shop = self.client.get("/shop")
        self.assertIn(b"No ratings", shop.data)

        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        products = self.client.get("/admin/products")
        self.assertEqual(products.status_code, 200)
        self.assertIn(b"No ratings (0)", products.data)
        self.assertIn(b"admin/products/1/ratings", products.data)

    def test_dashboard_does_not_show_completed_purchase_rating_list(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        dashboard = self.client.get("/dashboard")

        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(b"Rate your completed purchases", dashboard.data)
        self.assertIn(b"Recent Orders", dashboard.data)

    def test_completed_customer_can_submit_and_update_a_star_review(self):
        self.database.order["status"] = "completed"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        detail = self.client.get("/products/1")
        self.assertIn(b'name="rating" value="5"', detail.data)
        self.assertIn(b"Rate this product", detail.data)

        submitted = self.client.post(
            "/products/1/reviews",
            data={"rating": "5", "review_text": "Great quality and fit."},
        )
        self.assertEqual(submitted.status_code, 302)
        self.assertTrue(submitted.headers["Location"].endswith("#product-reviews"))
        self.assertEqual(len(self.database.product_reviews), 1)
        self.assertEqual(self.database.product_reviews[0]["rating"], 5)

        updated = self.client.post(
            "/products/1/reviews",
            data={"rating": "4", "review_text": "Updated after wearing it."},
        )
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(len(self.database.product_reviews), 1)
        self.assertEqual(self.database.product_reviews[0]["rating"], 4)
        self.assertEqual(
            self.database.product_reviews[0]["review_text"],
            "Updated after wearing it.",
        )
        product_page = self.client.get("/products/1")
        self.assertIn(b"4.0 / 5", product_page.data)
        self.assertIn(b"Updated after wearing it.", product_page.data)
        shop_page = self.client.get("/shop")
        self.assertIn(b"4.0", shop_page.data)
        self.assertIn(b"(1)", shop_page.data)

    def test_admin_with_completed_purchase_can_submit_a_star_review(self):
        self.database.order["status"] = "completed"
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        detail = self.client.get("/products/1")
        self.assertIn(b'name="rating" value="5"', detail.data)
        submitted = self.client.post(
            "/products/1/reviews",
            data={"rating": "5", "review_text": "Bought and completed through admin account."},
        )
        self.assertEqual(submitted.status_code, 302)
        self.assertEqual(len(self.database.product_reviews), 1)

    def test_rating_requires_completed_purchase_and_valid_star_value(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        no_completed_order = self.client.post(
            "/products/1/reviews",
            data={"rating": "5", "review_text": "Nice"},
        )
        self.assertEqual(no_completed_order.status_code, 302)
        self.assertEqual(self.database.product_reviews, [])

        self.database.order["status"] = "completed"
        invalid_rating = self.client.post(
            "/products/1/reviews",
            data={"rating": "6", "review_text": "Nice"},
        )
        self.assertEqual(invalid_rating.status_code, 302)
        self.assertEqual(self.database.product_reviews, [])

    def test_admin_can_view_product_rating_summary_and_customer_reviews(self):
        self.database.product_reviews = [
            {
                "id": 1,
                "product_id": 1,
                "user_id": 7,
                "rating": 5,
                "review_text": "Great quality and fit.",
                "created_at": datetime(2026, 1, 1),
            }
        ]
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        products = self.client.get("/admin/products")
        self.assertEqual(products.status_code, 200)
        self.assertIn(b"Ratings", products.data)
        self.assertIn(b"5.0 / 5", products.data)
        self.assertIn(b"(1)", products.data)
        self.assertIn(b"View", products.data)

        ratings = self.client.get("/admin/products/1/ratings")
        self.assertEqual(ratings.status_code, 200)
        self.assertIn(b"Great quality and fit.", ratings.data)
        self.assertIn(b"customer@example.com", ratings.data)
        self.assertIn(b"5 out of 5 stars", ratings.data)

    def test_customer_cannot_view_admin_product_ratings(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/admin/products/1/ratings")
        self.assertEqual(response.status_code, 403)

    def test_tshirt_size_is_required_and_preserved_in_cart(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        missing_size = self.client.post("/cart/add/1", data={"quantity": "1"})
        self.assertEqual(missing_size.status_code, 302)
        self.assertEqual(missing_size.headers["Location"], "/products/1")
        self.assertEqual(self.client.get("/cart").status_code, 200)
        with self.client.session_transaction() as store:
            self.assertEqual(store.get("cart", {}), {})

        added = self.client.post(
            "/cart/add/1",
            data={"quantity": "2", "size": "L", "return_to": "/products/1"},
        )
        self.assertEqual(added.headers["Location"], "http://localhost/products/1")
        self.client.post("/cart/add/1", data={"quantity": "9", "size": "M"})
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:L": 2})
        cart = self.client.get("/cart")
        self.assertEqual(cart.status_code, 200)
        self.assertIn(b"Size L", cart.data)
        self.assertIn(b'value="2"', cart.data)

        removed = self.client.post("/cart/remove/1", data={"size": "L"})
        self.assertEqual(removed.status_code, 302)
        with self.client.session_transaction() as store:
            self.assertEqual(store.get("cart", {}), {})

    def test_cart_update_shows_success_or_stock_error_feedback(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        with self.client.session_transaction() as store:
            store["cart"] = {"1:M": 2}

        updated = self.client.post(
            "/cart/update/1",
            data={"quantity": "3", "size": "M"},
            follow_redirects=True,
        )

        self.assertEqual(updated.status_code, 200)
        self.assertIn(b"Na-update ang dami ng produkto sa bag.", updated.data)
        self.assertIn(b'value="3"', updated.data)
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:M": 3})

        rejected = self.client.post(
            "/cart/update/1",
            data={"quantity": "11", "size": "M"},
            follow_redirects=True,
        )

        self.assertEqual(rejected.status_code, 200)
        self.assertIn(b"Hindi sapat ang stock", rejected.data)
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:M": 3})

    def test_cart_update_with_zero_quantity_removes_item_with_feedback(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        with self.client.session_transaction() as store:
            store["cart"] = {"1:M": 2}

        response = self.client.post(
            "/cart/update/1",
            data={"quantity": "0", "size": "M"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Inalis ang produkto sa bag.", response.data)
        self.assertIn(b"Your bag is taking a breather.", response.data)

    def test_shop_sorting_uses_a_safe_ordering_choice(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop?sort=price-low")
        self.assertEqual(response.status_code, 200)
        query, _params = next(
            (query, params)
            for query, params in self.database.queries
            if "SELECT id, name, slug" in query
        )
        self.assertIn("ORDER BY price ASC, created_at DESC", query)

        self.client.get("/shop?sort=price-low%20DESC")
        query, _params = next(
            (query, params)
            for query, params in reversed(self.database.queries)
            if "SELECT id, name, slug" in query
        )
        self.assertIn("ORDER BY created_at DESC", query)
        self.assertNotIn("price-low", query)

    def test_login_rejects_external_redirect_target(self):
        response = self.client.post(
            "/login?next=https://example.invalid",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/home")

    def test_login_from_entry_redirects_to_store_home(self):
        response = self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/home")
        homepage = self.client.get(response.headers["Location"])
        self.assertEqual(homepage.status_code, 200)
        self.assertNotIn(b"home-editorial", homepage.data)
        self.assertNotIn(b"Shop by Category", homepage.data)
        self.assertNotIn(b"GP Essential Tee", homepage.data)

    def test_csrf_rejects_unprotected_cart_post(self):
        csrf_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": self.database,
                "WTF_CSRF_ENABLED": True,
            }
        )
        response = csrf_app.test_client().post("/cart/add/1")
        self.assertEqual(response.status_code, 400)

    def test_admin_can_view_orders_and_update_order_status(self):
        admin_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": self.database,
                "ADMIN_EMAIL": "customer@example.com",
            }
        )
        admin_client = admin_app.test_client()
        login = admin_client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(login.status_code, 302)

        orders = admin_client.get("/admin/orders")
        self.assertEqual(orders.status_code, 200)
        self.assertIn(b"Order management", orders.data)
        self.assertIn(b"Awaiting acceptance", orders.data)
        self.assertIn(b"Accept order", orders.data)
        self.assertIn(b"#1", orders.data)
        self.assertIn(b"Gameplan Customer", orders.data)

        detail = admin_client.get("/admin/orders/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Shipping address", detail.data)
        self.assertIn(b"Update order status", detail.data)
        self.assertIn(b"GP Essential Tee", detail.data)

        accepted = admin_client.post(
            "/admin/orders/1/status",
            data={"status": "processing", "return_to": "orders"},
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted.headers["Location"], "/admin/orders")
        self.assertIn(
            ("UPDATE orders SET status = %s WHERE id = %s", ("processing", 1)),
            self.database.executions,
        )

        updated = admin_client.post(
            "/admin/orders/1/status",
            data={"status": "processing"},
        )
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(updated.headers["Location"], "/admin/orders/1")

    def test_admin_can_delete_order_and_its_items_from_manage_orders(self):
        admin_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": self.database,
                "ADMIN_EMAIL": "customer@example.com",
            }
        )
        admin_client = admin_app.test_client()
        admin_client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        orders = admin_client.get("/admin/orders")
        self.assertIn(b"Delete order", orders.data)
        detail = admin_client.get("/admin/orders/1")
        self.assertIn(b"order-delete-form", detail.data)

        deleted = admin_client.post("/admin/orders/1/delete")

        self.assertEqual(deleted.status_code, 302)
        self.assertEqual(deleted.headers["Location"], "/admin/orders")
        self.assertEqual(self.database.deleted_order_items, [1])
        self.assertEqual(self.database.deleted_orders, [1])
        refreshed_orders = admin_client.get("/admin/orders")
        self.assertIn(b"No orders have been placed yet.", refreshed_orders.data)
        self.assertEqual(admin_client.get("/admin/orders/1").status_code, 404)

    def test_admin_can_clear_all_orders_from_manage_orders(self):
        admin_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": self.database,
                "ADMIN_EMAIL": "customer@example.com",
            }
        )
        admin_client = admin_app.test_client()
        admin_client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        orders = admin_client.get("/admin/orders")
        self.assertIn(b"Delete all orders", orders.data)

        deleted = admin_client.post("/admin/orders/delete-all")

        self.assertEqual(deleted.status_code, 302)
        self.assertEqual(deleted.headers["Location"], "/admin/orders")
        self.assertEqual(self.database.deleted_order_items, ["all"])
        self.assertEqual(self.database.deleted_orders, ["all"])
        refreshed_orders = admin_client.get("/admin/orders")
        self.assertIn(b"No orders have been placed yet.", refreshed_orders.data)

    def test_customer_cannot_delete_an_order(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.post("/admin/orders/1/delete")
        bulk_response = self.client.post("/admin/orders/delete-all")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(bulk_response.status_code, 403)
        self.assertEqual(self.database.deleted_order_items, [])
        self.assertEqual(self.database.deleted_orders, [])

    def test_admin_order_detail_shows_the_selected_color(self):
        admin_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": self.database,
                "ADMIN_EMAIL": "customer@example.com",
            }
        )
        admin_client = admin_app.test_client()
        admin_client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        detail = admin_client.get("/admin/orders/1")

        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"<th>Color</th>", detail.data)
        self.assertIn(b"<td>White</td>", detail.data)

    def test_admin_product_form_uploads_and_previews_an_image(self):
        self.app.config.update(
            {
                "ADMIN_EMAIL": "customer@example.com",
                "PRODUCT_UPLOAD_FOLDER": self.get_temp_upload_folder(),
            }
        )
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        form = self.client.get("/admin/products/new")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b'class="product-image-upload-box"', form.data)
        self.assertIn(b'type="file"', form.data)
        self.assertIn(b'name="gallery_images"', form.data)
        self.assertIn(b'multiple', form.data)
        self.assertIn(b"Magdagdag ng JPG", form.data)
        self.assertNotIn(b"Image URL", form.data)

        response = self.client.post(
            "/admin/products/new",
            data={
                "name": "Uploaded Tee",
                "slug": "uploaded-tee",
                "description": "A test product",
                "category": "T-Shirts",
                "price": "600",
                "stock": "5",
                "image": (BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4), "tee.png"),
                "gallery_images": (
                    BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4),
                    "tee-back.png",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/shop")
        _query, params = next(
            (query, params)
            for query, params in self.database.executions
            if "INSERT INTO products" in query
        )
        self.assertTrue(params[5].startswith("/static/images/products/"))
        self.assertTrue(
            (Path(self.app.config["PRODUCT_UPLOAD_FOLDER"]) / Path(params[5]).name).is_file()
        )
        gallery_insert = next(
            params
            for query, params in self.database.executions
            if "INSERT INTO product_images" in query
        )
        self.assertEqual(gallery_insert[0], 1)
        self.assertTrue(gallery_insert[1].startswith("/static/images/products/"))
        self.assertTrue(
            (
                Path(self.app.config["PRODUCT_UPLOAD_FOLDER"])
                / Path(gallery_insert[1]).name
            ).is_file()
        )
        shop = self.client.get(response.headers["Location"])
        self.assertEqual(shop.status_code, 200)
        self.assertIn(b"Uploaded Tee", shop.data)
        self.assertIn(params[5].encode(), shop.data)

        execution_count = len(self.database.executions)
        invalid = self.client.post(
            "/admin/products/new",
            data={
                "name": "Bad Image Tee",
                "slug": "bad-image-tee",
                "description": "A test product",
                "category": "T-Shirts",
                "price": "600",
                "stock": "5",
                "image": (BytesIO(b"not an image"), "tee.png"),
            },
        )
        self.assertIn(b"valid na JPG, PNG, o WebP", invalid.data)
        self.assertEqual(len(self.database.executions), execution_count)

        too_many = self.client.post(
            "/admin/products/new",
            data={
                "name": "Too Many Images Tee",
                "slug": "too-many-images-tee",
                "description": "A test product",
                "category": "T-Shirts",
                "price": "600",
                "stock": "5",
                "image": (BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4), "tee.png"),
                "gallery_images": [
                    (BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4), f"angle-{index}.png")
                    for index in range(4)
                ],
            },
        )
        self.assertIn(b"Hanggang 3 additional product photos", too_many.data)
        self.assertEqual(len(self.database.executions), execution_count)

    def test_admin_can_save_black_and_white_for_a_product(self):
        self.app.config.update(
            {
                "ADMIN_EMAIL": "customer@example.com",
                "PRODUCT_UPLOAD_FOLDER": self.get_temp_upload_folder(),
            }
        )
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.post(
            "/admin/products/new",
            data={
                "name": "Two Tone Tee",
                "slug": "two-tone-tee",
                "description": "Black or white.",
                "category": "T-Shirts",
                "price": "600",
                "stock": "5",
                "colors": ["Black", "White"],
                "image": (BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4), "tee.png"),
            },
        )

        self.assertEqual(response.status_code, 302)
        color_inserts = [
            params
            for query, params in self.database.executions
            if "INSERT INTO product_colors" in query
        ]
        self.assertEqual(color_inserts, [(1, "Black"), (1, "White")])
        edit_form = self.client.get("/admin/products/1/edit")
        self.assertIn(b'value="Black" checked', edit_form.data)
        self.assertIn(b'value="White" checked', edit_form.data)

    def test_editing_product_appends_gallery_images_without_replacing_existing(self):
        self.database.product_gallery = [
            {"product_id": 1, "image_url": "/static/images/products/side.png"},
            {"product_id": 1, "image_url": "/static/images/products/back.png"},
        ]
        self.app.config.update(
            {
                "ADMIN_EMAIL": "customer@example.com",
                "PRODUCT_UPLOAD_FOLDER": self.get_temp_upload_folder(),
            }
        )
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        response = self.client.post(
            "/admin/products/1/edit",
            data={
                "name": PRODUCT["name"],
                "slug": PRODUCT["slug"],
                "description": PRODUCT["description"],
                "category": PRODUCT["category"],
                "price": str(PRODUCT["price"]),
                "stock": str(PRODUCT["stock"]),
                "gallery_images": (
                    BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4),
                    "detail.png",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        gallery_insert = next(
            params
            for query, params in self.database.executions
            if "INSERT INTO product_images" in query
        )
        self.assertEqual(gallery_insert[2], 2)
        self.assertFalse(
            any("DELETE FROM product_images" in query for query, _params in self.database.executions)
        )

    def test_editing_product_cannot_exceed_three_gallery_images_total(self):
        self.database.product_gallery = [
            {"product_id": 1, "image_url": f"/static/images/products/{index}.png"}
            for index in range(3)
        ]
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        execution_count = len(self.database.executions)

        response = self.client.post(
            "/admin/products/1/edit",
            data={
                "name": PRODUCT["name"],
                "slug": PRODUCT["slug"],
                "description": PRODUCT["description"],
                "category": PRODUCT["category"],
                "price": str(PRODUCT["price"]),
                "stock": str(PRODUCT["stock"]),
                "gallery_images": (
                    BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4),
                    "fourth.png",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hanggang 3 additional product photos", response.data)
        self.assertEqual(len(self.database.executions), execution_count)

    def test_admin_can_toggle_product_visibility_in_the_shop(self):
        self.app.config["ADMIN_EMAIL"] = "customer@example.com"
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        page = self.client.get("/admin/products")
        self.assertIn(b"Shop visibility", page.data)
        self.assertIn(b"Visible", page.data)
        self.assertIn(b"Hide from shop", page.data)

        response = self.client.post("/admin/products/1/visibility")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/products")
        self.assertIn(
            ("UPDATE products SET active = %s WHERE id = %s", (False, 1)),
            self.database.executions,
        )

    def get_temp_upload_folder(self):
        if not hasattr(self, "_upload_directory"):
            self._upload_directory = TemporaryDirectory()
            self.addCleanup(self._upload_directory.cleanup)
        return self._upload_directory.name


if __name__ == "__main__":
    unittest.main()
