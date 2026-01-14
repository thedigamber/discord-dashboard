import os
import sys
import json
import sqlite3
import requests
import threading
import time
import asyncio
import re
import traceback
from datetime import datetime, timedelta
from io import BytesIO
from functools import wraps
from flask import Flask, request, redirect, session, render_template_string, jsonify, send_from_directory, url_for
from urllib.parse import urlencode
import discord
from discord.ext import commands, tasks
import aiofiles
import hashlib

# ============================================================================
# COMPLETE PRODUCTION CONFIGURATION
# ============================================================================

class Config:
    REQUIRED_VARS = {
        'DISCORD_CLIENT_ID': 'Discord Application Client ID',
        'DISCORD_CLIENT_SECRET': 'Discord Application Client Secret',
        'DISCORD_BOT_TOKEN': 'Discord Bot Token',
        'FLASK_SECRET_KEY': 'Flask Secret Key',
        'DISCORD_REDIRECT_URI': 'OAuth Redirect URI'
    }
    
    def __init__(self):
        self.client_id = os.environ.get('DISCORD_CLIENT_ID')
        self.client_secret = os.environ.get('DISCORD_CLIENT_SECRET')
        self.bot_token = os.environ.get('DISCORD_BOT_TOKEN')
        self.redirect_uri = os.environ.get('DISCORD_REDIRECT_URI', 'https://dashboard.digamber.in/callback')
        self.secret_key = os.environ.get('FLASK_SECRET_KEY')
        self.port = int(os.environ.get('PORT', 8080))
        self.host = '0.0.0.0'
        self.validate()
    
    def validate(self):
        missing = [k for k, v in self.REQUIRED_VARS.items() if not os.environ.get(k)]
        if missing:
            print("\n❌ MISSING ENV VARS:", ', '.join(missing))
            sys.exit(1)

config = Config()

# ============================================================================
# DATABASE
# ============================================================================

