import os
import sys
import json
import sqlite3
import requests
import threading
import time
import asyncio
import re
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
    """Configuration manager with validation"""
    REQUIRED_VARS = {
        'DISCORD_CLIENT_ID': 'Discord Application Client ID',
        'DISCORD_CLIENT_SECRET': 'Discord Application Client Secret',
        'DISCORD_BOT_TOKEN': 'Discord Bot Token (from Bot section)',
        'FLASK_SECRET_KEY': 'Flask Secret Key (random 32+ char string)',
        'DISCORD_REDIRECT_URI': 'OAuth Redirect URI (e.g., https://dashboard.digamber.in/callback)'
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
            print("\n" + "="*60)
            print("❌ CRITICAL ERROR: Missing Environment Variables")
            print("="*60)
            for var in missing:
                print(f"   → {var}: {self.REQUIRED_VARS[var]}")
            print("="*60 + "\n")
            sys.exit(1)

config = Config()

# ============================================================================
# ADVANCED DATABASE ORM
# ============================================================================

class Database:
    """Production-grade database abstraction"""
    def __init__(self, db_path='dashboard.db'):
        self.db_path = db_path
        self.init_schema()
        self.create_indexes()
    
    def get_connection(self):
        """Get database connection with row factory"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_schema(self):
        """Initialize complete database schema"""
        conn = self.get_connection()
        c = conn.cursor()
        
        # Users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                avatar TEXT,
                access_token TEXT,
                refresh_token TEXT,
                expires_at INTEGER,
                created_at INTEGER DEFAULT (unixepoch()),
                updated_at INTEGER DEFAULT (unixepoch()),
                UNIQUE(id)
            )
        ''')
        
        # Messages table
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id TEXT,
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
        
        # Templates table
        c.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                embed_data TEXT,
                created_at INTEGER DEFAULT (unixepoch()),
                updated_at INTEGER DEFAULT (unixepoch()),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        
        # Welcome configuration table
        c.execute('''
            CREATE TABLE IF NOT EXISTS welcome_config (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message TEXT,
                embed_data TEXT,
                enabled INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at INTEGER DEFAULT (unixepoch()),
                updated_at INTEGER DEFAULT (unixepoch()),
                UNIQUE(guild_id)
            )
        ''')
        
        # Analytics table
        c.execute('''
            CREATE TABLE IF NOT EXISTS analytics (
                date TEXT PRIMARY KEY,
                messages_sent INTEGER DEFAULT 0,
                files_sent INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (unixepoch()),
                UNIQUE(date)
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ Database schema initialized")
    
    def create_indexes(self):
        """Create performance indexes"""
        conn = self.get_connection()
        c = conn.cursor()
        
        # Messaging indexes
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_user_time ON messages(user_id, sent_time DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_status_time ON messages(status, scheduled_time)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_guild ON messages(guild_id)')
        
        # Template indexes
        c.execute('CREATE INDEX IF NOT EXISTS idx_templates_user ON templates(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_templates_name ON templates(name)')
        
        # Analytics indexes
        c.execute('CREATE INDEX IF NOT EXISTS idx_analytics_date ON analytics(date)')
        
        # Welcome config indexes
        c.execute('CREATE INDEX IF NOT EXISTS idx_welcome_guild ON welcome_config(guild_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_welcome_enabled ON welcome_config(enabled)')
        
        conn.commit()
        conn.close()
        print("✅ Database indexes created")
    
    # User Operations
    def save_user(self, user_id, username, avatar, access_token, refresh_token=None, expires_at=None):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO users (id, username, avatar, access_token, refresh_token, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, avatar, access_token, refresh_token, expires_at))
        conn.commit()
        conn.close()
    
    def get_user(self, user_id):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
        conn.close()
        return user
    
    # Message Operations
    def save_message(self, user_id, guild_id, channel_ids, content, embeds, files, scheduled_time=None):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            INSERT INTO messages (user_id, guild_id, channel_id, content, embed_data, files, scheduled_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, guild_id, json.dumps(channel_ids), content, json.dumps(embeds), json.dumps(files), scheduled_time, 'pending' if scheduled_time else 'sent'))
        conn.commit()
        conn.close()
    
    def get_pending_messages(self):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM messages WHERE status = "pending" AND scheduled_time <= ?', (int(time.time()),))
        messages = c.fetchall()
        conn.close()
        return messages
    
    def update_message_status(self, msg_id, status, sent_time=None):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('UPDATE messages SET status = ?, sent_time = COALESCE(?, sent_time) WHERE id = ?', (status, sent_time, msg_id))
        conn.commit()
        conn.close()
    
    def get_user_messages(self, user_id, limit=50):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?', (user_id, limit))
        messages = c.fetchall()
        conn.close()
        return messages
    
    # Template Operations
    def save_template(self, user_id, name, content, embeds):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('INSERT INTO templates (user_id, name, content, embed_data) VALUES (?, ?, ?, ?)', 
                 (user_id, name, content, json.dumps(embeds)))
        conn.commit()
        template_id = c.lastrowid
        conn.close()
        return template_id
    
    def get_user_templates(self, user_id):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM templates WHERE user_id = ? ORDER BY updated_at DESC, created_at DESC', (user_id,))
        templates = c.fetchall()
        conn.close()
        return templates
    
    def delete_template(self, template_id, user_id):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('DELETE FROM templates WHERE id = ? AND user_id = ?', (template_id, user_id))
        deleted = c.rowcount
        conn.commit()
        conn.close()
        return deleted > 0
    
    # Welcome Configuration
    def save_welcome_config(self, guild_id, channel_id, message, embeds, enabled, created_by):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO welcome_config (guild_id, channel_id, message, embed_data, enabled, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (guild_id, channel_id, message, json.dumps(embeds), int(enabled), created_by))
        conn.commit()
        conn.close()
    
    def get_welcome_config(self, guild_id):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM welcome_config WHERE guild_id = ?', (guild_id,))
        config = c.fetchone()
        conn.close()
        return config
    
    # Analytics
    def update_analytics(self, messages=0, files=0):
        today = datetime.now().strftime('%Y-%m-%d')
        conn = self.get_connection()
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
    
    def get_analytics(self):
        today = datetime.now().strftime('%Y-%m-%d')
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        
        conn = self.get_connection()
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

db = Database()

# ============================================================================
# DISCORD OAUTH CLIENT
# ============================================================================

class DiscordOAuth:
    """Discord OAuth2 client"""
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
# DISCORD BOT (PRODUCTION-GRADE)
# ============================================================================

class DiscordBot:
    """Production Discord bot with all features"""
    def __init__(self):
        self.intents = discord.Intents.default()
        self.intents.message_content = False
        self.intents.guilds = True
        self.intents.members = True
        
        self.bot = commands.Bot(
            command_prefix='!',
            intents=self.intents,
            help_command=None,
            case_insensitive=True
        )
        self.ready = False
        
        self.setup_events()
    
    def setup_events(self):
        """Setup bot event handlers"""
        @self.bot.event
        async def on_ready():
            self.ready = True
            print(f"\n{'='*60}")
            print(f"✅ BOT READY: {self.bot.user} (ID: {self.bot.user.id})")
            print(f"✶ Guilds: {len(self.bot.guilds)}")
            print(f"✶ API Latency: {round(self.bot.latency * 1000)}ms")
            print(f"{'='*60}\n")
            
            # Start background tasks
            self.bot.loop.create_task(self.process_scheduled_messages())
            self.bot.loop.create_task(self.update_presence())
        
        @self.bot.event
        async def on_member_join(member):
            await self.handle_welcome(member)
        
        @self.bot.event
        async def on_guild_join(guild):
            print(f"➕ Joined new guild: {guild.name} ({guild.id})")
        
        @self.bot.event
        async def on_guild_remove(guild):
            print(f"➖ Left guild: {guild.name} ({guild.id})")
    
    async def handle_welcome(self, member):
        """Handle automatic welcome messages"""
        try:
            config = db.get_welcome_config(str(member.guild.id))
            if not config or not config['enabled']:
                return
            
            channel = self.bot.get_channel(int(config['channel_id']))
            if not channel:
                print(f"❌ Welcome channel not found for guild {member.guild.id}")
                return
            
            message = config['message'].replace('{user}', f'<@{member.id}>').replace('{username}', member.name).replace('{server}', member.guild.name)
            
            embeds = None
            if config['embed_data']:
                data = json.loads(config['embed_data'])
                if data and len(data) > 0:
                    embed_data = data[0]
                    embed = discord.Embed()
                    if embed_data.get('title'):
                        embed.title = embed_data['title'].replace('{user}', member.name).replace('{server}', member.guild.name)
                    if embed_data.get('description'):
                        embed.description = embed_data['description'].replace('{user}', member.name).replace('{server}', member.guild.name)
                    if embed_data.get('color'):
                        color = embed_data['color'].lstrip('#')
                        embed.color = int(color, 16)
                    embeds = [embed]
            
            await channel.send(content=message or None, embeds=embeds)
            print(f"✅ Welcome sent: {member.name} → {member.guild.name}")
            
        except Exception as e:
            print(f"❌ Welcome error: {e}")
    
    async def update_presence(self):
        """Update bot presence every 60 seconds"""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                await self.bot.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.watching,
                        name=f"{len(self.bot.guilds)} servers"
                    ),
                    status=discord.Status.online
                )
                await asyncio.sleep(60)
            except Exception as e:
                print(f"❌ Presence update error: {e}")
                await asyncio.sleep(60)
    
    async def get_mutual_guilds(self, user_id):
        """Get guilds where both bot and user are present"""
        while not self.ready:
            await asyncio.sleep(0.5)
        
        user_guilds = []
        for guild in self.bot.guilds:
            try:
                member = guild.get_member(int(user_id))
                if member:
                    user_guilds.append({
                        'id': str(guild.id),
                        'name': guild.name,
                        'icon': str(guild.icon.url) if guild.icon else None,
                        'member_count': guild.member_count
                    })
            except Exception as e:
                print(f"❌ Error checking guild {guild.id}: {e}")
                continue
        
        return sorted(user_guilds, key=lambda g: g['name'].lower())
    
    async def get_guild_channels(self, guild_id, user_id):
        """Get text channels where user can send messages"""
        while not self.ready:
            await asyncio.sleep(0.5)
        
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            print(f"❌ Guild not found: {guild_id}")
            return []
        
        channels = []
        for channel in guild.text_channels:
            try:
                member = guild.get_member(int(user_id))
                if member and channel.permissions_for(member).send_messages:
                    channels.append({
                        'id': str(channel.id),
                        'name': channel.name,
                        'position': channel.position
                    })
            except Exception as e:
                print(f"❌ Channel permission error: {channel.id} - {e}")
                continue
        
        return sorted(channels, key=lambda c: c['position'])
    
    async def send_message(self, channel_id, content, embeds_data=None, files=None):
        """Send message to Discord with all features"""
        while not self.ready:
            await asyncio.sleep(0.5)
        
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return False, f"Channel {channel_id} not found"
        
        try:
            discord_files = []
            if files:
                for file_path in files:
                    if os.path.exists(file_path):
                        discord_files.append(discord.File(file_path))
            
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
                            embed.add_field(name=f.get('name', ''), value=f.get('value', ''), inline=f.get('inline', False))
                    if data.get('thumbnail'): embed.set_thumbnail(url=data['thumbnail'])
                    if data.get('image'): embed.set_image(url=data['image'])
                    if data.get('footer'): embed.set_footer(**data['footer'])
                    if data.get('timestamp'): embed.timestamp = datetime.now()
                    embeds.append(embed)
                
                await channel.send(content=content or None, embeds=embeds, files=discord_files or None)
            else:
                await channel.send(content=content or None, files=discord_files or None)
            
            db.update_analytics(messages=1, files=len(discord_files))
            return True, "Message sent successfully"
        
        except discord.HTTPException as e:
            print(f"❌ Discord HTTP error: {e}")
            return False, f"Discord error: {e.text}"
        except discord.Forbidden:
            return False, "Bot lacks permission to send messages in this channel"
        except Exception as e:
            print(f"❌ Send message error: {e}")
            return False, f"Failed to send message: {str(e)}"
    
    async def process_scheduled_messages(self):
        """Background task to process scheduled messages"""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                messages = db.get_pending_messages()
                
                for msg in messages:
                    channel_ids = json.loads(msg['channel_id'])
                    content = msg['content']
                    embeds = json.loads(msg['embed_data']) if msg['embed_data'] else None
                    files = json.loads(msg['files']) if msg['files'] else None
                    
                    for channel_id in channel_ids:
                        success, _ = await self.send_message(channel_id, content, embeds, files)
                        status = 'sent' if success else 'failed'
                        db.update_message_status(msg['id'], status, int(time.time()))
                
                await asyncio.sleep(30)
            except Exception as e:
                print(f"❌ Scheduled message processor error: {e}")
                await asyncio.sleep(30)
    
    def run(self):
        """Run bot in separate thread"""
        try:
            self.bot.run(config.bot_token)
        except Exception as e:
            print(f"\n{'='*60}")
            print(f"❌ CRITICAL BOT ERROR: {e}")
            print("="*60)
            print("Possible causes:")
            print("1. Invalid bot token")
            print("2. Bot not invited to any servers")
            print("3. Intents not enabled in Discord Developer Portal")
            print("="*60 + "\n")
            sys.exit(1)

bot_manager = DiscordBot()

# ============================================================================
# AUTHENTICATION DECORATORS
# ============================================================================

def require_auth(f):
    """Decorator to require Discord authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required. Please login with Discord.'}), 401
        if not isinstance(session['user_id'], int):
            session.clear()
            return jsonify({'error': 'Invalid session. Please login again.'}), 401
        return f(*args, **kwargs)
    return decorated_function

def require_bot_ready(f):
    """Decorator to require bot to be ready"""
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        if not bot_manager.ready:
            return jsonify({
                'error': 'Bot is still initializing. This may take up to 60 seconds on first startup.',
                'retry_after': 30
            }), 503
        return await f(*args, **kwargs)
    return decorated_function

# ============================================================================
# FLASK APPLICATION
# ============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = config.secret_key
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # 25MB max file size

# ============================================================================
# OAUTH ROUTES
# ============================================================================

@app.route('/')
def index():
    """Root redirect"""
    if 'user_id' in session and isinstance(session['user_id'], int):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login')
def login():
    """Initiate Discord OAuth flow"""
    auth_url = DiscordOAuth.get_authorize_url()
    print(f"🔐 OAuth redirect: {auth_url}")
    return redirect(auth_url)

@app.route('/callback')
def callback():
    """Handle OAuth callback and create session"""
    code = request.args.get('code')
    if not code:
        print("❌ No authorization code in callback")
        return render_template_string(ERROR_PAGE, error='No authorization code received')
    
    try:
        # Exchange code for token
        token_data = DiscordOAuth.exchange_code(code)
        if 'access_token' not in token_data:
            error_msg = token_data.get('error_description', 'Unknown OAuth error')
            print(f"❌ Token exchange failed: {error_msg}")
            return render_template_string(ERROR_PAGE, error=f"Login failed: {error_msg}")
        
        # Get user data
        user_data = DiscordOAuth.get_user_data(token_data['access_token'])
        
        # Validate user ID
        if 'id' not in user_data:
            print("❌ No user ID in user data")
            return render_template_string(ERROR_PAGE, error='Invalid user data from Discord')
        
        # Create session
        session['user_id'] = int(user_data['id'])
        session['username'] = user_data['username']
        session['avatar'] = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else None
        session.permanent = True
        session.modified = True
        
        # Save to database
        db.save_user(
            session['user_id'],
            session['username'],
            session['avatar'],
            token_data['access_token'],
            token_data.get('refresh_token'),
            int(time.time() + token_data.get('expires_in', 604800))
        )
        
        print(f"✅ LOGIN SUCCESS: {session['username']} (ID: {session['user_id']})")
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"❌ CALLBACK ERROR: {e}")
        return render_template_string(ERROR_PAGE, error=f"Login failed: {str(e)}")

