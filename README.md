# MaxHeads Order App

Mobile web app for custom kick drum head orders.
Customers design with Grok or upload artwork, then pay via PayPal.

## Live URL
https://order.maxheads.com (once deployed)

## Quick Start (local)
```bash
cd maxheads-live
pip install -r requirements.txt
python3 app.py
# Open http://localhost:3000
```

## Credentials (.env)
Copy `.env.example` to `.env` and fill in:
- `XAI_API_KEY` — from console.x.ai
- `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET` — from developer.paypal.com
- `ORDER_EMAIL=heads@maxheads.com`

Optional SMTP for real order emails:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASS=your_app_password
```

## Deploy (Railway — easiest)

1. Push this folder to a GitHub repo
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Add environment variables from your `.env`
4. Railway gives you a URL
5. Point `order.maxheads.com` CNAME to that URL

## Deploy (Render)

1. Push to GitHub
2. https://render.com → New Web Service → connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn app:app --bind 0.0.0.0:$PORT`
5. Add env vars
6. Point domain

## What's included
- Full UI (size, options, Grok design, upload, pickup toggle)
- Real Grok image generation
- File upload
- PayPal Sandbox payments
- Order creation + email notification
- Health endpoint

## Pricing
- Head: $115
- Ground shipping: $25
- Store pickup: Free
- Grommet: +$20

## Next
- Test a full sandbox order
- Add SMTP credentials so emails hit heads@maxheads.com
- Deploy and connect order.maxheads.com
- Switch PayPal to live when ready
