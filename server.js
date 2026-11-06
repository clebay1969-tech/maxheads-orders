require('dotenv').config();
const express = require('express');
const cors = require('cors');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const axios = require('axios');
const { v4: uuidv4 } = require('uuid');

const app = express();
const PORT = process.env.PORT || 3000;

// Ensure uploads directory exists
const uploadsDir = path.join(__dirname, 'uploads');
if (!fs.existsSync(uploadsDir)) fs.mkdirSync(uploadsDir, { recursive: true });

// Middleware
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));
app.use(express.static(path.join(__dirname, 'public')));

// Multer for file uploads
const storage = multer.diskStorage({
  destination: uploadsDir,
  filename: (req, file, cb) => {
    const unique = `${Date.now()}-${Math.round(Math.random() * 1e9)}${path.extname(file.originalname)}`;
    cb(null, unique);
  }
});
const upload = multer({
  storage,
  limits: { fileSize: 50 * 1024 * 1024 }, // 50MB
  fileFilter: (req, file, cb) => {
    const allowed = /jpeg|jpg|png|gif|pdf|eps|ai|psd|webp/;
    const ext = allowed.test(path.extname(file.originalname).toLowerCase());
    const mime = allowed.test(file.mimetype) || file.mimetype === 'application/postscript' || file.mimetype === 'application/illustrator';
    if (ext || mime) return cb(null, true);
    cb(new Error('Invalid file type. Use PDF, EPS, AI, PSD, JPG, or PNG.'));
  }
});

// In-memory order store (replace with DB in production)
const orders = [];

// ========== GROK DESIGN ==========
app.post('/api/design', async (req, res) => {
  const { prompt, previousMessages = [] } = req.body;
  if (!prompt) return res.status(400).json({ error: 'Prompt required' });

  try {
    const messages = [
      {
        role: 'system',
        content: `You are a design assistant for MaxHeads custom kick drum heads. 
Generate bold, high-contrast, rock/metal style artwork suitable for printing on a circular drumhead.
Focus on logos, band names, skulls, fire, typography, and graphic designs that will look sharp when printed.
Keep designs centered and avoid fine details that won't print well.
Respond with a short description of the design direction, then the user will see a generated image.`
      },
      ...previousMessages,
      { role: 'user', content: prompt }
    ];

    const response = await axios.post(
      'https://api.x.ai/v1/chat/completions',
      {
        model: 'grok-2-latest',
        messages,
        max_tokens: 300
      },
      {
        headers: {
          'Authorization': `Bearer ${process.env.XAI_API_KEY}`,
          'Content-Type': 'application/json'
        }
      }
    );

    const reply = response.data.choices[0].message.content;

    // Attempt image generation via xAI Imagine (if available) or return design notes
    let imageUrl = null;
    try {
      const imgRes = await axios.post(
        'https://api.x.ai/v1/images/generations',
        {
          model: 'grok-imagine-image',
          prompt: `Circular drumhead graphic design, high contrast, bold, print-ready, centered composition: ${prompt}`,
          n: 1,
          size: '1024x1024'
        },
        {
          headers: {
            'Authorization': `Bearer ${process.env.XAI_API_KEY}`,
            'Content-Type': 'application/json'
          },
          timeout: 60000
        }
      );
      imageUrl = imgRes.data?.data?.[0]?.url || imgRes.data?.data?.[0]?.b64_json;
    } catch (imgErr) {
      console.log('Image gen not available or failed:', imgErr.message);
    }

    res.json({ reply, imageUrl });
  } catch (err) {
    console.error('Design error:', err.response?.data || err.message);
    res.status(500).json({ error: 'Design generation failed', details: err.message });
  }
});

// ========== FILE UPLOAD ==========
app.post('/api/upload', upload.single('artwork'), (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded' });
  res.json({
    filename: req.file.filename,
    originalName: req.file.originalname,
    path: `/uploads/${req.file.filename}`
  });
});

// Serve uploaded files
app.use('/uploads', express.static(uploadsDir));

