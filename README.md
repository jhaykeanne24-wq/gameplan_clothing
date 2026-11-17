# Gameplan Apparel

A responsive Flask/MySQL apparel storefront based on the provided Gameplan Apparel references. It includes a public catalog, email-verified account registration and sign-in, session cart, checkout/order recording, customer dashboard and profile, admin product CRUD, CSRF protection, password hashing, and SMTP notifications.

## Requirements

- Python 3.10+
- MySQL 8+ (XAMPP MySQL is suitable)
- Gmail SMTP credentials for account email verification and other transactional email

## Local setup

1. Start MySQL from the XAMPP Control Panel.
2. Create and activate a virtual environment, then install dependencies:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Configure the database. On first request, the app creates the configured database and missing tables automatically. The configured MySQL user must have permission to create the database and tables. To import the schema manually with PowerShell:

   ```powershell
   Get-Content .\schema.sql | & 'C:\xampp\mysql\bin\mysql.exe' -u root
   ```

   Add `-p` after `-u root` if your MySQL root account has a password.

4. Copy `.env.example` to `.env` and set a unique `SECRET_KEY`, the correct MySQL connection values, and your admin email if you want only one account to have admin access. Gmail SMTP is required for registration verification. Enable 2-Step Verification on the Gmail account, create a Google App Password, then put that App Password (not your regular Gmail password) in `SMTP_PASSWORD`:

   ```powershell
   Copy-Item .env.example .env
   ```

   ```env
   SECRET_KEY=your-secret-key
   MYSQL_HOST=127.0.0.1
   MYSQL_USER=root
   MYSQL_PASSWORD=
   MYSQL_DATABASE=gameplan_clothing
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your-gmail-address@gmail.com
   SMTP_PASSWORD=your-16-character-google-app-password
   MAIL_FROM=your-gmail-address@gmail.com
   ADMIN_EMAIL=yourname@example.com
   ```

5. Replace the active catalog with the three sample T-shirts and start Flask:

   ```powershell
   python -m flask --app app seed
   python -m flask --app app run --debug
   ```

   Open `http://127.0.0.1:5000`.
   The seed command deactivates currently active products without deleting their records or order history.

## Store administration

Register a customer account first, then grant store-admin access using MySQL:

```sql
UPDATE users SET role = 'admin' WHERE email = 'your-account@example.com';
```

Sign in again and open **My account → Manage products**. Product removal is a soft delete so existing order history stays intact.

## Included behavior

- The homepage hero slideshow starts with `static/img/login-background.png` and automatically loads additional `.jpg`, `.jpeg`, `.png`, or `.webp` images placed in `static/img/hero-slides`.
- Search, category and price filters, product detail, and responsive storefront layouts.
- Admins can upload a main PNG, JPG, or WebP product image plus up to 3 optional gallery images per product (8 MB total per form submission); new gallery images append to existing ones, are previewed before saving, and are stored under `static/images/products`.
- Admins can enable Black, White, and Grey as per-product color choices; configured colors are required on the product page and are preserved in the cart, checkout, and order history while sharing the product's stock count.
- Three reference-based T-shirts at ₱600 each, with S/M/L size selection, size-preserving cart and order items, and quantity controls. Existing databases receive the nullable order-item size column automatically.
- Product ratings use one editable 1–5 star review per customer and product; only customers with a completed purchase can rate. Customers see the average and reviews, and admins can view totals and individual feedback.
- Login/signup landing page for visitors; catalog, product details, and shopping bag require a signed-in customer.
- Email-verified registration with a one-time, hashed 6-digit OTP that expires after 10 minutes, plus resend cooldown and attempt limits.
- Registration, login, logout, hashed passwords, per-user profile updates, and expiring HTTP-only sessions.
- Server-side validation and CSRF protection on state-changing forms; parameterized SQL for database operations.
- Session-based bag, stock checks, shipping details, and transactional order/item creation.
- Gmail SMTP account verification, welcome, and order confirmation emails. Mail delivery failures are reported in the interface.
- Public contact page with a full-page background, contact details, and a validated form that emails `ADMIN_EMAIL` through the configured SMTP service.
- Cash on Delivery is the only checkout payment method; no online payment processing is integrated.

## Verification

Run the focused automated tests with:

```powershell
python -m unittest discover -s tests -v
```

The sample catalog uses remote Unsplash image URLs; replace them with client-approved assets before production. The supplied assignment also requires a real-client interview and review/sign-off; those are human project activities and are not represented as completed by this code.
