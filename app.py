#!/usr/bin/env python3
"""
MaxHeads Order App - Python Backend
Handles: file uploads, Grok design, PayPal, order emails
"""
import os
import json
import uuid
import base64
import io
from threading import Lock
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
        'xai': bool(os.getenv('XAI_API_KEY')),
        'openai': bool(os.getenv('OPENAI_API_KEY'))
    })


def artwork_url(path):
    if path.startswith(('https://', 'http://')):
        return path
    return os.getenv('APP_BASE_URL', 'https://maxheads-orders-production.up.railway.app').rstrip('/') + '/' + path.lstrip('/')


def save_design(result):
    # Decode and validate actual image bytes before publishing a local PNG.
    from PIL import Image
    if not isinstance(result, str) or not result:
        raise ValueError('No image returned')
    if result.startswith(('https://', 'http://')):
        response = requests.get(result, timeout=30)
        response.raise_for_status()
        raw = response.content
    else:
        raw = base64.b64decode(result, validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        image.verify()
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        fname = f"{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}.png"
        temp = UPLOADS / (fname + '.tmp')
        try:
            image.convert('RGBA').save(temp, format='PNG')
            temp.replace(UPLOADS / fname)
        finally:
            temp.unlink(missing_ok=True)
    return f'/uploads/{fname}'


OPENAI_DESIGN_INSTRUCTIONS = "You are the senior graphic designer for MaxHeads, creating original custom bass drumhead artwork for musicians, churches, organizations and brands.\n\nCREATIVE DIRECTION\nFollow the customer's brief above all stylistic defaults. Develop a coherent visual concept, strong focal point, intentional color palette and professional typography. Avoid generic clip-art compositions and unnecessary visual clutter.\nAdapt to the requested genre: classic and modern rock (bold, energetic, vintage or contemporary); metal (dramatic, intricate or aggressive); country and southern rock (Americana, western or rustic); blues and jazz (expressive, sophisticated); funk and soul (rhythmic, vibrant, retro); psychedelic and progressive (surreal, intricate, imaginative); punk and alternative (raw, independent); Christian rock (powerful, uplifting, contemporary rock); church and worship (welcoming, meaningful, elegant); corporate and custom branding (clean, consistent, professional). These are optional starting points, not mandatory motifs. Do not assume religious symbols, skulls, flames or other imagery unless appropriate to the brief.\n\nPRINT COMPOSITION\nCreate flat, front-facing artwork on a square canvas for a circular bass drumhead. Extend the background to all canvas edges. Keep essential lettering and imagery inside the central circular area with generous clearance from its edge. Prioritize legibility from a stage distance, strong contrast, clean edges and details that survive printing. Avoid tiny lettering and excessively fine detail. Do not draw a drum, rim, hardware, port hole, mockup, stage, watermark or manufacturer logo unless explicitly requested as artwork content. The application handles physical drumhead features separately.\n\nTEXT AND BRAND IDENTITY\nInclude only requested text. Reproduce requested spelling, capitalization and punctuation exactly. Do not invent a band name, slogan or extra words. Make requested band names prominent unless the customer directs otherwise. When given a logo, preserve its recognizable shapes, proportions, typography, spelling and colors unless explicitly asked to change them. Build complementary artwork around it; do not unnecessarily redraw it. Exact logo fidelity must be checked in the production proof.\n\nREVISIONS\nWhen an existing image is supplied, treat it as the current design. Make the requested changes while preserving all other approved elements as closely as possible. Do not restart the concept unless requested. If asked to remove text, remove only the specified text and reconstruct the underlying background consistently.\n\nOUTPUT\nGenerate the artwork itself, not a description of it. Aim for polished, original commercial artwork that a musician would proudly display onstage. Do not claim a specific print resolution, exact logo fidelity or production approval merely because an image was generated. Final print suitability requires human proofing."


def design_openai(data):
    """OpenAI image generation via Responses API."""
    prompt = data.get('prompt', '')
    previous_image = data.get('previousImageUrl')
    logo_path = data.get('logoPath')
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'OpenAI API key not configured'}), 500
    try:
        # Build messages for Responses API
        messages = [{'role': 'system', 'content': OPENAI_DESIGN_INSTRUCTIONS}]
        user_content = [{'type': 'input_text', 'text': prompt}]
        if previous_image:
            img_url = artwork_url(previous_image)
            user_content.append({'type': 'input_image', 'image_url': img_url})
        elif logo_path:
            logo_url = artwork_url(logo_path)
            user_content.append({'type': 'input_image', 'image_url': logo_url})
        messages.append({'role': 'user', 'content': user_content})
        res = requests.post(
            'https://api.openai.com/v1/responses',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'model': 'gpt-4.1', 'input': messages, 'tools': [{'type': 'image_generation'}]},
            timeout=180
        )
        if not res.ok:
            return jsonify({'error': f'OpenAI error: {res.status_code}', 'detail': res.text[:300]}), 500
        result = res.json()
        # Extract image from response
        image_url = None
        for item in result.get('output', []):
            if item.get('type') == 'image_generation_call':
                image_url = item.get('result')  # may be base64 or url
                break
        return jsonify({'reply': 'OpenAI design ready.', 'imageUrl': save_design(image_url), 'engine': 'openai'})
    except Exception:
        app.logger.exception('OpenAI design failed')
        return jsonify({'error': 'OpenAI could not generate or save the artwork. Please try again.'}), 502



