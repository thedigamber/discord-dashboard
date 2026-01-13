import discord
from discord.ext import commands
from flask import Flask, render_template_string, request, jsonify
import os
import asyncio
import json
import time
from datetime import datetime

# ========== CONFIG ==========
TOKEN = os.environ.get('BOT_TOKEN')

# ========== FLASK APP ==========
app = Flask(__name__)

# HTML Template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🤖 Discord Message Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Arial, sans-serif;
        }
        
        body {
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            color: white;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            padding: 40px 0;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 20px;
            margin-bottom: 30px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .title {
            font-size: 3rem;
            background: linear-gradient(90deg, #00dbde, #fc00ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        
        .subtitle {
            color: #aaa;
            font-size: 1.2rem;
        }
        
        .dashboard {
            display: grid;
            grid-template-columns: 300px 1fr;
            gap: 30px;
            margin-top: 30px;
        }
        
        .sidebar {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .main-content {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 30px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .guild-list {
            max-height: 500px;
            overflow-y: auto;
        }
        
        .guild-item {
            display: flex;
            align-items: center;
            padding: 15px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            margin-bottom: 10px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .guild-item:hover {
            background: rgba(255, 255, 255, 0.12);
            transform: translateX(5px);
        }
        
        .guild-item.active {
            background: linear-gradient(90deg, #00dbde, #0093E9);
        }
        
        .guild-icon {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            margin-right: 15px;
            background: #333;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
        }
        
        .guild-name {
            font-size: 1.1rem;
            font-weight: 500;
        }
        
        .form-group {
            margin-bottom: 25px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            color: #00ff88;
            font-weight: 500;
        }
        
        select, textarea, input {
            width: 100%;
            padding: 15px;
            background: rgba(255, 255, 255, 0.08);
            border: 2px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            color: white;
            font-size: 1rem;
            transition: border 0.3s;
        }
        
        select:focus, textarea:focus, input:focus {
            outline: none;
            border-color: #00ff88;
        }
        
        textarea {
            min-height: 150px;
            resize: vertical;
            font-family: monospace;
        }
        
        .preview-box {
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
            padding: 20px;
            margin: 20px 0;
            border-left: 4px solid #00ff88;
            white-space: pre-wrap;
            font-family: monospace;
        }
        
        .button-group {
            display: flex;
            gap: 15px;
            margin-top: 30px;
        }
        
        .btn {
            padding: 15px 30px;
            border: none;
            border-radius: 10px;
            font-size: 1.1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .btn-primary {
            background: linear-gradient(90deg, #00dbde, #0093E9);
            color: white;
        }
        
        .btn-primary:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 20px rgba(0, 219, 222, 0.3);
        }
        
        .btn-secondary {
            background: rgba(255, 255, 255, 0.1);
            color: white;
        }
        
        .btn-secondary:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        .emoji-picker {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(40px, 1fr));
            gap: 10px;
            margin-top: 10px;
            max-height: 200px;
            overflow-y: auto;
            padding: 10px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
        }
        
        .emoji-item {
            font-size: 24px;
            text-align: center;
            padding: 5px;
            cursor: pointer;
            border-radius: 5px;
            transition: background 0.3s;
        }
        
        .emoji-item:hover {
            background: rgba(255, 255, 255, 0.1);
        }
        
        .message-log {
            max-height: 300px;
            overflow-y: auto;
            margin-top: 30px;
            padding: 20px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
        }
        
        .log-item {
            padding: 15px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            margin-bottom: 10px;
            border-left: 3px solid #00ff88;
        }
        
        .log-time {
            color: #00ff88;
            font-size: 0.8rem;
            margin-bottom: 5px;
        }
        
        .log-content {
            font-size: 0.9rem;
            line-height: 1.4;
        }
        
        .status-bar {
            display: flex;
            justify-content: space-between;
            background: rgba(255, 255, 255, 0.05);
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .status-item {
            text-align: center;
        }
        
        .status-value {
            font-size: 1.5rem;
            font-weight: bold;
            color: #00ff88;
        }
        
        .status-label {
            color: #aaa;
            font-size: 0.9rem;
        }
    </style>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 class="title">🤖 Discord Message Dashboard</h1>
            <p class="subtitle">Send messages to any server where bot is present</p>
        </div>
        
        <div class="status-bar">
            <div class="status-item">
                <div class="status-value" id="serverCount">0</div>
                <div class="status-label">Servers</div>
            </div>
            <div class="status-item">
                <div class="status-value" id="channelCount">0</div>
                <div class="status-label">Channels</div>
            </div>
            <div class="status-item">
                <div class="status-value" id="emojiCount">0</div>
                <div class="status-label">Emojis</div>
            </div>
            <div class="status-item">
                <div class="status-value"><i class="fas fa-circle" style="color:#00ff88"></i></div>
                <div class="status-label">Status</div>
            </div>
        </div>
        
        <div class="dashboard">
            <!-- Sidebar: Server List -->
            <div class="sidebar">
                <h3><i class="fas fa-server"></i> Select Server</h3>
                <div class="guild-list" id="guildList">
                    <!-- Guilds will be loaded here -->
                    <div class="guild-item">
                        <div class="guild-icon">🤖</div>
                        <div class="guild-name">Loading servers...</div>
                    </div>
                </div>
            </div>
            
            <!-- Main Content: Message Form -->
            <div class="main-content">
                <h3><i class="fas fa-edit"></i> Compose Message</h3>
                
                <div class="form-group">
                    <label><i class="fas fa-hashtag"></i> Select Channel</label>
                    <select id="channelSelect">
                        <option value="">Select a server first</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label><i class="fas fa-heading"></i> Message Title</label>
                    <input type="text" id="messageTitle" placeholder="Enter message title (optional)">
                </div>
                
                <div class="form-group">
                    <label><i class="fas fa-comment"></i> Message Content</label>
                    <textarea id="messageContent" placeholder="Type your message here..."></textarea>
                </div>
                
                <div class="form-group">
                    <label><i class="fas fa-smile"></i> Emoji Picker</label>
                    <div class="emoji-picker" id="emojiPicker">
                        <!-- Emojis will be loaded here -->
                        <div class="emoji-item">😀</div>
                        <div class="emoji-item">😂</div>
                        <div class="emoji-item">❤️</div>
                        <div class="emoji-item">🔥</div>
                    </div>
                </div>
                
                <div class="form-group">
                    <label><i class="fas fa-eye"></i> Preview</label>
                    <div class="preview-box" id="messagePreview">
                        Preview will appear here...
                    </div>
                </div>
                
                <div class="button-group">
                    <button class="btn btn-primary" onclick="sendMessage()">
                        <i class="fas fa-paper-plane"></i> Send Message
                    </button>
                    <button class="btn btn-secondary" onclick="previewMessage()">
                        <i class="fas fa-eye"></i> Update Preview
                    </button>
                    <button class="btn btn-secondary" onclick="clearForm()">
                        <i class="fas fa-trash"></i> Clear
                    </button>
                </div>
                
                <!-- Message Log -->
                <div class="message-log">
                    <h4><i class="fas fa-history"></i> Recent Messages</h4>
                    <div id="messageLog">
                        <div class="log-item">
                            <div class="log-time">No messages sent yet</div>
                            <div class="log-content">Send your first message to see it here!</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let selectedGuild = null;
        let selectedChannel = null;
        let emojis = [];
        
        // Load guilds on page load
        document.addEventListener('DOMContentLoaded', function() {
            loadGuilds();
            updateEmojiPicker();
            updatePreview();
        });
        
        // Load guilds from API
        async function loadGuilds() {
            try {
                const response = await fetch('/api/guilds');
                const data = await response.json();
                
                const guildList = document.getElementById('guildList');
                guildList.innerHTML = '';
                
                data.guilds.forEach(guild => {
                    const guildItem = document.createElement('div');
                    guildItem.className = 'guild-item';
                    guildItem.innerHTML = `
                        <div class="guild-icon">${guild.icon ? `<img src="${guild.icon}" alt="${guild.name}">` : '🤖'}</div>
                        <div class="guild-name">${guild.name}</div>
                    `;
                    
                    guildItem.onclick = () => selectGuild(guild);
                    guildList.appendChild(guildItem);
                });
                
                // Update stats
                document.getElementById('serverCount').textContent = data.guilds.length;
                document.getElementById('channelCount').textContent = data.total_channels || 0;
                document.getElementById('emojiCount').textContent = data.total_emojis || 0;
                
            } catch (error) {
                console.error('Error loading guilds:', error);
            }
        }
        
        // Select guild
        function selectGuild(guild) {
            selectedGuild = guild;
            
            // Update UI
            document.querySelectorAll('.guild-item').forEach(item => {
                item.classList.remove('active');
            });
            event.currentTarget.classList.add('active');
            
            // Load channels for this guild
            loadChannels(guild.id);
            loadEmojis(guild.id);
        }
        
        // Load channels
        async function loadChannels(guildId) {
            try {
                const response = await fetch(`/api/channels?guild_id=${guildId}`);
                const data = await response.json();
                
                const channelSelect = document.getElementById('channelSelect');
                channelSelect.innerHTML = '<option value="">Select a channel</option>';
                
                data.channels.forEach(channel => {
                    const option = document.createElement('option');
                    option.value = channel.id;
                    option.textContent = `#${channel.name}`;
                    channelSelect.appendChild(option);
                });
                
                channelSelect.onchange = function() {
                    selectedChannel = this.value;
                };
                
            } catch (error) {
                console.error('Error loading channels:', error);
            }
        }
        
        // Load emojis
        async function loadEmojis(guildId) {
            try {
                const response = await fetch(`/api/emojis?guild_id=${guildId}`);
                const data = await response.json();
                
                emojis = data.emojis || [];
                updateEmojiPicker();
                
            } catch (error) {
                console.error('Error loading emojis:', error);
            }
        }
        
        // Update emoji picker
        function updateEmojiPicker() {
            const emojiPicker = document.getElementById('emojiPicker');
            emojiPicker.innerHTML = '';
            
            // Default emojis
            const defaultEmojis = ['😀', '😂', '❤️', '🔥', '👍', '🎉', '✨', '🌟', '🚀', '💯'];
            
            defaultEmojis.forEach(emoji => {
                const emojiItem = document.createElement('div');
                emojiItem.className = 'emoji-item';
                emojiItem.textContent = emoji;
                emojiItem.onclick = () => insertEmoji(emoji);
                emojiPicker.appendChild(emojiItem);
            });
            
            // Custom emojis
            emojis.forEach(emoji => {
                const emojiItem = document.createElement('div');
                emojiItem.className = 'emoji-item';
                emojiItem.innerHTML = emoji;
                emojiItem.onclick = () => insertEmoji(emoji);
                emojiPicker.appendChild(emojiItem);
            });
        }
        
        // Insert emoji into message
        function insertEmoji(emoji) {
            const textarea = document.getElementById('messageContent');
            const cursorPos = textarea.selectionStart;
            const text = textarea.value;
            
            textarea.value = text.substring(0, cursorPos) + emoji + text.substring(cursorPos);
            textarea.focus();
            textarea.selectionStart = textarea.selectionEnd = cursorPos + emoji.length;
            
            updatePreview();
        }
        
        // Update preview
        function updatePreview() {
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            const preview = document.getElementById('messagePreview');
            
            let previewHTML = '';
            
            if (title) {
                previewHTML += `<strong>${title}</strong>\\n\\n`;
            }
            
            previewHTML += content || 'No content entered yet';
            
            preview.textContent = previewHTML;
        }
        
        // Send message
        async function sendMessage() {
            if (!selectedGuild || !selectedChannel) {
                alert('Please select a server and channel first!');
                return;
            }
            
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            
            if (!content.trim()) {
                alert('Please enter a message!');
                return;
            }
            
            try {
                const response = await fetch('/api/send', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        guild_id: selectedGuild.id,
                        channel_id: selectedChannel,
                        title: title,
                        content: content
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    alert('✅ Message sent successfully!');
                    addToMessageLog(title, content, selectedGuild.name);
                    clearForm();
                } else {
                    alert('❌ Error: ' + (result.error || 'Failed to send message'));
                }
                
            } catch (error) {
                alert('❌ Network error: ' + error.message);
            }
        }
        
        // Add to message log
        function addToMessageLog(title, content, guildName) {
            const log = document.getElementById('messageLog');
            const now = new Date().toLocaleTimeString();
            
            const logItem = document.createElement('div');
            logItem.className = 'log-item';
            logItem.innerHTML = `
                <div class="log-time">${now} • ${guildName}</div>
                <div class="log-content">
                    ${title ? `<strong>${title}</strong><br>` : ''}
                    ${content.substring(0, 100)}${content.length > 100 ? '...' : ''}
                </div>
            `;
            
            log.insertBefore(logItem, log.firstChild);
            
            // Keep only last 10 logs
            const items = log.getElementsByClassName('log-item');
            if (items.length > 10) {
                log.removeChild(items[items.length - 1]);
            }
        }
        
        // Preview message
        function previewMessage() {
            updatePreview();
        }
        
        // Clear form
        function clearForm() {
            document.getElementById('messageTitle').value = '';
            document.getElementById('messageContent').value = '';
            updatePreview();
        }
        
        // Auto-update preview
        document.getElementById('messageTitle').addEventListener('input', updatePreview);
        document.getElementById('messageContent').addEventListener('input', updatePreview);
    </script>
</body>
</html>
'''

# ========== FLASK ROUTES ==========
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/guilds')
def api_guilds():
    """Get all guilds where bot is present"""
    guilds = []
    total_channels = 0
    total_emojis = 0
    
    for guild in bot.guilds:
        guild_info = {
            'id': str(guild.id),
            'name': guild.name,
            'icon': str(guild.icon.url) if guild.icon else None,
            'member_count': guild.member_count,
            'channel_count': len(guild.channels),
            'emoji_count': len(guild.emojis)
        }
        guilds.append(guild_info)
        
        total_channels += len(guild.channels)
        total_emojis += len(guild.emojis)
    
    return jsonify({
        'guilds': guilds,
        'total_channels': total_channels,
        'total_emojis': total_emojis
    })

@app.route('/api/channels')
def api_channels():
    """Get channels for a specific guild"""
    guild_id = request.args.get('guild_id')
    
    if not guild_id:
        return jsonify({'channels': []})
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'channels': []})
    
    # Get only text channels where bot can send messages
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
    """Get emojis for a specific guild"""
    guild_id = request.args.get('guild_id')
    
    if not guild_id:
        return jsonify({'emojis': []})
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'emojis': []})
    
    emojis = []
    for emoji in guild.emojis:
        emojis.append(str(emoji))
    
    return jsonify({'emojis': emojis})

@app.route('/api/send', methods=['POST'])
def api_send():
    """Send message to channel"""
    try:
        data = request.json
        guild_id = int(data['guild_id'])
        channel_id = int(data['channel_id'])
        title = data.get('title', '')
        content = data.get('content', '')
        
        guild = bot.get_guild(guild_id)
        if not guild:
            return jsonify({'success': False, 'error': 'Guild not found'})
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return jsonify({'success': False, 'error': 'Channel not found'})
        
        # Check permissions
        if not channel.permissions_for(guild.me).send_messages:
            return jsonify({'success': False, 'error': 'No permission to send messages'})
        
        # Create embed if title is provided
        if title:
            embed = discord.Embed(
                title=title,
                description=content,
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            asyncio.run_coroutine_threadsafe(channel.send(embed=embed), bot.loop)
        else:
            asyncio.run_coroutine_threadsafe(channel.send(content), bot.loop)
        
        # Log the message
        log_message = {
            'timestamp': time.time(),
            'guild': guild.name,
            'channel': channel.name,
            'title': title,
            'content': content[:100] + '...' if len(content) > 100 else content
        }
        bot_data['message_history'].append(log_message)
        
        # Keep only last 100 messages
        if len(bot_data['message_history']) > 100:
            bot_data['message_history'] = bot_data['message_history'][-100:]
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/messages')
def api_messages():
    """Get recent messages"""
    return jsonify({'messages': bot_data['message_history'][-10:]})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'bot': 'online'})

# ========== DISCORD BOT EVENTS ==========
@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    print(f"📊 Serving {len(bot.guilds)} servers")
    print(f"🌐 Dashboard available at: http://localhost:5000")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Message Dashboard"
        )
    )
    
    # Update bot data
    for guild in bot.guilds:
        bot_data['connected_guilds'].append({
            'id': str(guild.id),
            'name': guild.name
        })
        
        # Store emojis
        emoji_list = [str(emoji) for emoji in guild.emojis]
        bot_data['available_emojis'][str(guild.id)] = emoji_list

# ========== RUN APPLICATION ==========
def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def main():
    print("🚀 Starting Discord Message Dashboard...")
    
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Discord bot
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
