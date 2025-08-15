// Lightweight client that could be expanded to talk to the Python worker
const axios = require('axios');

const PY_WORKER = process.env.PY_WORKER || 'http://localhost:5001';

module.exports = {
  async render(payload) {
    const resp = await axios.post(`${PY_WORKER}/render`, payload);
    return resp.data;
  },
  async sample(payload) {
    const resp = await axios.post(`${PY_WORKER}/sample`, payload);
    return resp.data;
  }
};
