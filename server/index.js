const express = require('express');
const path = require('path');
const cors = require('cors');
const bodyParser = require('body-parser');
const mapsRouter = require('./routes/maps');

const app = express();
app.use(cors());
app.use(bodyParser.json());

// Serve frontend static (resolve absolute path)
const clientDir = path.join(__dirname, '..', 'client');
app.use(express.static(clientDir));

app.use('/api/maps', mapsRouter);

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Node server listening on http://localhost:${PORT}`);
  console.log(`Serving static from: ${clientDir}`);
  console.log('Proxying requests to Python worker at http://localhost:5001 by default.');
});
