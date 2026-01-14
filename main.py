import os
import sys
import json
import sqlite3
import requests
import base64
import hashlib
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from io import BytesIO
from PIL import Image
import aiofiles
import asyncio
import discord
from discord.ext import commands, tasks
from flask import Flask, request, redirect, session, render_template_string, jsonify, send_from_directory, url_for
from urllib.parse import urlencode
import urllib.parse

# Configuration
CONFIG = {
   'client_id': os.environ.get('DISCORD_CLIENT_ID'),
   'client_secret': os.environ.get('DISCORD_CLIENT_SECRET'),
   'bot_token': os.environ.get('DISCORD_BOT_TOKEN'),
   'redirect_uri': os.environ.get('DISCORD_REDIRECT_URI', 'https://dashboard.digamber.in/callback'),
   'secret_key': os.environ.get('FLASK_SECRET_KEY'),
   'app_port': int(os.environ.get('PORT', 8080)),
   'app_host': '0.0.0.0'
}

# Validate configuration
required_vars = ['client_id', 'client_secret', 'bot_token', 'secret_key']
missing = [var for var in required_vars if not CONFIG[var]]
if missing:
   print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
   sys.exit(1)

# Flask app
app = Flask(__name__)
app.secret_key = CONFIG['secret_key']

# Discord bot
intents = discord.Intents.default()
intents.message_content = False
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Database
DB_NAME = 'dashboard.db'
UPLOAD_DIR = 'uploads'

