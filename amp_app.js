// ============================================================
//  AnalyzeMyPlaylist — App Config & Spotify PKCE Auth
//  Replace CLIENT_ID with your Spotify Developer app client ID
//  Redirect URI must match what's set in your Spotify app dashboard
// ============================================================

const CONFIG = {
  CLIENT_ID: 'YOUR_SPOTIFY_CLIENT_ID',           // 🔑 Replace this
  REDIRECT_URI: window.location.origin + '/callback.html',
  SCOPES: [
    'user-read-private',
    'user-read-email',
    'user-top-read',
    'user-read-recently-played',
    'playlist-read-private',
    'playlist-read-collaborative',
    'user-library-read',
  ].join(' '),
};

// ── PKCE HELPERS ────────────────────────────────────────────

async function generateCodeVerifier(length = 64) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
  const arr = new Uint8Array(length);
  crypto.getRandomValues(arr);
  return Array.from(arr).map(b => chars[b % chars.length]).join('');
}

async function generateCodeChallenge(verifier) {
  const enc = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest('SHA-256', enc);
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// ── LOGIN ────────────────────────────────────────────────────

async function loginWithSpotify() {
  const verifier   = await generateCodeVerifier();
  const challenge  = await generateCodeChallenge(verifier);
  const state      = crypto.randomUUID();

  sessionStorage.setItem('pkce_verifier', verifier);
  sessionStorage.setItem('pkce_state', state);

  const params = new URLSearchParams({
    response_type:         'code',
    client_id:             CONFIG.CLIENT_ID,
    scope:                 CONFIG.SCOPES,
    redirect_uri:          CONFIG.REDIRECT_URI,
    state,
    code_challenge_method: 'S256',
    code_challenge:        challenge,
  });

  window.location.href = 'https://accounts.spotify.com/authorize?' + params.toString();
}

// ── TOKEN EXCHANGE ───────────────────────────────────────────

async function exchangeCodeForToken(code) {
  const verifier = sessionStorage.getItem('pkce_verifier');
  if (!verifier) throw new Error('No PKCE verifier found');

  const res = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type:    'authorization_code',
      code,
      redirect_uri:  CONFIG.REDIRECT_URI,
      client_id:     CONFIG.CLIENT_ID,
      code_verifier: verifier,
    }),
  });

  if (!res.ok) throw new Error('Token exchange failed: ' + await res.text());
  const data = await res.json();

  // Store tokens
  const expiry = Date.now() + data.expires_in * 1000;
  localStorage.setItem('amp_access_token',  data.access_token);
  localStorage.setItem('amp_refresh_token', data.refresh_token);
  localStorage.setItem('amp_token_expiry',  expiry);
  sessionStorage.removeItem('pkce_verifier');
  sessionStorage.removeItem('pkce_state');

  return data.access_token;
}

// ── TOKEN REFRESH ────────────────────────────────────────────

async function refreshAccessToken() {
  const refreshToken = localStorage.getItem('amp_refresh_token');
  if (!refreshToken) throw new Error('No refresh token');

  const res = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type:    'refresh_token',
      refresh_token: refreshToken,
      client_id:     CONFIG.CLIENT_ID,
    }),
  });

  if (!res.ok) throw new Error('Refresh failed');
  const data = await res.json();

  const expiry = Date.now() + data.expires_in * 1000;
  localStorage.setItem('amp_access_token', data.access_token);
  localStorage.setItem('amp_token_expiry', expiry);
  if (data.refresh_token) localStorage.setItem('amp_refresh_token', data.refresh_token);

  return data.access_token;
}

// ── GET VALID TOKEN ──────────────────────────────────────────

async function getToken() {
  const expiry = parseInt(localStorage.getItem('amp_token_expiry') || '0');
  if (Date.now() > expiry - 60000) return await refreshAccessToken();
  return localStorage.getItem('amp_access_token');
}

// ── LOGOUT ───────────────────────────────────────────────────

function logout() {
  ['amp_access_token', 'amp_refresh_token', 'amp_token_expiry', 'amp_user', 'amp_tier'].forEach(k => localStorage.removeItem(k));
  window.location.href = 'index.html';
}

// ── SPOTIFY API WRAPPER ──────────────────────────────────────

async function spotifyFetch(endpoint, params = {}) {
  const token = await getToken();
  const url = 'https://api.spotify.com/v1' + endpoint +
    (Object.keys(params).length ? '?' + new URLSearchParams(params) : '');
  const res = await fetch(url, { headers: { Authorization: 'Bearer ' + token } });
  if (!res.ok) throw new Error(`Spotify API error ${res.status}: ${endpoint}`);
  return res.json();
}

