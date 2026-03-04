# AnalyzeMyPlaylist.com

A Spotify-powered music DNA analyzer. Connect your account and get a full breakdown of your genres, top artists, eras, moods, language split, and vibe profile.

## Project Structure

```
analyzemyplaylist/
├── index.html       ← Landing page
├── callback.html    ← Spotify OAuth redirect handler
├── analyze.html     ← Main report/dashboard page
├── app.js           ← Spotify PKCE auth + account logic + freemium gating
├── analyzer.js      ← Data fetching + all analysis logic
└── README.md
```

## Setup

### 1. Create a Spotify App

1. Go to [developer.spotify.com](https://developer.spotify.com/dashboard)
2. Click **Create App**
3. Set the **Redirect URI** to:
   - Local: `http://localhost:5500/callback.html`
   - GitHub Pages: `https://YOUR_USERNAME.github.io/analyzemyplaylist/callback.html`
   - Production: `https://analyzemyplaylist.com/callback.html`
4. Copy your **Client ID**

### 2. Add your Client ID

Open `app.js` and replace line 8:
```js
CLIENT_ID: 'YOUR_SPOTIFY_CLIENT_ID',
```
With your actual Client ID.

### 3. Run locally

Use VS Code's **Live Server** extension (recommended) or any local HTTP server:
```bash
npx serve .
# then open http://localhost:3000
```

> ⚠️ You MUST use a server — Spotify OAuth won't work opening files directly in the browser.

### 4. Deploy to GitHub Pages

1. Push this folder to a GitHub repo
2. Go to **Settings → Pages**
3. Set source to **main branch / root**
4. Your site will be live at `https://YOUR_USERNAME.github.io/REPO_NAME/`
5. Add that URL + `/callback.html` to your Spotify app's redirect URIs

## Freemium Model

| Feature | Free | Premium ($4.99/mo) |
|---|---|---|
| Full genre breakdown | ✅ | ✅ |
| Top artists + tracks | ✅ | ✅ |
| Era breakdown | ✅ | ✅ |
| Mood profile | ✅ | ✅ |
| Language split | ✅ | ✅ |
| Vibe summary | ✅ | ✅ |
| Monthly trends | ❌ | ✅ |
| Shareable card (download) | ❌ | ✅ |
| Friend comparison | ❌ | ✅ |
| Playlist recommendations | ❌ | ✅ |

## Next Steps (Flask migration)

When moving to Flask:
- Move token storage from localStorage → server-side sessions
- Add Stripe for real payment processing
- Add a database (PostgreSQL) for user accounts + history
- Enable the friend comparison feature (requires both users to be in DB)
- Add actual AI-generated vibe summaries via OpenAI/Claude API

## Notes

- No backend needed for the current version — PKCE runs entirely client-side
- Spotify tokens are stored in localStorage (fine for MVP, upgrade to httpOnly cookies in production)
- Premium is currently simulated — wire up Stripe when ready
