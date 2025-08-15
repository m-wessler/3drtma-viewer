const express = require('express');
const axios = require('axios');
const router = express.Router();

const PY_WORKER = process.env.PY_WORKER || 'http://localhost:5001';

// POST /api/maps/generate
router.post('/generate', async (req, res) => {
  try {
    // Forward the request to the Python worker which handles GRIB logic.
    const resp = await axios.post(`${PY_WORKER}/render`, req.body, { timeout: 120000 });
    return res.json(resp.data);
  } catch (err) {
    console.error('Error forwarding to Python worker:', err.message);
    // Fallback: return a placeholder overlay for POC
    return res.json({
      image_url: 'https://via.placeholder.com/800x600.png?text=placeholder+overlay',
      bounds: [[-90, -180], [90, 180]],
      source: 'placeholder',
      info: 'Python worker not reachable; returned placeholder.'
    });
  }
});

// POST /api/maps/sample
router.post('/sample', async (req, res) => {
  try {
    const resp = await axios.post(`${PY_WORKER}/sample`, req.body, { timeout: 15000 });
    return res.json(resp.data);
  } catch (err) {
    console.error('Sample forwarding error:', err.message);
    return res.status(502).json({ error: 'Python worker not reachable', detail: err.message });
  }
});

module.exports = router;
