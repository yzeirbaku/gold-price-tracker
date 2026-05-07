// Default config for local dev. Cloudflare Pages overwrites this at build time
// with the production BACKEND_URL env var via the build command:
//   echo "window.BACKEND_URL = '${BACKEND_URL}';" > frontend/config.js
window.BACKEND_URL = 'http://127.0.0.1:8000';
