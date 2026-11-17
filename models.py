from dataclasses import dataclass

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash


@dataclass
class Product:
    id: int
    name: str
    slug: str
    description: str
    category: str
    price: float
    image_url: str
    stock: int
    featured: bool = False
    image_urls: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    average_rating: float = 0.0
    rating_count: int = 0


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.name = row["name"]
        self.email = row["email"]
        self.password_hash = row["password_hash"]
        self.role = row["role"]

    @classmethod
    def get(cls, db, user_id):
        row = db.query(
            "SELECT id, name, email, password_hash, role FROM users WHERE id = %s",
            (user_id,),
            one=True,
        )
        return cls(row) if row else None

    @classmethod
    def by_email(cls, db, email):
        row = db.query(
            "SELECT id, name, email, password_hash, role FROM users WHERE email = %s",
            (email,),
            one=True,
        )
        return cls(row) if row else None

    @staticmethod
    def hash_password(password):
        return generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        admin_email = current_app.config.get("ADMIN_EMAIL", "").strip().lower()
        return self.role == "admin" or (admin_email and self.email.lower() == admin_email)


class Catalog:
    def __init__(self, db):
        self.db = db

    def _image_urls_by_product(self, product_ids):
        if not product_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(product_ids))
        rows = self.db.query(
            "SELECT product_id, image_url FROM product_images "
            f"WHERE product_id IN ({placeholders}) ORDER BY product_id, position, id",
            tuple(product_ids),
        )
        image_urls = {}
        for row in rows:
            image_urls.setdefault(row["product_id"], []).append(row["image_url"])
        return image_urls

    def _colors_by_product(self, product_ids):
        if not product_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(product_ids))
        rows = self.db.query(
            "SELECT product_id, color FROM product_colors "
            f"WHERE product_id IN ({placeholders}) ORDER BY FIELD(color, 'Black', 'White', 'Grey')",
            tuple(product_ids),
        )
        colors = {}
        for row in rows:
            colors.setdefault(row["product_id"], []).append(row["color"])
        return colors

    def _ratings_by_product(self, product_ids):
        if not product_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(product_ids))
        rows = self.db.query(
            "SELECT product_id, AVG(rating) AS average_rating, "
            "COUNT(*) AS rating_count FROM product_reviews "
            f"WHERE product_id IN ({placeholders}) GROUP BY product_id",
            tuple(product_ids),
        )
        return {row["product_id"]: row for row in rows}

    def list(self, category=None, search=None, min_price=None, max_price=None, sort=None, color=None, size=None):
        conditions = ["active = TRUE"]
        params = []
        if category and category != "All Products":
            conditions.append("category = %s")
            params.append(category)
        if size:
            conditions.append("category = %s")
            params.append("T-Shirts")
        if search:
            conditions.append("(name LIKE %s OR description LIKE %s)")
            params.extend((f"%{search}%", f"%{search}%"))
        if min_price is not None:
            conditions.append("price >= %s")
            params.append(min_price)
        if max_price is not None:
            conditions.append("price <= %s")
            params.append(max_price)
        color_patterns = {
            "black": ("black",),
            "blue": ("blue",),
        }
        if color in color_patterns:
            conditions.append("(name LIKE %s OR description LIKE %s)")
            params.extend(
                f"%{term}%"
                for term in (color_patterns[color][0], color_patterns[color][0])
            )
        ordering = {
            "newest": "created_at DESC",
            "price-low": "price ASC, created_at DESC",
            "price-high": "price DESC, created_at DESC",
        }.get(sort, "featured DESC, created_at DESC")
        rows = self.db.query(
            "SELECT id, name, slug, description, category, price, image_url, stock, featured "
            "FROM products WHERE "
            + " AND ".join(conditions)
            + " ORDER BY "
            + ordering,
            tuple(params),
        )
        gallery_images = self._image_urls_by_product([row["id"] for row in rows])
        product_colors = self._colors_by_product([row["id"] for row in rows])
        ratings = self._ratings_by_product([row["id"] for row in rows])
        return [
            Product(
                **row,
                image_urls=tuple(gallery_images.get(row["id"], ())),
                colors=tuple(product_colors.get(row["id"], ())),
                average_rating=float(ratings.get(row["id"], {}).get("average_rating") or 0),
                rating_count=int(ratings.get(row["id"], {}).get("rating_count") or 0),
            )
            for row in rows
        ]

    def get(self, product_id):
        row = self.db.query(
            "SELECT id, name, slug, description, category, price, image_url, stock, featured "
            "FROM products WHERE id = %s AND active = TRUE",
            (product_id,),
            one=True,
        )
        if not row:
            return None
        gallery_images = self._image_urls_by_product([product_id])
        product_colors = self._colors_by_product([product_id])
        ratings = self._ratings_by_product([product_id])
        rating = ratings.get(product_id, {})
        return Product(
            **row,
            image_urls=tuple(gallery_images.get(product_id, ())),
            colors=tuple(product_colors.get(product_id, ())),
            average_rating=float(rating.get("average_rating") or 0),
            rating_count=int(rating.get("rating_count") or 0),
        )

    def categories(self):
        rows = self.db.query(
            "SELECT DISTINCT category FROM products WHERE active = TRUE ORDER BY category"
        )
        return [row["category"] for row in rows]