# Request limits are process-local, reset on restart, and are not a spending guarantee.
generation_counts = {}
MAX_GENERATIONS_PER_SESSION = 20
GLOBAL_DAILY_LIMIT = int(os.getenv('GLOBAL_DAILY_LIMIT', '100'))
daily_count = {'date': '', 'count': 0}
limit_lock = Lock()


def reserve_generation(session_id):
    with limit_lock:
        today = datetime.now().strftime('%Y-%m-%d')
        if daily_count['date'] != today:
            daily_count.update(date=today, count=0)
        if generation_counts.get(session_id, 0) >= MAX_GENERATIONS_PER_SESSION:
            return 'Generation limit reached. Contact MaxHeads.'
        if daily_count['count'] >= GLOBAL_DAILY_LIMIT:
            return 'Daily generation limit reached.'
        generation_counts[session_id] = generation_counts.get(session_id, 0) + 1
        daily_count['count'] += 1
        return None

@app.route('/api/design', methods=['POST'])
def design():
    data = request.json or {}
    prompt = data.get('prompt', '')
    logo_path = data.get('logoPath')
    previous = data.get('previousDesign', '')
    if not prompt:
        return jsonify({'error': 'Prompt required'}), 400
    engine = data.get('engine', 'grok')
    if engine not in ('grok', 'openai'):
        return jsonify({'error': 'Unknown design engine'}), 400
    session_id = request.headers.get('X-Session-Id', request.remote_addr or 'default')
    error = reserve_generation(session_id)
    if error:
        return jsonify({'error': error}), 429
    if engine == 'openai':
        return design_openai(data)
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
            timeout=15
        )
        chat_res.raise_for_status()
        reply = chat_res.json()['choices'][0]['message']['content']

        # Always edit the latest artwork first; never substitute a fresh image on failure.
        previous_image = data.get('previousImageUrl')
        base_image = previous_image or logo_path
        if base_image:
            edit_prompt = (f'Edit the design: {prompt}. Do not add any text or words unless asked.'
                           if previous_image else f'Modify this logo for a drum head: {prompt}. Keep the logo recognizable. No text.')
            img_res = requests.post(
                'https://api.x.ai/v1/images/edits',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': 'grok-imagine-image', 'prompt': edit_prompt,
                      'image': {'url': artwork_url(base_image), 'type': 'image_url'}}, timeout=60)
        else:
            img_prompt = f'Graphic design for a circular kick drum head. Create visual artwork based on this concept: {prompt}. If a band name or text is mentioned, include it in a bold, stylized font that fits the design. High contrast, centered, print-ready.'
            img_res = requests.post(
                'https://api.x.ai/v1/images/generations',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': 'grok-imagine-image', 'prompt': img_prompt, 'n': 1}, timeout=60)
        img_res.raise_for_status()
        result = img_res.json()['data'][0]
        image_url = save_design(result.get('url') or result.get('b64_json'))
        return jsonify({'reply': reply, 'imageUrl': image_url, 'engine': 'grok'})
    except Exception:
        app.logger.exception('Grok design failed')
        return jsonify({'error': 'Grok could not generate or save the artwork. Please try again.'}), 502


@app.route('/admin')
def admin():
    files = sorted(UPLOADS.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    html = '<html><body style="background:#111;color:#fff;font-family:sans-serif;padding:20px;"><h2>Recent Designs</h2>'
    for f in files:
        html += f'<div style="margin:10px 0;"><img src="/uploads/{f.name}" style="max-width:200px;border-radius:8px;" /><br><a href="/uploads/{f.name}" style="color:#e10600;">{f.name}</a></div>'
    html += '</body></html>'
    return html

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

def generate_order_pdf(order):
    """Generate a simple high-res PDF proof of the order."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        import io
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter
        c.setFont('Helvetica-Bold', 18)
        c.drawString(1*inch, h - 1*inch, f"MaxHeads Order — {order['id']}")
        c.setFont('Helvetica', 11)
        y = h - 1.5*inch
        lines = [
            f"Size: {order.get('size', '—')}",
            f"Port: {order.get('port', '—')}",
            f"Grommet: {order.get('grommet', '—')}",
            f"Design: {order.get('designType', '—')}",
            f"Customer: {order['customer'].get('name', '—')}",
            f"Email: {order['customer'].get('email', '—')}",
            f"Total: ${order.get('total', '—')}",
            "",
            "Artwork proof attached or available via order system.",
            "Final proof will be sent before printing."
        ]
        for line in lines:
            c.drawString(1*inch, y, line)
            y -= 16
        c.save()
        buf.seek(0)
        return buf.read()
    except Exception as e:
        print(f'PDF gen failed: {e}')
        return None

def send_order_email(order):
    """Send order notification with PDF attachment."""
    to_email = os.getenv('ORDER_EMAIL', 'heads@maxheads.com')
    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    smtp_port = int(os.getenv('SMTP_PORT', 587))

    subject = f"New MaxHeads Order — {order['id']}"
    # Build artwork link
    art_path = order.get('artworkPath') or ''
    art_link = ''
    if art_path:
        if art_path.startswith('http'):
            art_link = art_path
        else:
            art_link = request.host_url.rstrip('/') + art_path

    heads = order.get('heads') or [{}]
    heads_text = chr(10).join([f"  Head {i+1}: {h.get('size','—')} / Port {h.get('port','—')}" for i, h in enumerate(heads)]) or '  1 head'
    c = order.get('customer', {})

    body = f"""
MAXHEADS ORDER — {order['id']}
{order.get('createdAt', '')}

CUSTOMER
Name:    {c.get('name', '—')}
Email:   {c.get('email', '—')}
Phone:   {c.get('phone', '—')}
Address: {c.get('address', '—')}
City:    {c.get('city', '—')}, {c.get('state', '—')} {c.get('zip', '—')}
Band:    {c.get('band', '—')}
Notes:   {c.get('notes', '—')}

ORDER
{heads_text}
Shipping: {order.get('shipping', '—')}
Total:    ${order.get('total', '—')}
Payment:  {order.get('paymentMethod', 'PayPal')}
Engine:   {order.get('engine', 'grok')}

ARTWORK
{art_link or 'No file — Grok design or text-only'}
PROOF LOGO
{(os.getenv('APP_BASE_URL', 'https://maxheads-orders-production.up.railway.app') + order.get('proofLogo')) if order.get('proofLogo') else 'None'}

— MaxHeads
"""

    resend_key = os.getenv('RESEND_API_KEY')
    if not resend_key:
        print('=== ORDER EMAIL (no Resend key) ===')
        print(body)
        return False

    try:
        # Shop email
        requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {resend_key}', 'Content-Type': 'application/json'},
            json={
                'from': 'MaxHeads Orders <orders@maxheads.com>',
                'to': [to_email],
                'subject': subject,
                'text': body
            },
            timeout=15
        )
        print(f'Email sent to {to_email}')

        # Customer confirmation
        cust_email = c.get('email')
        if cust_email:
            requests.post(
                'https://api.resend.com/emails',
                headers={'Authorization': f'Bearer {resend_key}', 'Content-Type': 'application/json'},
                json={
                    'from': 'MaxHeads <orders@maxheads.com>',
                    'to': [cust_email],
                    'subject': f"MaxHeads Order Received — {order['id']}",
                    'text': f"""Hey {c.get('name', 'there')},

We got your MaxHeads order.

Order: {order['id']}
{heads_text}
Total: ${order.get('total', '—')}

We'll send a proof before anything gets printed.
Questions? Reply to this email or hit heads@maxheads.com.

Rock on,
MaxHeads"""
                },
                timeout=15
            )
            print(f'Confirmation sent to {cust_email}')

        return True
    except Exception as e:
        print(f'Resend email failed: {e}')
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
        'engine': data.get('engine', 'grok'),
        'proofLogo': data.get('proofLogo'),
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
    try:
        send_order_email(order)
    except Exception as e:
        print(f'Email error (order still created): {e}')
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