// ── USER ACCOUNT ─────────────────────────────────────────────

async function getOrCreateUser() {
  const profile = await spotifyFetch('/me');
  const user = {
    id:          profile.id,
    name:        profile.display_name,
    email:       profile.email,
    avatar:      profile.images?.[0]?.url || null,
    country:     profile.country,
    tier:        localStorage.getItem('amp_tier') || 'free',
    joinedAt:    localStorage.getItem('amp_joined') || new Date().toISOString(),
  };
  localStorage.setItem('amp_user', JSON.stringify(user));
  if (!localStorage.getItem('amp_joined')) localStorage.setItem('amp_joined', user.joinedAt);
  return user;
}

function getUser() {
  const raw = localStorage.getItem('amp_user');
  return raw ? JSON.parse(raw) : null;
}

function isPremium() {
  const user = getUser();
  return user?.tier === 'premium';
}

// ── FREEMIUM GATE ─────────────────────────────────────────────

function requirePremium(featureName, onUnlock) {
  if (isPremium()) { onUnlock(); return; }
  showUpgradeModal(featureName);
}

function showUpgradeModal(featureName = 'this feature') {
  const existing = document.getElementById('upgrade-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'upgrade-modal';
  modal.innerHTML = `
    <div style="position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:9000;display:flex;align-items:center;justify-content:center;padding:24px;backdrop-filter:blur(4px)">
      <div style="background:#141414;border:1px solid #2a2a2a;border-radius:20px;padding:48px 40px;max-width:440px;width:100%;text-align:center;position:relative">
        <button onclick="document.getElementById('upgrade-modal').remove()" style="position:absolute;top:16px;right:20px;background:none;border:none;color:#666;font-size:22px;cursor:pointer">×</button>
        <div style="font-size:40px;margin-bottom:16px">🔒</div>
        <div style="font-family:'Syne',sans-serif;font-size:22px;font-weight:800;color:#f2f2f2;margin-bottom:10px;letter-spacing:-0.02em">Premium Feature</div>
        <div style="font-size:14px;color:#b0b0b0;line-height:1.6;margin-bottom:32px"><strong style="color:#f2f2f2">${featureName}</strong> is available on Premium.<br/>Upgrade to unlock trends, shareable cards, friend comparisons, and playlist recommendations.</div>
        <div style="background:#1c1c1c;border-radius:14px;padding:24px;margin-bottom:28px;text-align:left">
          <div style="font-family:'DM Mono',monospace;font-size:10px;color:#1DB954;letter-spacing:0.15em;margin-bottom:14px">PREMIUM INCLUDES</div>
          ${['📈 Monthly listening trends', '🃏 Shareable report cards', '👥 Friend taste comparison', '🎵 Playlist recommendations', '📄 Full PDF export', '🔄 Unlimited re-analysis'].map(f => `<div style="font-size:13px;color:#b0b0b0;padding:5px 0;display:flex;gap:10px;align-items:center">${f}</div>`).join('')}
        </div>
        <div style="display:flex;flex-direction:column;gap:10px">
          <button onclick="handleUpgrade()" style="background:#1DB954;color:#000;border:none;border-radius:999px;padding:15px 28px;font-family:'Syne',sans-serif;font-size:15px;font-weight:700;cursor:pointer;transition:opacity 0.2s" onmouseover="this.style.opacity=0.85" onmouseout="this.style.opacity=1">Upgrade to Premium — $4.99/mo</button>
          <button onclick="document.getElementById('upgrade-modal').remove()" style="background:none;border:1px solid #2a2a2a;color:#666;border-radius:999px;padding:13px 28px;font-family:'Syne',sans-serif;font-size:14px;cursor:pointer">Maybe later</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

function handleUpgrade() {
  // In production: redirect to Stripe checkout
  // For now: simulate upgrade
  alert('Stripe integration coming soon! For now, premium will be simulated.');
  const user = getUser();
  if (user) {
    user.tier = 'premium';
    localStorage.setItem('amp_user', JSON.stringify(user));
    localStorage.setItem('amp_tier', 'premium');
  }
  document.getElementById('upgrade-modal')?.remove();
  window.location.reload();
}

// Export for use across pages
window.AMP = {
  CONFIG, loginWithSpotify, exchangeCodeForToken,
  getToken, logout, spotifyFetch,
  getOrCreateUser, getUser, isPremium,
  requirePremium, showUpgradeModal,
};