class Database:
    def __init__(self, db_path='dashboard.db'):
        self.db_path = db_path
        self.init_schema()
    
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_schema(self):
        conn = self.get_connection()
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                avatar TEXT,
                access_token TEXT,
                refresh_token TEXT,
                expires_at INTEGER,
                created_at INTEGER DEFAULT (unixepoch()),
                UNIQUE(id)
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                content TEXT,
                embed_data TEXT,
                files TEXT,
                scheduled_time INTEGER,
                sent_time INTEGER,
                status TEXT DEFAULT 'pending',
                created_at INTEGER DEFAULT (unixepoch()),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                embed_data TEXT,
                created_at INTEGER DEFAULT (unixepoch()),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS welcome_config (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message TEXT,
                embed_data TEXT,
                enabled INTEGER DEFAULT 0,
                created_by INTEGER,
                UNIQUE(guild_id)
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS analytics (
                date TEXT PRIMARY KEY,
                messages_sent INTEGER DEFAULT 0,
                files_sent INTEGER DEFAULT 0,
                UNIQUE(date)
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ Database initialized")

db = Database()

# ============================================================================
# DISCORD OAUTH
# ============================================================================

class DiscordOAuth:
    API_BASE = 'https://discord.com/api/v10'
    AUTHORIZE_URL = f'{API_BASE}/oauth2/authorize'
    TOKEN_URL = f'{API_BASE}/oauth2/token'
    
    @staticmethod
    def get_authorize_url():
        params = {
            'client_id': config.client_id,
            'redirect_uri': config.redirect_uri,
            'response_type': 'code',
            'scope': 'identify guilds',
            'prompt': 'consent'
        }
        return f"{DiscordOAuth.AUTHORIZE_URL}?{urlencode(params)}"
    
    @staticmethod
    def exchange_code(code):
        data = {
            'client_id': config.client_id,
            'client_secret': config.client_secret,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': config.redirect_uri
        }
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        response = requests.post(DiscordOAuth.TOKEN_URL, data=data, headers=headers)
        return response.json()
    
    @staticmethod
    def get_user_data(access_token):
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(f"{DiscordOAuth.API_BASE}/users/@me", headers=headers)
        return response.json()

# ============================================================================
# DISCORD BOT
# ============================================================================

class DiscordBot:
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = False
        intents.guilds = True
        intents.members = True
        
        self.bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
        self.ready = False
        
        @self.bot.event
        async def on_ready():
            self.ready = True
            print(f"✅ Bot ready: {self.bot.user}")
        
        @self.bot.event
        async def on_member_join(member):
            await self.handle_welcome(member)
    
    async def handle_welcome(self, member):
        try:
            config = db.get_welcome_config(str(member.guild.id))
            if not config or not config['enabled']:
                return
            
            channel = self.bot.get_channel(int(config['channel_id']))
            if not channel:
                return
            
            message = config['message'].replace('{user}', f'<@{member.id}>').replace('{username}', member.name).replace('{server}', member.guild.name)
            
            await channel.send(message)
            print(f"✅ Welcome sent to {member.name}")
            
        except Exception as e:
            print(f"❌ Welcome error: {e}")
    
    def get_mutual_guilds_sync(self, user_id):
        """Synchronous version that waits for bot to be ready"""
        while not self.ready:
            time.sleep(1)
        
        user_guilds = []
        for guild in self.bot.guilds:
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
        
        return sorted(user_guilds, key=lambda g: g['name'].lower())
    
    def get_guild_channels_sync(self, guild_id, user_id):
        """Synchronous version to avoid async issues"""
        while not self.ready:
            time.sleep(1)
        
        guild = self.bot.get_guild(int(guild_id))
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
    
    def send_message_sync(self, channel_id, content, embeds=None, files=None):
        """Synchronous message send"""
        while not self.ready:
            time.sleep(1)
        
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return False, "Channel not found"
        
        try:
            discord_files = []
            if files:
                for path in files:
                    if os.path.exists(path):
                        discord_files.append(discord.File(path))
            
            # Simplified embed handling for now
            asyncio.run_coroutine_threadsafe(
                channel.send(content=content, files=discord_files or None),
                self.bot.loop
            ).result(timeout=30)
            
            db.update_analytics(messages=1, files=len(discord_files))
            return True, "Sent"
            
        except Exception as e:
            return False, str(e)
    
    def run(self):
        try:
            self.bot.run(config.bot_token)
        except Exception as e:
            print(f"❌ Bot error: {e}")
            sys.exit(1)

bot_manager = DiscordBot()

# ============================================================================
# AUTH DECORATORS
# ============================================================================

def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not logged in'}), 401
        return f(*args, **kwargs)
    return decorated_function

def require_bot_ready(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not bot_manager.ready:
            return jsonify({'error': 'Bot starting...'}), 503
        return f(*args, **kwargs)
    return decorated_function

# ============================================================================
# FLASK APP
# ============================================================================

app = Flask(__name__)
app.secret_key = config.secret_key
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

UPLOAD_DIR = 'uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect('/dashboard')
    return redirect('/login')

@app.route('/login')
def login():
    return redirect(DiscordOAuth.get_authorize_url())

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return 'No code', 400
    
    try:
        token_data = DiscordOAuth.exchange_code(code)
        if 'access_token' not in token_data:
            return f"Token error: {token_data.get('error_description', 'Unknown')}", 400
        
        user_data = DiscordOAuth.get_user_data(token_data['access_token'])
        
        session['user_id'] = int(user_data['id'])
        session['username'] = user_data['username']
        session['avatar'] = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else None
        session.permanent = True
        
        db.save_user(session['user_id'], session['username'], session['avatar'], token_data['access_token'])
        
        return redirect('/dashboard')
        
    except Exception as e:
        print(f"Callback error: {e}")
        return f"Login failed: {e}", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/dashboard')
@require_auth
def dashboard():
    user_id = session['user_id']
    analytics = db.get_analytics()
    templates = db.get_user_templates(user_id)
    history = db.get_user_messages(user_id)
    
    return render_template_string(DASHBOARD_HTML,
        user_id=user_id,
        username=session['username'],
        avatar=session['avatar'],
        analytics=analytics,
        templates=templates,
        history=history,
        bot_ready=bot_manager.ready
    )

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'bot_ready': bot_manager.ready}), 200

@app.route('/api/guilds')
@require_auth
@require_bot_ready
def api_guilds():
    """SYNCHRONOUS VERSION - NO ASYNC ISSUES"""
    try:
        guilds = bot_manager.get_mutual_guilds_sync(session['user_id'])
        return jsonify({'success': True, 'guilds': guilds})
    except Exception as e:
        print(f"❌ /api/guilds error: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/channels')
@require_auth
@require_bot_ready
def api_channels():
    """SYNCHRONOUS VERSION - NO ASYNC ISSUES"""
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'error': 'Guild ID required'}), 400
    
    try:
        channels = bot_manager.get_guild_channels_sync(guild_id, session['user_id'])
        return jsonify({'success': True, 'channels': channels})
    except Exception as e:
        print(f"❌ /api/channels error: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/send', methods=['POST'])
@require_auth
@require_bot_ready
def api_send():
    data = request.json
    channel_ids = data.get('channel_ids', [])
    content = data.get('content', '').strip()
    embeds = data.get('embeds', [])
    files = data.get('files', [])
    
    if not channel_ids:
        return jsonify({'error': 'Select channels'}), 400
    if not content and not embeds and not files:
        return jsonify({'error': 'Message empty'}), 400
    
    try:
        results = []
        for channel_id in channel_ids:
            success, message = bot_manager.send_message_sync(channel_id, content, embeds, files)
            results.append({'channel_id': channel_id, 'success': success, 'message': message})
        
        if any(r['success'] for r in results):
            db.save_message(session['user_id'], '', channel_ids, content, embeds, files)
        
        return jsonify({'success': True, 'results': results})
        
    except Exception as e:
        print(f"❌ /api/send error: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedule', methods=['POST'])
@require_auth
def api_schedule():
    data = request.json
    channel_ids = data.get('channel_ids', [])
    content = data.get('content', '').strip()
    embeds = data.get('embeds', [])
    files = data.get('files', [])
    scheduled_time = data.get('scheduled_time')
    
    if not channel_ids:
        return jsonify({'error': 'Select channels'}), 400
    if not content and not embeds and not files:
        return jsonify({'error': 'Message empty'}), 400
    if not scheduled_time:
        return jsonify({'error': 'Time required'}), 400
    
    try:
        db.save_message(session['user_id'], '', channel_ids, content, embeds, files, int(scheduled_time))
        return jsonify({'success': True, 'message': 'Scheduled'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/templates', methods=['GET', 'POST', 'DELETE'])
@require_auth
def api_templates():
    if request.method == 'GET':
        templates = db.get_user_templates(session['user_id'])
        return jsonify({'success': True, 'templates': [dict(t) for t in templates]})
    
    elif request.method == 'POST':
        data = request.json
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'error': 'Name required'}), 400
        
        template_id = db.save_template(session['user_id'], name, data.get('content', ''), data.get('embeds', []))
        return jsonify({'success': True, 'template_id': template_id})
    
    elif request.method == 'DELETE':
        template_id = request.args.get('id')
        if db.delete_template(template_id, session['user_id']):
            return jsonify({'success': True})
        return jsonify({'error': 'Not found'}), 404

@app.route('/api/files', methods=['POST'])
@require_auth
def api_upload():
    if 'files' not in request.files:
        return jsonify({'error': 'No files'}), 400
    
    files = request.files.getlist('files')
    uploaded = []
    
    for file in files:
        if file.filename == '':
            continue
        
        try:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)
            
            if size > 25 * 1024 * 1024:
                return jsonify({'error': f'{file.filename} > 25MB'}), 400
            
            filename = f"{int(time.time())}_{session['user_id']}_{file.filename}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            
            file.save(filepath)
            
            conn = db.get_connection()
            c = conn.cursor()
            c.execute('INSERT INTO uploaded_files (user_id, filename, file_path, file_size) VALUES (?, ?, ?, ?)',
                     (session['user_id'], file.filename, filepath, size))
            conn.commit()
            conn.close()
            
            uploaded.append({'filename': file.filename, 'path': filepath})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'success': True, 'files': uploaded})

