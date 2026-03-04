// ============================================================
//  AnalyzeMyPlaylist — Data Fetcher & Analyzer
//  Pulls data from Spotify API and computes all stats
// ============================================================

const Analyzer = (() => {

  // ── FETCH ALL DATA ────────────────────────────────────────

  async function fetchAllData(timeRange = 'medium_term') {
    const [topArtists, topTracks, recentTracks, playlists, profile] = await Promise.all([
      AMP.spotifyFetch('/me/top/artists', { limit: 50, time_range: timeRange }),
      AMP.spotifyFetch('/me/top/tracks',  { limit: 50, time_range: timeRange }),
      AMP.spotifyFetch('/me/player/recently-played', { limit: 50 }),
      AMP.spotifyFetch('/me/playlists',   { limit: 50 }),
      AMP.spotifyFetch('/me'),
    ]);

    // Fetch audio features for top tracks
    const trackIds = topTracks.items.map(t => t.id).join(',');
    const audioFeatures = trackIds
      ? await AMP.spotifyFetch('/audio-features', { ids: trackIds })
      : { audio_features: [] };

    return { topArtists, topTracks, recentTracks, playlists, profile, audioFeatures };
  }

  // ── GENRE ANALYSIS ────────────────────────────────────────

  function analyzeGenres(topArtists) {
    const genreCount = {};
    topArtists.items.forEach(artist => {
      artist.genres.forEach(g => {
        genreCount[g] = (genreCount[g] || 0) + 1;
      });
    });

    // Group into broad categories
    const categories = {
      'Hip-Hop / Rap':        ['hip hop', 'rap', 'trap', 'drill', 'boom bap', 'conscious hip hop'],
      'R&B / Soul':           ['r&b', 'soul', 'neo soul', 'contemporary r&b', 'quiet storm'],
      'Latin / Reggaeton':    ['latin', 'reggaeton', 'salsa', 'bachata', 'dembow', 'latin pop', 'cumbia', 'merengue', 'latin trap'],
      'Pop':                  ['pop', 'dance pop', 'electropop', 'synth-pop', 'indie pop'],
      'Rock / Alternative':   ['rock', 'alternative', 'indie rock', 'punk', 'grunge', 'metal'],
      'Electronic / Dance':   ['edm', 'electronic', 'house', 'techno', 'dance', 'dubstep', 'drum and bass'],
      'Jazz / Blues':         ['jazz', 'blues', 'bebop', 'swing', 'fusion'],
      'Classical / Orchestral': ['classical', 'orchestral', 'opera', 'symphony'],
      'Reggae / Dancehall':   ['reggae', 'dancehall', 'ska'],
      'Country / Folk':       ['country', 'folk', 'americana', 'bluegrass'],
      'Other':                [],
    };

    const grouped = {};
    Object.keys(categories).forEach(cat => { grouped[cat] = 0; });

    Object.entries(genreCount).forEach(([genre, count]) => {
      let matched = false;
      for (const [cat, keywords] of Object.entries(categories)) {
        if (cat === 'Other') continue;
        if (keywords.some(k => genre.toLowerCase().includes(k))) {
          grouped[cat] += count;
          matched = true;
          break;
        }
      }
      if (!matched) grouped['Other'] += count;
    });

    // Sort and filter zeros
    const sorted = Object.entries(grouped)
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1]);

    const total = sorted.reduce((s, [, v]) => s + v, 0);
    return sorted.map(([name, count]) => ({
      name,
      count,
      pct: Math.round((count / total) * 100),
      raw: genreCount,
    }));
  }

  // ── ERA ANALYSIS ──────────────────────────────────────────

  function analyzeEras(topTracks) {
    const eraBuckets = {
      'Pre-1980': { min: 0,    max: 1979, count: 0 },
      '1980s':    { min: 1980, max: 1989, count: 0 },
      '1990s':    { min: 1990, max: 1999, count: 0 },
      '2000s':    { min: 2000, max: 2009, count: 0 },
      '2010s':    { min: 2010, max: 2019, count: 0 },
      '2020s':    { min: 2020, max: 2099, count: 0 },
    };

    topTracks.items.forEach(track => {
      const year = parseInt(track.album.release_date?.substring(0, 4));
      if (!year) return;
      for (const [label, bucket] of Object.entries(eraBuckets)) {
        if (year >= bucket.min && year <= bucket.max) {
          bucket.count++;
          break;
        }
      }
    });

    const total = topTracks.items.length || 1;
    return Object.entries(eraBuckets)
      .filter(([, b]) => b.count > 0)
      .map(([label, b]) => ({
        label,
        count: b.count,
        pct: Math.round((b.count / total) * 100),
      }))
      .sort((a, b) => b.count - a.count);
  }

  // ── AUDIO FEATURES / MOOD ─────────────────────────────────

  function analyzeMood(audioFeatures) {
    const features = audioFeatures.audio_features.filter(Boolean);
    if (!features.length) return null;

    const avg = key => features.reduce((s, f) => s + (f[key] || 0), 0) / features.length;

    const energy      = avg('energy');
    const valence     = avg('valence');      // happiness
    const danceability = avg('danceability');
    const acousticness = avg('acousticness');
    const tempo       = avg('tempo');

    // Map to mood buckets
    const moods = [];
    if (energy > 0.7)              moods.push({ name: 'High Energy 🔥', pct: Math.round(energy * 100) });
    if (valence < 0.4)             moods.push({ name: 'Melancholic 💔', pct: Math.round((1 - valence) * 100) });
    if (valence > 0.6)             moods.push({ name: 'Feel-Good 😊',   pct: Math.round(valence * 100) });
    if (danceability > 0.65)       moods.push({ name: 'Dance 💃',        pct: Math.round(danceability * 100) });
    if (acousticness > 0.5)        moods.push({ name: 'Chill / Acoustic 🌙', pct: Math.round(acousticness * 100) });
    if (energy < 0.4)              moods.push({ name: 'Low Key 😌',      pct: Math.round((1 - energy) * 100) });
    if (tempo > 120 && energy > 0.6) moods.push({ name: 'Hype 💥',       pct: Math.round((tempo / 200) * 100) });

    return {
      moods: moods.sort((a, b) => b.pct - a.pct).slice(0, 4),
      averages: { energy, valence, danceability, acousticness, tempo: Math.round(tempo) },
    };
  }

  // ── ARTIST STATS ──────────────────────────────────────────

  function analyzeArtists(topArtists) {
    return topArtists.items.slice(0, 12).map((artist, i) => ({
      rank:       i + 1,
      name:       artist.name,
      genres:     artist.genres.slice(0, 2),
      popularity: artist.popularity,
      image:      artist.images?.[1]?.url || artist.images?.[0]?.url || null,
      url:        artist.external_urls?.spotify,
    }));
  }

  // ── TOP TRACKS ────────────────────────────────────────────

  function analyzeTopTracks(topTracks) {
    return topTracks.items.slice(0, 10).map((track, i) => ({
      rank:     i + 1,
      name:     track.name,
      artist:   track.artists.map(a => a.name).join(', '),
      album:    track.album.name,
      duration: formatDuration(track.duration_ms),
      image:    track.album.images?.[2]?.url || null,
      url:      track.external_urls?.spotify,
      year:     track.album.release_date?.substring(0, 4),
    }));
  }

  // ── OBSCURITY / TASTE SCORE ───────────────────────────────

  function calcTasteScore(topArtists) {
    // Spotify popularity is 0-100, lower = more obscure
    const artists = topArtists.items;
    if (!artists.length) return 50;
    const avgPop = artists.reduce((s, a) => s + a.popularity, 0) / artists.length;
    // Invert: low mainstream = high taste score
    const score = Math.round(100 - avgPop);
    let label, desc;
    if (score >= 75) { label = 'Underground';    desc = 'You actively seek out artists most people haven\'t heard of.'; }
    else if (score >= 55) { label = 'Indie';    desc = 'You lean toward less mainstream artists with real depth.'; }
    else if (score >= 40) { label = 'Balanced';  desc = 'A healthy mix of popular and under-the-radar artists.'; }
    else if (score >= 25) { label = 'Mainstream'; desc = 'You stay close to the charts and that\'s completely fine.'; }
    else                   { label = 'Pop Core';  desc = 'You know every trending song. You\'re plugged in.'; }
    return { score, label, desc };
  }

  // ── VIBE SUMMARY (rule-based, no AI needed) ───────────────

  function generateVibeSummary(genres, eras, mood, tasteScore) {
    const topGenre   = genres[0]?.name || 'music';
    const topEra     = eras[0]?.label  || 'recent';
    const isNostalgia = eras.some(e => ['Pre-1980','1980s','1990s'].includes(e.label) && e.pct > 20);
    const isEnergetic = mood?.averages?.energy > 0.65;
    const isMelancholic = mood?.averages?.valence < 0.4;
    const isDancer    = mood?.averages?.danceability > 0.7;

    let vibes = [];
    if (isNostalgia)    vibes.push('old soul');
    if (isDancer)       vibes.push('natural dancer');
    if (isMelancholic)  vibes.push('deep feeler');
    if (isEnergetic)    vibes.push('high energy');
    if (tasteScore.score > 60) vibes.push('serious music head');

    const vibeStr = vibes.length
      ? vibes.slice(0, 2).join(' and ')
      : 'well-rounded listener';

    return `Your taste is rooted in ${topGenre}, with most of your listening living in the ${topEra}. You come across as a ${vibeStr}. ${tasteScore.desc}`;
  }

  // ── LANGUAGE DETECTION (heuristic from genres) ───────────

  function detectLanguage(topArtists) {
    const latinGenreKeywords = ['latin', 'reggaeton', 'salsa', 'bachata', 'cumbia', 'merengue', 'flamenco', 'bossa nova', 'samba', 'corrido', 'norteño'];
    const frenchKeywords     = ['french', 'chanson', 'variété'];
    const portugueseKeywords = ['brazilian', 'sertanejo', 'axe', 'pagode', 'forró'];
    const kpopKeywords       = ['k-pop', 'korean', 'j-pop', 'japanese'];

    let counts = { English: 0, Spanish: 0, Portuguese: 0, French: 0, Korean: 0, Other: 0 };

    topArtists.items.forEach(artist => {
      const genres = artist.genres.join(' ').toLowerCase();
      if      (latinGenreKeywords.some(k => genres.includes(k)))     counts.Spanish++;
      else if (portugueseKeywords.some(k => genres.includes(k)))     counts.Portuguese++;
      else if (frenchKeywords.some(k => genres.includes(k)))         counts.French++;
      else if (kpopKeywords.some(k => genres.includes(k)))           counts.Korean++;
      else                                                             counts.English++;
    });

    const total = Object.values(counts).reduce((s, v) => s + v, 0) || 1;
    return Object.entries(counts)
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([lang, count]) => ({
        lang,
        pct: Math.round((count / total) * 100),
        count,
      }));
  }

  // ── FULL ANALYSIS ─────────────────────────────────────────

  async function runFullAnalysis(timeRange = 'medium_term') {
    const raw       = await fetchAllData(timeRange);
    const genres    = analyzeGenres(raw.topArtists);
    const eras      = analyzeEras(raw.topTracks);
    const mood      = analyzeMood(raw.audioFeatures);
    const artists   = analyzeArtists(raw.topArtists);
    const tracks    = analyzeTopTracks(raw.topTracks);
    const tasteScore = calcTasteScore(raw.topArtists);
    const language  = detectLanguage(raw.topArtists);
    const vibe      = generateVibeSummary(genres, eras, mood, tasteScore);

    return {
      user:       AMP.getUser(),
      timeRange,
      genres,
      eras,
      mood,
      artists,
      tracks,
      tasteScore,
      language,
      vibe,
      totalPlaylists: raw.playlists.total,
      generatedAt: new Date().toISOString(),
    };
  }

  // ── UTILS ─────────────────────────────────────────────────

  function formatDuration(ms) {
    const m = Math.floor(ms / 60000);
    const s = Math.floor((ms % 60000) / 1000).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  return { runFullAnalysis, formatDuration };

})();

window.Analyzer = Analyzer;
