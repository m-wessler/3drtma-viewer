// client/js/map_client.js

const map = L.map('map').setView([39.0, -98.0], 4);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
}).addTo(map);

let currentOverlay = null;

async function generateOverlay() {
  const variable = document.getElementById('variable').value;
  const status = document.getElementById('status');
  status.textContent = 'Generating...';
  try {
    const resp = await fetch('/api/maps/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: '3drtma', variable })
    });
    const data = await resp.json();
    const url = data.image_url;
    const bounds = data.bounds || [[-90,-180],[90,180]];

    if (currentOverlay) map.removeLayer(currentOverlay);
    currentOverlay = L.imageOverlay(url, bounds, { opacity: parseFloat(document.getElementById('opacity').value) }).addTo(map);
    status.textContent = `Overlay: ${data.source || 'unknown'}`;
  } catch (err) {
    console.error('Generate error', err);
    status.textContent = 'Error generating overlay';
  }
}

async function sampleAt(latlng, mapEvent) {
  try {
    const resp = await fetch('/api/maps/sample', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: latlng.lat, lon: latlng.lng, source: '3drtma', variable: document.getElementById('variable').value })
    });
    const data = await resp.json();
    const content = `Value: ${data.value ?? 'n/a'} ${data.units || ''}<br/>Lat: ${latlng.lat.toFixed(4)} Lon: ${latlng.lng.toFixed(4)}<br/>Location: ${data.location || 'unknown'}`;
    L.popup().setLatLng(latlng).setContent(content).openOn(map);
  } catch (err) {
    console.error('Sample error', err);
  }
}

map.on('click', (e) => sampleAt(e.latlng));

document.getElementById('generate').addEventListener('click', generateOverlay);

document.getElementById('opacity').addEventListener('input', (e) => {
  const v = parseFloat(e.target.value);
  if (currentOverlay) currentOverlay.setOpacity(v);
});

// generate initial placeholder overlay
generateOverlay();