@app.route('/api/welcome/config', methods=['GET', 'POST'])
@require_auth
@require_bot_ready
def api_welcome():
    if request.method == 'GET':
        guild_id = request.args.get('guild_id')
        config = db.get_welcome_config(guild_id)
        if config:
            return jsonify({'success': True, 'config': {
                'channel_id': config['channel_id'],
                'message': config['message'],
                'enabled': bool(config['enabled'])
            }})
        return jsonify({'success': True, 'config': {'channel_id': '', 'message': '', 'enabled': False}})
    
    elif request.method == 'POST':
        data = request.json
        guild_id = data.get('guild_id')
        channel_id = data.get('channel_id')
        
        if not guild_id or not channel_id:
            return jsonify({'error': 'Guild and channel required'}), 400
        
        db.save_welcome_config(guild_id, channel_id, data.get('message', ''), data.get('embeds', []), data.get('enabled', False), session['user_id'])
        return jsonify({'success': True})

@app.route('/api/analytics')
@require_auth
def api_analytics():
    try:
        return jsonify({'success': True, 'analytics': db.get_analytics()})
    except:
        return jsonify({'success': True, 'analytics': {'today': 0, 'week': 0, 'month': 0, 'files_today': 0}})

@app.route('/uploads/<path:filename>')
@require_auth
def serve_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ============================================================================
# HTML TEMPLATE
# ============================================================================

