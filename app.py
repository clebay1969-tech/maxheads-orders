
#!/usr/bin/env python3
"""
MaxHeads Order App - Python Backend
Handles: file uploads, Grok design, PayPal, order emails
"""
import os
import json
import uuid
import base64
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)

UPLOADS = Path(__file__).parent / 'uploads'
UPLOADS.mkdir(exist_ok=True)

# In-memory orders
orders = []

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'paypal': bool(os.getenv('PAYPAL_CLIENT_ID')),
        'xai': bool(os.getenv('XAI_API_KEY'))
    })

@app.route('/api/design', methods=['POST'])
def design():
    data = request.json or {}
    prompt = data.get('prompt', '')
    if not prompt:
        return jsonify({'error': 'Prompt required'}), 400

    api_key = os.getenv('XAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'XAI API key not configured'}), 500

    try:
        # Chat completion for design direction
        chat_res = requests.post(
            'https://api.x.ai/v1/chat/completions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'grok-4.3',
                'messages': [
                    {'role': 'system', 'content': 'You are a design assistant for MaxHeads custom kick drum heads. Suggest bold, high-contrast rock/metal artwork suitable for circular drumhead printing. Keep responses short.'},
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': 200
            },
            timeout=30
        )
        chat_res.raise_for_status()
        reply = chat_res.json()['choices'][0]['message']['content']

        # Image generation
        image_url = None
        try:
            img_res = requests.post(
                'https://api.x.ai/v1/images/generations',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'grok-imagine-image',
                    'prompt': f'Circular drumhead graphic design, high contrast, bold, print-ready, centered: {prompt}',
                    'n': 1
                },
                timeout=60
            )
            if img_res.ok:
                img_data = img_res.json()
                image_url = img_data.get('data', [{}])[0].get('url')
        except Exception as e:
            print(f'Image gen failed: {e}')

        return jsonify({'reply': reply, 'imageUrl': image_url})
    except Exception as e:
        print(f'Design error: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload():
    if 'artwork' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['artwork']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = Path(f.filename).suffix.lower()
    allowed = {'.jpg', '.jpeg', '.png', '.pdf', '.eps', '.ai', '.psd', '.webp'}
    if ext not in allowed:
        return jsonify({'error': 'Invalid file type'}), 400

    filename = f'{datetime.now().strftime("%Y%m%d%H%M%S")}-{uuid.uuid4().hex[:8]}{ext}'
    f.save(UPLOADS / filename)
    return jsonify({'filename': filename, 'originalName': f.filename, 'path': f'/uploads/{filename}'})

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOADS, filename)

def send_order_email(order):
    """Send order notification to heads@maxheads.com"""
    to_email = os.getenv('ORDER_EMAIL', 'heads@maxheads.com')
    smtp_host = os.getenv('SMTP_HOST')
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    smtp_port = int(os.getenv('SMTP_PORT', 587))

    subject = f"New MaxHeads Order — {order['id']}"
    body = f"""
New custom kick head order received!

Order ID: {order['id']}
Date: {order['createdAt']}

CUSTOMER
Name: {order['customer'].get('name', '—')}
Email: {order['customer'].get('email', '—')}
Phone: {order['customer'].get('phone', '—')}
Address: {order['customer'].get('address', '—')}
Band: {order['customer'].get('band', '—')}

ORDER
Size: {order.get('size', '—')}
Port: {order.get('port', '—')}
Grommet: {order.get('grommet', '—')}
Design: {order.get('designType', '—')}
Shipping: {order['shipping']}
Total: ${order.get('total', '—')}

Artwork: {order.get('artworkPath') or 'Grok-generated design'}

— MaxHeads Order System
"""

    if smtp_host and smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart()
            msg['From'] = smtp_user
            msg['To'] = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_email, msg.as_string())
            print(f'Email sent to {to_email}')
            return True
        except Exception as e:
            print(f'SMTP email failed: {e}')
            return False
    else:
        # Log only until SMTP is configured
        print(f'=== ORDER EMAIL (no SMTP configured) ===')
        print(f'To: {to_email}')
        print(body)
        print('========================================')
        return False

