import os
import sys
import json
import sqlite3
import requests
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, request, redirect, session, render_template_string, jsonify, send_from_directory, url_for
from urllib.parse import urlencode
import asyncio
import discord
from discord.ext import commands, tasks

# --- CONFIG (NO CHANGES NEEDED) ---
CONFIG = {
    'client_id': os.environ.get('DISCORD_CLIENT_ID'),
    'client_secret': os.environ.get('DISCORD_CLIENT_SECRET'),
    'bot_token': os.environ.get('DISCORD_BOT_TOKEN'),
    'redirect_uri': os.environ.get('DISCORD_REDIRECT_URI', 'https://dashboard.digamber.in/callback'),
    'secret_key': os.environ.get('FLASK_SECRET_KEY'),
    'app_port': int(os.environ.get('PORT', 8080)),
    'app_host': '0.0.0.0'
}

required_vars = ['client_id', 'client_secret', 'bot_token', 'secret_key']
missing = [var for var in required_vars if not CONFIG[var]]
if missing:
    print(f"❌ ERROR: Missing environment variables: {', '.join(missing)}")
    sys.exit(1)

# --- FLASK APP SETUP ---
app = Flask(__name__)
app.secret_key = CONFIG['secret_key']
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.message_content = False
intents.guilds = True
intents.members = True  # REQUIRED for welcome messages

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
bot_ready = False  # Global flag