// ========== CREATE ORDER ==========
app.post('/api/orders', async (req, res) => {
  const {
    size, port, grommet, designType, artworkPath,
    customer, shipping, pickup, total
  } = req.body;

  const order = {
    id: `MH-${Date.now().toString(36).toUpperCase()}`,
    createdAt: new Date().toISOString(),
    size,
    port,
    grommet,
    designType,
    artworkPath,
    customer,
    shipping: pickup ? 'Store Pickup' : 'Ground Shipping',
    pickup: !!pickup,
    total,
    status: 'pending_payment'
  };

  orders.push(order);
  console.log('Order created:', order.id);

  // Send notification email (placeholder - wire nodemailer with real SMTP or Resend)
  try {
    await sendOrderEmail(order);
  } catch (e) {
    console.log('Email send failed (non-blocking):', e.message);
  }

  res.json({ orderId: order.id, order });
});

// ========== PAYPAL ==========
app.post('/api/paypal/create-order', async (req, res) => {
  const { amount, orderId } = req.body;
  try {
    const auth = Buffer.from(`${process.env.PAYPAL_CLIENT_ID}:${process.env.PAYPAL_SECRET}`).toString('base64');
    const base = process.env.PAYPAL_MODE === 'live'
      ? 'https://api-m.paypal.com'
      : 'https://api-m.sandbox.paypal.com';

    const tokenRes = await axios.post(`${base}/v1/oauth2/token`, 'grant_type=client_credentials', {
      headers: { Authorization: `Basic ${auth}`, 'Content-Type': 'application/x-www-form-urlencoded' }
    });
    const accessToken = tokenRes.data.access_token;

    const orderRes = await axios.post(`${base}/v2/checkout/orders`, {
      intent: 'CAPTURE',
      purchase_units: [{
        amount: { currency_code: 'USD', value: amount.toString() },
        description: `MaxHeads Custom Kick Head - ${orderId}`,
        custom_id: orderId
      }]
    }, {
      headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' }
    });

    res.json({ paypalOrderId: orderRes.data.id });
  } catch (err) {
    console.error('PayPal create error:', err.response?.data || err.message);
    res.status(500).json({ error: 'PayPal order creation failed' });
  }
});

app.post('/api/paypal/capture', async (req, res) => {
  const { paypalOrderId } = req.body;
  try {
    const auth = Buffer.from(`${process.env.PAYPAL_CLIENT_ID}:${process.env.PAYPAL_SECRET}`).toString('base64');
    const base = process.env.PAYPAL_MODE === 'live'
      ? 'https://api-m.paypal.com'
      : 'https://api-m.sandbox.paypal.com';

    const tokenRes = await axios.post(`${base}/v1/oauth2/token`, 'grant_type=client_credentials', {
      headers: { Authorization: `Basic ${auth}`, 'Content-Type': 'application/x-www-form-urlencoded' }
    });
    const accessToken = tokenRes.data.access_token;

    const captureRes = await axios.post(`${base}/v2/checkout/orders/${paypalOrderId}/capture`, {}, {
      headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' }
    });

    res.json({ status: captureRes.data.status, details: captureRes.data });
  } catch (err) {
    console.error('PayPal capture error:', err.response?.data || err.message);
    res.status(500).json({ error: 'PayPal capture failed' });
  }
});

// ========== ORDER EMAIL (placeholder) ==========
async function sendOrderEmail(order) {
  // For now, log the order. Wire nodemailer or Resend for real email.
  console.log('=== ORDER EMAIL ===');
  console.log(`To: ${process.env.ORDER_EMAIL}`);
  console.log(`Order: ${order.id}`);
  console.log(`Customer: ${order.customer?.name} <${order.customer?.email}>`);
  console.log(`Size: ${order.size} | Port: ${order.port} | Grommet: ${order.grommet}`);
  console.log(`Shipping: ${order.shipping} | Total: $${order.total}`);
  console.log(`Artwork: ${order.artworkPath || 'Grok design'}`);
  console.log('===================');
  // TODO: Implement real email with nodemailer + SMTP or Resend API
}

// Get order status
app.get('/api/orders/:id', (req, res) => {
  const order = orders.find(o => o.id === req.params.id);
  if (!order) return res.status(404).json({ error: 'Order not found' });
  res.json(order);
});

// Health check
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', paypal: !!process.env.PAYPAL_CLIENT_ID, xai: !!process.env.XAI_API_KEY });
});

app.listen(PORT, () => {
  console.log(`MaxHeads Order App running on http://localhost:${PORT}`);
  console.log(`PayPal mode: ${process.env.PAYPAL_MODE}`);
});
