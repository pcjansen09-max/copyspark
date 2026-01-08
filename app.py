"""
Main application for the CopySpark micro‑SaaS example.

This Flask application provides a simple copywriting generator with a free
usage tier and a paid "Pro" tier unlocked via Stripe subscription. Users
can register, log in, generate copy, and upgrade to a paid plan. Payment
integration uses Stripe Checkout and a webhook endpoint to listen for
subscription events. After payment confirmation the user is upgraded to
Pro with unlimited daily usage.

Environment variables are loaded from a `.env` file if present. See
`.env.example` for required variables.
"""

import os
from datetime import date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

import stripe

# Load environment variables from .env if present
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# Basic configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Stripe configuration
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')

# Free tier usage limit per day
FREE_DAILY_LIMIT = int(os.environ.get('FREE_DAILY_LIMIT', 3))


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    is_pro = db.Column(db.Boolean, default=False)
    daily_count = db.Column(db.Integer, default=0)
    last_usage_date = db.Column(db.Date, default=date.today)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


def ensure_daily_usage(user: User) -> None:
    """Reset daily usage counter if a new day has started."""
    if user.last_usage_date != date.today():
        user.daily_count = 0
        user.last_usage_date = date.today()
        db.session.commit()


def pro_required(f):
    """Decorator to ensure user is Pro or within free usage limit."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        ensure_daily_usage(current_user)
        if current_user.is_pro:
            return f(*args, **kwargs)
        if current_user.daily_count < FREE_DAILY_LIMIT:
            current_user.daily_count += 1
            db.session.commit()
            return f(*args, **kwargs)
        flash('Free plan limit reached. Please upgrade to Pro!', 'warning')
        return redirect(url_for('pricing'))

    return decorated_function


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        if User.query.filter_by(email=email).first():
            flash('Email already registered. Please log in.', 'danger')
            return redirect(url_for('login'))
        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'danger')
        return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')


@app.route('/generate', methods=['POST'])
@login_required
@pro_required
def generate():
    prompt = request.form['prompt']
    generated = f"Generated copy for: {prompt}"
    return render_template('dashboard.html', generated=generated)


@app.route('/pricing')
def pricing():
    return render_template('pricing.html', stripe_price_id=STRIPE_PRICE_ID)


@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    if not STRIPE_PRICE_ID:
        flash('Stripe not configured. Unable to create checkout session.', 'danger')
        return redirect(url_for('pricing'))
    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=current_user.email,
            line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            success_url=url_for('dashboard', _external=True) + '?success=1',
            cancel_url=url_for('pricing', _external=True),
        )
        return redirect(checkout_session.url)
    except Exception as e:
        flash(f'Error creating checkout session: {e}', 'danger')
        return redirect(url_for('pricing'))


@app.route('/webhook', methods=['POST'])
def webhook_received():
    # Verify webhook signature
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    if not STRIPE_WEBHOOK_SECRET:
        return '', 400
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    # Upgrade user to Pro when checkout session is completed
    if event['type'] == 'checkout.session.completed':
        session_obj = event['data']['object']
        customer_email = session_obj.get('customer_details', {}).get('email')
        user = User.query.filter_by(email=customer_email).first()
        if user:
            user.is_pro = True
            db.session.commit()
    return '', 200


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