# --- DATABASE ---
DB_NAME = 'dashboard.db'
UPLOAD_DIR = 'uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            avatar TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            guild_id TEXT,
            channel_id TEXT,
            content TEXT,
            embed_data TEXT,
            files TEXT,
            scheduled_time INTEGER,
            sent_time INTEGER,
            status TEXT DEFAULT 'pending'
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            content TEXT,
            embed_data TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS welcome_config (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT,
            message TEXT,
            embed_data TEXT,
            enabled INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

init_db()

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def update_analytics(messages=0, files=0):
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO analytics (date, messages_sent, files_sent)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            messages_sent = messages_sent + excluded.messages_sent,
            files_sent = files_sent + excluded.files_sent
    ''', (today, messages, files))
    conn.commit()
    conn.close()

def get_analytics():
    today = datetime.now().strftime('%Y-%m-%d')
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT SUM(messages_sent) as total FROM analytics WHERE date = ?', (today,))
    today_msgs = c.fetchone()['total'] or 0
    
    c.execute('SELECT SUM(messages_sent) as total FROM analytics WHERE date >= ?', (week_ago,))
    week_msgs = c.fetchone()['total'] or 0
    
    c.execute('SELECT SUM(messages_sent) as total FROM analytics WHERE date >= ?', (month_ago,))
    month_msgs = c.fetchone()['total'] or 0
    
    c.execute('SELECT SUM(files_sent) as total FROM analytics WHERE date = ?', (today,))
    today_files = c.fetchone()['total'] or 0
    
    conn.close()
    
    return {
        'today': today_msgs,
        'week': week_msgs,
        'month': month_msgs,
        'files_today': today_files
    }

# --- DISCORD OAUTH ---
API_BASE = 'https://discord.com/api/v10'

def generate_oauth_url():
    params = {
        'client_id': CONFIG['client_id'],
        'redirect_uri': CONFIG['redirect_uri'],
        'response_type': 'code',
        'scope': 'identify guilds'
    }
    return f"{API_BASE}/oauth2/authorize?{urlencode(params)}"

def exchange_code(code):
    data = {
        'client_id': CONFIG['client_id'],
        'client_secret': CONFIG['client_secret'],
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': CONFIG['redirect_uri']
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    response = requests.post(f"{API_BASE}/oauth2/token", data=data, headers=headers)
    return response.json()

def get_user_data(token):
    headers = {'Authorization': f'Bearer {token}'}
    return requests.get(f"{API_BASE}/users/@me", headers=headers).json()

def get_user_guilds(token):
    headers = {'Authorization': f'Bearer {token}'}
    return requests.get(f"{API_BASE}/users/@me/guilds", headers=headers).json()

# --- BOT HELPERS (FIXED) ---
async def get_user_mutual_guilds(user_id):
    """Returns guilds where both bot and user are present"""
    global bot_ready
    
    while not bot_ready:
        await asyncio.sleep(1)
    
    user_guilds = []
    for guild in bot.guilds:
        try:
            member = guild.get_member(int(user_id))
            if member:
                user_guilds.append({
                    'id': str(guild.id),
                    'name': guild.name,
                    'icon': str(guild.icon.url) if guild.icon else None
                })
        except:
            continue
    
    return user_guilds

async def get_guild_text_channels(guild_id, user_id):
    """Returns channels where user can send messages"""
    global bot_ready
    
    while not bot_ready:
        await asyncio.sleep(1)
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return []
    
    channels = []
    for channel in guild.text_channels:
        try:
            member = guild.get_member(int(user_id))
            if member and channel.permissions_for(member).send_messages:
                channels.append({
                    'id': str(channel.id),
                    'name': channel.name
                })
        except:
            continue
    
    return channels

async def send_discord_message(channel_id, content, embeds_data=None, files=None):
    """Send message to Discord channel"""
    global bot_ready
    
    while not bot_ready:
        await asyncio.sleep(1)
    
    channel = bot.get_channel(int(channel_id))
    if not channel:
        return False, "Channel not found"
    
    try:
        discord_files = []
        if files:
            for path in files:
                if os.path.exists(path):
                    discord_files.append(discord.File(path))
        
        if embeds_data:
            embeds = []
            for data in embeds_data:
                embed = discord.Embed()
                if data.get('title'): embed.title = data['title']
                if data.get('description'): embed.description = data['description']
                if data.get('color'): 
                    color = data['color'].lstrip('#')
                    embed.color = int(color, 16)
                if data.get('author'): embed.set_author(**data['author'])
                if data.get('fields'):
                    for f in data['fields']:
                        embed.add_field(**f)
                if data.get('thumbnail'): embed.set_thumbnail(url=data['thumbnail'])
                if data.get('image'): embed.set_image(url=data['image'])
                if data.get('footer'): embed.set_footer(**data['footer'])
                if data.get('timestamp'): embed.timestamp = datetime.now()
                embeds.append(embed)
            
            await channel.send(content=content, embeds=embeds, files=discord_files or None)
        else:
            await channel.send(content=content, files=discord_files or None)
        
        update_analytics(messages=1, files=len(discord_files))
        return True, "Sent successfully"
    
    except Exception as e:
        return False, str(e)

# --- SCHEDULED MESSAGES ---
async def process_scheduled():
    """Process pending scheduled messages"""
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        try:
            conn = get_db()
            c = conn.cursor()
            now = int(time.time())
            
            c.execute('SELECT * FROM messages WHERE status = "pending" AND scheduled_time <= ?', (now,))
            
            for msg in c.fetchall():
                channel_ids = json.loads(msg['channel_id'])
                content = msg['content']
                embeds = json.loads(msg['embed_data']) if msg['embed_data'] else None
                files = json.loads(msg['files']) if msg['files'] else None
                
                for channel_id in channel_ids:
                    success, _ = await send_discord_message(channel_id, content, embeds, files)
                    status = 'sent' if success else 'failed'
                    
                    c.execute('UPDATE messages SET status = ?, sent_time = ? WHERE id = ?', 
                             (status, now, msg['id']))
                    conn.commit()
            
            conn.close()
        except Exception as e:
            print(f"Schedule error: {e}")
        
        await asyncio.sleep(30)

# --- WELCOME MESSAGES ---
@bot.event
async def on_member_join(member):
    """Automatically send welcome message when new member joins"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM welcome_config WHERE guild_id = ? AND enabled = 1', (str(member.guild.id),))
        config = c.fetchone()
        conn.close()
        
        if not config:
            return
        
        channel = bot.get_channel(int(config['channel_id']))
        if not channel:
            return
        
        message = config['message'].replace('{user}', f'<@{member.id}>').replace('{username}', member.name).replace('{server}', member.guild.name)
        
        embeds = None
        if config['embed_data']:
            data = json.loads(config['embed_data'])
            if data:
                embed_data = data[0]
                embed = discord.Embed()
                if embed_data.get('title'): embed.title = embed_data['title'].replace('{user}', member.name).replace('{server}', member.guild.name)
                if embed_data.get('description'): embed.description = embed_data['description'].replace('{user}', member.name).replace('{server}', member.guild.name)
                if embed_data.get('color'): 
                    color = embed_data['color'].lstrip('#')
                    embed.color = int(color, 16)
                embeds = [embed]
        
        await channel.send(content=message or None, embeds=embeds)
        
    except Exception as e:
        print(f"Welcome error: {e}")

# --- FLASK ROUTES ---
@app.route('/')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    analytics = get_analytics()
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM templates WHERE user_id = ? ORDER BY id DESC', (session['user_id'],))
    templates = c.fetchall()
    
    c.execute('SELECT * FROM messages WHERE user_id = ? ORDER BY sent_time DESC LIMIT 50', (session['user_id'],))
    history = c.fetchall()
    conn.close()
    
    return render_template_string(HTML_TEMPLATE,
        user_id=session.get('user_id'),
        username=session.get('username'),
        avatar=session.get('avatar'),
        oauth_url=generate_oauth_url(),
        analytics=analytics,
        templates=templates,
        history=history,
        sidebar_collapsed=session.get('sidebar_collapsed', False)
    )

@app.route('/login')
def login():
    return redirect(generate_oauth_url())

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return 'No code provided', 400
    
    token_data = exchange_code(code)
    if 'access_token' not in token_data:
        return f"Token error: {token_data.get('error_description', 'Unknown')}", 400
    
    user_data = get_user_data(token_data['access_token'])
    
    session['user_id'] = int(user_data['id'])
    session['username'] = user_data['username']
    session['avatar'] = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else None
    session.permanent = True
    session.modified = True
    
    conn = get_db()
    c = conn.cursor()
    c.execute('REPLACE INTO users VALUES (?, ?, ?, ?, ?, ?)',
             (session['user_id'], session['username'], session['avatar'], token_data['access_token'], token_data.get('refresh_token'), int(time.time() + token_data.get('expires_in', 604800))))
    conn.commit()
    conn.close()
    
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('dashboard'))

@app.route('/health')
def health():
    return {'status': 'ok', 'bot_ready': bot_ready}, 200

@app.route('/api/guilds')
async def api_guilds():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if not bot_ready:
        return jsonify({'error': 'Bot is initializing...'}), 503
    
    try:
        guilds = await get_user_mutual_guilds(session['user_id'])
        return jsonify(guilds)
    except Exception as e:
        print(f"❌ Guilds error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/channels')
async def api_channels():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'error': 'Missing guild_id'}), 400
    
    if not bot_ready:
        return jsonify({'error': 'Bot is initializing...'}), 503
    
    try:
        channels = await get_guild_text_channels(guild_id, session['user_id'])
        return jsonify(channels)
    except Exception as e:
        print(f"❌ Channels error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/welcome/config', methods=['GET', 'POST'])
async def api_welcome_config():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if request.method == 'GET':
        guild_id = request.args.get('guild_id')
        if not guild_id:
            return jsonify({'error': 'Missing guild_id'}), 400
        
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM welcome_config WHERE guild_id = ?', (guild_id,))
        config = c.fetchone()
        conn.close()
        
        if config:
            return jsonify({
                'channel_id': config['channel_id'],
                'message': config['message'],
                'embeds': json.loads(config['embed_data']) if config['embed_data'] else [],
                'enabled': bool(config['enabled'])
            })
        return jsonify({'message': '', 'channel_id': '', 'enabled': False, 'embeds': []})
    
    elif request.method == 'POST':
        data = request.json
        guild_id = data.get('guild_id')
        channel_id = data.get('channel_id')
        message = data.get('message', '')
        embeds = data.get('embeds', [])
        enabled = data.get('enabled', False)
        
        if not guild_id or not channel_id:
            return jsonify({'error': 'Guild and channel required'}), 400
        
        conn = get_db()
        c = conn.cursor()
        c.execute('REPLACE INTO welcome_config VALUES (?, ?, ?, ?, ?)',
                 (guild_id, channel_id, message, json.dumps(embeds), int(enabled)))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Welcome config saved'})

@app.route('/api/send', methods=['POST'])
async def api_send():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    channel_ids = data.get('channel_ids', [])
    content = data.get('content', '').strip()
    embeds = data.get('embeds', [])
    files = data.get('files', [])
    
    if not channel_ids:
        return jsonify({'error': 'Select channels first'}), 400
    
    if not content and not embeds and not files:
        return jsonify({'error': 'Message cannot be empty'}), 401
    
    if not bot_ready:
        return jsonify({'error': 'Bot is initializing...'}), 503
    
    results = []
    for channel_id in channel_ids:
        success, msg = await send_discord_message(channel_id, content, embeds, files)
        results.append({'channel_id': channel_id, 'success': success, 'message': msg})
    
    # Save to history
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO messages (user_id, channel_id, content, embed_data, files, sent_time, status) VALUES (?, ?, ?, ?, ?, ?, ?)',
             (session['user_id'], json.dumps(channel_ids), content, json.dumps(embeds), json.dumps(files), int(time.time()), 'sent'))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'results': results})

@app.route('/api/files', methods=['POST'])
async def api_files():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if 'files' not in request.files:
        return jsonify({'error': 'No files'}), 400
    
    files = request.files.getlist('files')
    uploaded = []
    
    for file in files:
        if file.filename == '':
            continue
        
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        
        if size > 25 * 1024 * 1024:
            return jsonify({'error': f'{file.filename} > 25MB'}), 400
        
        filename = f"{int(time.time())}_{session['user_id']}_{file.filename}"
        path = os.path.join(UPLOAD_DIR, filename)
        
        await file.save(path)
        
        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT INTO files (user_id, filename, file_path, uploaded_at) VALUES (?, ?, ?, ?)',
                 (session['user_id'], file.filename, path, int(time.time())))
        conn.commit()
        conn.close()
        
        uploaded.append({'filename': file.filename, 'path': path})
    
    return jsonify({'success': True, 'files': uploaded})

@app.route('/api/templates', methods=['GET', 'POST', 'DELETE'])
def api_templates():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    conn = get_db()
    c = conn.cursor()
    
    if request.method == 'GET':
        c.execute('SELECT * FROM templates WHERE user_id = ? ORDER BY id DESC', (session['user_id'],))
        return jsonify([dict(row) for row in c.fetchall()])
    
    elif request.method == 'POST':
        data = request.json
        name = data.get('name')
        content = data.get('content')
        embeds = data.get('embeds', [])
        
        if not name:
            return jsonify({'error': 'Name required'}), 400
        
        c.execute('INSERT INTO templates (user_id, name, content, embed_data) VALUES (?, ?, ?, ?)',
                 (session['user_id'], name, content, json.dumps(embeds)))
        conn.commit()
        return jsonify({'success': True})
    
    elif request.method == 'DELETE':
        template_id = request.args.get('id')
        c.execute('DELETE FROM templates WHERE id = ? AND user_id = ?', (template_id, session['user_id']))
        conn.commit()
        return jsonify({'success': True})
    
    conn.close()

@app.route('/api/analytics', methods=['GET'])
def api_analytics():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    return jsonify(get_analytics())

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# --- HTML TEMPLATE ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Discord Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1e1f29;
            color: #ffffff;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .header {
            background: #2a2b38;
            padding: 15px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 2px solid #5865F2;
        }
        .header-left { display: flex; align-items: center; gap: 15px; }
        .hamburger {
            background: none;
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
        }
        .logo { font-size: 20px; font-weight: bold; color: #5865F2; }
        .user-info { display: flex; align-items: center; gap: 10px; }
        .avatar {
            width: 40px; height: 40px; border-radius: 50%;
            background: #5865F2; display: flex; align-items: center; justify-content: center;
            font-weight: bold;
        }
        .logout-btn {
            background: #5865F2; color: white; border: none;
            padding: 8px 16px; border-radius: 6px; cursor: pointer;
        }
        .container {
            display: flex; flex: 1; overflow: hidden;
        }
        .sidebar {
            width: 280px; background: #2a2b38; padding: 20px;
            overflow-y: auto; border-right: 1px solid #40424e;
            transition: transform 0.3s;
        }
        .sidebar.collapsed { transform: translateX(-280px); }
        .main-content {
            flex: 1; padding: 20px; overflow-y: auto;
            transition: margin-left 0.3s;
        }
        .main-content.expanded { margin-left: -280px; }
        .card {
            background: #2a2b38; border-radius: 8px; padding: 20px;
            margin-bottom: 20px; border: 1px solid #40424e;
        }
        .card h2 {
            color: #5865F2; margin-bottom: 15px; font-size: 18px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .section-title { color: #5865F2; margin-bottom: 15px; font-size: 16px; }
        .server-list, .channel-list {
            display: flex; flex-direction: column; gap: 8px;
        }
        .server-item, .channel-item {
            background: #40424e; padding: 10px; border-radius: 6px;
            cursor: pointer; transition: background 0.2s;
            display: flex; align-items: center; gap: 10px;
        }
        .server-item:hover, .channel-item:hover { background: #5865F2; }
        .server-item.selected, .channel-item.selected { background: #5865F2; }
        .form-group { margin-bottom: 15px; }
        label {
            display: block; margin-bottom: 5px; color: #b9bbbe;
            font-size: 12px; text-transform: uppercase; font-weight: bold;
        }
        input, textarea, select {
            width: 100%; padding: 10px; background: #40424e;
            border: 1px solid #62646e; border-radius: 6px; color: white;
        }
        .char-counter { text-align: right; font-size: 12px; color: #b9bbbe; margin-top: 5px; }
        .file-upload {
            border: 2px dashed #62646e; padding: 30px; text-align: center;
            border-radius: 6px; cursor: pointer;
        }
        .file-upload:hover { border-color: #5865F2; }
        .file-list { margin-top: 15px; display: flex; flex-wrap: wrap; gap: 10px; }
        .file-item {
            background: #40424e; padding: 8px 12px; border-radius: 6px;
            display: flex; align-items: center; gap: 8px;
        }
        .remove-file { color: #ff6b6b; cursor: pointer; font-weight: bold; }
        .btn {
            background: #5865F2; color: white; border: none;
            padding: 12px 24px; border-radius: 6px; cursor: pointer;
            font-weight: bold;
        }
        .btn:hover { background: #4752C4; }
        .btn-secondary { background: #62646e; }
        .btn-danger { background: #ff6b6b; }
        .button-group { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
        .loading {
            display: inline-block; width: 20px; height: 20px;
            border: 3px solid rgba(255,255,255,.3); border-radius: 50%;
            border-top-color: #5865F2; animation: spin 1s ease-in-out infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .toast {
            position: fixed; bottom: 20px; right: 20px; background: #2a2b38;
            color: white; padding: 15px 20px; border-radius: 6px;
            border: 1px solid #40424e; display: none; align-items: center;
            gap: 10px; z-index: 1000;
        }
        .toast.show { display: flex; }
        .toast.success { border-left: 4px solid #4ade80; }
        .toast.error { border-left: 4px solid #ff6b6b; }
        @media (max-width: 768px) {
            .sidebar { width: 100%; height: 300px; position: absolute; z-index: 50; }
            .sidebar.collapsed { transform: translateY(-300px); }
        }
    </style>
</head>
<body>
    {% if user_id %}
    <div class="header">
        <div class="header-left">
            <button class="hamburger" onclick="toggleSidebar()">☰</button>
            <div class="logo">Discord Dashboard</div>
        </div>
        <div class="user-info">
            {% if avatar %}<img src="{{ avatar }}" class="avatar">{% else %}<div class="avatar">{{ username[0] }}</div>{% endif %}
            <span>{{ username }}</span>
            <button class="logout-btn" onclick="logout()">Logout</button>
        </div>
    </div>

    <div class="container">
        <div class="sidebar {% if sidebar_collapsed %}collapsed{% endif %}" id="sidebar">
            <div class="card">
                <h2>Servers <span id="serverCount" style="font-size: 12px; color: #b9bbbe;">(0)</span></h2>
                <div class="server-list" id="serverList">
                    <div class="loading" style="margin: 20px auto;"></div>
                </div>
            </div>

            <div class="card">
                <h2>Channels</h2>
                <div class="channel-list" id="channelList">
                    <i style="color: #b9bbbe;">Select a server</i>
                </div>
            </div>

            <div class="card" id="welcomeCard" style="display: none;">
                <h2>Welcome Setup</h2>
                <div class="form-group">
                    <label>Channel</label>
                    <select id="welcomeChannel">
                        <option value="">Choose channel...</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Message (use {user}, {server})</label>
                    <textarea id="welcomeMsg" rows="2">Welcome {user} to {server}!</textarea>
                </div>
                <div class="checkbox-group" style="margin-bottom: 15px;">
                    <input type="checkbox" id="welcomeEnabled"> <span>Enable auto-welcome</span>
                </div>
                <button class="btn" onclick="saveWelcome()" style="width: 100%; padding: 8px;">Save</button>
            </div>
        </div>

        <div class="main-content {% if sidebar_collapsed %}expanded{% endif %}" id="mainContent">
            <div class="card">
                <h2>Message Composer</h2>
                <div class="form-group">
                    <label>Message Content</label>
                    <textarea id="messageContent" rows="4"></textarea>
                    <div class="char-counter" id="charCounter">0 / 2000</div>
                </div>

                <div class="section-title">Embeds</div>
                <button class="btn btn-secondary" onclick="addEmbed()" style="margin-bottom: 15px;">+ Add Embed</button>
                <div id="embedList"></div>

                <div class="section-title">Files</div>
                <div class="file-upload" onclick="document.getElementById('fileInput').click()">
                    <p>📎 Click or drag files here (Max 25MB each)</p>
                </div>
                <input type="file" id="fileInput" multiple accept="*" style="display: none;">
                <div class="file-list" id="fileList"></div>

                <div class="button-group">
                    <button class="btn" onclick="sendMessage()">Send Now</button>
                    <button class="btn btn-secondary" onclick="scheduleMessage()">Schedule</button>
                    <button class="btn btn-secondary" onclick="saveTemplate()">Save Template</button>
                </div>
            </div>

            <div class="card">
                <h2>Saved Templates</h2>
                <div id="templateList">
                    {% for t in templates %}
                    <div class="template-item">
                        <span>{{ t.name }}</span>
                        <div>
                            <button class="btn btn-secondary" onclick="loadTemplate({{ t.id }})">Load</button>
                            <button class="btn btn-danger" onclick="deleteTemplate({{ t.id }})">Delete</button>
                        </div>
                    </div>
                    {% else %}
                    <i style="color: #b9bbbe;">No templates yet</i>
                    {% endfor %}
                </div>
            </div>

            <div class="card">
                <h2>Message History</h2>
                <div id="historyList">
                    {% for h in history %}
                    <div class="history-item">
                        <div>
                            <strong>{{ h.sent_time|default('Scheduled', true) }}</strong><br>
                            <small>{{ h.content[:60] }}{% if h.content|length > 60 %}...{% endif %}</small>
                        </div>
                        <button class="btn btn-secondary" onclick="resend({{ h.id }})">Resend</button>
                    </div>
                    {% else %}
                    <i style="color: #b9bbbe;">No messages yet</i>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div style="position: fixed; bottom: 0; left: 0; right: 0; background: #2a2b38; border-top: 1px solid #40424e; padding: 10px 20px; display: flex; justify-content: space-around;">
            <div class="stat-item"><div class="stat-value">{{ analytics.today }}</div><div class="stat-label">Today</div></div>
            <div class="stat-item"><div class="stat-value">{{ analytics.week }}</div><div class="stat-label">Week</div></div>
            <div class="stat-item"><div class="stat-value">{{ analytics.month }}</div><div class="stat-label">Month</div></div>
            <div class="stat-item"><div class="stat-value">{{ analytics.files_today }}</div><div class="stat-label">Files</div></div>
        </div>
    </div>

    <div id="toast" class="toast"></div>

    {% else %}
    <div style="display: flex; justify-content: center; align-items: center; height: 100vh;">
        <div class="card" style="text-align: center; max-width: 400px;">
            <h1 style="color: #5865F2;">Discord Dashboard</h1>
            <p style="margin: 20px 0; color: #b9bbbe;">Professional Discord message management</p>
            <a href="{{ oauth_url }}" class="btn" style="display: block; text-decoration: none;">Login with Discord</a>
        </div>
    </div>
    {% endif %}

    <script>
        let servers = [];
        let selectedServer = null;
        let selectedChannels = [];
        let uploadedFiles = [];
        let embeds = [];

        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('collapsed');
            document.getElementById('mainContent').classList.toggle('expanded');
        }

        document.addEventListener('DOMContentLoaded', async () => {
            if ({{ 'true' if user_id else 'false' }}) {
                await loadServers();
                initFileUpload();
                initCharCounter();
            }
        });

        async function loadServers() {
            const container = document.getElementById('serverList');
            container.innerHTML = '<div class="loading" style="margin: 20px auto;"></div>';
            
            try {
                const res = await fetch('/api/guilds');
                const data = await res.json();
                
                if (!res.ok) throw new Error(data.error || 'Failed to load');
                
                servers = data;
                document.getElementById('serverCount').textContent = `(${data.length})`;
                
                container.innerHTML = data.map(s => `
                    <div class="server-item" onclick="selectServer('${s.id}', this)">
                        ${s.icon ? `<img src="${s.icon}" style="width: 32px; height: 32px; border-radius: 50%;">` : `<div style="width: 32px; height: 32px; background: #5865F2; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold;">${s.name[0]}</div>`}
                        <span>${s.name}</span>
                    </div>
                `).join('');
            } catch (e) {
                container.innerHTML = `<div style="color: #ff6b6b;">❌ ${e.message}</div>`;
            }
        }

        async function selectServer(serverId, el) {
            document.querySelectorAll('.server-item').forEach(i => i.classList.remove('selected'));
            el.classList.add('selected');
            selectedServer = serverId;
            selectedChannels = [];
            
            // Load channels
            const container = document.getElementById('channelList');
            container.innerHTML = '<div class="loading" style="margin: 20px auto;"></div>';
            
            try {
                const res = await fetch(`/api/channels?guild_id=${serverId}`);
                const data = await res.json();
                
                if (!res.ok) throw new Error(data.error || 'Failed to load channels');
                
                container.innerHTML = data.map(c => `
                    <div class="channel-item" onclick="selectChannel('${c.id}', this)">#${c.name}</div>
                `).join('');
                
                // Load welcome config for this server
                await loadWelcomeConfig(serverId);
                document.getElementById('welcomeCard').style.display = 'block';
                
            } catch (e) {
                container.innerHTML = `<div style="color: #ff6b6b;">❌ ${e.message}</div>`;
            }
        }

        function selectChannel(channelId, el) {
            el.classList.toggle('selected');
            if (el.classList.contains('selected')) {
                if (!selectedChannels.includes(channelId)) selectedChannels.push(channelId);
            } else {
                selectedChannels = selectedChannels.filter(id => id !== channelId);
            }
        }

        async function loadWelcomeConfig(guildId) {
            try {
                const res = await fetch(`/api/welcome/config?guild_id=${guildId}`);
                const data = await res.json();
                
                document.getElementById('welcomeChannel').value = data.channel_id || '';
                document.getElementById('welcomeMsg').value = data.message || 'Welcome {user} to {server}!';
                document.getElementById('welcomeEnabled').checked = data.enabled || false;
            } catch {}
        }

        async function saveWelcome() {
            if (!selectedServer) return showToast('Select server first', 'error');
            
            const data = {
                guild_id: selectedServer,
                channel_id: document.getElementById('welcomeChannel').value,
                message: document.getElementById('welcomeMsg').value,
                embeds: [],
                enabled: document.getElementById('welcomeEnabled').checked
            };
            
            const res = await fetch('/api/welcome/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            
            const result = await res.json();
            showToast(result.message || 'Saved!', res.ok ? 'success' : 'error');
        }

        function initCharCounter() {
            const ta = document.getElementById('messageContent');
            const counter = document.getElementById('charCounter');
            ta.addEventListener('input', () => {
                counter.textContent = `${ta.value.length} / 2000`;
                counter.style.color = ta.value.length > 2000 ? '#ff6b6b' : '#b9bbbe';
            });
        }

        function initFileUpload() {
            const zone = document.querySelector('.file-upload');
            const input = document.getElementById('fileInput');
            
            zone.addEventListener('dragover', e => { e.preventDefault(); zone.style.borderColor = '#5865F2'; });
            zone.addEventListener('dragleave', () => zone.style.borderColor = '#62646e');
            zone.addEventListener('drop', e => { e.preventDefault(); zone.style.borderColor = '#62646e'; uploadFiles(Array.from(e.dataTransfer.files)); });
            input.addEventListener('change', e => uploadFiles(Array.from(e.target.files)));
        }

        async function uploadFiles(files) {
            const form = new FormData();
            files.forEach(f => form.append('files', f));
            
            const res = await fetch('/api/files', { method: 'POST', body: form });
            const data = await res.json();
            
            if (data.success) {
                uploadedFiles.push(...data.files);
                renderFiles();
                showToast('Files uploaded', 'success');
            } else {
                showToast(data.error, 'error');
            }
        }

        function renderFiles() {
            const container = document.getElementById('fileList');
            container.innerHTML = uploadedFiles.map(f => `
                <div class="file-item">
                    <span>${f.filename}</span>
                    <span class="remove-file" onclick="removeFile('${f.path}')">×</span>
                </div>
            `).join('');
        }

        function removeFile(path) {
            uploadedFiles = uploadedFiles.filter(f => f.path !== path);
            renderFiles();
        }

        function addEmbed() {
            if (embeds.length >= 10) return showToast('Max 10 embeds', 'error');
            embeds.push({ title: '', description: '', color: '#5865F2', author: { name: '', url: '', icon_url: '' }, fields: [], thumbnail: '', image: '', footer: { text: '' }, timestamp: false });
            renderEmbeds();
        }

        function removeEmbed(i) {
            embeds.splice(i, 1);
            renderEmbeds();
        }

        function addField(i) {
            embeds[i].fields.push({ name: '', value: '', inline: true });
            renderEmbeds();
        }

        function removeField(i, fi) {
            embeds[i].fields.splice(fi, 1);
            renderEmbeds();
        }

        function renderEmbeds() {
            const container = document.getElementById('embedList');
            container.innerHTML = embeds.map((e, i) => `
                <div class="card">
                    <h2>Embed #${i + 1} <button class="btn btn-danger" onclick="removeEmbed(${i})" style="padding: 4px 8px;">Remove</button></h2>
                    <div class="form-group"><input type="text" placeholder="Title" value="${e.title}" onchange="embeds[${i}].title = this.value"></div>
                    <div class="form-group"><textarea rows="3" placeholder="Description" onchange="embeds[${i}].description = this.value">${e.description}</textarea></div>
                    <div class="form-group"><input type="color" value="${e.color}" onchange="embeds[${i}].color = this.value"> Color</div>
                    <div class="section-title">Fields <button class="btn btn-secondary" onclick="addField(${i})" style="padding: 4px 8px;">+</button></div>
                    ${e.fields.map((f, fi) => `
                        <div class="field-item">
                            <div style="display: flex; justify-content: space-between;">
                                <strong>Field #${fi + 1}</strong>
                                <button class="btn btn-danger" onclick="removeField(${i}, ${fi})" style="padding: 2px 6px;">×</button>
                            </div>
                            <input type="text" placeholder="Name" value="${f.name}" onchange="embeds[${i}].fields[${fi}].name = this.value">
                            <input type="text" placeholder="Value" value="${f.value}" onchange="embeds[${i}].fields[${fi}].value = this.value" style="margin-top: 5px;">
                            <label><input type="checkbox" ${f.inline ? 'checked' : ''} onchange="embeds[${i}].fields[${fi}].inline = this.checked"> Inline</label>
                        </div>
                    `).join('')}
                </div>
            `).join('');
        }

        async function sendMessage() {
            const content = document.getElementById('messageContent').value;
            
            if (selectedChannels.length === 0) return showToast('Select channels', 'error');
            if (!content && embeds.length === 0 && uploadedFiles.length === 0) return showToast('Message empty', 'error');
            
            const data = {
                channel_ids: selectedChannels,
                content: content,
                embeds: embeds,
                files: uploadedFiles.map(f => f.path)
            };
            
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            
            const result = await res.json();
            showToast(result.success ? 'Message sent!' : result.error || 'Failed', result.success ? 'success' : 'error');
            
            if (result.success) {
                document.getElementById('messageContent').value = '';
                embeds = []; uploadedFiles = [];
                renderEmbeds(); renderFiles();
            }
        }

        function scheduleMessage() {
            const now = Math.floor(Date.now() / 1000);
            const time = prompt(`Schedule time (seconds since epoch)\\nCurrent: ${now}\\n5 min from now: ${now + 300}`);
            if (!time) return;
            
            const data = {
                channel_ids: selectedChannels,
                content: document.getElementById('messageContent').value,
                embeds: embeds,
                files: uploadedFiles.map(f => f.path),
                scheduled_time: parseInt(time)
            };
            
            fetch('/api/schedule', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            }).then(r => r.json()).then(d => showToast(d.success ? 'Scheduled!' : d.error || 'Failed', d.success ? 'success' : 'error'));
        }

        async function saveTemplate() {
            const name = prompt('Template name:');
            if (!name) return;
            
            const data = {
                name: name,
                content: document.getElementById('messageContent').value,
                embeds: embeds
            };
            
            const res = await fetch('/api/templates', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            
            const result = await res.json();
            showToast(result.success ? 'Template saved!' : result.error || 'Failed', result.success ? 'success' : 'error');
            if (result.success) location.reload();
        }

        async function loadTemplate(id) {
            const res = await fetch('/api/templates');
            const templates = await res.json();
            const t = templates.find(x => x.id === id);
            if (t) {
                document.getElementById('messageContent').value = t.content;
                embeds = JSON.parse(t.embed_data) || [];
                renderEmbeds();
                showToast('Template loaded', 'success');
            }
        }

        async function deleteTemplate(id) {
            if (!confirm('Delete?')) return;
            const res = await fetch(`/api/templates?id=${id}`, { method: 'DELETE' });
            const result = await res.json();
            showToast(result.success ? 'Deleted' : 'Failed', result.success ? 'success' : 'error');
            if (result.success) location.reload();
        }

        function logout() {
            window.location.href = '/logout';
        }

        function showToast(msg, type = 'success') {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.className = `toast ${type} show`;
            setTimeout(() => toast.classList.remove('show'), 3000);
        }
    </script>
</body>
</html>
'''

@bot.event
async def on_ready():
    global bot_ready
    bot_ready = True
    print(f"✅ Bot ready: {bot.user}")
    bot.loop.create_task(process_scheduled())

def run_bot():
    try:
        bot.run(CONFIG['bot_token'])
    except Exception as e:
        print(f"❌ Bot error: {e}")

def run_flask():
    app.run(host=CONFIG['app_host'], port=CONFIG['app_port'], debug=False)

if __name__ == '__main__':
    threading.Thread(target=run_bot, daemon=True).start()
    run_flask()
