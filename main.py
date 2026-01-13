import discord
from discord.ext import commands
from flask import Flask, render_template_string, redirect, url_for, session, request, jsonify
import os
import time
import asyncio
import json
import threading
from datetime import datetime

# ========== CONFIGURATION ==========
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET')
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
DISCORD_REDIRECT_URI = os.environ.get('DISCORD_REDIRECT_URI', 'https://dashboard.digamber.in/callback')
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')

# ========== FLASK APP ==========
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# ========== DISCORD BOT ==========
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.emojis = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Storage
user_sessions = {}
message_history = []

# HTML TEMPLATE
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🤖 Discord Message Dashboard</title>
    <style>
        body {
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            color: white;
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            padding: 40px 0;
            background: rgba(255,255,255,0.05);
            border-radius: 20px;
            margin-bottom: 30px;
        }
        h1 {
            font-size: 3em;
            background: linear-gradient(90deg, #00dbde, #fc00ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        .login-btn {
            background: #7289da;
            color: white;
            padding: 15px 30px;
            border: none;
            border-radius: 5px;
            font-size: 1.2em;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            margin-top: 20px;
        }
        .login-btn:hover {
            background: #5b6eae;
        }
        .dashboard {
            display: grid;
            grid-template-columns: 300px 1fr;
            gap: 20px;
        }
        .sidebar {
            background: rgba(255,255,255,0.05);
            padding: 20px;
            border-radius: 10px;
        }
        .main {
            background: rgba(255,255,255,0.05);
            padding: 20px;
            border-radius: 10px;
        }
        .guild-item {
            padding: 10px;
            margin: 5px 0;
            background: rgba(255,255,255,0.1);
            border-radius: 5px;
            cursor: pointer;
        }
        .guild-item:hover {
            background: rgba(255,255,255,0.2);
        }
        .channel-item {
            padding: 8px;
            margin: 3px 0;
            background: rgba(255,255,255,0.05);
            border-radius: 3px;
            cursor: pointer;
        }
        .channel-item:hover {
            background: rgba(255,255,255,0.1);
        }
        textarea, input, select {
            width: 100%;
            padding: 10px;
            margin: 10px 0;
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 5px;
            color: white;
        }
        button {
            background: #00ff88;
            color: black;
            padding: 12px 24px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
            margin: 5px;
        }
        button:hover {
            background: #00cc66;
        }
        .emoji-picker {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin: 10px 0;
        }
        .emoji {
            font-size: 24px;
            cursor: pointer;
            padding: 5px;
        }
        .emoji:hover {
            background: rgba(255,255,255,0.2);
            border-radius: 5px;
        }
        .preview {
            background: rgba(0,0,0,0.3);
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
            white-space: pre-wrap;
        }
        .message-log {
            max-height: 300px;
            overflow-y: auto;
            margin-top: 20px;
        }
        .log-item {
            padding: 10px;
            margin: 5px 0;
            background: rgba(255,255,255,0.05);
            border-radius: 5px;
            border-left: 3px solid #00ff88;
        }
    </style>
</head>
<body>
    {% if not user %}
    <div class="container">
        <div class="header">
            <h1>🤖 Discord Message Dashboard</h1>
            <p>Send messages to any server where bot is present</p>
            <a href="/login" class="login-btn">Login with Discord</a>
        </div>
    </div>
    {% else %}
    <div class="container">
        <div class="header">
            <h1>Welcome, {{ user.username }}!</h1>
            <p>Select a server and channel to send messages</p>
            <a href="/logout" style="color:#ff6666; text-decoration:none;">Logout</a>
        </div>
        
        <div class="dashboard">
            <div class="sidebar">
                <h3>Your Servers</h3>
                <div id="guilds">
                    {% for guild in guilds %}
                    <div class="guild-item" onclick="selectGuild('{{ guild.id }}', '{{ guild.name|replace("'", "\\'") }}')">
                        {{ guild.name }}
                    </div>
                    {% endfor %}
                </div>
            </div>
            
            <div class="main">
                <div id="selectedGuild" style="margin-bottom:20px; color:#00ff88;"></div>
                
                <div id="channelSection" style="display:none;">
                    <h3>Select Channel</h3>
                    <div id="channels"></div>
                </div>
                
                <div id="messageSection" style="display:none;">
                    <h3>Compose Message</h3>
                    
                    <input type="text" id="title" placeholder="Message Title (optional)">
                    
                    <textarea id="content" rows="6" placeholder="Type your message here..."></textarea>
                    
                    <div id="emojiSection">
                        <h4>Emojis</h4>
                        <div id="emojis" class="emoji-picker"></div>
                    </div>
                    
                    <h4>Preview</h4>
                    <div id="preview" class="preview"></div>
                    
                    <div>
                        <button onclick="sendMessage()">📨 Send Message</button>
                        <button onclick="sendEmbed()">🎨 Send as Embed</button>
                        <button onclick="clearForm()">🗑️ Clear</button>
                    </div>
                </div>
                
                <div class="message-log">
                    <h3>Recent Messages</h3>
                    <div id="logs"></div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let selectedGuildId = null;
        let selectedChannelId = null;
        
        function selectGuild(guildId, guildName) {
            selectedGuildId = guildId;
            document.getElementById('selectedGuild').innerHTML = `📁 Selected: <strong>${guildName}</strong>`;
            
            // Load channels
            fetch(`/api/channels?guild_id=${guildId}`)
                .then(r => r.json())
                .then(data => {
                    const channelsDiv = document.getElementById('channels');
                    channelsDiv.innerHTML = '';
                    
                    data.channels.forEach(channel => {
                        const div = document.createElement('div');
                        div.className = 'channel-item';
                        div.innerHTML = `#${channel.name}`;
                        div.onclick = () => selectChannel(channel.id, channel.name);
                        channelsDiv.appendChild(div);
                    });
                    
                    document.getElementById('channelSection').style.display = 'block';
                });
            
            // Load emojis
            fetch(`/api/emojis?guild_id=${guildId}`)
                .then(r => r.json())
                .then(data => {
                    const emojisDiv = document.getElementById('emojis');
                    emojisDiv.innerHTML = '';
                    
                    data.emojis.forEach(emoji => {
                        const span = document.createElement('span');
                        span.className = 'emoji';
                        span.innerHTML = emoji;
                        span.onclick = () => insertEmoji(emoji);
                        emojisDiv.appendChild(span);
                    });
                });
        }
        
        function selectChannel(channelId, channelName) {
            selectedChannelId = channelId;
            document.getElementById('selectedGuild').innerHTML += ` | 📢 Channel: <strong>${channelName}</strong>`;
            document.getElementById('messageSection').style.display = 'block';
            updatePreview();
        }
        
        function insertEmoji(emoji) {
            const textarea = document.getElementById('content');
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            textarea.value = textarea.value.substring(0, start) + emoji + textarea.value.substring(end);
            textarea.focus();
            textarea.selectionStart = textarea.selectionEnd = start + emoji.length;
            updatePreview();
        }
        
        function updatePreview() {
            const title = document.getElementById('title').value;
            const content = document.getElementById('content').value;
            const preview = document.getElementById('preview');
            
            let html = '';
            if (title) {
                html += `<strong style="color:#00ff88">${title}</strong><br><br>`;
            }
            html += content || 'No content entered';
            preview.innerHTML = html;
        }
        
        function sendMessage() {
            if (!selectedGuildId || !selectedChannelId) {
                alert('Please select a server and channel first!');
                return;
            }
            
            const title = document.getElementById('title').value;
            const content = document.getElementById('content').value;
            
            if (!content.trim()) {
                alert('Please enter a message!');
                return;
            }
            
            fetch('/api/send', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    guild_id: selectedGuildId,
                    channel_id: selectedChannelId,
                    title: title,
                    content: content,
                    embed: false
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert('✅ Message sent!');
                    addToLog(title, content, '📨');
                    clearForm();
                } else {
                    alert('❌ Error: ' + data.error);
                }
            });
        }
        
        function sendEmbed() {
            if (!selectedGuildId || !selectedChannelId) {
                alert('Please select a server and channel first!');
                return;
            }
            
            const title = document.getElementById('title').value;
            const content = document.getElementById('content').value;
            
            if (!content.trim()) {
                alert('Please enter a message!');
                return;
            }
            
            fetch('/api/send', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    guild_id: selectedGuildId,
                    channel_id: selectedChannelId,
                    title: title,
                    content: content,
                    embed: true
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert('✅ Embed sent!');
                    addToLog(title, content, '🎨');
                    clearForm();
                } else {
                    alert('❌ Error: ' + data.error);
                }
            });
        }
        
        function addToLog(title, content, type) {
            const logs = document.getElementById('logs');
            const time = new Date().toLocaleTimeString();
            const logItem = document.createElement('div');
            logItem.className = 'log-item';
            logItem.innerHTML = `<small>${time} ${type}</small><br>${title ? `<strong>${title}</strong><br>` : ''}${content.substring(0, 50)}...`;
            logs.insertBefore(logItem, logs.firstChild);
        }
        
        function clearForm() {
            document.getElementById('title').value = '';
            document.getElementById('content').value = '';
            updatePreview();
        }
        
        // Auto-update preview
        document.getElementById('title').addEventListener('input', updatePreview);
        document.getElementById('content').addEventListener('input', updatePreview);
        
        // Load message logs
        fetch('/api/logs')
            .then(r => r.json())
            .then(data => {
                const logs = document.getElementById('logs');
                data.messages.forEach(msg => {
                    const logItem = document.createElement('div');
                    logItem.className = 'log-item';
                    logItem.innerHTML = `<small>${new Date(msg.timestamp * 1000).toLocaleTimeString()} ${msg.type}</small><br>${msg.title ? `<strong>${msg.title}</strong><br>` : ''}${msg.content}`;
                    logs.appendChild(logItem);
                });
            });
    </script>
    {% endif %}