# Ensure upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Database initialization
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
       CREATE TABLE IF NOT EXISTS files (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           user_id INTEGER,
           filename TEXT,
           file_path TEXT,
           uploaded_at INTEGER
       )
   ''')
   
   c.execute('''
       CREATE TABLE IF NOT EXISTS analytics (
           date TEXT PRIMARY KEY,
           messages_sent INTEGER DEFAULT 0,
           files_sent INTEGER DEFAULT 0
       )
   ''')
   
   conn.commit()
   conn.close()

init_db()

# Database helpers
def get_db():
   conn = sqlite3.connect(DB_NAME)
   conn.row_factory = sqlite3.Row
   return conn

def update_analytics(message_increment=0, file_increment=0):
   today = datetime.now().strftime('%Y-%m-%d')
   conn = get_db()
   c = conn.cursor()
   c.execute('''
       INSERT INTO analytics (date, messages_sent, files_sent)
       VALUES (?, ?, ?)
       ON CONFLICT(date) DO UPDATE SET
           messages_sent = messages_sent + excluded.messages_sent,
           files_sent = files_sent + excluded.files_sent
   ''', (today, message_increment, file_increment))
   conn.commit()
   conn.close()

def get_analytics():
   today = datetime.now().strftime('%Y-%m-%d')
   week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
   month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
   
   conn = get_db()
   c = conn.cursor()
   
   c.execute('SELECT SUM(messages_sent) as total FROM analytics WHERE date = ?', (today,))
   today_messages = c.fetchone()['total'] or 0
   
   c.execute('SELECT SUM(messages_sent) as total FROM analytics WHERE date >= ?', (week_ago,))
   week_messages = c.fetchone()['total'] or 0
   
   c.execute('SELECT SUM(messages_sent) as total FROM analytics WHERE date >= ?', (month_ago,))
   month_messages = c.fetchone()['total'] or 0
   
   c.execute('SELECT SUM(files_sent) as total FROM analytics WHERE date = ?', (today,))
   today_files = c.fetchone()['total'] or 0
   
   conn.close()
   
   return {
       'today': today_messages,
       'week': week_messages,
       'month': month_messages,
       'files_today': today_files
   }

# Discord OAuth helpers
API_BASE_URL = 'https://discord.com/api/v10'
AUTHORIZATION_BASE_URL = f'{API_BASE_URL}/oauth2/authorize'
TOKEN_URL = f'{API_BASE_URL}/oauth2/token'

def generate_oauth_url():
   params = {
       'client_id': CONFIG['client_id'],
       'redirect_uri': CONFIG['redirect_uri'],
       'response_type': 'code',
       'scope': 'identify guilds'
   }
   return f'{AUTHORIZATION_BASE_URL}?{urlencode(params)}'

def exchange_code(code):
   data = {
       'client_id': CONFIG['client_id'],
       'client_secret': CONFIG['client_secret'],
       'grant_type': 'authorization_code',
       'code': code,
       'redirect_uri': CONFIG['redirect_uri']
   }
   headers = {'Content-Type': 'application/x-www-form-urlencoded'}
   response = requests.post(TOKEN_URL, data=data, headers=headers)
   return response.json()

def get_user_data(access_token):
   headers = {'Authorization': f'Bearer {access_token}'}
   response = requests.get(f'{API_BASE_URL}/users/@me', headers=headers)
   return response.json()

def get_user_guilds(access_token):
   headers = {'Authorization': f'Bearer {access_token}'}
   response = requests.get(f'{API_BASE_URL}/users/@me/guilds', headers=headers)
   return response.json()

# Discord bot helpers
async def get_mutual_guilds(user_id):
   user = await bot.fetch_user(user_id)
   mutual_guilds = []
   for guild in bot.guilds:
       try:
           member = await guild.fetch_member(user_id)
           if member:
               mutual_guilds.append({
                   'id': str(guild.id),
                   'name': guild.name,
                   'icon': str(guild.icon.url) if guild.icon else None
               })
       except:
           continue
   return mutual_guilds

async def get_guild_channels(guild_id, user_id):
   guild = bot.get_guild(int(guild_id))
   if not guild:
       return []
   
   channels = []
   for channel in guild.text_channels:
       permissions = channel.permissions_for(guild.get_member(int(user_id)))
       if permissions.send_messages:
           channels.append({
               'id': str(channel.id),
               'name': channel.name,
               'type': 'text'
           })
   return channels

async def send_message(channel_id, content, embeds=None, files=None):
   channel = bot.get_channel(int(channel_id))
   if not channel:
       return False, "Channel not found"
   
   try:
       if files:
           discord_files = []
           for file_path in files:
               if os.path.exists(file_path):
                   discord_files.append(discord.File(file_path))
           
           if embeds:
               await channel.send(content=content, embeds=embeds, files=discord_files)
           else:
               await channel.send(content=content, files=discord_files)
       elif embeds:
           await channel.send(content=content, embeds=embeds)
       else:
           await channel.send(content=content)
       
       update_analytics(messages_sent=1, files_sent=len(files) if files else 0)
       return True, "Message sent successfully"
   except Exception as e:
       return False, str(e)

# Background task for scheduled messages
async def process_scheduled_messages():
   await bot.wait_until_ready()
   while not bot.is_closed():
       try:
           conn = get_db()
           c = conn.cursor()
           current_time = int(time.time())
           
           c.execute('''
               SELECT * FROM messages 
               WHERE status = 'pending' AND scheduled_time <= ? 
               ORDER BY scheduled_time ASC
           ''', (current_time,))
           
           scheduled_messages = c.fetchall()
           
           for msg in scheduled_messages:
               user_id = msg['user_id']
               channel_id = msg['channel_id']
               content = msg['content']
               embed_data = json.loads(msg['embed_data']) if msg['embed_data'] else None
               files = json.loads(msg['files']) if msg['files'] else None
               
               embeds = []
               if embed_data:
                   for embed_info in embed_data:
                       embed = discord.Embed()
                       if embed_info.get('title'):
                           embed.title = embed_info['title']
                       if embed_info.get('description'):
                           embed.description = embed_info['description']
                       if embed_info.get('color'):
                           embed.color = int(embed_info['color'], 16)
                       if embed_info.get('author'):
                           embed.set_author(
                               name=embed_info['author'].get('name'),
                               url=embed_info['author'].get('url'),
                               icon_url=embed_info['author'].get('icon_url')
                           )
                       if embed_info.get('fields'):
                           for field in embed_info['fields']:
                               embed.add_field(
                                   name=field.get('name'),
                                   value=field.get('value'),
                                   inline=field.get('inline', False)
                               )
                       if embed_info.get('thumbnail'):
                           embed.set_thumbnail(url=embed_info['thumbnail'])
                       if embed_info.get('image'):
                           embed.set_image(url=embed_info['image'])
                       if embed_info.get('footer'):
                           embed.set_footer(
                               text=embed_info['footer'].get('text'),
                               icon_url=embed_info['footer'].get('icon_url')
                           )
                       if embed_info.get('timestamp'):
                           embed.timestamp = datetime.now()
                       
                       embeds.append(embed)
               
               success, status = await send_message(channel_id, content, embeds, files)
               
               c.execute('''
                   UPDATE messages 
                   SET status = ?, sent_time = ? 
                   WHERE id = ?
               ''', ('sent' if success else 'failed', current_time, msg['id']))
               
               conn.commit()
           
           conn.close()
       except Exception as e:
           print(f"Error processing scheduled messages: {e}")
       
       await asyncio.sleep(30)

# Flask routes
@app.route('/')
def dashboard():
   if 'user_id' not in session:
       return redirect(url_for('login'))
   
   analytics = get_analytics()
   
   conn = get_db()
   c = conn.cursor()
   c.execute('SELECT * FROM templates WHERE user_id = ?', (session['user_id'],))
   templates = c.fetchall()
   
   c.execute('''
       SELECT * FROM messages 
       WHERE user_id = ? 
       ORDER BY sent_time DESC 
       LIMIT 50
   ''', (session['user_id'],))
   message_history = c.fetchall()
   conn.close()
   
   return render_template_string(HTML_TEMPLATE, 
       user=session,
       oauth_url=generate_oauth_url(),
       analytics=analytics,
       templates=templates,
       message_history=message_history
   )

@app.route('/login')
def login():
   return redirect(generate_oauth_url())

@app.route('/callback')
def callback():
   code = request.args.get('code')
   if not code:
       return 'No authorization code provided', 400
   
   token_response = exchange_code(code)
   if 'access_token' not in token_response:
       return f'Failed to get access token: {token_response.get("error_description", "Unknown error")}', 400
   
   access_token = token_response['access_token']
   user_data = get_user_data(access_token)
   
   session['user_id'] = int(user_data['id'])
   session['username'] = user_data['username']
   session['avatar'] = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else None
   
   conn = get_db()
   c = conn.cursor()
   c.execute('''
       INSERT OR REPLACE INTO users (id, username, avatar, access_token)
       VALUES (?, ?, ?, ?)
   ''', (session['user_id'], session['username'], session['avatar'], access_token))
   conn.commit()
   conn.close()
   
   return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
   session.clear()
   return redirect(url_for('dashboard'))

@app.route('/health')
def health():
   return 'OK', 200

@app.route('/api/guilds')
async def api_guilds():
   if 'user_id' not in session:
       return jsonify({'error': 'Not authenticated'}), 401
   
   guilds = await get_mutual_guilds(session['user_id'])
   return jsonify(guilds)

@app.route('/api/channels')
async def api_channels():
   if 'user_id' not in session:
       return jsonify({'error': 'Not authenticated'}), 401
   
   guild_id = request.args.get('guild_id')
   if not guild_id:
       return jsonify({'error': 'Missing guild_id'}), 400
   
   try:
       channels = await get_guild_channels(guild_id, session['user_id'])
       return jsonify(channels)
   except Exception as e:
       return jsonify({'error': str(e)}), 500

@app.route('/api/send', methods=['POST'])
async def api_send():
   if 'user_id' not in session:
       return jsonify({'error': 'Not authenticated'}), 401
   
   data = request.json
   channel_ids = data.get('channel_ids', [])
   content = data.get('content', '').strip()
   embeds_data = data.get('embeds', [])
   file_paths = data.get('files', [])
   
   if not channel_ids:
       return jsonify({'error': 'No channels selected'}), 400
   
   if not content and not embeds_data and not file_paths:
       return jsonify({'error': 'Message cannot be empty'}), 400
   
   results = []
   
   for channel_id in channel_ids:
       success, message = await send_message(channel_id, content, embeds_data, file_paths)
       results.append({'channel_id': channel_id, 'success': success, 'message': message})
   
   # Save to history
   conn = get_db()
   c = conn.cursor()
   c.execute('''
       INSERT INTO messages (user_id, guild_id, channel_id, content, embed_data, files, sent_time, status)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
   ''', (
       session['user_id'],
       '',  # guild_id can be retrieved from channel if needed
       json.dumps(channel_ids),
       content,
       json.dumps(embeds_data),
       json.dumps(file_paths),
       int(time.time()),
       'sent'
   ))
   conn.commit()
   conn.close()
   
   return jsonify({
       'success': all(r['success'] for r in results),
       'results': results
   })

@app.route('/api/upload', methods=['POST'])
async def api_upload():
   if 'user_id' not in session:
       return jsonify({'error': 'Not authenticated'}), 401
   
   if 'files' not in request.files:
       return jsonify({'error': 'No files provided'}), 400
   
   files = request.files.getlist('files')
   uploaded_files = []
   
   for file in files:
       if file.filename == '':
           continue
       
       if len(file.read()) > 25 * 1024 * 1024:
           return jsonify({'error': f'File {file.filename} exceeds 25MB limit'}), 400
       
       file.seek(0)
       filename = f"{int(time.time())}_{session['user_id']}_{file.filename}"
       file_path = os.path.join(UPLOAD_DIR, filename)
       
       await file.save(file_path)
       
       conn = get_db()
       c = conn.cursor()
       c.execute('''
           INSERT INTO files (user_id, filename, file_path, uploaded_at)
           VALUES (?, ?, ?, ?)
       ''', (session['user_id'], file.filename, file_path, int(time.time())))
       conn.commit()
       conn.close()
       
       uploaded_files.append({
           'filename': file.filename,
           'path': file_path
       })
   
   return jsonify({'success': True, 'files': uploaded_files})

@app.route('/api/schedule', methods=['POST'])
def api_schedule():
   if 'user_id' not in session:
       return jsonify({'error': 'Not authenticated'}), 401
   
   data = request.json
   channel_ids = data.get('channel_ids', [])
   content = data.get('content', '').strip()
   embeds_data = data.get('embeds', [])
   file_paths = data.get('files', [])
   scheduled_time = data.get('scheduled_time')
   
   if not channel_ids:
       return jsonify({'error': 'No channels selected'}), 400
   
   if not content and not embeds_data and not file_paths:
       return jsonify({'error': 'Message cannot be empty'}), 400
   
   if not scheduled_time:
       return jsonify({'error': 'No scheduled time provided'}), 400
   
   try:
       scheduled_timestamp = int(scheduled_time)
       if scheduled_timestamp <= int(time.time()):
           return jsonify({'error': 'Scheduled time must be in the future'}), 400
   except:
       return jsonify({'error': 'Invalid scheduled time format'}), 400
   
   conn = get_db()
   c = conn.cursor()
   c.execute('''
       INSERT INTO messages (user_id, guild_id, channel_id, content, embed_data, files, scheduled_time, status)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
   ''', (
       session['user_id'],
       '',
       json.dumps(channel_ids),
       content,
       json.dumps(embeds_data),
       json.dumps(file_paths),
       scheduled_timestamp,
       'pending'
   ))
   conn.commit()
   conn.close()
   
   return jsonify({'success': True, 'message': 'Message scheduled successfully'})

@app.route('/api/templates', methods=['GET', 'POST', 'DELETE'])
def api_templates():
   if 'user_id' not in session:
       return jsonify({'error': 'Not authenticated'}), 401
   
   conn = get_db()
   c = conn.cursor()
   
   if request.method == 'GET':
       c.execute('SELECT * FROM templates WHERE user_id = ?', (session['user_id'],))
       templates = c.fetchall()
       conn.close()
       return jsonify([dict(t) for t in templates])
   
   elif request.method == 'POST':
       data = request.json
       name = data.get('name')
       content = data.get('content')
       embeds_data = data.get('embeds', [])
       
       if not name:
           return jsonify({'error': 'Template name is required'}), 400
       
       c.execute('''
           INSERT INTO templates (user_id, name, content, embed_data)
           VALUES (?, ?, ?, ?)
       ''', (session['user_id'], name, content, json.dumps(embeds_data)))
       conn.commit()
       conn.close()
       
       return jsonify({'success': True, 'message': 'Template saved'})
   
   elif request.method == 'DELETE':
       template_id = request.args.get('id')
       if not template_id:
           return jsonify({'error': 'Template ID is required'}), 400
       
       c.execute('DELETE FROM templates WHERE id = ? AND user_id = ?', 
                (template_id, session['user_id']))
       conn.commit()
       conn.close()
       
       return jsonify({'success': True, 'message': 'Template deleted'})

@app.route('/api/analytics', methods=['GET'])
def api_analytics():
   if 'user_id' not in session:
       return jsonify({'error': 'Not authenticated'}), 401
   
   analytics = get_analytics()
   return jsonify(analytics)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
   return send_from_directory(UPLOAD_DIR, filename)

# HTML Template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
   <meta charset="UTF-8">
   <meta name="viewport" content="width=device-width, initial-scale=1.0">
   <title>Discord Message Dashboard</title>
   <style>
       * {
           margin: 0;
           padding: 0;
           box-sizing: border-box;
       }

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
           font-size: 18px;
           font-weight: bold;
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

       .container {
           display: flex;
           flex: 1;
           overflow: hidden;
       }

       .sidebar {
           width: 280px;
           background: #2a2b38;
           padding: 20px;
           overflow-y: auto;
           border-right: 1px solid #40424e;
       }

       .main-content {
           flex: 1;
           padding: 20px;
           overflow-y: auto;
       }

       .card {
           background: #2a2b38;
           border-radius: 8px;
           padding: 20px;
           margin-bottom: 20px;
           border: 1px solid #40424e;
       }

       .card h2 {
           color: #5865F2;
           margin-bottom: 15px;
           font-size: 18px;
       }

       .server-list, .channel-list {
           display: flex;
           flex-direction: column;
           gap: 8px;
       }

       .server-item, .channel-item {
           background: #40424e;
           padding: 10px;
           border-radius: 6px;
           cursor: pointer;
           transition: background 0.2s;
           display: flex;
           align-items: center;
           gap: 10px;
       }

       .server-item:hover, .channel-item:hover {
           background: #5865F2;
       }

       .server-item.selected, .channel-item.selected {
           background: #5865F2;
       }

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
       }

       input, textarea, select {
           width: 100%;
           padding: 10px;
           background: #40424e;
           border: 1px solid #62646e;
           border-radius: 6px;
           color: white;
           font-size: 14px;
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

       .embed-builder {
           margin-top: 20px;
       }

       .embed-preview {
           background: #40424e;
           padding: 15px;
           border-radius: 6px;
           margin-top: 15px;
           border-left: 4px solid #5865F2;
       }

       .file-upload {
           border: 2px dashed #62646e;
           padding: 30px;
           text-align: center;
           border-radius: 6px;
           cursor: pointer;
           transition: border-color 0.2s;
       }

       .file-upload:hover {
           border-color: #5865F2;
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
       }

       .btn {
           background: #5865F2;
           color: white;
           border: none;
           padding: 12px 24px;
           border-radius: 6px;
           cursor: pointer;
           font-weight: bold;
           transition: background 0.2s;
           font-size: 14px;
       }

       .btn:hover {
           background: #4752C4;
       }

       .btn-secondary {
           background: #62646e;
       }

       .btn-secondary:hover {
           background: #72747e;
       }

       .btn-danger {
           background: #ff6b6b;
       }

       .btn-danger:hover {
           background: #ff5252;
       }

       .button-group {
           display: flex;
           gap: 10px;
           margin-top: 20px;
           flex-wrap: wrap;
       }

       .stats-box {
           display: grid;
           grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 15px;
           margin-top: 15px;
       }

       .stat-item {
           background: #40424e;
           padding: 15px;
           border-radius: 6px;
           text-align: center;
       }

       .stat-value {
           font-size: 24px;
           font-weight: bold;
           color: #5865F2;
       }

       .stat-label {
           font-size: 12px;
           color: #b9bbbe;
           margin-top: 5px;
       }

       .history-item {
           background: #40424e;
           padding: 12px;
           border-radius: 6px;
           margin-bottom: 8px;
           display: flex;
           justify-content: space-between;
           align-items: center;
       }

       .history-item-info {
           flex: 1;
       }

       .history-item-actions {
           display: flex;
           gap: 8px;
       }

       .template-item {
           display: flex;
           justify-content: space-between;
           align-items: center;
           padding: 10px;
           background: #40424e;
           border-radius: 6px;
           margin-bottom: 8px;
       }

       .loading {
           display: inline-block;
           width: 20px;
           height: 20px;
           border: 3px solid rgba(255,255,255,.3);
           border-radius: 50%;
           border-top-color: #5865F2;
           animation: spin 1s ease-in-out infinite;
       }

       @keyframes spin {
           to { transform: rotate(360deg); }
       }

       .toast {
           position: fixed;
           bottom: 20px;
           right: 20px;
           background: #2a2b38;
           color: white;
           padding: 15px 20px;
           border-radius: 6px;
           border: 1px solid #40424e;
           box-shadow: 0 4px 6px rgba(0,0,0,0.1);
           display: none;
           align-items: center;
           gap: 10px;
           z-index: 1000;
       }

       .toast.show {
           display: flex;
       }

       .toast.success {
           border-left: 4px solid #4ade80;
       }

       .toast.error {
           border-left: 4px solid #ff6b6b;
       }

       .embed-color-picker {
           display: flex;
           gap: 10px;
           align-items: center;
           margin-top: 10px;
       }

       .color-preset {
           width: 30px;
           height: 30px;
           border-radius: 6px;
           cursor: pointer;
           border: 2px solid transparent;
           transition: border-color 0.2s;
       }

       .color-preset:hover {
           border-color: white;
       }

       .color-preset.selected {
           border-color: #5865F2;
       }

       .field-item {
           background: #40424e;
           padding: 15px;
           border-radius: 6px;
           margin-bottom: 10px;
       }

       .field-header {
           display: flex;
           justify-content: space-between;
           align-items: center;
           margin-bottom: 10px;
       }

       .checkbox-group {
           display: flex;
           align-items: center;
           gap: 8px;
       }

       .checkbox-group input[type="checkbox"] {
           width: auto;
       }

       @media (max-width: 768px) {
           .container {
               flex-direction: column;
           }
           
           .sidebar {
               width: 100%;
               max-height: 200px;
           }
           
           .stats-box {
               grid-template-columns: 1fr 1fr;
           }
       }
   </style>
</head>
<body>
   {% if user_id %}
       <!-- Dashboard -->
       <div class="header">
           <div class="logo">Discord Message Dashboard</div>
           <div class="user-info">
               {% if avatar %}
                   <img src="{{ avatar }}" alt="{{ username }}" class="avatar">
               {% else %}
                   <div class="avatar">{{ username[0] }}</div>
               {% endif %}
               <span>{{ username }}</span>
               <button class="logout-btn" onclick="logout()">Logout</button>
           </div>
       </div>

       <div class="container">
           <div class="sidebar">
               <div class="card">
                   <h2>Servers</h2>
                   <div class="server-list" id="serverList">
                       <div class="loading" style="margin: 20px auto;"></div>
                   </div>
               </div>

               <div class="card">
                   <h2>Channels</h2>
                   <div class="channel-list" id="channelList">
                       <i>Select a server to view channels</i>
                   </div>
               </div>
           </div>

           <div class="main-content">
               <!-- Message Composer -->
               <div class="card">
                   <h2>Message Composer</h2>
                   <div class="form-group">
                       <label>Content</label>
                       <textarea id="messageContent" rows="4" placeholder="Enter your message..."></textarea>
                       <div class="char-counter" id="charCounter">0 / 2000</div>
                   </div>

                   <!-- Embed Builder -->
                   <div class="embed-builder">
                       <h3>Embeds <button class="btn btn-secondary" onclick="addEmbed()">+ Add Embed</button></h3>
                       <div id="embedList"></div>
                       <div class="embed-preview" id="embedPreview" style="display: none;"></div>
                   </div>

                   <!-- File Upload -->
                   <div class="form-group">
                       <label>Files</label>
                       <div class="file-upload" onclick="document.getElementById('fileInput').click()">
                           <p>Click here or drag files to upload</p>
                           <p style="font-size: 12px; margin-top: 8px;">Max 25MB per file</p>
                       </div>
                       <input type="file" id="fileInput" multiple accept="image/*,.pdf,.txt" style="display: none;">
                       <div class="file-list" id="fileList"></div>
                   </div>

                   <!-- Actions -->
                   <div class="button-group">
                       <button class="btn" onclick="sendMessage()">Send Now</button>
                       <button class="btn btn-secondary" onclick="scheduleMessage()">Schedule</button>
                       <button class="btn btn-secondary" onclick="saveTemplate()">Save Template</button>
                   </div>
               </div>

               <!-- Templates -->
               <div class="card">
                   <h2>Saved Templates</h2>
                   <div id="templateList">
                       {% for template in templates %}
                           <div class="template-item">
                               <span>{{ template.name }}</span>
                               <button class="btn btn-secondary" style="padding: 6px 12px;" onclick="loadTemplate({{ template.id }})">Load</button>
                               <button class="btn btn-danger" style="padding: 6px 12px;" onclick="deleteTemplate({{ template.id }})">Delete</button>
                           </div>
                       {% else %}
                           <i>No templates saved yet</i>
                       {% endfor %}
                   </div>
               </div>

               <!-- Message History -->
               <div class="card">
                   <h2>Message History</h2>
                   <div id="messageHistory">
                       {% for message in message_history %}
                           <div class="history-item">
                               <div class="history-item-info">
                                   <strong>{{ message.sent_time|default('Scheduled', true) }}</strong>
                                   <br><small>{{ message.content[:50] }}...</small>
                               </div>
                               <div class="history-item-actions">
                                   <button class="btn btn-secondary" style="padding: 6px 12px;" onclick="resendMessage({{ message.id }})">Resend</button>
                               </div>
                           </div>
                       {% else %}
                           <i>No messages yet</i>
                       {% endfor %}
                   </div>
               </div>
           </div>

           <!-- Stats Bar -->
           <div style="position: fixed; bottom: 0; left: 0; right: 0; background: #2a2b38; border-top: 1px solid #40424e; padding: 10px 20px; display: flex; justify-content: space-around;">
               <div class="stat-item" style="padding: 5px; background: none;">
                   <div class="stat-value" id="statsToday">{{ analytics.today }}</div>
                   <div class="stat-label">Messages Today</div>
               </div>
               <div class="stat-item" style="padding: 5px; background: none;">
                   <div class="stat-value" id="statsWeek">{{ analytics.week }}</div>
                   <div class="stat-label">This Week</div>
               </div>
               <div class="stat-item" style="padding: 5px; background: none;">
                   <div class="stat-value" id="statsMonth">{{ analytics.month }}</div>
                   <div class="stat-label">This Month</div>
               </div>
               <div class="stat-item" style="padding: 5px; background: none;">
                   <div class="stat-value" id="statsFiles">{{ analytics.files_today }}</div>
                   <div class="stat-label">Files Today</div>
               </div>
           </div>
       </div>

       <!-- Toast Notifications -->
       <div id="toast" class="toast"></div>
   {% else %}
       <!-- Login Page -->
       <div style="display: flex; justify-content: center; align-items: center; height: 100vh; background: #1e1f29;">
           <div class="card" style="text-align: center; max-width: 400px;">
               <h1 style="color: #5865F2; margin-bottom: 20px;">Discord Message Dashboard</h1>
               <p style="margin-bottom: 30px; color: #b9bbbe;">Professional Discord message management at your fingertips</p>
               <a href="{{ oauth_url }}" class="btn" style="display: block; text-decoration: none;">Login with Discord</a>
               <p style="margin-top: 20px; font-size: 12px; color: #72747e;">Secure OAuth2 authentication</p>
           </div>
       </div>
   {% endif %}

   <script>
       let selectedServer = null;
       let selectedChannels = [];
       let uploadedFiles = [];
       let embeds = [];

       // Initialize
       document.addEventListener('DOMContentLoaded', function() {
           if ({{ 'true' if user_id else 'false' }}) {
               loadServers();
               initFileUpload();
               initCharCounter();
               initEmbedBuilder();
           }
       });

       // Character counter
       function initCharCounter() {
           const textarea = document.getElementById('messageContent');
           const counter = document.getElementById('charCounter');
           
           textarea.addEventListener('input', function() {
               const length = this.value.length;
               counter.textContent = `${length} / 2000`;
               counter.style.color = length > 2000 ? '#ff6b6b' : '#b9bbbe';
           });
       }

       // File upload
       function initFileUpload() {
           const dropZone = document.querySelector('.file-upload');
           const fileInput = document.getElementById('fileInput');
           
           dropZone.addEventListener('dragover', (e) => {
               e.preventDefault();
               dropZone.style.borderColor = '#5865F2';
           });
           
           dropZone.addEventListener('dragleave', () => {
               dropZone.style.borderColor = '#62646e';
           });
           
           dropZone.addEventListener('drop', (e) => {
               e.preventDefault();
               dropZone.style.borderColor = '#62646e';
               const files = Array.from(e.dataTransfer.files);
               uploadFiles(files);
           });
           
           fileInput.addEventListener('change', (e) => {
               const files = Array.from(e.target.files);
               uploadFiles(files);
           });
       }

       async function uploadFiles(files) {
           const formData = new FormData();
           files.forEach(file => formData.append('files', file));
           
           const response = await fetch('/api/upload', {
               method: 'POST',
               body: formData,
               headers: {
                   'X-Requested-With': 'XMLHttpRequest'
               }
           });
           
           const result = await response.json();
           if (result.success) {
               result.files.forEach(file => uploadedFiles.push(file));
               renderFileList();
               showToast('Files uploaded successfully', 'success');
           } else {
               showToast(result.error || 'Upload failed', 'error');
           }
       }

       function renderFileList() {
           const container = document.getElementById('fileList');
           container.innerHTML = uploadedFiles.map(file => `
               <div class="file-item">
                   <span>${file.filename}</span>
                   <span class="remove-file" onclick="removeFile('${file.path}')">×</span>
               </div>
           `).join('');
       }

       function removeFile(path) {
           uploadedFiles = uploadedFiles.filter(f => f.path !== path);
           renderFileList();
       }

       // Embed builder
       function initEmbedBuilder() {
           document.getElementById('messageContent').addEventListener('input', updateEmbedPreview);
       }

       function addEmbed() {
           if (embeds.length >= 10) {
               showToast('Maximum 10 embeds allowed', 'error');
               return;
           }
           
           const embed = {
               title: '',
               description: '',
               color: '#5865F2',
               author: { name: '', url: '', icon_url: '' },
               fields: [],
               thumbnail: '',
               image: '',
               footer: { text: '', icon_url: '' },
               timestamp: false
           };
           
           embeds.push(embed);
           renderEmbedList();
       }

       function removeEmbed(index) {
           embeds.splice(index, 1);
           renderEmbedList();
       }

       function addField(embedIndex) {
           embeds[embedIndex].fields.push({
               name: '',
               value: '',
               inline: true
           });
           renderEmbedList();
       }

       function removeField(embedIndex, fieldIndex) {
           embeds[embedIndex].fields.splice(fieldIndex, 1);
           renderEmbedList();
       }

       function renderEmbedList() {
           const container = document.getElementById('embedList');
           container.innerHTML = embeds.map((embed, index) => `
               <div class="field-item">
                   <div class="field-header">
                       <strong>Embed #${index + 1}</strong>
                       <button class="btn btn-danger" style="padding: 4px 8px;" onclick="removeEmbed(${index})">Remove</button>
                   </div>
                   <div class="form-group"><input type="text" placeholder="Title" value="${embed.title}" onchange="updateEmbed(${index}, 'title', this.value)"></div>
                   <div class="form-group"><textarea rows="3" placeholder="Description" onchange="updateEmbed(${index}, 'description', this.value)">${embed.description}</textarea></div>
                   <div class="embed-color-picker">
                       <input type="color" value="${embed.color}" onchange="updateEmbed(${index}, 'color', this.value)">
                       <span>Color</span>
                   </div>
                   <div class="form-group"><input type="text" placeholder="Author Name" value="${embed.author.name}" onchange="updateEmbed(${index}, 'author.name', this.value)"></div>
                   <div class="form-group"><input type="text" placeholder="Author URL" value="${embed.author.url}" onchange="updateEmbed(${index}, 'author.url', this.value)"></div>
                   <div class="form-group"><input type="text" placeholder="Thumbnail URL" value="${embed.thumbnail}" onchange="updateEmbed(${index}, 'thumbnail', this.value)"></div>
                   <div class="form-group"><input type="text" placeholder="Image URL" value="${embed.image}" onchange="updateEmbed(${index}, 'image', this.value)"></div>
                   <div class="form-group"><input type="text" placeholder="Footer Text" value="${embed.footer.text}" onchange="updateEmbed(${index}, 'footer.text', this.value)"></div>
                   <div class="checkbox-group"><input type="checkbox" ${embed.timestamp ? 'checked' : ''} onchange="updateEmbed(${index}, 'timestamp', this.checked)"> <span>Include Timestamp</span></div>
                   <div style="margin-top: 10px;">
                       <strong>Fields</strong>
                       <button class="btn btn-secondary" style="padding: 4px 8px; margin-left: 10px;" onclick="addField(${index})">+ Add Field</button>
                       <div style="margin-top: 10px;">
                           ${embed.fields.map((field, fIndex) => `
                               <div class="field-item" style="background: #62646e;">
                                   <div class="field-header">
                                       <strong>Field #${fIndex + 1}</strong>
                                       <button class="btn btn-danger" style="padding: 2px 6px;" onclick="removeField(${index}, ${fIndex})">×</button>
                                   </div>
                                   <div class="form-group"><input type="text" placeholder="Field Name" value="${field.name}" onchange="updateField(${index}, ${fIndex}, 'name', this.value)"></div>
                                   <div class="form-group"><input type="text" placeholder="Field Value" value="${field.value}" onchange="updateField(${index}, ${fIndex}, 'value', this.value)"></div>
                                   <div class="checkbox-group"><input type="checkbox" ${field.inline ? 'checked' : ''} onchange="updateField(${index}, ${fIndex}, 'inline', this.checked)"> <span>Inline</span></div>
                               </div>
                           `).join('')}
                       </div>
                   </div>
               </div>
           `).join('');
           
           updateEmbedPreview();
       }

       function updateEmbed(index, path, value) {
           const keys = path.split('.');
           let obj = embeds[index];
           
           for (let i = 0; i < keys.length - 1; i++) {
               obj = obj[keys[i]];
           }
           
           obj[keys[keys.length - 1]] = value;
           updateEmbedPreview();
       }

       function updateField(embedIndex, fieldIndex, key, value) {
           embeds[embedIndex].fields[fieldIndex][key] = value;
           updateEmbedPreview();
       }

       function updateEmbedPreview() {
           const preview = document.getElementById('embedPreview');
           if (embeds.length === 0) {
               preview.style.display = 'none';
               return;
           }
           
           preview.style.display = 'block';
           preview.innerHTML = embeds.map(embed => `
               <div style="background: ${embed.color}; padding: 1px; border-radius: 4px; margin-bottom: 10px;">
                   <div style="background: #2a2b38; padding: 15px; border-radius: 3px;">
                       ${embed.author.name ? `<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                           ${embed.author.icon_url ? `<img src="${embed.author.icon_url}" style="width: 24px; height: 24px; border-radius: 50%;">` : ''}
                           <span style="font-weight: bold;">${embed.author.name}</span>
                       </div>` : ''}
                       ${embed.title ? `<h3 style="margin: 8px 0; color: ${embed.color};">${embed.title}</h3>` : ''}
                       ${embed.description ? `<p style="color: #b9bbbe; margin: 8px 0;">${embed.description}</p>` : ''}
                       ${embed.thumbnail ? `<img src="${embed.thumbnail}" style="max-width: 80px; max-height: 80px; float: right; border-radius: 4px;">` : ''}
                       ${embed.fields.map(field => `
                           <div style="margin: 8px 0; ${field.inline ? 'display: inline-block; width: 49%;' : ''}">
                               <strong>${field.name}</strong>
                               <div style="color: #b9bbbe;">${field.value}</div>
                           </div>
                       `).join('')}
                       ${embed.image ? `<img src="${embed.image}" style="max-width: 100%; border-radius: 4px; margin-top: 8px;">` : ''}
                       ${embed.footer.text ? `<div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #40424e; font-size: 12px; color: #72747e;">
                           ${embed.footer.icon_url ? `<img src="${embed.footer.icon_url}" style="width: 20px; height: 20px; border-radius: 50%; vertical-align: middle; margin-right: 8px;">` : ''}
                           ${embed.footer.text}
                           ${embed.timestamp ? ` • ${new Date().toLocaleString()}` : ''}
                       </div>` : ''}
                   </div>
               </div>
           `).join('');
       }

       // Load servers
       async function loadServers() {
           try {
               const response = await fetch('/api/guilds');
               const servers = await response.json();
               
               const container = document.getElementById('serverList');
               container.innerHTML = servers.map(server => `
                   <div class="server-item" onclick="selectServer('${server.id}', this)">
                       ${server.icon ? `<img src="${server.icon}" style="width: 32px; height: 32px; border-radius: 50%;">` : '<div style="width: 32px; height: 32px; background: #5865F2; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold;">' + server.name[0] + '</div>'}
                       <span>${server.name}</span>
                   </div>
               `).join('');
           } catch (e) {
               showToast('Failed to load servers', 'error');
           }
       }

       async function selectServer(serverId, element) {
           document.querySelectorAll('.server-item').forEach(item => item.classList.remove('selected'));
           element.classList.add('selected');
           selectedServer = serverId;
           selectedChannels = [];
           
           try {
               const response = await fetch(`/api/channels?guild_id=${serverId}`);
               const channels = await response.json();
               
               const container = document.getElementById('channelList');
               container.innerHTML = channels.map(channel => `
                   <div class="channel-item" onclick="selectChannel('${channel.id}', this)">
                       <span>#${channel.name}</span>
                   </div>
               `).join('');
           } catch (e) {
               showToast('Failed to load channels', 'error');
           }
       }

       function selectChannel(channelId, element) {
           element.classList.toggle('selected');
           if (element.classList.contains('selected')) {
               selectedChannels.push(channelId);
           } else {
               selectedChannels = selectedChannels.filter(id => id !== channelId);
           }
       }

       // Send message
       async function sendMessage() {
           const content = document.getElementById('messageContent').value;
           
           if (selectedChannels.length === 0) {
               showToast('Please select at least one channel', 'error');
               return;
           }
           
           if (!content && embeds.length === 0 && uploadedFiles.length === 0) {
               showToast('Message cannot be empty', 'error');
               return;
           }
           
           const data = {
               channel_ids: selectedChannels,
               content: content,
               embeds: embeds,
               files: uploadedFiles.map(f => f.path)
           };
           
           const response = await fetch('/api/send', {
               method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify(data)
           });
           
           const result = await response.json();
           if (result.success) {
               showToast('Message sent successfully!', 'success');
               document.getElementById('messageContent').value = '';
               embeds = [];
               uploadedFiles = [];
               renderEmbedList();
               renderFileList();
           } else {
               showToast('Failed to send message', 'error');
           }
       }

       // Schedule message
       function scheduleMessage() {
           const scheduledTime = prompt('Enter scheduled time (in seconds since epoch):');
           if (!scheduledTime) return;
           
           const data = {
               channel_ids: selectedChannels,
               content: document.getElementById('messageContent').value,
               embeds: embeds,
               files: uploadedFiles.map(f => f.path),
               scheduled_time: scheduledTime
           };
           
           fetch('/api/schedule', {
               method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify(data)
           }).then(r => r.json()).then(result => {
               if (result.success) {
                   showToast('Message scheduled successfully!', 'success');
               } else {
                   showToast('Failed to schedule message', 'error');
               }
           });
       }

       // Templates
       function saveTemplate() {
           const name = prompt('Enter template name:');
           if (!name) return;
           
           const data = {
               name: name,
               content: document.getElementById('messageContent').value,
               embeds: embeds
           };
           
           fetch('/api/templates', {
               method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify(data)
           }).then(r => r.json()).then(result => {
               if (result.success) {
                   showToast('Template saved!', 'success');
                   location.reload();
               } else {
                   showToast('Failed to save template', 'error');
               }
           });
       }

       function loadTemplate(id) {
           fetch('/api/templates').then(r => r.json()).then(templates => {
               const template = templates.find(t => t.id === id);
               if (template) {
                   document.getElementById('messageContent').value = template.content;
                   embeds = JSON.parse(template.embed_data);
                   renderEmbedList();
                   showToast('Template loaded!', 'success');
               }
           });
       }

       function deleteTemplate(id) {
           if (!confirm('Delete this template?')) return;
           
           fetch(`/api/templates?id=${id}`, {method: 'DELETE'})
               .then(r => r.json())
               .then(result => {
                   if (result.success) {
                       showToast('Template deleted!', 'success');
                       location.reload();
                   }
               });
       }

       // Logout
       function logout() {
           window.location.href = '/logout';
       }

       // Toast
       function showToast(message, type = 'success') {
           const toast = document.getElementById('toast');
           toast.textContent = message;
           toast.className = `toast ${type} show`;
           
           setTimeout(() => {
               toast.classList.remove('show');
           }, 3000);
       }
   </script>
</body>
</html>
'''

@bot.event
async def on_ready():
   print(f'Bot is ready as {bot.user}')
   bot.loop.create_task(process_scheduled_messages())

def run_bot():
   try:
       bot.run(CONFIG['bot_token'])
   except Exception as e:
       print(f"Bot connection failed: {e}")

def run_flask():
   app.run(host=CONFIG['app_host'], port=CONFIG['app_port'])

if __name__ == '__main__':
   # Run bot in a separate thread
   bot_thread = threading.Thread(target=run_bot, daemon=True)
   bot_thread.start()
   
   # Run Flask app
   run_flask()