@app.route('/logout')
def logout():
    """Logout and clear session"""
    user_id = session.get('user_id')
    print(f"👋 LOGOUT: {user_id}")
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@require_auth
def dashboard():
    """Main dashboard with all features"""
    try:
        user_id = session['user_id']
        username = session['username']
        avatar = session['avatar']
        
        analytics = db.get_analytics()
        templates = db.get_user_templates(user_id)
        history = db.get_user_messages(user_id)
        
        return render_template_string(DASHBOARD_HTML,
            user_id=user_id,
            username=username,
            avatar=avatar,
            analytics=analytics,
            templates=templates,
            history=history,
            oauth_url=DiscordOAuth.get_authorize_url(),
            bot_ready=bot_manager.ready
        )
        
    except Exception as e:
        print(f"❌ DASHBOARD ERROR: {e}")
        session.clear()
        return redirect(url_for('login'))

# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/api/health')
def health():
    """Health check for Render"""
    return jsonify({
        'status': 'healthy',
        'bot_ready': bot_manager.ready,
        'timestamp': int(time.time())
    }), 200

@app.route('/api/guilds')
@require_auth
@require_bot_ready
async def api_guilds():
    """Get user's guilds where bot is present"""
    try:
        guilds = await bot_manager.get_mutual_guilds(session['user_id'])
        return jsonify({'success': True, 'guilds': guilds})
    except Exception as e:
        print(f"❌ /api/guilds error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/channels')
@require_auth
@require_bot_ready
async def api_channels():
    """Get channels for a guild"""
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'error': 'Guild ID is required'}), 400
    
    try:
        channels = await bot_manager.get_guild_channels(guild_id, session['user_id'])
        return jsonify({'success': True, 'channels': channels})
    except Exception as e:
        print(f"❌ /api/channels error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/send', methods=['POST'])
@require_auth
@require_bot_ready
async def api_send():
    """Send message to selected channels"""
    data = request.json
    channel_ids = data.get('channel_ids', [])
    content = data.get('content', '').strip()
    embeds = data.get('embeds', [])
    files = data.get('files', [])
    
    # Validate inputs
    if not channel_ids:
        return jsonify({'error': 'Please select at least one channel'}), 400
    if not content and not embeds and not files:
        return jsonify({'error': 'Message cannot be empty'}), 400
    if len(content) > 2000:
        return jsonify({'error': 'Message exceeds 2000 character limit'}), 400
    
    try:
        results = []
        for channel_id in channel_ids:
            success, message = await bot_manager.send_message(channel_id, content, embeds, files)
            results.append({'channel_id': channel_id, 'success': success, 'message': message})
        
        # Save to history if at least one success
        if any(r['success'] for r in results):
            db.save_message(session['user_id'], None, channel_ids, content, embeds, files)
        
        return jsonify({'success': True, 'results': results})
        
    except Exception as e:
        print(f"❌ /api/send error: {e}")
        return jsonify({'error': 'Failed to send messages'}), 500

@app.route('/api/schedule', methods=['POST'])
@require_auth
@require_bot_ready
async def api_schedule():
    """Schedule message for later"""
    data = request.json
    channel_ids = data.get('channel_ids', [])
    content = data.get('content', '').strip()
    embeds = data.get('embeds', [])
    files = data.get('files', [])
    scheduled_time = data.get('scheduled_time')
    
    # Validation
    if not channel_ids:
        return jsonify({'error': 'Select at least one channel'}), 400
    if not content and not embeds and not files:
        return jsonify({'error': 'Message cannot be empty'}), 400
    if not scheduled_time:
        return jsonify({'error': 'Schedule time is required'}), 400
    
    try:
        scheduled_timestamp = int(scheduled_time)
        if scheduled_timestamp <= int(time.time()):
            return jsonify({'error': 'Schedule time must be in the future'}), 400
        
        db.save_message(session['user_id'], None, channel_ids, content, embeds, files, scheduled_timestamp)
        
        return jsonify({
            'success': True,
            'message': f'Message scheduled for {datetime.fromtimestamp(scheduled_timestamp).strftime("%Y-%m-%d %H:%M:%S")}'
        })
        
    except Exception as e:
        print(f"❌ /api/schedule error: {e}")
        return jsonify({'error': 'Failed to schedule message'}), 500

@app.route('/api/templates', methods=['GET', 'POST', 'DELETE'])
@require_auth
def api_templates():
    """Template management"""
    user_id = session['user_id']
    
    if request.method == 'GET':
        try:
            templates = db.get_user_templates(user_id)
            return jsonify({'success': True, 'templates': [dict(t) for t in templates]})
        except Exception as e:
            return jsonify({'error': 'Failed to fetch templates'}), 500
    
    elif request.method == 'POST':
        data = request.json
        name = data.get('name', '').strip()
        content = data.get('content', '')
        embeds = data.get('embeds', [])
        
        if not name:
            return jsonify({'error': 'Template name is required'}), 400
        
        try:
            template_id = db.save_template(user_id, name, content, embeds)
            return jsonify({'success': True, 'template_id': template_id, 'message': 'Template saved'})
        except Exception as e:
            return jsonify({'error': 'Failed to save template'}), 500
    
    elif request.method == 'DELETE':
        template_id = request.args.get('id')
        if not template_id:
            return jsonify({'error': 'Template ID is required'}), 400
        
        try:
            if db.delete_template(template_id, user_id):
                return jsonify({'success': True, 'message': 'Template deleted'})
            return jsonify({'error': 'Template not found or not yours'}), 404
        except Exception as e:
            return jsonify({'error': 'Failed to delete template'}), 500

@app.route('/api/files', methods=['POST'])
@require_auth
async def api_upload():
    """File upload with validation"""
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files')
    uploaded_files = []
    
    for file in files:
        if file.filename == '':
            continue
        
        try:
            # Validate file size
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            
            if file_size > 25 * 1024 * 1024:
                return jsonify({'error': f"'{file.filename}' exceeds 25MB limit"}), 400
            
            # Save file
            filename = f"{int(time.time())}_{session['user_id']}_{hashlib.md5(file.filename.encode()).hexdigest()[:8]}_{file.filename}"
            file_path = os.path.join(UPLOAD_DIR, filename)
            
            await file.save(file_path)
            
            # Database record
            conn = db.get_connection()
            c = conn.cursor()
            c.execute('INSERT INTO uploaded_files (user_id, filename, file_path, file_size) VALUES (?, ?, ?, ?)',
                     (session['user_id'], file.filename, file_path, file_size))
            conn.commit()
            conn.close()
            
            uploaded_files.append({'filename': file.filename, 'path': file_path})
            
        except Exception as e:
            print(f"❌ File save error: {e}")
            return jsonify({'error': f"Failed to save {file.filename}"}), 500
    
    return jsonify({'success': True, 'files': uploaded_files})

@app.route('/api/welcome/config', methods=['GET', 'POST'])
@require_auth
@require_bot_ready
async def api_welcome():
    """Welcome message configuration"""
    if request.method == 'GET':
        guild_id = request.args.get('guild_id')
        if not guild_id:
            return jsonify({'error': 'Guild ID required'}), 400
        
        config = db.get_welcome_config(guild_id)
        if config:
            return jsonify({
                'success': True,
                'config': {
                    'channel_id': config['channel_id'],
                    'message': config['message'],
                    'embeds': json.loads(config['embed_data']) if config['embed_data'] else [],
                    'enabled': bool(config['enabled'])
                }
            })
        return jsonify({'success': True, 'config': {'channel_id': '', 'message': '', 'enabled': False, 'embeds': []}})
    
    elif request.method == 'POST':
        data = request.json
        guild_id = data.get('guild_id')
        channel_id = data.get('channel_id')
        message = data.get('message', '')
        embeds = data.get('embeds', [])
        enabled = data.get('enabled', False)
        
        if not guild_id or not channel_id:
            return jsonify({'error': 'Guild ID and Channel ID are required'}), 400
        
        # Validate channel exists and bot can send
        try:
            channel = bot_manager.bot.get_channel(int(channel_id))
            if not channel:
                return jsonify({'error': 'Channel not found'}), 400
            if not channel.permissions_for(channel.guild.me).send_messages:
                return jsonify({'error': 'Bot cannot send messages in this channel'}), 400
        except:
            return jsonify({'error': 'Invalid channel ID'}), 400
        
        try:
            db.save_welcome_config(guild_id, channel_id, message, embeds, enabled, session['user_id'])
            return jsonify({'success': True, 'message': 'Welcome configuration saved successfully'})
        except Exception as e:
            print(f"❌ Save welcome config error: {e}")
            return jsonify({'error': 'Failed to save welcome configuration'}), 500

@app.route('/api/analytics', methods=['GET'])
@require_auth
def api_analytics():
    """Get analytics data"""
    try:
        analytics = db.get_analytics()
        return jsonify({'success': True, 'analytics': analytics})
    except Exception as e:
        print(f"❌ Analytics error: {e}")
        return jsonify({'error': 'Failed to fetch analytics'}), 500

@app.route('/uploads/<path:filename>')
@require_auth
def serve_upload(filename):
    """Serve uploaded file"""
    return send_from_directory(UPLOAD_DIR, filename)

# ============================================================================
# COMPLETE HTML TEMPLATE
# ============================================================================

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Discord Message Dashboard</title>
    <style>
        /* ===== CSS RESET & BASE STYLES ===== */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1e1f29;
            color: #ffffff;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            line-height: 1.5;
        }

        /* ===== HEADER ===== */
        .header {
            background: #2a2b38;
            padding: 15px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 2px solid #5865F2;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 15px;
        }

        .hamburger {
            background: none;
            border: none;
            color: #ffffff;
            font-size: 24px;
            cursor: pointer;
            padding: 5px;
            border-radius: 4px;
            transition: background 0.2s;
        }

        .hamburger:hover {
            background: #40424e;
        }

        .logo {
            font-size: 20px;
            font-weight: bold;
            color: #5865F2;
        }

        .user-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: #5865F2;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 18px;
            object-fit: cover;
        }

        .logout-btn {
            background: #5865F2;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            transition: background 0.2s;
        }

        .logout-btn:hover {
            background: #4752C4;
        }

        /* ===== MAIN LAYOUT ===== */
        .container {
            display: flex;
            flex: 1;
            overflow: hidden;
        }

        .sidebar {
            width: 300px;
            background: #2a2b38;
            padding: 20px;
            overflow-y: auto;
            border-right: 1px solid #40424e;
            transition: transform 0.3s ease;
            box-shadow: 2px 0 10px rgba(0,0,0,0.1);
        }

        .sidebar.collapsed {
            transform: translateX(-300px);
        }

        .main-content {
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            transition: margin-left 0.3s ease;
        }

        .main-content.expanded {
            margin-left: -300px;
        }

        /* ===== CARDS & COMPONENTS ===== */
        .card {
            background: #2a2b38;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid #40424e;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }

        .card h2 {
            color: #5865F2;
            margin-bottom: 15px;
            font-size: 18px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .section-title {
            color: #5865F2;
            font-size: 16px;
            font-weight: bold;
            margin-bottom: 10px;
        }

        /* ===== SERVER & CHANNEL LISTS ===== */
        .server-list, .channel-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .server-item, .channel-item {
            background: #40424e;
            padding: 12px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .server-item:hover, .channel-item:hover {
            background: #5865F2;
            transform: translateX(2px);
        }

        .server-item.selected, .channel-item.selected {
            background: #5865F2;
            box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.3);
        }

        .server-icon {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #5865F2;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 14px;
        }

        /* ===== FORM ELEMENTS ===== */
        .form-group {
            margin-bottom: 15px;
        }

        label {
            display: block;
            margin-bottom: 5px;
            color: #b9bbbe;
            font-size: 12px;
            text-transform: uppercase;
            font-weight: bold;
            letter-spacing: 0.5px;
        }

        input, textarea, select {
            width: 100%;
            padding: 10px;
            background: #40424e;
            border: 1px solid #62646e;
            border-radius: 6px;
            color: white;
            font-size: 14px;
            transition: border-color 0.2s;
        }

        input:focus, textarea:focus, select:focus {
            outline: none;
            border-color: #5865F2;
        }

        .char-counter {
            text-align: right;
            font-size: 12px;
            color: #b9bbbe;
            margin-top: 5px;
        }

        /* ===== FILE UPLOAD ===== */
        .file-upload {
            border: 2px dashed #62646e;
            padding: 30px;
            text-align: center;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
            background: #2a2b38;
        }

        .file-upload:hover {
            border-color: #5865F2;
            background: #40424e;
        }

        .file-list {
            margin-top: 15px;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .file-item {
            background: #40424e;
            padding: 8px 12px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
        }

        .remove-file {
            color: #ff6b6b;
            cursor: pointer;
            font-weight: bold;
            font-size: 18px;
        }

        /* ===== BUTTONS ===== */
        .btn {
            background: #5865F2;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
            font-size: 14px;
        }

        .btn:hover {
            background: #4752C4;
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(88, 101, 242, 0.3);
        }

        .btn:active {
            transform: translateY(0);
        }

        .btn-secondary {
            background: #62646e;
        }

        .btn-secondary:hover {
            background: #72747e;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }

        .btn-danger {
            background: #ff6b6b;
        }

        .btn-danger:hover {
            background: #ff5252;
            box-shadow: 0 4px 12px rgba(255, 107, 107, 0.3);
        }

        .button-group {
            display: flex;
            gap: 10px;
            margin-top: 20px;
            flex-wrap: wrap;
        }

        /* ===== LOADING & TOASTS ===== */
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: #5865F2;
            animation: spin 1s ease-in-out infinite;
        }

        @keyframes spin { to { transform: rotate(360deg); } }

        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #2a2b38;
            color: white;
            padding: 15px 20px;
            border-radius: 6px;
            border: 1px solid #40424e;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            display: none;
            align-items: center;
            gap: 10px;
            z-index: 1000;
            max-width: 400px;
            animation: slideUp 0.3s ease;
        }

        @keyframes slideUp {
            from { transform: translateY(100px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }

        .toast.show { display: flex; }
        .toast.success { border-left: 4px solid #4ade80; }
        .toast.error { border-left: 4px solid #ff6b6b; }

        /* ===== SIDEBAR SPECIFIC ===== */
        .sidebar .card {
            margin-bottom: 20px;
        }

        .sidebar .card h2 {
            font-size: 16px;
            margin-bottom: 10px;
        }

        .welcome-config {
            background: #2a2b38;
            padding: 15px;
            border-radius: 6px;
            border: 1px solid #40424e;
            margin-top: 15px;
        }

        .error-message {
            background: #ff6b6b;
            color: white;
            padding: 10px;
            border-radius: 6px;
            margin: 10px 0;
            display: none;
        }

        .error-message.show {
            display: block;
        }

        /* ===== TEMPLATES & HISTORY ===== */
        .template-item, .history-item {
            background: #40424e;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .template-item div, .history-item div {
            display: flex;
            gap: 8px;
        }

        /* ===== STATS BAR ===== */
        .stats-bar {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: #2a2b38;
            border-top: 1px solid #40424e;
            padding: 10px 20px;
            display: flex;
            justify-content: space-around;
            z-index: 100;
        }

        .stat-item { text-align: center; }
        .stat-value {
            font-size: 24px;
            font-weight: bold;
            color: #5865F2;
        }
        .stat-label {
            font-size: 11px;
            color: #b9bbbe;
            text-transform: uppercase;
        }

        /* ===== EMBED BUILDER ===== */
        .embed-builder {
            margin-top: 15px;
            padding: 15px;
            background: #2a2b38;
            border-radius: 6px;
            border: 1px solid #40424e;
        }

        .embed-preview {
            background: #40424e;
            padding: 15px;
            border-radius: 6px;
            margin-top: 15px;
            border-left: 4px solid #5865F2;
        }

        /* ===== RESPONSIVE ===== */
        @media (max-width: 768px) {
            .sidebar {
                width: 100%;
                height: 300px;
                position: absolute;
                z-index: 50;
            }
            
            .sidebar.collapsed {
                transform: translateY(-300px);
            }
            
            .main-content {
                padding: 15px;
            }
            
            .stats-bar {
                flex-wrap: wrap;
                gap: 10px;
            }
            
            .stat-item { flex: 1; }
        }

        /* ===== SCROLLBAR STYLING ===== */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }

        ::-webkit-scrollbar-track {
            background: #1e1f29;
        }

        ::-webkit-scrollbar-thumb {
            background: #40424e;
            border-radius: 4px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: #5865F2;
        }
    </style>
</head>
<body>
    {% if user_id %}
    <div class="header">
        <div class="header-left">
            <button class="hamburger" onclick="toggleSidebar()" aria-label="Toggle sidebar">☰</button>
            <div class="logo">Discord Dashboard</div>
        </div>
        <div class="user-info">
            {% if avatar %}<img src="{{ avatar }}" class="avatar" alt="{{ username }}">{% else %}<div class="avatar">{{ username[0] }}</div>{% endif %}
            <span>{{ username }}</span>
            <button class="logout-btn" onclick="logout()">Logout</button>
        </div>
    </div>

    <div class="container">
        <div class="sidebar" id="sidebar">
            <div class="card">
                <h2>Servers <span id="serverCount" style="color: #b9bbbe; font-size: 12px;">(0)</span></h2>
                <div class="server-list" id="serverList">
                    <div class="loading" style="margin: 20px auto;"></div>
                </div>
                <div class="error-message" id="serverError"></div>
            </div>

            <div class="card">
                <h2>Channels</h2>
                <div class="channel-list" id="channelList">
                    <i style="color: #b9bbbe;">Select a server to view channels</i>
                </div>
            </div>

            <div class="card welcome-config" id="welcomeCard" style="display: none;">
                <h2>Welcome Setup</h2>
                <div class="form-group">
                    <label>Welcome Channel</label>
                    <select id="welcomeChannel">
                        <option value="">Choose a channel...</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Message (use {user}, {server})</label>
                    <textarea id="welcomeMsg" rows="2" placeholder="Welcome {user} to {server}!">Welcome {user} to {server}!</textarea>
                </div>
                <div class="checkbox-group" style="margin: 10px 0;">
                    <input type="checkbox" id="welcomeEnabled">
                    <span>Enable auto-welcome for new members</span>
                </div>
                <button class="btn" onclick="saveWelcome()" style="width: 100%; padding: 8px;">Save Welcome Config</button>
            </div>
        </div>

        <div class="main-content" id="mainContent">
            <div class="card">
                <h2>Message Composer</h2>
                <div class="form-group">
                    <label>Message Content</label>
                    <textarea id="messageContent" rows="4" placeholder="Enter your message here..."></textarea>
                    <div class="char-counter" id="charCounter">0 / 2000</div>
                </div>

                <div class="section-title">Embeds</div>
                <button class="btn btn-secondary" onclick="addEmbed()" style="margin-bottom: 15px;">+ Add Embed (Max 10)</button>
                <div id="embedList"></div>

                <div class="section-title">File Attachments</div>
                <div class="file-upload" onclick="document.getElementById('fileInput').click()">
                    <p>📎 Click here or drag files to upload</p>
                    <small style="color: #b9bbbe;">Max 25MB per file, supports images, PDFs, text</small>
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
                    <i style="color: #b9bbbe;">No templates saved yet</i>
                    {% endfor %}
                </div>
            </div>

            <div class="card">
                <h2>Message History <small style="color: #b9bbbe; font-size: 12px;">(Last 50)</small></h2>
                <div id="historyList">
                    {% for h in history %}
                    <div class="history-item">
                        <div>
                            <strong>{{ h.sent_time|default('Scheduled', true) }}</strong><br>
                            <small style="color: #b9bbbe;">{{ (h.content[:60] + '...') if h.content and h.content|length > 60 else (h.content or 'No text') }}</small>
                        </div>
                        <button class="btn btn-secondary" onclick="resendMessage({{ h.id }})">Resend</button>
                    </div>
                    {% else %}
                    <i style="color: #b9bbbe;">No messages yet</i>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div class="stats-bar">
            <div class="stat-item">
                <div class="stat-value">{{ analytics.today }}</div>
                <div class="stat-label">Messages Today</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">{{ analytics.week }}</div>
                <div class="stat-label">This Week</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">{{ analytics.month }}</div>
                <div class="stat-label">This Month</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">{{ analytics.files_today }}</div>
                <div class="stat-label">Files Today</div>
            </div>
        </div>
    </div>

    <div id="toast" class="toast"></div>

    {% else %}
    <div style="display: flex; justify-content: center; align-items: center; height: 100vh; background: #1e1f29;">
        <div class="card" style="text-align: center; max-width: 400px; padding: 30px;">
            <h1 style="color: #5865F2; margin-bottom: 20px;">Discord Dashboard</h1>
            <p style="margin-bottom: 30px; color: #b9bbbe;">Professional Discord message management at your fingertips</p>
            <a href="{{ oauth_url }}" class="btn" style="display: block; text-decoration: none; padding: 15px;">Login with Discord</a>
            <p style="margin-top: 20px; font-size: 12px; color: #72747e;">Secure OAuth2 authentication</p>
        </div>
    </div>
    {% endif %}

    <script>
        // ===== GLOBAL STATE =====
        let servers = [];
        let selectedServer = null;
        let selectedChannels = [];
        let uploadedFiles = [];
        let embeds = [];
        let botReady = {{ 'true' if bot_ready else 'false' }};
        let currentUserId = {{ user_id|default('null') }};

        // ===== INITIALIZATION =====
        document.addEventListener('DOMContentLoaded', async function() {
            if (currentUserId) {
                await initializeDashboard();
            }
        });

        async function initializeDashboard() {
            showToast('Initializing dashboard...', 'info');
            
            // Wait for bot to be ready
            const checkBotReady = async () => {
                try {
                    const health = await fetch('/api/health');
                    const data = await health.json();
                    if (data.bot_ready) {
                        botReady = true;
                        await loadServers();
                        setupEventListeners();
                        showToast('Dashboard ready!', 'success');
                    } else {
                        setTimeout(checkBotReady, 5000);
                        showToast('Waiting for bot to connect...', 'info');
                    }
                } catch (e) {
                    showToast('Connection error. Retrying...', 'error');
                    setTimeout(checkBotReady, 5000);
                }
            };
            
            checkBotReady();
        }

        async function loadServers() {
            const container = document.getElementById('serverList');
            const errorDiv = document.getElementById('serverError');
            const countSpan = document.getElementById('serverCount');
            
            container.innerHTML = '<div class="loading" style="margin: 20px auto;"></div>';
            errorDiv.classList.remove('show');
            
            try {
                const response = await fetch('/api/guilds');
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to load servers');
                }
                
                servers = data.guilds || data;
                countSpan.textContent = `(${servers.length})`;
                
                if (servers.length === 0) {
                    container.innerHTML = '<div style="color: #b9bbbe; text-align: center;">No servers found. Invite bot to your server first.</div>';
                    return;
                }
                
                container.innerHTML = servers.map(s => `
                    <div class="server-item" onclick="selectServer('${s.id}', this)" data-server-id="${s.id}">
                        ${s.icon ? `<img src="${s.icon}" class="server-icon" alt="${s.name}">` : `<div class="server-icon">${s.name[0]}</div>`}
                        <div>
                            <div>${s.name}</div>
                            <small style="color: #b9bbbe;">${s.member_count || 0} members</small>
                        </div>
                    </div>
                `).join('');
                
            } catch (e) {
                console.error('Load servers error:', e);
                errorDiv.textContent = `❌ ${e.message}`;
                errorDiv.classList.add('show');
                container.innerHTML = '<div style="color: #ff6b6b;">Failed to load servers. Check console for details.</div>';
            }
        }

        async function selectServer(serverId, element) {
            document.querySelectorAll('.server-item').forEach(i => i.classList.remove('selected'));
            element.classList.add('selected');
            selectedServer = serverId;
            selectedChannels = [];
            
            // Clear channel selection
            document.getElementById('channelList').innerHTML = '<div class="loading" style="margin: 20px auto;"></div>';
            
            // Load channels
            try {
                const response = await fetch(`/api/channels?guild_id=${serverId}`);
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to load channels');
                }
                
                const channels = data.channels || data;
                
                if (channels.length === 0) {
                    document.getElementById('channelList').innerHTML = '<div style="color: #b9bbbe;">No channels where you can send messages.</div>';
                } else {
                    document.getElementById('channelList').innerHTML = channels.map(c => `
                        <div class="channel-item" onclick="selectChannel('${c.id}', this)" data-channel-id="${c.id}">#${c.name}</div>
                    `).join('');
                }
                
                // Load welcome config for this server
                await loadWelcomeConfig(serverId);
                document.getElementById('welcomeCard').style.display = 'block';
                
            } catch (e) {
                console.error('Load channels error:', e);
                document.getElementById('channelList').innerHTML = `<div style="color: #ff6b6b;">❌ ${e.message}</div>`;
            }
        }

        function selectChannel(channelId, element) {
            element.classList.toggle('selected');
            if (element.classList.contains('selected')) {
                if (!selectedChannels.includes(channelId)) {
                    selectedChannels.push(channelId);
                }
            } else {
                selectedChannels = selectedChannels.filter(id => id !== channelId);
            }
        }

        async function loadWelcomeConfig(guildId) {
            try {
                const response = await fetch(`/api/welcome/config?guild_id=${guildId}`);
                const data = await response.json();
                
                if (data.success && data.config) {
                    document.getElementById('welcomeChannel').value = data.config.channel_id || '';
                    document.getElementById('welcomeMsg').value = data.config.message || 'Welcome {user} to {server}!';
                    document.getElementById('welcomeEnabled').checked = data.config.enabled || false;
                } else {
                    document.getElementById('welcomeChannel').value = '';
                    document.getElementById('welcomeMsg').value = 'Welcome {user} to {server}!';
                    document.getElementById('welcomeEnabled').checked = false;
                }
                
                // Update channel options
                if (selectedServer === guildId) {
                    const channelList = document.getElementById('channelList');
                    const channelSelect = document.getElementById('welcomeChannel');
                    const channels = Array.from(channelList.querySelectorAll('.channel-item')).map(el => ({
                        id: el.dataset.channelId,
                        name: el.textContent.substring(1)
                    }));
                    
                    channelSelect.innerHTML = '<option value="">Choose a channel...</option>' +
                        channels.map(c => `<option value="${c.id}">#${c.name}</option>`).join('');
                }
                
            } catch (e) {
                console.error('Load welcome config error:', e);
            }
        }

        async function saveWelcome() {
            if (!selectedServer) {
                showToast('Select a server first', 'error');
                return;
            }
            
            const guildId = selectedServer;
            const channelId = document.getElementById('welcomeChannel').value;
            const message = document.getElementById('welcomeMsg').value;
            const enabled = document.getElementById('welcomeEnabled').checked;
            
            if (!channelId) {
                showToast('Select a welcome channel', 'error');
                return;
            }
            
            showToast('Saving welcome config...', 'info');
            
            try {
                const response = await fetch('/api/welcome/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        guild_id: guildId,
                        channel_id: channelId,
                        message: message,
                        embeds: [],
                        enabled: enabled
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showToast(data.message || 'Welcome config saved!', 'success');
                } else {
                    showToast(data.error || 'Failed to save', 'error');
                }
                
            } catch (e) {
                showToast('Error saving config', 'error');
            }
        }

        // ===== MESSAGE COMPOSER =====
        function setupEventListeners() {
            const textarea = document.getElementById('messageContent');
            const counter = document.getElementById('charCounter');
            
            textarea.addEventListener('input', () => {
                counter.textContent = `${textarea.value.length} / 2000`;
                counter.style.color = textarea.value.length > 2000 ? '#ff6b6b' : '#b9bbbe';
            });
            
            const fileZone = document.querySelector('.file-upload');
            const fileInput = document.getElementById('fileInput');
            
            fileZone.addEventListener('dragover', e => {
                e.preventDefault();
                fileZone.style.borderColor = '#5865F2';
            });
            
            fileZone.addEventListener('dragleave', () => {
                fileZone.style.borderColor = '#62646e';
            });
            
            fileZone.addEventListener('drop', e => {
                e.preventDefault();
                fileZone.style.borderColor = '#62646e';
                handleFiles(Array.from(e.dataTransfer.files));
            });
            
            fileInput.addEventListener('change', e => {
                handleFiles(Array.from(e.target.files));
            });
        }

        async function handleFiles(files) {
            const maxSize = 25 * 1024 * 1024;
            const allowedTypes = ['image/', 'application/pdf', 'text/'];
            
            for (const file of files) {
                if (file.size > maxSize) {
                    showToast(`'${file.name}' exceeds 25MB limit`, 'error');
                    continue;
                }
                
                const isAllowed = allowedTypes.some(type => file.type.startsWith(type)) ||
                                 file.name.endsWith('.pdf') || file.name.endsWith('.txt');
                
                if (!isAllowed) {
                    showToast(`'${file.name}' type not supported`, 'error');
                    continue;
                }
            }
            
            const formData = new FormData();
            files.forEach(f => formData.append('files', f));
            
            showToast('Uploading files...', 'info');
            
            try {
                const response = await fetch('/api/files', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success) {
                    uploadedFiles.push(...data.files);
                    renderFiles();
                    showToast(`Uploaded ${data.files.length} file(s)`, 'success');
                } else {
                    showToast(data.error || 'Upload failed', 'error');
                }
                
            } catch (e) {
                showToast('Upload error occurred', 'error');
            }
        }

        function renderFiles() {
            const container = document.getElementById('fileList');
            container.innerHTML = uploadedFiles.map(f => `
                <div class="file-item">
                    <span>📄 ${f.filename}</span>
                    <span class="remove-file" onclick="removeFile('${f.path}')">×</span>
                </div>
            `).join('');
        }

        function removeFile(path) {
            uploadedFiles = uploadedFiles.filter(f => f.path !== path);
            renderFiles();
        }

        // ===== EMBED BUILDER =====
        function addEmbed() {
            if (embeds.length >= 10) {
                showToast('Maximum 10 embeds allowed', 'error');
                return;
            }
            
            embeds.push({
                title: '',
                description: '',
                color: '#5865F2',
                author: { name: '', url: '', icon_url: '' },
                fields: [],
                thumbnail: '',
                image: '',
                footer: { text: '', icon_url: '' },
                timestamp: false
            });
            
            renderEmbeds();
        }

        function removeEmbed(index) {
            embeds.splice(index, 1);
            renderEmbeds();
        }

        function addField(embedIndex) {
            embeds[embedIndex].fields.push({
                name: '',
                value: '',
                inline: true
            });
            renderEmbeds();
        }

        function removeField(embedIndex, fieldIndex) {
            embeds[embedIndex].fields.splice(fieldIndex, 1);
            renderEmbeds();
        }

        function renderEmbeds() {
            const container = document.getElementById('embedList');
            container.innerHTML = embeds.map((embed, i) => `
                <div class="card">
                    <h2>Embed #${i + 1} <button class="btn btn-danger" onclick="removeEmbed(${i})" style="padding: 4px 8px;">Remove</button></h2>
                    <div class="form-group"><input type="text" placeholder="Title" value="${embed.title}" onchange="embeds[${i}].title = this.value"></div>
                    <div class="form-group"><textarea rows="3" placeholder="Description" onchange="embeds[${i}].description = this.value">${embed.description}</textarea></div>
                    <div class="form-group"><input type="color" value="${embed.color}" onchange="embeds[${i}].color = this.value"> Embed Color</div>
                    <div class="form-group"><input type="text" placeholder="Author Name" value="${embed.author.name}" onchange="embeds[${i}].author.name = this.value"></div>
                    <div class="section-title">Fields <button class="btn btn-secondary" onclick="addField(${i})" style="padding: 4px 8px;">+ Add Field</button></div>
                    ${embed.fields.map((field, fi) => `
                        <div class="field-item" style="background: #62646e; padding: 10px; border-radius: 4px; margin-bottom: 8px;">
                            <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                                <strong>Field #${fi + 1}</strong>
                                <button class="btn btn-danger" onclick="removeField(${i}, ${fi})" style="padding: 2px 6px;">×</button>
                            </div>
                            <input type="text" placeholder="Field Name" value="${field.name}" onchange="embeds[${i}].fields[${fi}].name = this.value">
                            <input type="text" placeholder="Field Value" value="${field.value}" onchange="embeds[${i}].fields[${fi}].value = this.value" style="margin-top: 5px;">
                            <label style="margin-top: 5px;"><input type="checkbox" ${field.inline ? 'checked' : ''} onchange="embeds[${i}].fields[${fi}].inline = this.checked"> Inline</label>
                        </div>
                    `).join('')}
                </div>
            `).join('');
        }

        // ===== MESSAGE ACTIONS =====
        async function sendMessage() {
            if (!botReady) {
                showToast('Bot is still initializing. Please wait...', 'error');
                return;
            }
            
            if (selectedChannels.length === 0) {
                showToast('Select at least one channel', 'error');
                return;
            }
            
            const content = document.getElementById('messageContent').value;
            if (!content && embeds.length === 0 && uploadedFiles.length === 0) {
                showToast('Message cannot be empty', 'error');
                return;
            }
            
            if (content.length > 2000) {
                showToast('Message exceeds 2000 character limit', 'error');
                return;
            }
            
            showToast('Sending messages...', 'info');
            
            try {
                const response = await fetch('/api/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        channel_ids: selectedChannels,
                        content: content,
                        embeds: embeds,
                        files: uploadedFiles.map(f => f.path)
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showToast(`Messages sent to ${selectedChannels.length} channel(s)!`, 'success');
                    document.getElementById('messageContent').value = '';
                    embeds = [];
                    uploadedFiles = [];
                    renderEmbeds();
                    renderFiles();
                } else {
                    showToast(data.error || 'Failed to send', 'error');
                }
                
            } catch (e) {
                showToast('Send error occurred', 'error');
            }
        }

        async function scheduleMessage() {
            if (!botReady) {
                showToast('Bot is initializing...', 'error');
                return;
            }
            
            if (selectedChannels.length === 0) {
                showToast('Select channels', 'error');
                return;
            }
            
            const content = document.getElementById('messageContent').value;
            if (!content && embeds.length === 0 && uploadedFiles.length === 0) {
                showToast('Message empty', 'error');
                return;
            }
            
            const now = Math.floor(Date.now() / 1000);
            const scheduledTime = prompt(
                `Schedule Message\\n\\n` +
                `Current time: ${new Date(now * 1000).toLocaleString()}\\n` +
                `Example: ${new Date((now + 3600) * 1000).toLocaleString()} (1 hour from now)\\n\\n` +
                `Enter timestamp in seconds:`
            );
            
            if (!scheduledTime) return;
            
            try {
                const timestamp = parseInt(scheduledTime);
                if (isNaN(timestamp) || timestamp <= now) {
                    showToast('Invalid time. Must be in future.', 'error');
                    return;
                }
                
                showToast('Scheduling...', 'info');
                
                const response = await fetch('/api/schedule', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        channel_ids: selectedChannels,
                        content: content,
                        embeds: embeds,
                        files: uploadedFiles.map(f => f.path),
                        scheduled_time: timestamp
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showToast(data.message || 'Message scheduled!', 'success');
                } else {
                    showToast(data.error || 'Schedule failed', 'error');
                }
                
            } catch (e) {
                showToast('Schedule error', 'error');
            }
        }

        async function saveTemplate() {
            const name = prompt('Template name:');
            if (!name) return;
            
            if (name.length > 50) {
                showToast('Name too long (max 50 chars)', 'error');
                return;
            }
            
            const response = await fetch('/api/templates', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    name: name,
                    content: document.getElementById('messageContent').value,
                    embeds: embeds
                })
            });
            
            const data = await response.json();
            
            if (data.success) {
                showToast('Template saved!', 'success');
                location.reload();
            } else {
                showToast(data.error || 'Save failed', 'error');
            }
        }

        async function loadTemplate(id) {
            try {
                const response = await fetch('/api/templates');
                const data = await response.json();
                
                if (!response.ok) throw new Error(data.error);
                
                const template = data.templates.find(t => t.id === id);
                if (template) {
                    document.getElementById('messageContent').value = template.content;
                    embeds = JSON.parse(template.embed_data) || [];
                    renderEmbeds();
                    showToast('Template loaded', 'success');
                }
            } catch (e) {
                showToast('Load template failed', 'error');
            }
        }

        async function deleteTemplate(id) {
            if (!confirm('Delete this template?')) return;
            
            const response = await fetch(`/api/templates?id=${id}`, {
                method: 'DELETE'
            });
            
            const data = await response.json();
            
            if (data.success) {
                showToast('Template deleted', 'success');
                location.reload();
            } else {
                showToast(data.error || 'Delete failed', 'error');
            }
        }

        async function resendMessage(msgId) {
            if (!confirm('Resend this message?')) return;
            
            showToast('Resending...', 'info');
            
            try {
                const response = await fetch(`/api/resend/${msgId}`, {
                    method: 'POST'
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showToast('Message resent!', 'success');
                } else {
                    showToast(data.error || 'Resend failed', 'error');
                }
                
            } catch (e) {
                showToast('Resend error', 'error');
            }
        }

        // ===== UTILITY FUNCTIONS =====
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const mainContent = document.getElementById('mainContent');
            sidebar.classList.toggle('collapsed');
            mainContent.classList.toggle('expanded');
            
            // Save preference
            const isCollapsed = sidebar.classList.contains('collapsed');
            localStorage.setItem('sidebarCollapsed', isCollapsed);
        }

        function logout() {
            if (confirm('Are you sure you want to logout?')) {
                window.location.href = '/logout';
            }
        }

        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = `toast ${type} show`;
            
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }

        // Load sidebar preference
        window.addEventListener('load', () => {
            const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
            if (isCollapsed) {
                document.getElementById('sidebar').classList.add('collapsed');
                document.getElementById('mainContent').classList.add('expanded');
            }
        });
    </script>