@app.route('/api/orders', methods=['POST'])
def create_order():
    data = request.json or {}
    order = {
        'id': f'MH-{datetime.now().strftime("%Y%m%d")}-{uuid.uuid4().hex[:6].upper()}',
        'createdAt': datetime.now().isoformat(),
        'size': data.get('size'),
        'port': data.get('port'),
        'grommet': data.get('grommet'),
        'designType': data.get('designType'),
        'artworkPath': data.get('artworkPath'),
        'customer': data.get('customer', {}),
        'shipping': 'Store Pickup' if data.get('pickup') else 'Ground Shipping',
        'pickup': bool(data.get('pickup')),
        'total': data.get('total'),
        'status': 'pending_payment'
    }
    orders.append(order)
    print(f'=== NEW ORDER ===')
    print(f'ID: {order["id"]}')
    print(f'Customer: {order["customer"].get("name")} <{order["customer"].get("email")}>')
    print(f'Size: {order["size"]} | Total: ${order["total"]}')
    print(f'Shipping: {order["shipping"]}')
    print(f'=================')
    send_order_email(order)
    return jsonify({'orderId': order['id'], 'order': order})

@app.route('/api/paypal/create-order', methods=['POST'])
def paypal_create():
    data = request.json or {}
    amount = data.get('amount', '115.00')
    order_id = data.get('orderId', 'unknown')

    client_id = os.getenv('PAYPAL_CLIENT_ID')
    secret = os.getenv('PAYPAL_SECRET')
    mode = os.getenv('PAYPAL_MODE', 'sandbox')
    base = 'https://api-m.paypal.com' if mode == 'live' else 'https://api-m.sandbox.paypal.com'

    try:
        # Get access token
        auth = requests.auth.HTTPBasicAuth(client_id, secret)
        token_res = requests.post(f'{base}/v1/oauth2/token', auth=auth, data={'grant_type': 'client_credentials'})
        token_res.raise_for_status()
        token = token_res.json()['access_token']

        # Create order
        order_res = requests.post(
            f'{base}/v2/checkout/orders',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={
                'intent': 'CAPTURE',
                'purchase_units': [{
                    'amount': {'currency_code': 'USD', 'value': str(amount)},
                    'description': f'MaxHeads Custom Kick Head - {order_id}',
                    'custom_id': order_id
                }]
            }
        )
        order_res.raise_for_status()
        return jsonify({'paypalOrderId': order_res.json()['id']})
    except Exception as e:
        print(f'PayPal error: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/paypal/capture', methods=['POST'])
def paypal_capture():
    data = request.json or {}
    paypal_order_id = data.get('paypalOrderId')
    if not paypal_order_id:
        return jsonify({'error': 'Missing paypalOrderId'}), 400

    client_id = os.getenv('PAYPAL_CLIENT_ID')
    secret = os.getenv('PAYPAL_SECRET')
    mode = os.getenv('PAYPAL_MODE', 'sandbox')
    base = 'https://api-m.paypal.com' if mode == 'live' else 'https://api-m.sandbox.paypal.com'

    try:
        auth = requests.auth.HTTPBasicAuth(client_id, secret)
        token_res = requests.post(f'{base}/v1/oauth2/token', auth=auth, data={'grant_type': 'client_credentials'})
        token_res.raise_for_status()
        token = token_res.json()['access_token']

        capture_res = requests.post(
            f'{base}/v2/checkout/orders/{paypal_order_id}/capture',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        )
        capture_res.raise_for_status()
        return jsonify({'status': capture_res.json().get('status'), 'details': capture_res.json()})
    except Exception as e:
        print(f'PayPal capture error: {e}')
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print('MaxHeads Order App starting...')
    print(f'PayPal mode: {os.getenv("PAYPAL_MODE", "sandbox")}')
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 3000)), debug=True)
