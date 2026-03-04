from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps
from dotenv import load_dotenv
import os, json, secrets, requests, csv, io, re
from collections import defaultdict

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///analyzemyplaylist.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE']   = False
app.config['SESSION_COOKIE_HTTPONLY'] = True

db = SQLAlchemy(app)

GOOGLE_CLIENT_ID     = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI  = os.getenv('GOOGLE_REDIRECT_URI', 'http://127.0.0.1:5000/auth/google/callback')
GOOGLE_SCOPES        = 'openid email profile https://www.googleapis.com/auth/youtube.readonly'
LASTFM_API_KEY       = os.getenv('LASTFM_API_KEY', 'e9fbf5f4c9f26111fba6af3b3df87de8')
SPOTIFY_CLIENT_ID    = os.getenv('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET= os.getenv('SPOTIFY_CLIENT_SECRET', '')
LASTFM_BASE          = 'https://ws.audioscrobbler.com/2.0/'

# ── MODELS ────────────────────────────────────────────────────

class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    tier          = db.Column(db.String(20), default='free')
    avatar_url    = db.Column(db.String(500), nullable=True)
    google_id     = db.Column(db.String(100), unique=True, nullable=True)
    youtube_token = db.Column(db.Text, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    analyses      = db.relationship('Analysis', backref='user', lazy=True)
    friends_sent  = db.relationship('Friendship', foreign_keys='Friendship.user_id',    backref='sender',   lazy=True)
    friends_recv  = db.relationship('Friendship', foreign_keys='Friendship.friend_id',  backref='receiver', lazy=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)
    def check_password(self, pw):
        if not self.password_hash: return False
        return check_password_hash(self.password_hash, pw)
    def is_premium(self): return self.tier == 'premium'

class Analysis(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    source     = db.Column(db.String(20))
    data       = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    def get_data(self): return json.loads(self.data) if self.data else {}

class Friendship(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    friend_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status    = db.Column(db.String(20), default='pending')  # pending / accepted
    created_at= db.Column(db.DateTime, default=datetime.utcnow)

# ── HELPERS ───────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def current_user():
    if 'user_id' not in session: return None
    return db.session.get(User, session['user_id'])

# ── PAGES ─────────────────────────────────────────────────────

@app.route('/')
def index():
    stats = get_global_platform_stats()
    return render_template('index.html', user=current_user(), stats=stats)

@app.route('/dashboard')
@login_required
def dashboard():
    user     = current_user()
    analyses = Analysis.query.filter_by(user_id=user.id).order_by(Analysis.created_at.desc()).limit(10).all()
    friends  = get_friends(user)
    pending  = get_pending_requests(user)
    return render_template('dashboard.html', user=user, analyses=analyses, friends=friends, pending=pending)

@app.route('/analyze')
@login_required
def analyze():
    return render_template('analyze.html', user=current_user())

@app.route('/charts')
def charts():
    return render_template('charts.html', user=current_user())

@app.route('/account')
@login_required
def account():
    return render_template('account.html', user=current_user())

@app.route('/login')
def login():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return render_template('login.html', google_enabled=bool(GOOGLE_CLIENT_ID))

@app.route('/signup')
def signup():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return render_template('signup.html', google_enabled=bool(GOOGLE_CLIENT_ID))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ── EMAIL AUTH ────────────────────────────────────────────────

@app.route('/api/signup', methods=['POST'])
def api_signup():
    data = request.get_json()
    email = data.get('email','').strip().lower()
    username = data.get('username','').strip()
    password = data.get('password','')
    if not email or not username or not password:
        return jsonify({'error':'All fields are required'}),400
    if len(password)<6:
        return jsonify({'error':'Password must be at least 6 characters'}),400
    if User.query.filter_by(email=email).first():
        return jsonify({'error':'Email already registered'}),400
    if User.query.filter_by(username=username).first():
        return jsonify({'error':'Username already taken'}),400
    user = User(email=email, username=username)
    user.set_password(password)
    db.session.add(user); db.session.commit()
    session.permanent = True; session['user_id'] = user.id
    return jsonify({'success':True,'redirect':url_for('dashboard')})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    identifier = data.get('identifier','').strip().lower()
    password   = data.get('password','')
    user = User.query.filter((User.email==identifier)|(User.username==identifier)).first()
    if not user or not user.check_password(password):
        return jsonify({'error':'Incorrect email/username or password'}),401
    session.permanent = True; session['user_id'] = user.id
    return jsonify({'success':True,'redirect':url_for('dashboard')})

# ── GOOGLE OAUTH ──────────────────────────────────────────────

@app.route('/auth/google')
def auth_google():
    if not GOOGLE_CLIENT_ID:
        flash('Google Sign In is not configured.','error')
        return redirect(url_for('login'))
    state = secrets.token_urlsafe(16)
    session.permanent = True; session['oauth_state'] = state; session.modified = True
    from urllib.parse import urlencode
    params = {'client_id':GOOGLE_CLIENT_ID,'redirect_uri':GOOGLE_REDIRECT_URI,
              'response_type':'code','scope':GOOGLE_SCOPES,'state':state,
              'access_type':'offline','prompt':'select_account'}
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(params))

@app.route('/auth/google/callback')
def auth_google_callback():
    if request.args.get('error'):
        flash('Google sign in was cancelled.','info'); return redirect(url_for('login'))
    code = request.args.get('code')
    if not code:
        flash('No authorization code received.','error'); return redirect(url_for('login'))
    stored = session.get('oauth_state')
    if stored and request.args.get('state') != stored:
        flash('Security mismatch — please try again.','error'); return redirect(url_for('login'))
    try:
        tokens = requests.post('https://oauth2.googleapis.com/token', data={
            'code':code,'client_id':GOOGLE_CLIENT_ID,'client_secret':GOOGLE_CLIENT_SECRET,
            'redirect_uri':GOOGLE_REDIRECT_URI,'grant_type':'authorization_code'}).json()
        if 'error' in tokens:
            flash('Failed to get token from Google.','error'); return redirect(url_for('login'))
        access_token = tokens.get('access_token')
        profile = requests.get('https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization':f'Bearer {access_token}'}).json()
        google_id = profile.get('id'); email = profile.get('email','').lower()
        name = profile.get('name',''); avatar_url = profile.get('picture','')
        if not google_id or not email:
            flash('Could not get your Google account info.','error'); return redirect(url_for('login'))
        user = User.query.filter_by(google_id=google_id).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if user: user.google_id=google_id; user.avatar_url=avatar_url
            else:
                base = ''.join(c for c in name.lower().replace(' ','_') if c.isalnum() or c=='_') or 'user'
                username = base; i=1
                while User.query.filter_by(username=username).first(): username=f'{base}{i}'; i+=1
                user = User(email=email,username=username,google_id=google_id,avatar_url=avatar_url)
                db.session.add(user)
        user.youtube_token = access_token
        db.session.commit()
        session.permanent=True; session['user_id']=user.id; session.modified=True
        return redirect(url_for('dashboard'))
    except Exception as e:
        flash(f'Sign in error: {str(e)}','error'); return redirect(url_for('login'))

# ── LASTFM CHARTS API ─────────────────────────────────────────

# Country code -> Last.fm country name mapping
COUNTRIES = {
    'global': None,
    'us': 'United States', 'gb': 'United Kingdom', 'de': 'Germany',
    'fr': 'France', 'es': 'Spain', 'it': 'Italy', 'br': 'Brazil',
    'mx': 'Mexico', 'ar': 'Argentina', 'co': 'Colombia', 'cl': 'Chile',
    'jp': 'Japan', 'kr': 'South Korea', 'au': 'Australia', 'ca': 'Canada',
    'nl': 'Netherlands', 'se': 'Sweden', 'no': 'Norway', 'pl': 'Poland',
    'pt': 'Portugal', 'tr': 'Turkey', 'ru': 'Russia', 'in': 'India',
    'za': 'South Africa', 'ng': 'Nigeria', 'ph': 'Philippines',
    'id': 'Indonesia', 'th': 'Thailand', 'pr': 'Puerto Rico',
}

_charts_cache = {}
_cache_time   = {}

def get_lastfm_charts(country_code='global', limit=20):
    cache_key = f'{country_code}_{limit}'
    now = datetime.utcnow().timestamp()
    if cache_key in _charts_cache and now - _cache_time.get(cache_key,0) < 1800:
        return _charts_cache[cache_key]

    country = COUNTRIES.get(country_code)
    try:
        if country_code == 'global' or not country:
            resp = requests.get(LASTFM_BASE, params={
                'method':'chart.gettoptracks','api_key':LASTFM_API_KEY,
                'format':'json','limit':limit}, timeout=8)
        else:
            resp = requests.get(LASTFM_BASE, params={
                'method':'geo.gettoptracks','country':country,
                'api_key':LASTFM_API_KEY,'format':'json','limit':limit}, timeout=8)

        data  = resp.json()
        raw   = data.get('tracks',{}).get('track',[])
        tracks = []
        for i,t in enumerate(raw):
            preview = get_spotify_preview(t.get('name',''), t.get('artist',{}).get('name',''))
            tracks.append({
                'rank':       i+1,
                'title':      t.get('name',''),
                'artist':     t.get('artist',{}).get('name',''),
                'listeners':  int(t.get('listeners',0) or 0),
                'playcount':  int(t.get('playcount',0) or 0),
                'url':        t.get('url',''),
                'image':      next((img['#text'] for img in (t.get('image') or []) if img.get('size')=='large' and img.get('#text')),None),
                'preview_url': preview,
            })
        _charts_cache[cache_key] = tracks
        _cache_time[cache_key]   = now
        return tracks
    except Exception as e:
        print(f'[LastFM] Error: {e}')
        return []

def get_lastfm_top_artists(country_code='global', limit=10):
    cache_key = f'artists_{country_code}'
    now = datetime.utcnow().timestamp()
    if cache_key in _charts_cache and now - _cache_time.get(cache_key,0) < 1800:
        return _charts_cache[cache_key]
    country = COUNTRIES.get(country_code)
    try:
        if country_code == 'global' or not country:
            resp = requests.get(LASTFM_BASE, params={
                'method':'chart.gettopartists','api_key':LASTFM_API_KEY,
                'format':'json','limit':limit}, timeout=8)
            raw = resp.json().get('artists',{}).get('artist',[])
        else:
            resp = requests.get(LASTFM_BASE, params={
                'method':'geo.gettopartists','country':country,
                'api_key':LASTFM_API_KEY,'format':'json','limit':limit}, timeout=8)
            raw = resp.json().get('topartists',{}).get('artist',[])
        artists = [{'name':a.get('name',''),'listeners':int(a.get('listeners',0) or 0),
                    'url':a.get('url','')} for a in raw]
        _charts_cache[cache_key] = artists
        _cache_time[cache_key]   = now
        return artists
    except Exception as e:
        print(f'[LastFM Artists] Error: {e}')
        return []

# ── SPOTIFY PREVIEW ───────────────────────────────────────────

_spotify_token   = None
_spotify_token_exp = 0

def get_spotify_token():
    global _spotify_token, _spotify_token_exp
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET: return None
    now = datetime.utcnow().timestamp()
    if _spotify_token and now < _spotify_token_exp - 60: return _spotify_token
    try:
        import base64
        creds = base64.b64encode(f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}'.encode()).decode()
        r = requests.post('https://accounts.spotify.com/api/token',
            headers={'Authorization':f'Basic {creds}'},
            data={'grant_type':'client_credentials'}, timeout=8)
        d = r.json()
        _spotify_token     = d.get('access_token')
        _spotify_token_exp = now + d.get('expires_in', 3600)
        return _spotify_token
    except: return None

def get_spotify_preview(title, artist):
    token = get_spotify_token()
    if not token: return None
    try:
        r = requests.get('https://api.spotify.com/v1/search',
            headers={'Authorization':f'Bearer {token}'},
            params={'q':f'track:{title} artist:{artist}','type':'track','limit':1}, timeout=5)
        items = r.json().get('tracks',{}).get('items',[])
        if items: return items[0].get('preview_url')
    except: pass
    return None

# ── CHARTS ROUTES ─────────────────────────────────────────────

@app.route('/api/charts')
def api_charts():
    country = request.args.get('country','global').lower()
    if country not in COUNTRIES: country = 'global'
    tracks  = get_lastfm_charts(country, limit=20)
    artists = get_lastfm_top_artists(country, limit=8)
    return jsonify({'tracks':tracks,'artists':artists,'country':country,
                    'country_name': COUNTRIES.get(country) or 'Global'})

@app.route('/api/charts/preview')
def api_charts_preview():
    title  = request.args.get('title','')
    artist = request.args.get('artist','')
    url    = get_spotify_preview(title, artist)
    return jsonify({'preview_url': url})

# ── GLOBAL PLATFORM STATS ─────────────────────────────────────

def get_global_platform_stats():
    try:
        total_analyses = Analysis.query.count()
        all_analyses   = Analysis.query.all()
        artist_counts  = defaultdict(int)
        for a in all_analyses:
            d = a.get_data()
            for art in d.get('top_artists',[])[:5]:
                if isinstance(art, dict) and art.get('name'):
                    artist_counts[art['name']] += 1
        top_artist = max(artist_counts, key=artist_counts.get) if artist_counts else '—'
        return {'total_analyses': total_analyses, 'top_artist': top_artist,
                'total_users': User.query.count()}
    except: return {'total_analyses':0,'top_artist':'—','total_users':0}

# ── FRIENDS SYSTEM ────────────────────────────────────────────

def get_friends(user):
    accepted = Friendship.query.filter(
        ((Friendship.user_id==user.id)|(Friendship.friend_id==user.id)),
        Friendship.status=='accepted').all()
    friends = []
    for f in accepted:
        friend = db.session.get(User, f.friend_id if f.user_id==user.id else f.user_id)
        if friend: friends.append(friend)
    return friends

def get_pending_requests(user):
    pending = Friendship.query.filter_by(friend_id=user.id, status='pending').all()
    return [db.session.get(User, f.user_id) for f in pending if db.session.get(User, f.user_id)]

@app.route('/api/friends/add', methods=['POST'])
@login_required
def api_friends_add():
    user = current_user()
    data = request.get_json()
    username = data.get('username','').strip()
    if not username: return jsonify({'error':'Username required'}),400
    friend = User.query.filter_by(username=username).first()
    if not friend: return jsonify({'error':f'No user found with username "{username}"'}),404
    if friend.id == user.id: return jsonify({'error':"You can't add yourself"}),400
    existing = Friendship.query.filter(
        ((Friendship.user_id==user.id)&(Friendship.friend_id==friend.id))|
        ((Friendship.user_id==friend.id)&(Friendship.friend_id==user.id))).first()
    if existing:
        if existing.status=='accepted': return jsonify({'error':'Already friends'}),400
        return jsonify({'error':'Friend request already sent'}),400
    f = Friendship(user_id=user.id, friend_id=friend.id, status='pending')
    db.session.add(f); db.session.commit()
    return jsonify({'success':True,'message':f'Friend request sent to {friend.username}!'})

@app.route('/api/friends/accept', methods=['POST'])
@login_required
def api_friends_accept():
    user = current_user()
    data = request.get_json()
    from_id = data.get('user_id')
    f = Friendship.query.filter_by(user_id=from_id, friend_id=user.id, status='pending').first()
    if not f: return jsonify({'error':'Request not found'}),404
    f.status = 'accepted'; db.session.commit()
    sender = db.session.get(User, from_id)
    return jsonify({'success':True,'message':f'You and {sender.username} are now friends!'})

@app.route('/api/friends/decline', methods=['POST'])
@login_required
def api_friends_decline():
    user = current_user()
    data = request.get_json()
    from_id = data.get('user_id')
    f = Friendship.query.filter_by(user_id=from_id, friend_id=user.id, status='pending').first()
    if not f: return jsonify({'error':'Request not found'}),404
    db.session.delete(f); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/friends/compare/<int:friend_id>')
@login_required
def api_friends_compare(friend_id):
    user   = current_user()
    friend = db.session.get(User, friend_id)
    if not friend: return jsonify({'error':'User not found'}),404
    # Verify they are actually friends
    f = Friendship.query.filter(
        ((Friendship.user_id==user.id)&(Friendship.friend_id==friend.id))|
        ((Friendship.user_id==friend.id)&(Friendship.friend_id==user.id)),
        Friendship.status=='accepted').first()
    if not f: return jsonify({'error':'Not friends'}),403

    my_analysis = Analysis.query.filter_by(user_id=user.id).order_by(Analysis.created_at.desc()).first()
    fr_analysis = Analysis.query.filter_by(user_id=friend.id).order_by(Analysis.created_at.desc()).first()
    if not my_analysis: return jsonify({'error':"You haven't analyzed your music yet"}),400
    if not fr_analysis: return jsonify({'error':f'{friend.username} hasn\'t analyzed their music yet'}),400

    my_data = my_analysis.get_data()
    fr_data = fr_analysis.get_data()

    my_artists = {a['name'] for a in my_data.get('top_artists',[]) if isinstance(a,dict)}
    fr_artists = {a['name'] for a in fr_data.get('top_artists',[]) if isinstance(a,dict)}
    shared = list(my_artists & fr_artists)

    my_tracks = {t['name'] for t in my_data.get('top_tracks',[]) if isinstance(t,dict)}
    fr_tracks = {t['name'] for t in fr_data.get('top_tracks',[]) if isinstance(t,dict)}
    shared_tracks = list(my_tracks & fr_tracks)

    total = len(my_artists | fr_artists)
    overlap_pct = round(len(shared) / total * 100) if total else 0

    if overlap_pct >= 60: vibe = "You two share a musical soul. Your playlists could be twins."
    elif overlap_pct >= 30: vibe = "Solid overlap — you'd agree on the playlist at a party."
    elif overlap_pct >= 10: vibe = "Different tastes, but enough in common to find common ground."
    else: vibe = "Polar opposites. A collab playlist between you two would be wild."

    mixed = []
    my_list = [a['name'] for a in my_data.get('top_artists',[]) if isinstance(a,dict)]
    fr_list = [a['name'] for a in fr_data.get('top_artists',[]) if isinstance(a,dict)]
    for i in range(max(len(my_list),len(fr_list))):
        if i < len(my_list): mixed.append({'name':my_list[i],'owner':user.username})
        if i < len(fr_list): mixed.append({'name':fr_list[i],'owner':friend.username})

    return jsonify({
        'my_username':      user.username,
        'friend_username':  friend.username,
        'my_avatar':        user.avatar_url or '',
        'friend_avatar':    friend.avatar_url or '',
        'my_top_artists':   my_data.get('top_artists',[])[:10],
        'friend_top_artists': fr_data.get('top_artists',[])[:10],
        'shared_artists':   shared[:10],
        'shared_tracks':    shared_tracks[:10],
        'overlap_pct':      overlap_pct,
        'compatibility_vibe': vibe,
        'mixed_playlist':   mixed[:20],
    })

# ── ANALYSIS ROUTES ───────────────────────────────────────────

@app.route('/api/analyze/upload', methods=['POST'])
@login_required
def api_analyze_upload():
    user = current_user()
    if 'file' not in request.files: return jsonify({'error':'No file uploaded'}),400
    f = request.files['file']
    if not f.filename.endswith('.json'): return jsonify({'error':'Please upload a .json file'}),400
    try:
        raw = json.loads(f.read().decode('utf-8'))
        result = analyze_spotify(raw)
        a = Analysis(user_id=user.id, source='upload', data=json.dumps(result))
        db.session.add(a); db.session.commit(); result['analysis_id']=a.id
        return jsonify(result)
    except json.JSONDecodeError: return jsonify({'error':'Invalid JSON'}),400
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/api/analyze/apple', methods=['POST'])
@login_required
def api_analyze_apple():
    user = current_user()
    if 'file' not in request.files: return jsonify({'error':'No file uploaded'}),400
    f = request.files['file']; fname = f.filename.lower()
    try:
        if fname.endswith('.csv'):
            raw = f.read()
            for enc in ('utf-8','utf-8-sig','latin-1'):
                try: text = raw.decode(enc); break
                except: continue
            result = analyze_apple_csv(text)
        elif fname.endswith('.pdf'):
            result = analyze_apple_pdf(f.read())
        else:
            return jsonify({'error':'Please upload a .csv or .pdf file'}),400
        a = Analysis(user_id=user.id, source='apple', data=json.dumps(result))
        db.session.add(a); db.session.commit(); result['analysis_id']=a.id
        return jsonify(result)
    except Exception as e: return jsonify({'error':f'Could not read file: {str(e)}'}),500

@app.route('/api/analyze/youtube')
@login_required
def api_analyze_youtube():
    user = current_user()
    if not user.youtube_token: return jsonify({'error':'Connect Google account first','needs_google':True}),400
    try:
        h = {'Authorization':f'Bearer {user.youtube_token}'}
        liked = requests.get('https://www.googleapis.com/youtube/v3/videos',headers=h,
            params={'part':'snippet,statistics','myRating':'like','maxResults':50}).json()
        subs  = requests.get('https://www.googleapis.com/youtube/v3/subscriptions',headers=h,
            params={'part':'snippet','mine':'true','maxResults':50}).json()
        pls   = requests.get('https://www.googleapis.com/youtube/v3/playlists',headers=h,
            params={'part':'snippet,contentDetails','mine':'true','maxResults':25}).json()
        result = analyze_youtube(liked, subs, pls)
        a = Analysis(user_id=user.id, source='youtube', data=json.dumps(result))
        db.session.add(a); db.session.commit(); result['analysis_id']=a.id
        return jsonify(result)
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/api/analysis/<int:aid>')
@login_required
def api_get_analysis(aid):
    user = current_user()
    a = Analysis.query.filter_by(id=aid,user_id=user.id).first()
    if not a: return jsonify({'error':'Not found'}),404
    return jsonify(a.get_data())

# ── USER API ──────────────────────────────────────────────────

@app.route('/api/user/update', methods=['POST'])
@login_required
def api_user_update():
    user = current_user(); data = request.get_json()
    if 'username' in data:
        t = User.query.filter_by(username=data['username']).first()
        if t and t.id!=user.id: return jsonify({'error':'Username taken'}),400
        user.username = data['username'].strip()
    if 'email' in data:
        t = User.query.filter_by(email=data['email'].lower()).first()
        if t and t.id!=user.id: return jsonify({'error':'Email in use'}),400
        user.email = data['email'].strip().lower()
    if data.get('new_password'):
        if not user.check_password(data.get('current_password','')): return jsonify({'error':'Wrong current password'}),400
        user.set_password(data['new_password'])
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/user/upgrade', methods=['POST'])
@login_required
def api_user_upgrade():
    user = current_user(); user.tier='premium'; db.session.commit()
    return jsonify({'success':True})

# ── ANALYSIS ENGINES ──────────────────────────────────────────

GENRE_SETS = {
    'reggaeton': {'Bad Bunny','Daddy Yankee','Wisin & Yandel','Ozuna','Tego Calderón','Plan B',
                  'Arcángel','Don Omar','J Balvin','Maluma','Anuel AA','Rauw Alejandro',
                  'Myke Towers','Bryant Myers','De La Ghetto','Zion & Lennox','Tony Dize','Randy'},
    'salsa':     {"Adolescent's Orquesta","Willie Colón & Héctor Lavoe","Frankie Ruiz","Héctor Lavoe",
                  "Willie Colón","El Gran Combo de Puerto Rico","Eddie Santiago","Jerry Rivera",
                  "Tito Rojas","Andy Montañez","Dimensión Latina","Fania All-Stars","Lalo Rodríguez"},
    'bachata':   {'Aventura','Romeo Santos','Anthony Santos','Luis Vargas','Teodoro Reyes'},
    'hiphop':    {'Kendrick Lamar','MF DOOM','Madvillain','Eminem','2Pac','The Notorious B.I.G.',
                  '50 Cent','Slick Rick','Eazy-E','Tyler, The Creator','Lil Uzi Vert',
                  'YoungBoy Never Broke Again','King Von','Nelly'},
    'classic_latin': {'José José','Los Ángeles Negros','Los Ecos','Grover Washington, Jr.'},
}

def detect_genres(top_names):
    scores = {g:0 for g in GENRE_SETS}
    for name in top_names:
        for genre, aset in GENRE_SETS.items():
            if name in aset: scores[genre]+=1
    return sorted(scores.items(), key=lambda x:x[1], reverse=True)

def build_smart_vibe(top_artists, total_songs, total_hours, source):
    if not top_artists: return "Upload your music data to discover your Music DNA."
    top_names = [a[0] for a in top_artists]
    top1=top_names[0]; top2=top_names[1] if len(top_names)>1 else None; top3=top_names[2] if len(top_names)>2 else None
    count=len(top_names); genre_scores=detect_genres(top_names)
    top_genre=genre_scores[0][0] if genre_scores and genre_scores[0][1]>0 else None
    second_genre=genre_scores[1][0] if len(genre_scores)>1 and genre_scores[1][1]>0 else None
    genre_lines={'reggaeton':f"Your playlist is pure Latin heat — {top1} leads the way and the energy never drops.",
        'salsa':f"Old school soul runs deep here. {top1} sets the tone, and this playlist sounds like a Saturday night in the barrio.",
        'bachata':f"Bachata hits different, and you know it. {top1} is your go-to for the feels.",
        'hiphop':f"Bars matter to you. {top1} is your anchor, and this playlist thinks as hard as it hits.",
        'classic_latin':f"You've got taste that goes back. {top1} and the classics give this playlist a timeless weight."}
    genre_line=genre_lines.get(top_genre,f"Your #1 is {top1} — and your taste doesn't fit in a single box.")
    diversity=f"With {count} artists across the playlist, you're a true explorer — nothing stays in one lane for long." if count>=15 else \
              f"You know what you like, but you're not afraid to roam. {count} artists keep things fresh." if count>=8 else \
              f"You go deep, not wide. A tight {count} artists means when you're in, you're really in."
    fusion=""
    if top_genre and second_genre and genre_scores[1][1]>0:
        combos={('reggaeton','salsa'):"The mix of reggaeton and salsa in here is pure Puerto Rico DNA.",
            ('reggaeton','hiphop'):"Trap beats and perreo in the same playlist — this is the modern Latin crossover.",
            ('salsa','bachata'):"Salsa and bachata back to back — you understand both sides of the dance floor.",
            ('hiphop','reggaeton'):"Hip-hop and reggaeton together means you grew up with both coasts.",
            ('salsa','hiphop'):"Salsa and hip-hop in one playlist is a rare combo — this is genuinely unique taste.",
            ('bachata','reggaeton'):"Bachata for the heart, reggaeton for the party — you've got both covered."}
        fusion=combos.get((top_genre,second_genre),combos.get((second_genre,top_genre),""))
    depth=f"A {total_songs}-song playlist is a statement — this isn't background music, this is a whole world." if source in ('apple',) and total_songs>=150 else \
          f"{total_songs} songs deep — you clearly built this with intention." if source in ('apple',) and total_songs>=80 else \
          f"Tight and focused at {total_songs} songs. Every track earns its spot." if source in ('apple',) else \
          "The hours you've put in here are serious. Music isn't background noise — it's the main event." if total_hours>500 else \
          f"{total_hours} hours of listening history. Music is clearly woven into your daily life." if total_hours>200 else \
          "A focused listener. You know exactly what you want and you don't waste time getting there."
    parts=[genre_line]
    if fusion: parts.append(fusion)
    parts.append(diversity); parts.append(depth)
    if top2 and top3: parts.append(f"Beyond {top1}, {top2} and {top3} round out the core of your sound.")
    return " ".join(parts)

def build_upload_result(artist_plays, track_plays, source='upload', total_songs=None):
    top_artists=sorted(artist_plays.items(),key=lambda x:x[1],reverse=True)[:20]
    top_tracks =sorted(track_plays.values(),key=lambda x:x['plays'],reverse=True)[:20]
    total_plays=sum(artist_plays.values()); n_songs=total_songs or len(track_plays)
    top=top_artists[0][0] if top_artists else '—'
    vibe=build_smart_vibe(top_artists,n_songs,0,source)
    is_playlist=source in ('apple',)
    track_out=[{'name':t['name'],'artist':t['artist'],**({} if is_playlist else {'plays':t['plays']})} for t in top_tracks]
    artist_out=[{'name':a,**({} if is_playlist else {'plays':p})} for a,p in top_artists]
    return {'source':source,'top_artists':artist_out,'top_tracks':track_out,
            'total_plays':0 if is_playlist else total_plays,'total_hours':0,
            'total_tracks':n_songs,'top_artist':top,'top_track':top_tracks[0]['name'] if top_tracks else '—',
            'vibe':vibe,'is_playlist':is_playlist,'generated_at':datetime.utcnow().isoformat()}

def analyze_spotify(raw):
    streams=raw if isinstance(raw,list) else raw.get('items',raw.get('streams',[]))
    artist_plays={}; track_plays={}; total_ms=0
    for s in streams:
        artist=s.get('artistName') or s.get('master_metadata_album_artist_name','')
        track =s.get('trackName')  or s.get('master_metadata_track_name','')
        ms    =s.get('msPlayed')   or s.get('ms_played',0)
        if not isinstance(ms,(int,float)): ms=0
        total_ms+=ms
        if artist and ms>10000: artist_plays[artist]=artist_plays.get(artist,0)+1
        if track and artist and ms>10000:
            key=f'{track}||{artist}'
            if key not in track_plays: track_plays[key]={'name':track,'artist':artist,'plays':0}
            track_plays[key]['plays']+=1
    top_artists=sorted(artist_plays.items(),key=lambda x:x[1],reverse=True)[:20]
    top_tracks =sorted(track_plays.values(),key=lambda x:x['plays'],reverse=True)[:20]
    total_hours=round(total_ms/3_600_000,1)
    top=top_artists[0][0] if top_artists else '—'
    vibe=build_smart_vibe(top_artists,len(track_plays),total_hours,'spotify')
    return {'source':'upload','top_artists':[{'name':a,'plays':p} for a,p in top_artists],
            'top_tracks':top_tracks,'total_plays':sum(artist_plays.values()),
            'total_hours':total_hours,'total_tracks':len(track_plays),'top_artist':top,
            'top_track':top_tracks[0]['name'] if top_tracks else '—','vibe':vibe,
            'is_playlist':False,'generated_at':datetime.utcnow().isoformat()}

def analyze_apple_csv(text):
    artist_plays={}; track_plays={}
    reader=csv.DictReader(io.StringIO(text))
    for row in reader:
        artist=(row.get('Artist Name') or row.get('Artist') or row.get('artist_name','') or '').strip()
        track =(row.get('Song Name') or row.get('Track Name') or row.get('Title') or row.get('song_name','') or '').strip()
        try: plays=int((row.get('Play Count') or row.get('Plays') or '1').strip())
        except: plays=1
        if artist: artist_plays[artist]=artist_plays.get(artist,0)+plays
        if artist and track:
            key=f'{track}||{artist}'
            if key not in track_plays: track_plays[key]={'name':track,'artist':artist,'plays':0}
            track_plays[key]['plays']+=plays
    return build_upload_result(artist_plays,track_plays,source='apple')

def analyze_apple_pdf(raw_bytes):
    try: import pdfplumber
    except ImportError: raise Exception('Run: pip install pdfplumber')
    artist_plays=defaultdict(int); track_plays={}; total_hours=0.0
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for page in pdf.pages:
            words=page.extract_words(keep_blank_chars=False,x_tolerance=3,y_tolerance=3)
            lines=defaultdict(list)
            for w in words:
                lines[round(w['top']/4)*4].append(w)
            for y in sorted(lines.keys()):
                rw=sorted(lines[y],key=lambda w:w['x0'])
                texts=[w['text'] for w in rw]; fl=' '.join(texts)
                if re.match(r'^(Main|Page|Title)',fl): continue
                if re.match(r'^\d+ songs',fl): continue
                if not texts or not re.match(r'^\d+$',texts[0]): continue
                title =' '.join(w['text'] for w in rw if w['x0']<240)[len(texts[0]):].strip()
                time  =' '.join(w['text'] for w in rw if 240<=w['x0']<270).strip()
                artist=' '.join(w['text'] for w in rw if w['x0']>=415).strip()
                if artist and title:
                    artist_plays[artist]+=1
                    key=f'{title}||{artist}'
                    if key not in track_plays: track_plays[key]={'name':title,'artist':artist,'plays':0}
                    track_plays[key]['plays']+=1
                if time and ':' in time:
                    try: m,s=time.split(':'); total_hours+=int(m)/60+int(s)/3600
                    except: pass
    if not artist_plays: raise Exception('Could not parse tracks. Make sure it is an Apple Music playlist export.')
    result=build_upload_result(dict(artist_plays),track_plays,source='apple',total_songs=len(track_plays))
    result['total_hours']=round(total_hours,1)
    return result

def analyze_youtube(liked,subs,playlists):
    liked_items=liked.get('items',[]); sub_items=subs.get('items',[])
    ch=defaultdict(int)
    for item in liked_items:
        c=item.get('snippet',{}).get('channelTitle','')
        if c: ch[c]+=1
    top_channels=sorted(ch.items(),key=lambda x:x[1],reverse=True)[:12]
    top_subs=[s['snippet']['title'] for s in sub_items[:12] if s.get('snippet')]
    top_videos=[{'title':i.get('snippet',{}).get('title',''),'channel':i.get('snippet',{}).get('channelTitle',''),
                 'url':f"https://youtube.com/watch?v={i.get('id','')}"}for i in liked_items[:10]]
    total_liked=liked.get('pageInfo',{}).get('totalResults',len(liked_items))
    total_subs=subs.get('pageInfo',{}).get('totalResults',len(sub_items))
    top_channel=top_channels[0][0] if top_channels else '—'
    depth="You've liked a lot of videos — YouTube is central to how you find music." if total_liked>500 else \
          "A solid collection of liked videos shows real taste." if total_liked>100 else \
          "You're selective about what you like — quality over quantity."
    breadth=f"Your most liked channel is {top_channel}. "+("With {total_subs} subscriptions, you cast a wide net." if total_subs>30 else "You keep your subscriptions tight.")
    return {'source':'youtube','top_channels':[{'name':c,'likes':n}for c,n in top_channels],
            'top_subs':top_subs,'top_videos':top_videos,'total_liked':total_liked,
            'total_subs':total_subs,'total_pls':len(playlists.get('items',[])),
            'top_channel':top_channel,'vibe':f"{depth} {breadth}",'generated_at':datetime.utcnow().isoformat()}

# ── INIT ──────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