</body>
</html>
'''

# ============================================================================
# ERROR PAGE TEMPLATE
# ============================================================================

ERROR_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error - Discord Dashboard</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1e1f29;
            color: white;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .error-card {
            background: #2a2b38;
            padding: 30px;
            border-radius: 8px;
            border: 1px solid #40424e;
            max-width: 500px;
        }
        h1 { color: #ff6b6b; }
        .btn {
            background: #5865F2;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            margin-top: 15px;
        }
    </style>
</head>
<body>
    <div class="error-card">
        <h1>❌ Error</h1>
        <p>{{ error }}</p>
        <a href="/" class="btn">Try Again</a>
    </div>
</body>
</html>
'''

# ============================================================================
# APPLICATION RUNNER
# ============================================================================

def run_bot():
    """Run bot in separate thread"""
    print("\n🤖 Starting Discord bot...")
    bot_manager.run()

def run_app():
    """Run Flask app"""
    print("\n🌐 Starting Flask server...")
    app.run(
        host=config.host,
        port=config.port,
        debug=False,
        threaded=True
    )

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 DISCORD MESSAGE DASHBOARD - PRODUCTION SERVER")
    print("="*60)
    print(f"📡 Port: {config.port}")
    print(f"🔐 OAuth Redirect: {config.redirect_uri}")
    print(f"🤖 Bot User: Loading...")
    print("="*60 + "\n")
    
    # Start bot in thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Give bot time to start
    time.sleep(2)
    
    # Start Flask app
    run_app()