</body>
</html>
'''

# ========== FLASK ROUTES ==========
@app.route('/')
def index():
    user = session.get('user')
    if not user:
        return render_template_string(HTML_TEMPLATE, user=None)
    
    # Get user's guilds where bot is also present
    user_guilds = []
    for guild in bot.guilds:
        if str(guild.id) in session.get('user_guilds', []):
            user_guilds.append({
                'id': str(guild.id),
                'name': guild.name,
                'icon': str(guild.icon.url) if guild.icon else None
            })
    
    return render_template_string(HTML_TEMPLATE, user=user, guilds=user_guilds)

@app.route('/login')
def login():
    # Discord OAuth2 URL
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'identify guilds'
    }
    url = f"https://discord.com/api/oauth2/authorize?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    return redirect(url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400
    
    # Exchange code for token
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'scope': 'identify guilds'
    }
    
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    import requests
    r = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    if r.status_code != 200:
        return f"Token exchange failed: {r.text}", 400
    
    token_data = r.json()
    access_token = token_data['access_token']
    
    # Get user info
    headers = {'Authorization': f'Bearer {access_token}'}
    r = requests.get('https://discord.com/api/users/@me', headers=headers)
    if r.status_code != 200:
        return "Failed to get user info", 400
    
    user = r.json()
    
    # Get user guilds
    r = requests.get('https://discord.com/api/users/@me/guilds', headers=headers)
    user_guilds = r.json() if r.status_code == 200 else []
    
    # Store in session
    session['user'] = user
    session['access_token'] = access_token
    session['user_guilds'] = [str(g['id']) for g in user_guilds]
    
    # Store in bot data
    user_sessions[str(user['id'])] = {
        'user': user,
        'access_token': access_token,
        'guilds': [str(g['id']) for g in user_guilds]
    }
    
    return redirect('/')

@app.route('/logout')
def logout():
    user_id = session.get('user', {}).get('id')
    if user_id:
        user_sessions.pop(str(user_id), None)
    session.clear()
    return redirect('/')

@app.route('/api/channels')
def api_channels():
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'channels': []})
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'channels': []})
    
    channels = []
    for channel in guild.channels:
        if isinstance(channel, discord.TextChannel) and channel.permissions_for(guild.me).send_messages:
            channels.append({
                'id': str(channel.id),
                'name': channel.name,
                'type': 'text'
            })
    
    return jsonify({'channels': channels})

@app.route('/api/emojis')
def api_emojis():
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'emojis': []})
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'emojis': []})
    
    emojis = [str(emoji) for emoji in guild.emojis]
    return jsonify({'emojis': emojis})

@app.route('/api/send', methods=['POST'])
def api_send():
    try:
        data = request.json
        guild_id = int(data['guild_id'])
        channel_id = int(data['channel_id'])
        title = data.get('title', '')
        content = data.get('content', '')
        embed = data.get('embed', False)
        
        guild = bot.get_guild(guild_id)
        if not guild:
            return jsonify({'success': False, 'error': 'Guild not found'})
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return jsonify({'success': False, 'error': 'Channel not found'})
        
        if not channel.permissions_for(guild.me).send_messages:
            return jsonify({'success': False, 'error': 'No permission to send messages'})
        
        # Send message
        if embed:
            embed_obj = discord.Embed(
                title=title,
                description=content,
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            asyncio.run_coroutine_threadsafe(channel.send(embed=embed_obj), bot.loop)
        else:
            if title:
                final_content = f"**{title}**\n\n{content}"
            else:
                final_content = content
            asyncio.run_coroutine_threadsafe(channel.send(final_content), bot.loop)
        
        # Log
        message_history.append({
            'timestamp': time.time(),
            'guild': guild.name,
            'channel': channel.name,
            'title': title,
            'content': content[:50] + '...' if len(content) > 50 else content,
            'type': '🎨' if embed else '📨'
        })
        
        if len(message_history) > 50:
            message_history.pop(0)
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/logs')
def api_logs():
    return jsonify({'messages': message_history[-10:]})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'bot': 'online'})

# ========== BOT EVENTS ==========
@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    print(f"📊 Serving {len(bot.guilds)} servers")

# ========== RUN ==========
def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False)

def main():
    print("🚀 Starting Discord Message Dashboard...")
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