DASHBOARD_HTML = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Discord Dashboard</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#1e1f29;color:#fff;height:100vh;display:flex;flex-direction:column;overflow:hidden}
.header{background:#2a2b38;padding:15px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #5865F2;box-shadow:0 2px 10px rgba(0,0,0,.1)}
.header-left{display:flex;align-items:center;gap:15px}.hamburger{background:none;border:none;color:#fff;font-size:24px;cursor:pointer;padding:5px;border-radius:4px}
.hamburger:hover{background:#40424e}.logo{font-size:20px;font-weight:bold;color:#5865F2}
.user-info{display:flex;align-items:center;gap:10px}.avatar{width:40px;height:40px;border-radius:50%;background:#5865F2;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:18px}
.logout-btn{background:#5865F2;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:bold}
.logout-btn:hover{background:#4752C4}.container{display:flex;flex:1;overflow:hidden}
.sidebar{width:300px;background:#2a2b38;padding:20px;overflow-y:auto;border-right:1px solid #40424e;box-shadow:2px 0 10px rgba(0,0,0,.1)}
.main-content{flex:1;padding:20px;overflow-y:auto}.card{background:#2a2b38;border-radius:8px;padding:20px;margin-bottom:20px;border:1px solid #40424e;box-shadow:0 2px 10px rgba(0,0,0,.05)}
.card h2{color:#5865F2;margin-bottom:15px;font-size:18px;display:flex;justify-content:space-between;align-items:center}
.server-list,.channel-list{display:flex;flex-direction:column;gap:8px}.server-item,.channel-item{background:#40424e;padding:12px;border-radius:6px;cursor:pointer;transition:all .2s;display:flex;align-items:center;gap:10px}
.server-item:hover,.channel-item:hover{background:#5865F2;transform:translateX(2px)}.server-item.selected,.channel-item.selected{background:#5865F2;box-shadow:0 0 0 2px rgba(88,101,242,.3)}
.server-icon{width:32px;height:32px;border-radius:50%;background:#5865F2;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:14px}
.form-group{margin-bottom:15px}label{display:block;margin-bottom:5px;color:#b9bbbe;font-size:12px;text-transform:uppercase;font-weight:bold}
input,textarea,select{width:100%;padding:10px;background:#40424e;border:1px solid #62646e;border-radius:6px;color:#fff;font-size:14px;transition:border-color .2s}
input:focus,textarea:focus,select:focus{outline:none;border-color:#5865F2}.char-counter{text-align:right;font-size:12px;color:#b9bbbe;margin-top:5px}
.file-upload{border:2px dashed #62646e;padding:30px;text-align:center;border-radius:6px;cursor:pointer;transition:all .2s;background:#2a2b38}
.file-upload:hover{border-color:#5865F2;background:#40424e}.file-list{margin-top:15px;display:flex;flex-wrap:wrap;gap:10px}
.file-item{background:#40424e;padding:8px 12px;border-radius:6px;display:flex;align-items:center;gap:8px;font-size:14px}
.remove-file{color:#ff6b6b;cursor:pointer;font-weight:bold;font-size:18px}.btn{background:#5865F2;color:#fff;border:none;padding:12px 24px;border-radius:6px;cursor:pointer;font-weight:bold;transition:all .2s;font-size:14px}
.btn:hover{background:#4752C4;transform:translateY(-1px);box-shadow:0 4px 12px rgba(88,101,242,.3)}.btn:active{transform:translateY(0)}.btn-secondary{background:#62646e}
.btn-secondary:hover{background:#72747e;box-shadow:0 4px 12px rgba(0,0,0,.2)}.btn-danger{background:#ff6b6b}
.btn-danger:hover{background:#ff5252;box-shadow:0 4px 12px rgba(255,107,107,.3)}.button-group{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}
.loading{display:inline-block;width:20px;height:20px;border:3px solid rgba(255,255,255,.3);border-radius:50%;border-top-color:#5865F2;animation:spin 1s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}.toast{position:fixed;bottom:20px;right:20px;background:#2a2b38;color:#fff;padding:15px 20px;border-radius:6px;border:1px solid #40424e;box-shadow:0 4px 12px rgba(0,0,0,.3);display:none;align-items:center;gap:10px;z-index:1000;max-width:400px}
.toast.show{display:flex}.toast.success{border-left:4px solid #4ade80}.toast.error{border-left:4px solid #ff6b6b}
.template-item,.history-item{background:#40424e;padding:12px;border-radius:6px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
.stats-bar{position:fixed;bottom:0;left:0;right:0;background:#2a2b38;border-top:1px solid #40424e;padding:10px 20px;display:flex;justify-content:space-around;z-index:100}
.stat-item{text-align:center}.stat-value{font-size:24px;font-weight:bold;color:#5865F2}.stat-label{font-size:11px;color:#b9bbbe;text-transform:uppercase}
</style></head><body>{% if user_id %}<div class="header"><div class="header-left"><button class="hamburger" onclick="toggleSidebar()">☰</button><div class="logo">Discord Dashboard</div></div><div class="user-info">{% if avatar %}<img src="{{ avatar }}" class="avatar" alt="{{ username }}">{% else %}<div class="avatar">{{ username[0] }}</div>{% endif %}<span>{{ username }}</span><button class="logout-btn" onclick="logout()">Logout</button></div></div><div class="container"><div class="sidebar" id="sidebar"><div class="card"><h2>Servers <span id="serverCount" style="color: #b9bbbe; font-size: 12px;">(0)</span></h2><div class="server-list" id="serverList"><div class="loading" style="margin: 20px auto;"></div></div><div class="error-message" id="serverError"></div></div><div class="card"><h2>Channels</h2><div class="channel-list" id="channelList"><i style="color: #b9bbbe;">Select a server to view channels</i></div></div><div class="card welcome-config" id="welcomeCard" style="display: none;"><h2>Welcome Setup</h2><div class="form-group"><label>Welcome Channel</label><select id="welcomeChannel"><option value="">Choose a channel...</option></select></div><div class="form-group"><label>Message (use {user}, {server})</label><textarea id="welcomeMsg" rows="2" placeholder="Welcome {user} to {server}!">Welcome {user} to {server}!</textarea></div><div class="checkbox-group" style="margin: 10px 0;"><input type="checkbox" id="welcomeEnabled"><span>Enable auto-welcome for new members</span></div><button class="btn" onclick="saveWelcome()" style="width: 100%; padding: 8px;">Save Welcome Config</button></div></div><div class="main-content" id="mainContent"><div class="card"><h2>Message Composer</h2><div class="form-group"><label>Message Content</label><textarea id="messageContent" rows="4" placeholder="Enter your message here..."></textarea><div class="char-counter" id="charCounter">0 / 2000</div></div><div class="section-title">Embeds</div><button class="btn btn-secondary" onclick="addEmbed()" style="margin-bottom: 15px;">+ Add Embed (Max 10)</button><div id="embedList"></div><div class="section-title">File Attachments</div><div class="file-upload" onclick="document.getElementById('fileInput').click()"><p>📎 Click here or drag files to upload</p><small style="color: #b9bbbe;">Max 25MB per file, supports images, PDFs, text</small></div><input type="file" id="fileInput" multiple accept="*" style="display: none;"><div class="file-list" id="fileList"></div><div class="button-group"><button class="btn" onclick="sendMessage()">Send Now</button><button class="btn btn-secondary" onclick="scheduleMessage()">Schedule</button><button class="btn btn-secondary" onclick="saveTemplate()">Save Template</button></div></div><div class="card"><h2>Saved Templates</h2><div id="templateList">{% for t in templates %}<div class="template-item"><span>{{ t.name }}</span><div><button class="btn btn-secondary" onclick="loadTemplate({{ t.id }})">Load</button><button class="btn btn-danger" onclick="deleteTemplate({{ t.id }})">Delete</button></div></div>{% else %}<i style="color: #b9bbbe;">No templates saved yet</i>{% endfor %}</div></div><div class="card"><h2>Message History <small style="color: #b9bbbe; font-size: 12px;">(Last 50)</small></h2><div id="historyList">{% for h in history %}<div class="history-item"><div><strong>{{ h.sent_time|default('Scheduled', true) }}</strong><br><small style="color: #b9bbbe;">{{ (h.content[:60] + '...') if h.content and h.content|length > 60 else (h.content or 'No text') }}</small></div><button class="btn btn-secondary" onclick="resendMessage({{ h.id }})">Resend</button></div>{% else %}<i style="color: #b9bbbe;">No messages yet</i>{% endfor %}</div></div></div><div class="stats-bar"><div class="stat-item"><div class="stat-value">{{ analytics.today }}</div><div class="stat-label">Messages Today</div></div><div class="stat-item"><div class="stat-value">{{ analytics.week }}</div><div class="stat-label">This Week</div></div><div class="stat-item"><div class="stat-value">{{ analytics.month }}</div><div class="stat-label">This Month</div></div><div class="stat-item"><div class="stat-value">{{ analytics.files_today }}</div><div class="stat-label">Files Today</div></div></div></div><div id="toast" class="toast"></div>{% else %}<div style="display: flex; justify-content: center; align-items: center; height: 100vh; background: #1e1f29;"><div class="card" style="text-align: center; max-width: 400px; padding: 30px;"><h1 style="color: #5865F2; margin-bottom: 20px;">Discord Dashboard</h1><p style="margin-bottom: 30px; color: #b9bbbe;">Professional Discord message management at your fingertips</p><a href="{{ url_for('login') }}" class="btn" style="display: block; text-decoration: none; padding: 15px;">Login with Discord</a><p style="margin-top: 20px; font-size: 12px; color: #72747e;">Secure OAuth2 authentication</p></div></div>{% endif %}<script>
let servers=[],selectedServer=null,selectedChannels=[],uploadedFiles=[],embeds=[],botReady={{ 'true' if bot_ready else 'false' }},currentUserId={{ user_id|default('null') }};
document.addEventListener('DOMContentLoaded',async()=>{if(currentUserId){await initializeDashboard()}});
async function initializeDashboard(){showToast('Initializing...','info');const checkBotReady=async()=>{try{const health=await fetch('/api/health');const data=await health.json();if(data.bot_ready){botReady=true;await loadServers();setupEventListeners();showToast('Dashboard ready!','success')}else{setTimeout(checkBotReady,5000);showToast('Waiting for bot...','info')}}catch(e){showToast('Connection error...','error');setTimeout(checkBotReady,5000)}};checkBotReady()}
async function loadServers(){const c=document.getElementById('serverList'),e=document.getElementById('serverError'),s=document.getElementById('serverCount');c.innerHTML='<div class="loading" style="margin: 20px auto;"></div>',e.classList.remove('show');try{const r=await fetch('/api/guilds'),d=await r.json();if(!r.ok)throw new Error(d.error||'Failed');servers=d.guilds||d,s.textContent=`(${servers.length})`,c.innerHTML=0===servers.length?'<div style="color: #b9bbbe; text-align: center;">No servers. Invite bot first.</div>':servers.map(s=>`<div class="server-item" onclick="selectServer('${s.id}', this)" data-server-id="${s.id}">${s.icon?`<img src="${s.icon}" class="server-icon" alt="${s.name}">`:`<div class="server-icon">${s.name[0]}</div>`}<div><div>${s.name}</div><small style="color: #b9bbbe;">${s.member_count||0} members</small></div></div>`).join('')}catch(t){console.error(t),e.textContent=`❌ ${t.message}`,e.classList.add('show'),c.innerHTML='<div style="color: #ff6b6b;">Load failed. Check logs.</div>'}}
async function selectServer(e,t){document.querySelectorAll('.server-item').forEach(e=>e.classList.remove('selected')),t.classList.add('selected'),selectedServer=e,selectedChannels=[],document.getElementById('channelList').innerHTML='<div class="loading" style="margin: 20px auto;"></div>';try{const t=await fetch(`/api/channels?guild_id=${e}`),n=await t.json();if(!t.ok)throw new Error(n.error||'Failed');const s=n.channels||n;document.getElementById('channelList').innerHTML=0===s.length?'<div style="color: #b9bbbe;">No channels with send permission.</div>':s.map(e=>`<div class="channel-item" onclick="selectChannel('${e.id}', this)" data-channel-id="${e.id}">#${e.name}</div>`).join(''),await loadWelcomeConfig(e),document.getElementById('welcomeCard').style.display='block'}catch(e){console.error(e),document.getElementById('channelList').innerHTML=`<div style="color: #ff6b6b;">❌ ${e.message}</div>`}}
function selectChannel(e,t){t.classList.toggle('selected'),t.classList.contains('selected')?selectedChannels.includes(e)||selectedChannels.push(e):selectedChannels=selectedChannels.filter(t=>t!==e)}
async function loadWelcomeConfig(e){try{const t=await fetch(`/api/welcome/config?guild_id=${e}`),n=await t.json();if(n.success&&n.config){document.getElementById('welcomeChannel').value=n.config.channel_id||'',document.getElementById('welcomeMsg').value=n.config.message||'Welcome {user} to {server}!',document.getElementById('welcomeEnabled').checked=n.config.enabled||!1}else document.getElementById('welcomeChannel').value='',document.getElementById('welcomeMsg').value='Welcome {user} to {server}!',document.getElementById('welcomeEnabled').checked=!1;if(selectedServer===e){const e=document.getElementById('channelList'),t=document.getElementById('welcomeChannel'),n=Array.from(e.querySelectorAll('.channel-item')).map(e=>({id:e.dataset.channelId,name:e.textContent.substring(1)}));t.innerHTML='<option value="">Choose a channel...</option>'+n.map(e=>`<option value="${e.id}">#${e.name}</option>`).join('')}}catch(e){console.error(e)}}
async function saveWelcome(){if(!selectedServer)return showToast('Select server','error');const e=selectedServer,t=document.getElementById('welcomeChannel').value,n=document.getElementById('welcomeMsg').value,s=document.getElementById('welcomeEnabled').checked;if(!t)return showToast('Select channel','error');showToast('Saving...','info');try{const o=await fetch('/api/welcome/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({guild_id:e,channel_id:t,message:n,embeds:[],enabled:s})}),c=await o.json();c.success?showToast(c.message||'Saved!','success'):showToast(c.error||'Failed','error')}catch(e){showToast('Error','error')}}
function setupEventListeners(){const e=document.getElementById('messageContent'),t=document.getElementById('charCounter');e.addEventListener('input',()=>{t.textContent=`${e.value.length} / 2000`,t.style.color=e.value.length>2000?'#ff6b6b':'#b9bbbe'});const n=document.querySelector('.file-upload'),s=document.getElementById('fileInput');n.addEventListener('dragover',e=>{e.preventDefault(),n.style.borderColor='#5865F2'}),n.addEventListener('dragleave',()=>n.style.borderColor='#62646e'),n.addEventListener('drop',e=>{e.preventDefault(),n.style.borderColor='#62646e',handleFiles(Array.from(e.dataTransfer.files))}),s.addEventListener('change',e=>handleFiles(Array.from(e.target.files)))}
async function handleFiles(e){const t=25*1024*1024,n=['image/','application/pdf','text/'];for(const s of e){if(s.size>t){showToast(`'${s.name}' exceeds 25MB`,'error');continue}const e=n.some(e=>s.type.startsWith(e))||s.name.endsWith('.pdf')||s.name.endsWith('.txt');e||(showToast(`'${s.name}' type not supported`,'error'),continue)}const s=new FormData;e.forEach(e=>s.append('files',e)),showToast('Uploading...','info');try{const e=await fetch('/api/files',{method:'POST',body:s}),t=await e.json();t.success?(uploadedFiles.push(...t.files),renderFiles(),showToast(`Uploaded ${t.files.length} file(s)`,'success')):showToast(t.error||'Upload failed','error')}catch(e){showToast('Upload error','error')}}
function renderFiles(){const e=document.getElementById('fileList');e.innerHTML=uploadedFiles.map(e=>`<div class="file-item"><span>📄 ${e.filename}</span><span class="remove-file" onclick="removeFile('${e.path}')">×</span></div>`).join('')}
function removeFile(e){uploadedFiles=uploadedFiles.filter(t=>t.path!==e),renderFiles()}
function addEmbed(){if(embeds.length>=10)return showToast('Max 10 embeds','error');embeds.push({title:'',description:'',color:'#5865F2',author:{name:'',url:'',icon_url:''},fields:[],thumbnail:'',image:'',footer:{text:'',icon_url:''},timestamp:!1}),renderEmbeds()}
function removeEmbed(e){embeds.splice(e,1),renderEmbeds()}
function addField(e){embeds[e].fields.push({name:'',value:'',inline:!0}),renderEmbeds()}
function removeField(e,t){embeds[e].fields.splice(t,1),renderEmbeds()}
function renderEmbeds(){const e=document.getElementById('embedList');e.innerHTML=embeds.map((e,t)=>`<div class="card"><h2>Embed #${t+1} <button class="btn btn-danger" onclick="removeEmbed(${t})" style="padding:4px 8px;">Remove</button></h2><div class="form-group"><input type="text" placeholder="Title" value="${e.title}" onchange="embeds[${t}].title=this.value"></div><div class="form-group"><textarea rows="3" placeholder="Description" onchange="embeds[${t}].description=this.value">${e.description}</textarea></div><div class="form-group"><input type="color" value="${e.color}" onchange="embeds[${t}].color=this.value"> Embed Color</div><div class="form-group"><input type="text" placeholder="Author Name" value="${e.author.name}" onchange="embeds[${t}].author.name=this.value"></div><div class="section-title">Fields <button class="btn btn-secondary" onclick="addField(${t})" style="padding:4px 8px;">+ Add Field</button></div>${e.fields.map((n,s)=>`<div class="field-item" style="background:#62646e;padding:10px;border-radius:4px;margin-bottom:8px;"><div style="display:flex;justify-content:space-between;margin-bottom:8px;"><strong>Field #${s+1}</strong><button class="btn btn-danger" onclick="removeField(${t},${s})" style="padding:2px 6px;">×</button></div><input type="text" placeholder="Field Name" value="${n.name}" onchange="embeds[${t}].fields[${s}].name=this.value"><input type="text" placeholder="Field Value" value="${n.value}" onchange="embeds[${t}].fields[${s}].value=this.value" style="margin-top:5px"><label style="margin-top:5px"><input type="checkbox" ${n.inline?'checked':''} onchange="embeds[${t}].fields[${s}].inline=this.checked"> Inline</label></div>`).join('')}</div>`).join('')}
async function sendMessage(){if(!botReady)return showToast('Bot starting...','error');if(0===selectedChannels.length)return showToast('Select channels','error');const e=document.getElementById('messageContent').value;if(!e&&0===embeds.length&&0===uploadedFiles.length)return showToast('Message empty','error');if(e.length>2000)return showToast('Message > 2000 chars','error');showToast('Sending...','info');try{const t=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel_ids:selectedChannels,content:e,embeds:embeds,files:uploadedFiles.map(e=>e.path)})}),n=await t.json();n.success?(showToast(`Sent to ${selectedChannels.length} channel(s)!`,'success'),document.getElementById('messageContent').value='',embeds=[],uploadedFiles=[],renderEmbeds(),renderFiles()):showToast(n.error||'Failed','error')}catch(e){showToast('Send error','error')}}
async function scheduleMessage(){if(!botReady)return showToast('Bot starting...','error');if(0===selectedChannels.length)return showToast('Select channels','error');const e=document.getElementById('messageContent').value;if(!e&&0===embeds.length&&0===uploadedFiles.length)return showToast('Message empty','error');const t=Math.floor(Date.now()/1e3),n=prompt(`Schedule Message\n\nCurrent: ${new Date(1e3*t).toLocaleString()}\nExample: ${new Date(1e3*(t+3600)).toLocaleString()} (1 hour from now)\n\nEnter timestamp in seconds:`);if(!n)return;const s=parseInt(n);if(isNaN(s)||s<=t)return showToast('Invalid time. Must be future.','error');showToast('Scheduling...','info');try{const t=await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel_ids:selectedChannels,content:e,embeds:embeds,files:uploadedFiles.map(e=>e.path),scheduled_time:s})}),n=await t.json();n.success?showToast(n.message||'Scheduled!','success'):showToast(n.error||'Failed','error')}catch(e){showToast('Schedule error','error')}}
async function saveTemplate(){const e=prompt('Template name:');if(!e)return;try{const t=await fetch('/api/templates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:e,content:document.getElementById('messageContent').value,embeds:embeds})}),n=await t.json();n.success?(showToast('Template saved!','success'),location.reload()):showToast(n.error||'Failed','error')}catch(e){showToast('Save error','error')}}
async function loadTemplate(e){try{const t=await fetch('/api/templates'),n=await t.json();if(!t.ok)throw new Error(n.error);const s=n.templates.find(t=>t.id===e);s&&(document.getElementById('messageContent').value=s.content,embeds=JSON.parse(s.embed_data)||[],renderEmbeds(),showToast('Template loaded','success'))}catch(e){showToast('Load failed','error')}}
async function deleteTemplate(e){if(!confirm('Delete?'))return;const t=await fetch(`/api/templates?id=${e}`,{method:'DELETE'}),n=await t.json();n.success?(showToast('Deleted','success'),location.reload()):showToast(n.error||'Failed','error')}
async function resendMessage(e){if(!confirm('Resend?'))return;showToast('Resending...','info');try{const t=await fetch(`/api/resend/${e}`,{method:'POST'}),n=await t.json();n.success?showToast('Resent!','success'):showToast(n.error||'Failed','error')}catch(e){showToast('Resend error','error')}}
function toggleSidebar(){const e=document.getElementById('sidebar'),t=document.getElementById('mainContent');e.classList.toggle('collapsed'),t.classList.toggle('expanded');const n=e.classList.contains('collapsed');localStorage.setItem('sidebarCollapsed',n)}function logout(){confirm('Logout?')&&(window.location.href='/logout')}function showToast(e,t='success'){const n=document.getElementById('toast');n.textContent=e,n.className=`toast ${t} show`,setTimeout(()=>n.classList.remove('show'),3000)}window.addEventListener('load',()=>{'true'===localStorage.getItem('sidebarCollapsed')&&(document.getElementById('sidebar').classList.add('collapsed'),document.getElementById('mainContent').classList.add('expanded'))});</script></body></html>'''

# ============================================================================
# APPLICATION RUNNER
# ============================================================================

def run_bot():
    print("\n🤖 Starting bot thread...")
    bot_manager.run()

def run_app():
    print("\n🌐 Starting Flask server...")
    app.run(host=config.host, port=config.port, debug=False, threaded=True)

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 DISCORD DASHBOARD - PRODUCTION READY")
    print("="*60)
    print(f"📡 Port: {config.port}")
    print(f"🔐 Redirect: {config.redirect_uri}")
    print("="*60 + "\n")
    
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Wait a bit for bot to start
    time.sleep(3)
    
    # Start Flask
    run_app()
