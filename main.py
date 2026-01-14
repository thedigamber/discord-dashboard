# ========== ULTIMATE DISCORD MESSAGE DASHBOARD PRO ==========
import sys
import types
import os
import time
import asyncio
import json
import threading
import sqlite3
from datetime import datetime, timedelta
from io import BytesIO
import base64
import uuid
import schedule
import aiofiles
from pathlib import Path

# ========== FIX AUDIOOP ERROR ==========
class AudioopBlocker:
    def find_spec(self, fullname, path, target=None):
        if fullname in ['audioop', '_audioop']:
            return types.SimpleNamespace(
                loader=None,
                origin='dummy',
                submodule_search_locations=[]
            )
        return None

sys.meta_path.insert(0, AudioopBlocker())

audioop_module = types.ModuleType('audioop')
audioop_module.ulaw2lin = lambda x, y: x
audioop_module.lin2ulaw = lambda x, y: x
sys.modules['audioop'] = audioop_module
sys.modules['_audioop'] = types.ModuleType('_audioop')

# ========== IMPORTS ==========
import discord
from discord.ext import commands
from flask import Flask, render_template_string, redirect, url_for, session, request, jsonify, send_file
from flask_cors import CORS
import requests
from werkzeug.utils import secure_filename
from PIL import Image
import io

# ========== CONFIGURATION ==========
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET')
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
DISCORD_REDIRECT_URI = os.environ.get('DISCORD_REDIRECT_URI', 'https://dashboard.digamber.in/callback')
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')

# ========== FLASK APP ==========
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
CORS(app)

# ========== DATABASE SETUP ==========
def init_db():
    conn = sqlite3.connect('dashboard.db')
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            avatar TEXT,
            access_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            guild_id TEXT,
            channel_id TEXT,
            message_id TEXT,
            title TEXT,
            content TEXT,
            embed_data TEXT,
            file_data TEXT,
            is_embed INTEGER DEFAULT 0,
            is_scheduled INTEGER DEFAULT 0,
            scheduled_time TIMESTAMP,
            sent_time TIMESTAMP,
            status TEXT DEFAULT 'sent',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Templates table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS templates (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            name TEXT,
            title TEXT,
            content TEXT,
            embed_data TEXT,
            is_embed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Analytics table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            messages_sent INTEGER DEFAULT 0,
            files_sent INTEGER DEFAULT 0,
            embeds_sent INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0
        )
    ''')
    
    # Files table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            filename TEXT,
            file_type TEXT,
            file_size INTEGER,
            file_data BLOB,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ========== DISCORD BOT ==========
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.emojis = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ========== GLOBAL STORAGE ==========
user_sessions = {}
uploaded_files = {}
message_history = []
bot_guilds = []
available_emojis = {}
active_schedules = {}
bot_start_time = time.time()

# ========== FILE UPLOAD CONFIG ==========
ALLOWED_EXTENSIONS = {
    'image': ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'],
    'document': ['pdf', 'txt', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'],
    'audio': ['mp3', 'wav', 'ogg', 'm4a'],
    'video': ['mp4', 'mov', 'avi', 'mkv', 'webm'],
    'archive': ['zip', 'rar', '7z', 'tar', 'gz']
}

UPLOAD_FOLDER = 'uploads'
Path(UPLOAD_FOLDER).mkdir(exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in [
        ext for exts in ALLOWED_EXTENSIONS.values() for ext in exts
    ]

def get_file_type(filename):
    ext = filename.rsplit('.', 1)[1].lower()
    for file_type, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return file_type
    return 'other'

# ========== SCHEDULING SYSTEM ==========
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

# ========== HTML TEMPLATE START ==========
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚀 Discord Dashboard Pro</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #7289da;
            --primary-dark: #5b6eae;
            --secondary: #43b581;
            --secondary-dark: #3a9d6e;
            --danger: #f04747;
            --warning: #faa81a;
            --info: #00b0f4;
            --dark: #1e1f29;
            --darker: #16171e;
            --light: #99aab5;
            --lighter: #ffffff;
            --card-bg: rgba(255, 255, 255, 0.05);
            --card-border: rgba(255, 255, 255, 0.1);
            --shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            --radius: 16px;
            --transition: all 0.3s ease;
        }

        [data-theme="light"] {
            --dark: #f8f9fa;
            --darker: #e9ecef;
            --light: #6c757d;
            --lighter: #212529;
            --card-bg: rgba(0, 0, 0, 0.03);
            --card-border: rgba(0, 0, 0, 0.1);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background: linear-gradient(135deg, var(--darker) 0%, var(--dark) 100%);
            color: var(--lighter);
            font-family: 'Poppins', sans-serif;
            min-height: 100vh;
            padding: 20px;
            transition: var(--transition);
        }

        .container {
            max-width: 1800px;
            margin: 0 auto;
        }

        /* Header */
        .header {
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            border-radius: var(--radius);
            padding: 40px;
            margin-bottom: 30px;
            border: 1px solid var(--card-border);
            box-shadow: var(--shadow);
            position: relative;
            overflow: hidden;
        }

        .header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
        }

        .title {
            font-size: 3.2rem;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
            font-weight: 800;
        }

        .subtitle {
            font-size: 1.2rem;
            color: var(--light);
            max-width: 800px;
            line-height: 1.6;
        }

        /* Theme Toggle */
        .theme-toggle {
            position: absolute;
            top: 30px;
            right: 30px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 50px;
            padding: 10px 20px;
            display: flex;
            gap: 15px;
            cursor: pointer;
            transition: var(--transition);
        }

        .theme-toggle:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }

        /* Login Button */
        .login-btn {
            display: inline-flex;
            align-items: center;
            gap: 15px;
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            color: white;
            padding: 18px 45px;
            border: none;
            border-radius: 50px;
            font-size: 1.3rem;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            transition: var(--transition);
            box-shadow: 0 5px 20px rgba(114, 137, 218, 0.4);
            margin-top: 30px;
        }

        .login-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 30px rgba(114, 137, 218, 0.6);
        }

        /* Dashboard Layout */
        .dashboard {
            display: grid;
            grid-template-columns: 320px 1fr 400px;
            gap: 25px;
            margin-top: 25px;
        }

        @media (max-width: 1400px) {
            .dashboard {
                grid-template-columns: 1fr;
            }
        }

        /* Sidebar */
        .sidebar {
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            border-radius: var(--radius);
            padding: 25px;
            border: 1px solid var(--card-border);
            box-shadow: var(--shadow);
            height: fit-content;
            position: sticky;
            top: 20px;
        }

        .sidebar-title {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 1.4rem;
            margin-bottom: 25px;
            padding-bottom: 15px;
            border-bottom: 2px solid var(--card-border);
        }

        /* Main Content */
        .main-content {
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            border-radius: var(--radius);
            padding: 30px;
            border: 1px solid var(--card-border);
            box-shadow: var(--shadow);
        }

        /* Right Sidebar */
        .right-sidebar {
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            border-radius: var(--radius);
            padding: 25px;
            border: 1px solid var(--card-border);
            box-shadow: var(--shadow);
            height: fit-content;
            position: sticky;
            top: 20px;
        }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 30px;
            border-bottom: 2px solid var(--card-border);
            padding-bottom: 15px;
            flex-wrap: wrap;
        }

        .tab-btn {
            padding: 12px 25px;
            background: transparent;
            border: none;
            border-radius: 50px;
            color: var(--light);
            font-weight: 600;
            cursor: pointer;
            transition: var(--transition);
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .tab-btn:hover {
            background: rgba(255, 255, 255, 0.1);
            color: var(--lighter);
        }

        .tab-btn.active {
            background: var(--primary);
            color: white;
            box-shadow: 0 4px 15px rgba(114, 137, 218, 0.3);
        }

        /* Cards */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.05);
            padding: 25px;
            border-radius: var(--radius);
            border: 1px solid var(--card-border);
            transition: var(--transition);
        }

        .stat-card:hover {
            transform: translateY(-5px);
            box-shadow: var(--shadow);
        }

        .stat-icon {
            font-size: 2.5rem;
            margin-bottom: 15px;
            color: var(--primary);
        }

        .stat-value {
            font-size: 2.2rem;
            font-weight: 700;
            color: var(--lighter);
            margin-bottom: 5px;
        }

        .stat-label {
            font-size: 0.9rem;
            color: var(--light);
        }

        /* Forms */
        .form-section {
            background: rgba(255, 255, 255, 0.03);
            padding: 25px;
            border-radius: var(--radius);
            margin-bottom: 25px;
            border: 1px solid var(--card-border);
        }

        .form-title {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 1.3rem;
            margin-bottom: 20px;
            color: var(--lighter);
        }

        .form-group {
            margin-bottom: 20px;
        }

        .form-label {
            display: block;
            margin-bottom: 10px;
            font-weight: 600;
            color: var(--lighter);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .form-control {
            width: 100%;
            padding: 16px;
            background: rgba(255, 255, 255, 0.08);
            border: 2px solid rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            color: var(--lighter);
            font-size: 1rem;
            font-family: 'Poppins', sans-serif;
            transition: var(--transition);
        }

        .form-control:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(114, 137, 218, 0.2);
        }

        textarea.form-control {
            min-height: 150px;
            resize: vertical;
        }

        /* File Upload */
        .file-upload {
            border: 2px dashed var(--card-border);
            border-radius: 12px;
            padding: 30px;
            text-align: center;
            cursor: pointer;
            transition: var(--transition);
            margin-bottom: 20px;
        }

        .file-upload:hover {
            border-color: var(--primary);
            background: rgba(114, 137, 218, 0.05);
        }

        .file-upload input {
            display: none;
        }

        .upload-icon {
            font-size: 3rem;
            color: var(--primary);
            margin-bottom: 15px;
        }

        .file-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 15px;
        }

        .file-item {
            background: rgba(255, 255, 255, 0.08);
            padding: 10px 15px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 0.9rem;
        }

        .file-remove {
            color: var(--danger);
            cursor: pointer;
            padding: 5px;
        }

        /* Embed Builder */
        .embed-builder {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }

        .embed-preview {
            background: #2b2d31;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            border-left: 4px solid var(--primary);
        }

        .embed-color-picker {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }

        .color-option {
            width: 40px;
            height: 40px;
            border-radius: 8px;
            cursor: pointer;
            border: 3px solid transparent;
            transition: var(--transition);
        }

        .color-option:hover {
            transform: scale(1.1);
        }

        .color-option.active {
            border-color: white;
            box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.3);
        }

        /* Schedule Form */
        .schedule-input {
            display: flex;
            gap: 15px;
            align-items: center;
        }

        .datetime-input {
            flex: 1;
            display: flex;
            gap: 10px;
        }

        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            padding: 16px 30px;
            border: none;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: var(--transition);
            text-decoration: none;
            font-family: 'Poppins', sans-serif;
        }

        .btn-primary {
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            color: white;
        }

        .btn-primary:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(114, 137, 218, 0.4);
        }

        .btn-success {
            background: linear-gradient(135deg, var(--secondary), var(--secondary-dark));
            color: white;
        }

        .btn-success:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(67, 181, 129, 0.4);
        }

        .btn-danger {
            background: linear-gradient(135deg, var(--danger), #d63c3c);
            color: white;
        }

        .btn-warning {
            background: linear-gradient(135deg, var(--warning), #e69500);
            color: white;
        }

        .btn-group {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            margin-top: 25px;
        }

        /* Tables */
        .data-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }

        .data-table th {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px;
            text-align: left;
            font-weight: 600;
            color: var(--lighter);
            border-bottom: 2px solid var(--card-border);
        }

        .data-table td {
            padding: 15px;
            border-bottom: 1px solid var(--card-border);
            color: var(--light);
        }

        .data-table tr:hover {
            background: rgba(255, 255, 255, 0.05);
        }

        /* Modals */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            backdrop-filter: blur(10px);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }

        .modal.active {
            display: flex;
        }

        .modal-content {
            background: var(--dark);
            border-radius: var(--radius);
            padding: 40px;
            max-width: 800px;
            width: 90%;
            max-height: 90vh;
            overflow-y: auto;
            border: 1px solid var(--card-border);
            box-shadow: var(--shadow);
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
        }

        .modal-close {
            background: none;
            border: none;
            color: var(--light);
            font-size: 1.5rem;
            cursor: pointer;
            padding: 5px;
        }

        /* Loading */
        .loading {
            display: inline-block;
            width: 50px;
            height: 50px;
            border: 5px solid rgba(255, 255, 255, 0.1);
            border-radius: 50%;
            border-top-color: var(--primary);
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* Toast */
        .toast {
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: var(--primary);
            color: white;
            padding: 20px 30px;
            border-radius: 12px;
            font-weight: 600;
            z-index: 1000;
            box-shadow: var(--shadow);
            animation: slideInRight 0.3s ease, fadeOut 0.3s 2.7s;
            display: flex;
            align-items: center;
            gap: 15px;
        }

        @keyframes slideInRight {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }

        /* Responsive */
        @media (max-width: 768px) {
            .header {
                padding: 30px 20px;
            }
            
            .title {
                font-size: 2.5rem;
            }
            
            .dashboard {
                grid-template-columns: 1fr;
            }
            
            .tabs {
                overflow-x: auto;
                padding-bottom: 10px;
            }
            
            .btn-group {
                flex-direction: column;
            }
            
            .btn {
                width: 100%;
                justify-content: center;
            }
        }

        /* Utility Classes */
        .hidden {
            display: none !important;
        }

        .text-center {
            text-align: center;
        }

        .text-success {
            color: var(--secondary) !important;
        }

        .text-danger {
            color: var(--danger) !important;
        }

        .text-warning {
            color: var(--warning) !important;
        }

        .mb-3 {
            margin-bottom: 30px;
        }

        .mt-3 {
            margin-top: 30px;
        }

        .p-3 {
            padding: 30px;
        }

        .d-flex {
            display: flex;
        }

        .align-center {
            align-items: center;
        }

        .justify-between {
            justify-content: space-between;
        }

        .gap-2 {
            gap: 20px;
        }

        .w-100 {
            width: 100%;
        }

        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 10px;
        }

        ::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
        }

        ::-webkit-scrollbar-thumb {
            background: linear-gradient(180deg, var(--primary), var(--secondary));
            border-radius: 10px;
        }

        /* Animation */
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .animate-fadein {
            animation: fadeIn 0.5s ease;
        }
    </style>
</head>
<body>
    <div class="container">
        {% if not user %}
        <!-- Login Page -->
        <div class="header">
            <h1 class="title">🚀 Discord Dashboard Pro</h1>
            <p class="subtitle">Advanced message management system with file uploads, scheduling, analytics, and more.</p>
            
            <a href="/login" class="login-btn">
                <i class="fab fa-discord"></i> Login with Discord
            </a>
            
            <div class="stats-grid mt-3">
                <div class="stat-card">
                    <div class="stat-icon">🤖</div>
                    <div class="stat-value" id="serverCount">0</div>
                    <div class="stat-label">Active Servers</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">📊</div>
                    <div class="stat-value" id="totalUsers">0</div>
                    <div class="stat-label">Total Users</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">⚡</div>
                    <div class="stat-value">24/7</div>
                    <div class="stat-label">Uptime</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">🔒</div>
                    <div class="stat-value">100%</div>
                    <div class="stat-label">Secure</div>
                </div>
            </div>
        </div>

        <div class="main-content">
            <h2 class="form-title"><i class="fas fa-star"></i> Premium Features</h2>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon"><i class="fas fa-upload"></i></div>
                    <h3>File Upload</h3>
                    <p>Support for images, documents, audio, video, and archives</p>
                </div>
                <div class="stat-card">
                    <div class="stat-icon"><i class="fas fa-clock"></i></div>
                    <h3>Scheduling</h3>
                    <p>Schedule messages for future delivery</p>
                </div>
                <div class="stat-card">
                    <div class="stat-icon"><i class="fas fa-paint-brush"></i></div>
                    <h3>Embed Builder</h3>
                    <p>Create beautiful embeds with colors, fields, and images</p>
                </div>
                <div class="stat-card">
                    <div class="stat-icon"><i class="fas fa-copy"></i></div>
                    <h3>Templates</h3>
                    <p>Save and reuse message templates</p>
                </div>
                <div class="stat-card">
                    <div class="stat-icon"><i class="fas fa-chart-bar"></i></div>
                    <h3>Analytics</h3>
                    <p>Track message statistics and performance</p>
                </div>
                <div class="stat-card">
                    <div class="stat-icon"><i class="fas fa-bolt"></i></div>
                    <h3>Bulk Send</h3>
                    <p>Send messages to multiple channels at once</p>
                </div>
            </div>
        </div>
        
        {% else %}
        <!-- Dashboard -->
        <div class="header">
            <div class="d-flex justify-between align-center">
                <div>
                    <h1 class="title">Welcome, {{ user.username }}!</h1>
                    <p class="subtitle">Manage your Discord messages with advanced features</p>
                </div>
                <div class="d-flex gap-2 align-center">
                    <div class="theme-toggle" onclick="toggleTheme()">
                        <i class="fas fa-sun"></i>
                        <i class="fas fa-moon"></i>
                    </div>
                    <a href="/logout" class="btn btn-danger">
                        <i class="fas fa-sign-out-alt"></i> Logout
                    </a>
                </div>
            </div>
        </div>

        <div class="dashboard">
            <!-- Left Sidebar -->
            <div class="sidebar">
                <div class="sidebar-title">
                    <i class="fas fa-server"></i> Your Servers
                    <span id="guildCount" class="text-success">0</span>
                </div>
                
                <div class="guild-list" id="guildList">
                    <div class="text-center p-3">
                        <div class="loading"></div>
                        <p class="mt-3">Loading servers...</p>
                    </div>
                </div>
                
                <div class="mt-3">
                    <div class="sidebar-title">
                        <i class="fas fa-bolt"></i> Quick Stats
                    </div>
                    <div class="form-section">
                        <div class="d-flex justify-between mb-2">
                            <span>Messages Sent:</span>
                            <strong id="totalMessages">0</strong>
                        </div>
                        <div class="d-flex justify-between mb-2">
                            <span>Files Uploaded:</span>
                            <strong id="totalFiles">0</strong>
                        </div>
                        <div class="d-flex justify-between mb-2">
                            <span>Templates:</span>
                            <strong id="totalTemplates">0</strong>
                        </div>
                        <div class="d-flex justify-between">
                            <span>Scheduled:</span>
                            <strong id="totalScheduled">0</strong>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Main Content -->
            <div class="main-content">
                <!-- Tabs -->
                <div class="tabs">
                    <button class="tab-btn active" onclick="switchTab('compose')">
                        <i class="fas fa-edit"></i> Compose
                    </button>
                    <button class="tab-btn" onclick="switchTab('files')">
                        <i class="fas fa-upload"></i> Files
                    </button>
                    <button class="tab-btn" onclick="switchTab('templates')">
                        <i class="fas fa-copy"></i> Templates
                    </button>
                    <button class="tab-btn" onclick="switchTab('schedule')">
                        <i class="fas fa-clock"></i> Schedule
                    </button>
                    <button class="tab-btn" onclick="switchTab('analytics')">
                        <i class="fas fa-chart-bar"></i> Analytics
                    </button>
                    <button class="tab-btn" onclick="switchTab('history')">
                        <i class="fas fa-history"></i> History
                    </button>
                </div>

                <!-- Tab Contents -->
                <div id="tabContents">
                    <!-- Compose Tab -->
                    <div id="composeTab" class="tab-content">
                        <!-- Server/Channel Selector -->
                        <div class="form-section">
                            <h3 class="form-title"><i class="fas fa-hashtag"></i> Select Destination</h3>
                            <div class="form-group">
                                <label class="form-label">Server</label>
                                <select class="form-control" id="guildSelect" onchange="loadChannels()">
                                    <option value="">Select a server</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Channel</label>
                                <select class="form-control" id="channelSelect">
                                    <option value="">Select a channel</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">
                                    <i class="fas fa-bullhorn"></i> Bulk Send
                                    <input type="checkbox" id="bulkSend" onchange="toggleBulkSend()">
                                </label>
                                <div id="bulkChannels" class="hidden">
                                    <label class="form-label">Select Multiple Channels</label>
                                    <div id="channelCheckboxes" class="file-list"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Message Form -->
                        <div class="form-section">
                            <h3 class="form-title"><i class="fas fa-edit"></i> Compose Message</h3>
                            
                            <div class="form-group">
                                <label class="form-label">Title</label>
                                <input type="text" class="form-control" id="messageTitle" placeholder="Message title...">
                            </div>
                            
                            <div class="form-group">
                                <label class="form-label">Content</label>
                                <textarea class="form-control" id="messageContent" rows="6" 
                                          placeholder="Type your message here..."></textarea>
                            </div>

                            <!-- Embed Builder -->
                            <div class="form-group">
                                <label class="form-label">
                                    <i class="fas fa-paint-brush"></i> Embed Options
                                    <input type="checkbox" id="useEmbed" onchange="toggleEmbedBuilder()">
                                </label>
                                
                                <div id="embedBuilder" class="hidden">
                                    <div class="embed-builder">
                                        <div class="form-group">
                                            <label class="form-label">Embed Color</label>
                                            <div class="embed-color-picker">
                                                <div class="color-option" style="background:#5865F2;" onclick="setEmbedColor('#5865F2')"></div>
                                                <div class="color-option" style="background:#57F287;" onclick="setEmbedColor('#57F287')"></div>
                                                <div class="color-option" style="background:#FEE75C;" onclick="setEmbedColor('#FEE75C')"></div>
                                                <div class="color-option" style="background:#EB459E;" onclick="setEmbedColor('#EB459E')"></div>
                                                <div class="color-option" style="background:#ED4245;" onclick="setEmbedColor('#ED4245')"></div>
                                                <div class="color-option" style="background:#FFFFFF;" onclick="setEmbedColor('#FFFFFF')"></div>
                                            </div>
                                            <input type="color" class="form-control mt-2" id="customColor" onchange="setEmbedColor(this.value)">
                                        </div>
                                        
                                        <div class="form-group">
                                            <label class="form-label">Embed Image URL</label>
                                            <input type="text" class="form-control" id="embedImage" placeholder="https://example.com/image.jpg">
                                        </div>
                                        
                                        <div class="form-group">
                                            <label class="form-label">Embed Thumbnail URL</label>
                                            <input type="text" class="form-control" id="embedThumbnail" placeholder="https://example.com/thumbnail.jpg">
                                        </div>
                                    </div>
                                    
                                    <div class="embed-preview" id="embedPreview">
                                        <div style="color: #5865F2; font-weight: bold; margin-bottom: 10px;" id="previewTitle"></div>
                                        <div style="color: #b5bac1;" id="previewContent"></div>
                                    </div>
                                </div>
                            </div>

                            <!-- File Upload -->
                            <div class="form-group">
                                <label class="form-label"><i class="fas fa-paperclip"></i> Attachments</label>
                                <div class="file-upload" onclick="document.getElementById('fileInput').click()">
                                    <div class="upload-icon">
                                        <i class="fas fa-cloud-upload-alt"></i>
                                    </div>
                                    <h4>Click to upload files</h4>
                                    <p>Supports images, documents, audio, video, and archives (Max 50MB)</p>
                                    <input type="file" id="fileInput" multiple onchange="handleFileUpload(event)">
                                </div>
                                <div class="file-list" id="fileList"></div>
                            </div>

                            <!-- Preview -->
                            <div class="form-section">
                                <h3 class="form-title"><i class="fas fa-eye"></i> Preview</h3>
                                <div id="messagePreview" class="embed-preview">
                                    Preview will appear here...
                                </div>
                            </div>

                            <!-- Action Buttons -->
                            <div class="btn-group">
                                <button class="btn btn-primary" onclick="sendMessage()">
                                    <i class="fas fa-paper-plane"></i> Send Message
                                </button>
                                <button class="btn btn-success" onclick="saveTemplate()">
                                    <i class="fas fa-save"></i> Save Template
                                </button>
                                <button class="btn btn-warning" onclick="scheduleMessage()">
                                    <i class="fas fa-clock"></i> Schedule
                                </button>
                                <button class="btn btn-danger" onclick="clearForm()">
                                    <i class="fas fa-trash"></i> Clear
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Files Tab -->
                    <div id="filesTab" class="tab-content hidden">
                        <div class="form-section">
                            <h3 class="form-title"><i class="fas fa-upload"></i> File Manager</h3>
                            <div class="btn-group">
                                <button class="btn btn-primary" onclick="uploadNewFile()">
                                    <i class="fas fa-plus"></i> Upload New
                                </button>
                                <button class="btn btn-danger" onclick="clearAllFiles()">
                                    <i class="fas fa-trash"></i> Clear All
                                </button>
                            </div>
                            <div class="file-list mt-3" id="uploadedFiles"></div>
                        </div>
                    </div>

                    <!-- Templates Tab -->
                    <div id="templatesTab" class="tab-content hidden">
                        <div class="form-section">
                            <h3 class="form-title"><i class="fas fa-copy"></i> Message Templates</h3>
                            <div class="btn-group">
                                <button class="btn btn-primary" onclick="createNewTemplate()">
                                    <i class="fas fa-plus"></i> New Template
                                </button>
                            </div>
                            <div class="mt-3" id="templatesList"></div>
                        </div>
                    </div>

                    <!-- Schedule Tab -->
                    <div id="scheduleTab" class="tab-content hidden">
                        <div class="form-section">
                            <h3 class="form-title"><i class="fas fa-clock"></i> Scheduled Messages</h3>
                            <div id="scheduledList"></div>
                        </div>
                    </div>

                    <!-- Analytics Tab -->
                    <div id="analyticsTab" class="tab-content hidden">
                        <div class="form-section">
                            <h3 class="form-title"><i class="fas fa-chart-bar"></i> Analytics Dashboard</h3>
                            <div class="stats-grid">
                                <div class="stat-card">
                                    <div class="stat-icon"><i class="fas fa-paper-plane"></i></div>
                                    <div class="stat-value" id="analyticsTotal">0</div>
                                    <div class="stat-label">Total Messages</div>
                                </div>
                                <div class="stat-card">
                                    <div class="stat-icon"><i class="fas fa-image"></i></div>
                                    <div class="stat-value" id="analyticsFiles">0</div>
                                    <div class="stat-label">Files Sent</div>
                                </div>
                                <div class="stat-card">
                                    <div class="stat-icon"><i class="fas fa-paint-brush"></i></div>
                                    <div class="stat-value" id="analyticsEmbeds">0</div>
                                    <div class="stat-label">Embeds Sent</div>
                                </div>
                                <div class="stat-card">
                                    <div class="stat-icon"><i class="fas fa-chart-line"></i></div>
                                    <div class="stat-value" id="analyticsSuccess">100%</div>
                                    <div class="stat-label">Success Rate</div>
                                </div>
                            </div>
                            <div id="analyticsChart" class="mt-3">
                                <canvas id="messageChart" width="400" height="200"></canvas>
                            </div>
                        </div>
                    </div>

                    <!-- History Tab -->
                    <div id="historyTab" class="tab-content hidden">
                        <div class="form-section">
                            <h3 class="form-title"><i class="fas fa-history"></i> Message History</h3>
                            <div class="btn-group">
                                <button class="btn btn-primary" onclick="refreshHistory()">
                                    <i class="fas fa-sync"></i> Refresh
                                </button>
                                <button class="btn btn-danger" onclick="clearHistory()">
                                    <i class="fas fa-trash"></i> Clear History
                                </button>
                            </div>
                            <table class="data-table mt-3">
                                <thead>
                                    <tr>
                                        <th>Time</th>
                                        <th>Server</th>
                                        <th>Type</th>
                                        <th>Content</th>
                                        <th>Status</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>
                                <tbody id="historyTable">
                                    <!-- History rows will be inserted here -->
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right Sidebar -->
            <div class="right-sidebar">
                <div class="sidebar-title">
                    <i class="fas fa-bolt"></i> Quick Actions
                </div>
                
                <div class="form-section">
                    <button class="btn btn-primary w-100 mb-2" onclick="quickSend('announcement')">
                        <i class="fas fa-bullhorn"></i> Announcement
                    </button>
                    <button class="btn btn-success w-100 mb-2" onclick="quickSend('welcome')">
                        <i class="fas fa-door-open"></i> Welcome Message
                    </button>
                    <button class="btn btn-warning w-100 mb-2" onclick="quickSend('poll')">
                        <i class="fas fa-poll"></i> Create Poll
                    </button>
                    <button class="btn btn-danger w-100 mb-2" onclick="quickSend('alert')">
                        <i class="fas fa-exclamation-triangle"></i> Alert
                    </button>
                </div>

                <div class="sidebar-title mt-3">
                    <i class="fas fa-history"></i> Recent Activity
                </div>
                <div id="recentActivity"></div>
            </div>
        </div>
        {% endif %}
    </div>

    <!-- Modals -->
    <div id="templateModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2><i class="fas fa-copy"></i> Save Template</h2>
                <button class="modal-close" onclick="closeModal('templateModal')">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Template Name</label>
                <input type="text" class="form-control" id="templateName" placeholder="Enter template name...">
            </div>
            <div class="btn-group">
                <button class="btn btn-success" onclick="confirmSaveTemplate()">Save</button>
                <button class="btn btn-danger" onclick="closeModal('templateModal')">Cancel</button>
            </div>
        </div>
    </div>

    <div id="scheduleModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2><i class="fas fa-clock"></i> Schedule Message</h2>
                <button class="modal-close" onclick="closeModal('scheduleModal')">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Schedule Date & Time</label>
                <input type="datetime-local" class="form-control" id="scheduleDateTime">
            </div>
            <div class="btn-group">
                <button class="btn btn-success" onclick="confirmSchedule()">Schedule</button>
                <button class="btn btn-danger" onclick="closeModal('scheduleModal')">Cancel</button>
            </div>
        </div>
    </div>

    <div id="uploadModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2><i class="fas fa-upload"></i> Upload Files</h2>
                <button class="modal-close" onclick="closeModal('uploadModal')">&times;</button>
            </div>
            <div class="file-upload" onclick="document.getElementById('modalFileInput').click()">
                <div class="upload-icon">
                    <i class="fas fa-cloud-upload-alt"></i>
                </div>
                <h4>Drop files here or click to upload</h4>
                <p>Maximum 50MB per file</p>
                <input type="file" id="modalFileInput" multiple onchange="handleModalUpload(event)">
            </div>
            <div class="file-list mt-3" id="modalFileList"></div>
            <div class="btn-group mt-3">
                <button class="btn btn-success" onclick="confirmUpload()">Upload</button>
                <button class="btn btn-danger" onclick="closeModal('uploadModal')">Cancel</button>
            </div>
        </div>
    </div>

    <!-- Toast Container -->
    <div id="toastContainer"></div>

    <script>
        // Global Variables
        let selectedFiles = [];
        let selectedGuild = null;
        let selectedChannels = [];
        let currentTab = 'compose';
        let embedColor = '#5865F2';
        let currentTemplates = [];
        let uploadedFilesList = [];
        let scheduledMessages = [];

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            {% if user %}
            initializeDashboard();
            {% else %}
            updatePublicStats();
            {% endif %}
            
            // Set theme
            const theme = localStorage.getItem('theme') || 'dark';
            document.documentElement.setAttribute('data-theme', theme);
            
            // Update time every minute
            setInterval(updateTime, 60000);
            updateTime();
        });

        // Theme Toggle
        function toggleTheme() {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        }

        // Tab Switching
        function switchTab(tabName) {
            // Update tab buttons
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.currentTarget.classList.add('active');
            
            // Hide all tabs
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.add('hidden');
            });
            
            // Show selected tab
            document.getElementById(tabName + 'Tab').classList.remove('hidden');
            currentTab = tabName;
            
            // Load tab data
            switch(tabName) {
                case 'files':
                    loadUploadedFiles();
                    break;
                case 'templates':
                    loadTemplates();
                    break;
                case 'schedule':
                    loadScheduledMessages();
                    break;
                case 'analytics':
                    loadAnalytics();
                    break;
                case 'history':
                    loadHistory();
                    break;
            }
        }

        // Initialize Dashboard
        async function initializeDashboard() {
            await loadGuilds();
            await loadStats();
            await loadRecentActivity();
            updatePreview();
            
            // Load Chart.js if needed
            if (typeof Chart !== 'undefined') {
                initializeChart();
            }
        }

        // Load Guilds
        async function loadGuilds() {
            try {
                const response = await fetch('/api/guilds');
                const data = await response.json();
                
                const guildSelect = document.getElementById('guildSelect');
                const guildList = document.getElementById('guildList');
                const guildCount = document.getElementById('guildCount');
                
                guildSelect.innerHTML = '<option value="">Select a server</option>';
                guildList.innerHTML = '';
                
                if (data.guilds && data.guilds.length > 0) {
                    guildCount.textContent = data.guilds.length;
                    
                    data.guilds.forEach(guild => {
                        // Add to select dropdown
                        const option = document.createElement('option');
                        option.value = guild.id;
                        option.textContent = guild.name;
                        guildSelect.appendChild(option);
                        
                        // Add to guild list
                        const guildItem = document.createElement('div');
                        guildItem.className = 'file-item';
                        guildItem.innerHTML = `
                            <i class="fas fa-server"></i>
                            <span>${guild.name}</span>
                            <span style="margin-left: auto; font-size: 0.8em; color: var(--light);">
                                ${guild.member_count} members
                            </span>
                        `;
                        guildItem.onclick = () => {
                            guildSelect.value = guild.id;
                            loadChannels();
                        };
                        guildList.appendChild(guildItem);
                    });
                }
            } catch (error) {
                console.error('Error loading guilds:', error);
                showToast('Error loading servers', 'danger');
            }
        }

        // Load Channels
        async function loadChannels() {
            const guildId = document.getElementById('guildSelect').value;
            if (!guildId) return;
            
            try {
                const response = await fetch(`/api/channels?guild_id=${guildId}`);
                const data = await response.json();
                
                const channelSelect = document.getElementById('channelSelect');
                const channelCheckboxes = document.getElementById('channelCheckboxes');
                
                channelSelect.innerHTML = '<option value="">Select a channel</option>';
                channelCheckboxes.innerHTML = '';
                
                if (data.channels && data.channels.length > 0) {
                    data.channels.forEach(channel => {
                        // Add to select dropdown
                        const option = document.createElement('option');
                        option.value = channel.id;
                        option.textContent = `#${channel.name}`;
                        channelSelect.appendChild(option);
                        
                        // Add to checkboxes for bulk send
                        const checkbox = document.createElement('div');
                        checkbox.className = 'file-item';
                        checkbox.innerHTML = `
                            <input type="checkbox" value="${channel.id}" id="ch_${channel.id}" 
                                   onchange="updateSelectedChannels(this)">
                            <label for="ch_${channel.id}">#${channel.name}</label>
                        `;
                        channelCheckboxes.appendChild(checkbox);
                    });
                }
            } catch (error) {
                console.error('Error loading channels:', error);
            }
        }

        // Toggle Bulk Send
        function toggleBulkSend() {
            const bulkSend = document.getElementById('bulkSend').checked;
            const bulkChannels = document.getElementById('bulkChannels');
            const channelSelect = document.getElementById('channelSelect');
            
            if (bulkSend) {
                bulkChannels.classList.remove('hidden');
                channelSelect.disabled = true;
                channelSelect.value = '';
            } else {
                bulkChannels.classList.add('hidden');
                channelSelect.disabled = false;
                selectedChannels = [];
            }
        }

        // Update Selected Channels
        function updateSelectedChannels(checkbox) {
            if (checkbox.checked) {
                selectedChannels.push(checkbox.value);
            } else {
                selectedChannels = selectedChannels.filter(id => id !== checkbox.value);
            }
        }

        // Handle File Upload
        function handleFileUpload(event) {
            const files = Array.from(event.target.files);
            const fileList = document.getElementById('fileList');
            
            files.forEach(file => {
                if (file.size > 50 * 1024 * 1024) {
                    showToast(`File ${file.name} exceeds 50MB limit`, 'danger');
                    return;
                }
                
                selectedFiles.push(file);
                
                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                fileItem.innerHTML = `
                    <i class="fas fa-file"></i>
                    <span>${file.name} (${formatBytes(file.size)})</span>
                    <span class="file-remove" onclick="removeFile('${file.name}')">
                        <i class="fas fa-times"></i>
                    </span>
                `;
                fileList.appendChild(fileItem);
            });
            
            event.target.value = '';
            showToast(`${files.length} file(s) added`, 'success');
        }

        // Remove File
        function removeFile(fileName) {
            selectedFiles = selectedFiles.filter(file => file.name !== fileName);
            loadFileList();
        }

        // Load File List
        function loadFileList() {
            const fileList = document.getElementById('fileList');
            fileList.innerHTML = '';
            
            selectedFiles.forEach(file => {
                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                fileItem.innerHTML = `
                    <i class="fas fa-file"></i>
                    <span>${file.name} (${formatBytes(file.size)})</span>
                    <span class="file-remove" onclick="removeFile('${file.name}')">
                        <i class="fas fa-times"></i>
                    </span>
                `;
                fileList.appendChild(fileItem);
            });
        }

        // Toggle Embed Builder
        function toggleEmbedBuilder() {
            const useEmbed = document.getElementById('useEmbed').checked;
            const embedBuilder = document.getElementById('embedBuilder');
            
            if (useEmbed) {
                embedBuilder.classList.remove('hidden');
                updatePreview();
            } else {
                embedBuilder.classList.add('hidden');
                updatePreview();
            }
        }

        // Set Embed Color
        function setEmbedColor(color) {
            embedColor = color;
            document.getElementById('customColor').value = color;
            updatePreview();
        }

        // Update Preview
        function updatePreview() {
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            const useEmbed = document.getElementById('useEmbed').checked;
            const preview = document.getElementById('messagePreview');
            
            let previewHTML = '';
            
            if (useEmbed) {
                previewHTML = `
                    <div style="border-left: 4px solid ${embedColor}; padding-left: 15px;">
                        ${title ? `<div style="color: ${embedColor}; font-weight: bold; margin-bottom: 10px;">${title}</div>` : ''}
                        <div style="color: #b5bac1;">${content || 'No content'}</div>
                        ${selectedFiles.length > 0 ? `<div style="margin-top: 10px; color: var(--light);"><i class="fas fa-paperclip"></i> ${selectedFiles.length} attachment(s)</div>` : ''}
                    </div>
                `;
            } else {
                previewHTML = `
                    ${title ? `<strong>${title}</strong><br><br>` : ''}
                    ${content || '<em style="color: var(--light);">No content entered yet...</em>'}
                    ${selectedFiles.length > 0 ? `<br><br><i class="fas fa-paperclip"></i> ${selectedFiles.length} attachment(s)` : ''}
                `;
            }
            
            preview.innerHTML = previewHTML;
        }

        // Send Message
        async function sendMessage() {
            const guildId = document.getElementById('guildSelect').value;
            const channelId = document.getElementById('channelSelect').value;
            const bulkSend = document.getElementById('bulkSend').checked;
            
            if (!guildId) {
                showToast('Please select a server', 'danger');
                return;
            }
            
            if (!bulkSend && !channelId) {
                showToast('Please select a channel', 'danger');
                return;
            }
            
            if (bulkSend && selectedChannels.length === 0) {
                showToast('Please select at least one channel for bulk send', 'danger');
                return;
            }
            
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            const useEmbed = document.getElementById('useEmbed').checked;
            
            if (!content.trim() && selectedFiles.length === 0) {
                showToast('Please enter content or attach files', 'danger');
                return;
            }
            
            try {
                const formData = new FormData();
                formData.append('guild_id', guildId);
                formData.append('title', title);
                formData.append('content', content);
                formData.append('embed', useEmbed);
                formData.append('embed_color', embedColor);
                formData.append('bulk_send', bulkSend);
                
                if (bulkSend) {
                    formData.append('channel_ids', JSON.stringify(selectedChannels));
                } else {
                    formData.append('channel_id', channelId);
                }
                
                // Add files
                selectedFiles.forEach(file => {
                    formData.append('files', file);
                });
                
                // Add embed data if enabled
                if (useEmbed) {
                    formData.append('embed_image', document.getElementById('embedImage').value);
                    formData.append('embed_thumbnail', document.getElementById('embedThumbnail').value);
                }
                
                const response = await fetch('/api/send', {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showToast(result.message || 'Message sent successfully!', 'success');
                    clearForm();
                    loadStats();
                    loadRecentActivity();
                } else {
                    showToast('Error: ' + (result.error || 'Failed to send message'), 'danger');
                }
            } catch (error) {
                showToast('Network error: ' + error.message, 'danger');
            }
        }

        // Clear Form
        function clearForm() {
            document.getElementById('messageTitle').value = '';
            document.getElementById('messageContent').value = '';
            document.getElementById('embedImage').value = '';
            document.getElementById('embedThumbnail').value = '';
            document.getElementById('useEmbed').checked = false;
            document.getElementById('bulkSend').checked = false;
            
            selectedFiles = [];
            selectedChannels = [];
            
            loadFileList();
            toggleEmbedBuilder();
            toggleBulkSend();
            updatePreview();
        }

        // Save Template
        function saveTemplate() {
            const content = document.getElementById('messageContent').value;
            if (!content.trim()) {
                showToast('Please enter content to save as template', 'warning');
                return;
            }
            
            openModal('templateModal');
        }

        // Confirm Save Template
        async function confirmSaveTemplate() {
            const name = document.getElementById('templateName').value;
            if (!name.trim()) {
                showToast('Please enter template name', 'warning');
                return;
            }
            
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            const useEmbed = document.getElementById('useEmbed').checked;
            const embedColor = document.getElementById('customColor').value;
            
            try {
                const response = await fetch('/api/templates', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        name: name,
                        title: title,
                        content: content,
                        embed: useEmbed,
                        embed_color: embedColor
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showToast('Template saved successfully!', 'success');
                    closeModal('templateModal');
                    loadTemplates();
                } else {
                    showToast('Error saving template: ' + result.error, 'danger');
                }
            } catch (error) {
                showToast('Error: ' + error.message, 'danger');
            }
        }

        // Schedule Message
        function scheduleMessage() {
            const content = document.getElementById('messageContent').value;
            if (!content.trim() && selectedFiles.length === 0) {
                showToast('Please enter content to schedule', 'warning');
                return;
            }
            
            openModal('scheduleModal');
        }

        // Confirm Schedule
        async function confirmSchedule() {
            const scheduleTime = document.getElementById('scheduleDateTime').value;
            if (!scheduleTime) {
                showToast('Please select schedule time', 'warning');
                return;
            }
            
            const guildId = document.getElementById('guildSelect').value;
            const channelId = document.getElementById('channelSelect').value;
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            const useEmbed = document.getElementById('useEmbed').checked;
            
            try {
                const response = await fetch('/api/schedule', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        guild_id: guildId,
                        channel_id: channelId,
                        title: title,
                        content: content,
                        embed: useEmbed,
                        embed_color: embedColor,
                        scheduled_time: scheduleTime
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showToast('Message scheduled successfully!', 'success');
                    closeModal('scheduleModal');
                    loadScheduledMessages();
                } else {
                    showToast('Error scheduling message: ' + result.error, 'danger');
                }
            } catch (error) {
                showToast('Error: ' + error.message, 'danger');
            }
        }

        // Load Stats
        async function loadStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();
                
                document.getElementById('totalMessages').textContent = data.total_messages || 0;
                document.getElementById('totalFiles').textContent = data.total_files || 0;
                document.getElementById('totalTemplates').textContent = data.total_templates || 0;
                document.getElementById('totalScheduled').textContent = data.total_scheduled || 0;
            } catch (error) {
                console.error('Error loading stats:', error);
            }
        }

        // Load Uploaded Files
        async function loadUploadedFiles() {
            try {
                const response = await fetch('/api/files');
                const data = await response.json();
                uploadedFilesList = data.files || [];
                
                const container = document.getElementById('uploadedFiles');
                container.innerHTML = '';
                
                uploadedFilesList.forEach(file => {
                    const fileItem = document.createElement('div');
                    fileItem.className = 'file-item';
                    fileItem.innerHTML = `
                        <i class="fas fa-file"></i>
                        <span>${file.filename} (${formatBytes(file.file_size)})</span>
                        <span style="margin-left: auto; font-size: 0.8em; color: var(--light);">
                            ${file.file_type}
                        </span>
                        <span class="file-remove" onclick="deleteFile('${file.id}')">
                            <i class="fas fa-trash"></i>
                        </span>
                    `;
                    container.appendChild(fileItem);
                });
            } catch (error) {
                console.error('Error loading files:', error);
            }
        }

        // Load Templates
        async function loadTemplates() {
            try {
                const response = await fetch('/api/templates');
                const data = await response.json();
                currentTemplates = data.templates || [];
                
                const container = document.getElementById('templatesList');
                container.innerHTML = '';
                
                currentTemplates.forEach(template => {
                    const templateCard = document.createElement('div');
                    templateCard.className = 'stat-card';
                    templateCard.innerHTML = `
                        <div class="d-flex justify-between align-center">
                            <div>
                                <h4>${template.name}</h4>
                                <p style="color: var(--light); font-size: 0.9em; margin-top: 5px;">
                                    ${template.content.substring(0, 100)}${template.content.length > 100 ? '...' : ''}
                                </p>
                            </div>
                            <div class="d-flex gap-2">
                                <button class="btn btn-primary btn-sm" onclick="useTemplate('${template.id}')">
                                    <i class="fas fa-use"></i>
                                </button>
                                <button class="btn btn-danger btn-sm" onclick="deleteTemplate('${template.id}')">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    `;
                    container.appendChild(templateCard);
                });
            } catch (error) {
                console.error('Error loading templates:', error);
            }
        }

        // Load Scheduled Messages
        async function loadScheduledMessages() {
            try {
                const response = await fetch('/api/scheduled');
                const data = await response.json();
                scheduledMessages = data.scheduled || [];
                
                const container = document.getElementById('scheduledList');
                container.innerHTML = '';
                
                scheduledMessages.forEach(schedule => {
                    const date = new Date(schedule.scheduled_time);
                    const now = new Date();
                    const timeLeft = date - now;
                    
                    const card = document.createElement('div');
                    card.className = 'stat-card';
                    card.innerHTML = `
                        <div class="d-flex justify-between align-center">
                            <div>
                                <h4>${schedule.title || 'No Title'}</h4>
                                <p style="color: var(--light); font-size: 0.9em; margin-top: 5px;">
                                    Scheduled: ${date.toLocaleString()}
                                </p>
                                <p style="color: var(--light); font-size: 0.8em;">
                                    ${schedule.content.substring(0, 80)}${schedule.content.length > 80 ? '...' : ''}
                                </p>
                            </div>
                            <div class="d-flex gap-2">
                                <span class="badge ${timeLeft > 0 ? 'badge-warning' : 'badge-success'}">
                                    ${timeLeft > 0 ? formatTimeLeft(timeLeft) : 'Ready'}
                                </span>
                                <button class="btn btn-danger btn-sm" onclick="cancelSchedule('${schedule.id}')">
                                    <i class="fas fa-times"></i>
                                </button>
                            </div>
                        </div>
                    `;
                    container.appendChild(card);
                });
            } catch (error) {
                console.error('Error loading scheduled messages:', error);
            }
        }

        // Load Analytics
        async function loadAnalytics() {
            try {
                const response = await fetch('/api/analytics');
                const data = await response.json();
                
                document.getElementById('analyticsTotal').textContent = data.total_messages || 0;
                document.getElementById('analyticsFiles').textContent = data.total_files || 0;
                document.getElementById('analyticsEmbeds').textContent = data.total_embeds || 0;
                document.getElementById('analyticsSuccess').textContent = data.success_rate || '100%';
                
                // Update chart if available
                if (window.messageChart && data.daily_stats) {
                    updateChart(data.daily_stats);
                }
            } catch (error) {
                console.error('Error loading analytics:', error);
            }
        }

        // Load History
        async function loadHistory() {
            try {
                const response = await fetch('/api/history');
                const data = await response.json();
                
                const table = document.getElementById('historyTable');
                table.innerHTML = '';
                
                data.history.forEach(item => {
                    const date = new Date(item.timestamp * 1000);
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${date.toLocaleString()}</td>
                        <td>${item.guild}</td>
                        <td><span class="badge ${item.type.includes('Embed') ? 'badge-success' : 'badge-primary'}">${item.type}</span></td>
                        <td>${item.content}</td>
                        <td><span class="badge badge-success">${item.status}</span></td>
                        <td>
                            <button class="btn btn-sm btn-primary" onclick="resendMessage('${item.id}')">
                                <i class="fas fa-redo"></i>
                            </button>
                            <button class="btn btn-sm btn-danger" onclick="deleteMessage('${item.id}')">
                                <i class="fas fa-trash"></i>
                            </button>
                        </td>
                    `;
                    table.appendChild(row);
                });
            } catch (error) {
                console.error('Error loading history:', error);
            }
        }

        // Load Recent Activity
        async function loadRecentActivity() {
            try {
                const response = await fetch('/api/activity');
                const data = await response.json();
                
                const container = document.getElementById('recentActivity');
                container.innerHTML = '';
                
                data.activity.forEach(activity => {
                    const activityItem = document.createElement('div');
                    activityItem.className = 'file-item mb-2';
                    activityItem.innerHTML = `
                        <i class="fas fa-${activity.icon}"></i>
                        <div>
                            <div>${activity.message}</div>
                            <small style="color: var(--light);">${activity.time}</small>
                        </div>
                    `;
                    container.appendChild(activityItem);
                });
            } catch (error) {
                console.error('Error loading activity:', error);
            }
        }

        // Quick Send Templates
        function quickSend(type) {
            let title = '';
            let content = '';
            let embed = false;
            
            switch(type) {
                case 'announcement':
                    title = '📢 Important Announcement';
                    content = 'Attention everyone! This is an important announcement.';
                    embed = true;
                    embedColor = '#ED4245';
                    break;
                case 'welcome':
                    title = '👋 Welcome to the Server!';
                    content = 'Hello and welcome to our server! Please read the rules and introduce yourself.';
                    embed = true;
                    embedColor = '#57F287';
                    break;
                case 'poll':
                    title = '📊 Community Poll';
                    content = 'What do you think about this?\n\n✅ Yes\n❌ No\n🤔 Maybe';
                    embed = false;
                    break;
                case 'alert':
                    title = '⚠️ Important Alert';
                    content = 'Please read this important information immediately!';
                    embed = true;
                    embedColor = '#FEE75C';
                    break;
            }
            
            document.getElementById('messageTitle').value = title;
            document.getElementById('messageContent').value = content;
            document.getElementById('useEmbed').checked = embed;
            
            if (embed) {
                setEmbedColor(embedColor);
                toggleEmbedBuilder();
            }
            
            updatePreview();
            showToast(`Loaded ${type} template`, 'success');
        }

        // Modal Functions
        function openModal(modalId) {
            document.getElementById(modalId).classList.add('active');
        }

        function closeModal(modalId) {
            document.getElementById(modalId).classList.remove('active');
        }

        // Utility Functions
        function formatBytes(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        function formatTimeLeft(ms) {
            const days = Math.floor(ms / (1000 * 60 * 60 * 24));
            const hours = Math.floor((ms % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            const minutes = Math.floor((ms % (1000 * 60 * 60)) / (1000 * 60));
            
            if (days > 0) return `${days}d ${hours}h`;
            if (hours > 0) return `${hours}h ${minutes}m`;
            return `${minutes}m`;
        }

        function showToast(message, type = 'success') {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = 'toast';
            
            const icon = type === 'success' ? 'check-circle' : 
                        type === 'danger' ? 'exclamation-circle' : 'info-circle';
            
            toast.innerHTML = `
                <i class="fas fa-${icon}"></i>
                <span>${message}</span>
            `;
            
            container.appendChild(toast);
            
            setTimeout(() => {
                toast.remove();
            }, 3000);
        }

        function updateTime() {
            const now = new Date();
            document.querySelectorAll('.current-time').forEach(el => {
                el.textContent = now.toLocaleTimeString();
            });
        }

        // Event Listeners
        document.getElementById('messageTitle').addEventListener('input', updatePreview);
        document.getElementById('messageContent').addEventListener('input', updatePreview);
        document.getElementById('embedImage').addEventListener('input', updatePreview);
        document.getElementById('embedThumbnail').addEventListener('input', updatePreview);

        // Keyboard Shortcuts
        document.addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                sendMessage();
            }
            if (e.ctrlKey && e.key === 's') {
                e.preventDefault();
                saveTemplate();
            }
            if (e.key === 'Escape') {
                clearForm();
            }
        });

        // Initialize Chart
        function initializeChart() {
            const ctx = document.getElementById('messageChart').getContext('2d');
            window.messageChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Messages Sent',
                        data: [],
                        borderColor: '#7289da',
                        backgroundColor: 'rgba(114, 137, 218, 0.1)',
                        fill: true
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            labels: {
                                color: '#ffffff'
                            }
                        }
                    },
                    scales: {
                        x: {
                            ticks: {
                                color: '#ffffff'
                            },
                            grid: {
                                color: 'rgba(255, 255, 255, 0.1)'
                            }
                        },
                        y: {
                            ticks: {
                                color: '#ffffff'
                            },
                            grid: {
                                color: 'rgba(255, 255, 255, 0.1)'
                            }
                        }
                    }
                }
            });
        }

        function updateChart(dailyStats) {
            if (!window.messageChart) return;
            
            const labels = Object.keys(dailyStats);
            const data = Object.values(dailyStats).map(stat => stat.messages_sent || 0);
            
            window.messageChart.data.labels = labels;
            window.messageChart.data.datasets[0].data = data;
            window.messageChart.update();
        }

        // Public Stats (for login page)
        async function updatePublicStats() {
            try {
                const response = await fetch('/api/public_stats');
                if (response.ok) {
                    const data = await response.json();
                    document.getElementById('serverCount').textContent = data.servers || '0';
                    document.getElementById('totalUsers').textContent = data.total_users || '0';
                }
            } catch (error) {
                console.error('Error fetching public stats:', error);
            }
        }
    </script>
</body>
</html>
'''

# ========== FLASK ROUTES ==========
@app.route('/')
def index():
    user = session.get('user')
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    
    ping = 0
    try:
        ping = round(bot.latency * 1000)
    except:
        pass
    
    return render_template_string(HTML_TEMPLATE, 
                                user=user, 
                                current_time=current_time,
                                ping=ping)

@app.route('/login')
def login():
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
    
    try:
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
        
        # Store in database
        conn = sqlite3.connect('dashboard.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (id, username, avatar, access_token)
            VALUES (?, ?, ?, ?)
        ''', (user['id'], user['username'], user.get('avatar'), access_token))
        conn.commit()
        conn.close()
        
        return redirect('/')
        
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route('/logout')
def logout():
    user_id = session.get('user', {}).get('id')
    if user_id:
        user_sessions.pop(str(user_id), None)
    session.clear()
    return redirect('/')

# ========== API ROUTES ==========
@app.route('/api/guilds')
def api_guilds():
    """Get mutual guilds"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({'guilds': []})
    
    user_guild_ids = session.get('user_guilds', [])
    mutual_guilds = []
    
    for guild in bot.guilds:
        if str(guild.id) in user_guild_ids:
            guild_info = {
                'id': str(guild.id),
                'name': guild.name,
                'icon': str(guild.icon.url) if guild.icon else None,
                'member_count': guild.member_count,
                'channel_count': len([c for c in guild.channels if isinstance(c, discord.TextChannel)]),
                'emoji_count': len(guild.emojis)
            }
            mutual_guilds.append(guild_info)
    
    return jsonify({'guilds': mutual_guilds})

@app.route('/api/channels')
def api_channels():
    """Get channels for a guild"""
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
                'topic': channel.topic or '',
                'position': channel.position
            })
    
    channels.sort(key=lambda x: x['position'])
    return jsonify({'channels': channels})

@app.route('/api/send', methods=['POST'])
def api_send():
    """Send message with file upload support"""
    try:
        # Check if user is logged in
        user_id = session.get('user', {}).get('id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Not authenticated'})
        
        # Get form data
        guild_id = request.form.get('guild_id')
        channel_id = request.form.get('channel_id')
        channel_ids = request.form.get('channel_ids')
        title = request.form.get('title', '')
        content = request.form.get('content', '')
        embed = request.form.get('embed', 'false') == 'true'
        embed_color = request.form.get('embed_color', '#5865F2')
        embed_image = request.form.get('embed_image', '')
        embed_thumbnail = request.form.get('embed_thumbnail', '')
        bulk_send = request.form.get('bulk_send', 'false') == 'true'
        
        if not guild_id:
            return jsonify({'success': False, 'error': 'No guild specified'})
        
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify({'success': False, 'error': 'Guild not found'})
        
        # Determine target channels
        target_channels = []
        
        if bulk_send and channel_ids:
            try:
                channel_ids_list = json.loads(channel_ids)
                for ch_id in channel_ids_list:
                    channel = guild.get_channel(int(ch_id))
                    if channel and channel.permissions_for(guild.me).send_messages:
                        target_channels.append(channel)
            except:
                pass
        elif channel_id:
            channel = guild.get_channel(int(channel_id))
            if channel and channel.permissions_for(guild.me).send_messages:
                target_channels.append(channel)
        
        if not target_channels:
            return jsonify({'success': False, 'error': 'No valid channels specified'})
        
        # Handle file uploads
        files = request.files.getlist('files')
        file_paths = []
        
        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}_{filename}")
                file.save(file_path)
                file_paths.append(file_path)
                
                # Store in database
                conn = sqlite3.connect('dashboard.db')
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO files (id, user_id, filename, file_type, file_size, file_data)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (str(uuid.uuid4()), user_id, filename, get_file_type(filename), 
                      os.path.getsize(file_path), open(file_path, 'rb').read()))
                conn.commit()
                conn.close()
        
        # Send messages to all target channels
        sent_count = 0
        for target_channel in target_channels:
            try:
                # Create embed if enabled
                if embed:
                    embed_obj = discord.Embed(
                        title=title if title else None,
                        description=content,
                        color=discord.Color.from_str(embed_color),
                        timestamp=discord.utils.utcnow()
                    )
                    
                    if embed_image:
                        embed_obj.set_image(url=embed_image)
                    if embed_thumbnail:
                        embed_obj.set_thumbnail(url=embed_thumbnail)
                    
                    # Add files to embed
                    files_to_send = []
                    for file_path in file_paths:
                        files_to_send.append(discord.File(file_path))
                    
                    # Send message
                    asyncio.run_coroutine_threadsafe(
                        target_channel.send(embed=embed_obj, files=files_to_send if files_to_send else None),
                        bot.loop
                    )
                else:
                    # Prepare regular message
                    message_content = f"**{title}**\n\n{content}" if title else content
                    
                    # Add files
                    files_to_send = []
                    for file_path in file_paths:
                        files_to_send.append(discord.File(file_path))
                    
                    # Send message
                    asyncio.run_coroutine_threadsafe(
                        target_channel.send(content=message_content, files=files_to_send if files_to_send else None),
                        bot.loop
                    )
                
                sent_count += 1
                
                # Log the message
                message_history.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': time.time(),
                    'guild': guild.name,
                    'channel': target_channel.name,
                    'title': title,
                    'content': content[:100] + '...' if len(content) > 100 else content,
                    'type': '🎨 Embed' if embed else '📨 Message',
                    'files': len(files),
                    'status': 'sent'
                })
                
                # Store in database
                conn = sqlite3.connect('dashboard.db')
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO messages (id, user_id, guild_id, channel_id, title, content, 
                                         embed_data, is_embed, sent_time, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (str(uuid.uuid4()), user_id, guild_id, str(target_channel.id),
                     title, content, json.dumps({'color': embed_color}), embed, 
                     datetime.now().isoformat(), 'sent'))
                conn.commit()
                
                # Update analytics
                today = datetime.now().date().isoformat()
                cursor.execute('''
                    INSERT OR IGNORE INTO analytics (date) VALUES (?)
                ''', (today,))
                cursor.execute('''
                    UPDATE analytics 
                    SET messages_sent = messages_sent + 1,
                        files_sent = files_sent + ?,
                        embeds_sent = embeds_sent + ?
                    WHERE date = ?
                ''', (len(files), 1 if embed else 0, today))
                conn.commit()
                conn.close()
                
            except Exception as e:
                print(f"Error sending to {target_channel.name}: {e}")
        
        # Clean up uploaded files
        for file_path in file_paths:
            try:
                os.remove(file_path)
            except:
                pass
        
        return jsonify({
            'success': True,
            'message': f'Message sent to {sent_count} channel(s)',
            'sent_count': sent_count
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/templates', methods=['GET', 'POST'])
def api_templates():
    """Handle templates"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'})
    
    if request.method == 'GET':
        # Get templates
        conn = sqlite3.connect('dashboard.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, title, content, is_embed, created_at
            FROM templates 
            WHERE user_id = ?
            ORDER BY created_at DESC
        ''', (user_id,))
        
        templates = []
        for row in cursor.fetchall():
            templates.append({
                'id': row[0],
                'name': row[1],
                'title': row[2],
                'content': row[3],
                'embed': bool(row[4]),
                'created_at': row[5]
            })
        
        conn.close()
        return jsonify({'success': True, 'templates': templates})
    
    else:
        # Save template
        try:
            data = request.json
            name = data.get('name')
            title = data.get('title', '')
            content = data.get('content', '')
            embed = data.get('embed', False)
            embed_color = data.get('embed_color', '#5865F2')
            
            if not name or not content:
                return jsonify({'success': False, 'error': 'Name and content required'})
            
            conn = sqlite3.connect('dashboard.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO templates (id, user_id, name, title, content, is_embed, embed_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), user_id, name, title, content, embed, 
                 json.dumps({'color': embed_color})))
            conn.commit()
            conn.close()
            
            return jsonify({'success': True})
            
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

@app.route('/api/schedule', methods=['POST'])
def api_schedule():
    """Schedule a message"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'})
    
    try:
        data = request.json
        guild_id = data.get('guild_id')
        channel_id = data.get('channel_id')
        title = data.get('title', '')
        content = data.get('content', '')
        embed = data.get('embed', False)
        embed_color = data.get('embed_color', '#5865F2')
        scheduled_time = data.get('scheduled_time')
        
        if not guild_id or not channel_id or not content or not scheduled_time:
            return jsonify({'success': False, 'error': 'Missing required fields'})
        
        # Parse scheduled time
        try:
            scheduled_dt = datetime.fromisoformat(scheduled_time.replace('Z', '+00:00'))
        except:
            return jsonify({'success': False, 'error': 'Invalid date format'})
        
        # Store in database
        schedule_id = str(uuid.uuid4())
        conn = sqlite3.connect('dashboard.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO messages (id, user_id, guild_id, channel_id, title, content, 
                                 embed_data, is_embed, is_scheduled, scheduled_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (schedule_id, user_id, guild_id, channel_id, title, content,
             json.dumps({'color': embed_color}), embed, True, scheduled_dt.isoformat(), 'scheduled'))
        conn.commit()
        conn.close()
        
        # Schedule the message
        def send_scheduled_message():
            try:
                guild = bot.get_guild(int(guild_id))
                if guild:
                    channel = guild.get_channel(int(channel_id))
                    if channel and channel.permissions_for(guild.me).send_messages:
                        if embed:
                            embed_obj = discord.Embed(
                                title=title if title else None,
                                description=content,
                                color=discord.Color.from_str(embed_color),
                                timestamp=discord.utils.utcnow()
                            )
                            asyncio.run_coroutine_threadsafe(
                                channel.send(embed=embed_obj), bot.loop
                            )
                        else:
                            message_content = f"**{title}**\n\n{content}" if title else content
                            asyncio.run_coroutine_threadsafe(
                                channel.send(content=message_content), bot.loop
                            )
                        
                        # Update database
                        conn = sqlite3.connect('dashboard.db')
                        cursor = conn.cursor()
                        cursor.execute('''
                            UPDATE messages 
                            SET status = 'sent', sent_time = ?
                            WHERE id = ?
                        ''', (datetime.now().isoformat(), schedule_id))
                        conn.commit()
                        conn.close()
            except Exception as e:
                print(f"Error sending scheduled message: {e}")
        
        # Calculate delay
        delay = (scheduled_dt - datetime.now()).total_seconds()
        if delay > 0:
            threading.Timer(delay, send_scheduled_message).start()
            active_schedules[schedule_id] = scheduled_dt
        
        return jsonify({'success': True, 'schedule_id': schedule_id})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/scheduled')
def api_scheduled():
    """Get scheduled messages"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({'scheduled': []})
    
    conn = sqlite3.connect('dashboard.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, guild_id, channel_id, title, content, embed_data, scheduled_time
        FROM messages 
        WHERE user_id = ? AND is_scheduled = 1 AND status = 'scheduled'
        ORDER BY scheduled_time ASC
    ''', (user_id,))
    
    scheduled = []
    for row in cursor.fetchall():
        scheduled.append({
            'id': row[0],
            'guild_id': row[1],
            'channel_id': row[2],
            'title': row[3],
            'content': row[4],
            'embed_data': json.loads(row[5]) if row[5] else {},
            'scheduled_time': row[6]
        })
    
    conn.close()
    return jsonify({'scheduled': scheduled})

@app.route('/api/analytics')
def api_analytics():
    """Get analytics data"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({})
    
    conn = sqlite3.connect('dashboard.db')
    cursor = conn.cursor()
    
    # Get total counts
    cursor.execute('SELECT COUNT(*) FROM messages WHERE user_id = ?', (user_id,))
    total_messages = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM files WHERE user_id = ?', (user_id,))
    total_files = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM messages WHERE user_id = ? AND is_embed = 1', (user_id,))
    total_embeds = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM templates WHERE user_id = ?', (user_id,))
    total_templates = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM messages WHERE user_id = ? AND is_scheduled = 1 AND status = "scheduled"', (user_id,))
    total_scheduled = cursor.fetchone()[0]
    
    # Get daily stats for last 7 days
    daily_stats = {}
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).date().isoformat()
        cursor.execute('''
            SELECT messages_sent, files_sent, embeds_sent 
            FROM analytics 
            WHERE date = ?
        ''', (date,))
        
        row = cursor.fetchone()
        if row:
            daily_stats[date] = {
                'messages_sent': row[0],
                'files_sent': row[1],
                'embeds_sent': row[2]
            }
        else:
            daily_stats[date] = {'messages_sent': 0, 'files_sent': 0, 'embeds_sent': 0}
    
    conn.close()
    
    return jsonify({
        'total_messages': total_messages,
        'total_files': total_files,
        'total_embeds': total_embeds,
        'total_templates': total_templates,
        'total_scheduled': total_scheduled,
        'success_rate': '100%',
        'daily_stats': daily_stats
    })

@app.route('/api/history')
def api_history():
    """Get message history"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({'history': []})
    
    conn = sqlite3.connect('dashboard.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, guild_id, channel_id, title, content, is_embed, sent_time, status
        FROM messages 
        WHERE user_id = ? AND is_scheduled = 0
        ORDER BY sent_time DESC
        LIMIT 50
    ''', (user_id,))
    
    history = []
    for row in cursor.fetchall():
        history.append({
            'id': row[0],
            'guild': row[1],
            'channel': row[2],
            'title': row[3],
            'content': row[4],
            'type': '🎨 Embed' if row[5] else '📨 Message',
            'timestamp': row[6],
            'status': row[7]
        })
    
    conn.close()
    return jsonify({'history': history})

@app.route('/api/activity')
def api_activity():
    """Get recent activity"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({'activity': []})
    
    activities = []
    
    # Add recent messages
    conn = sqlite3.connect('dashboard.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT title, content, sent_time 
        FROM messages 
        WHERE user_id = ? AND sent_time IS NOT NULL
        ORDER BY sent_time DESC 
        LIMIT 5
    ''', (user_id,))
    
    for row in cursor.fetchall():
        activities.append({
            'icon': 'paper-plane',
            'message': f"Sent: {row[0] or 'Message'}",
            'time': row[2][:19] if row[2] else ''
        })
    
    # Add file uploads
    cursor.execute('''
        SELECT filename, uploaded_at 
        FROM files 
        WHERE user_id = ?
        ORDER BY uploaded_at DESC 
        LIMIT 3
    ''', (user_id,))
    
    for row in cursor.fetchall():
        activities.append({
            'icon': 'upload',
            'message': f"Uploaded: {row[0]}",
            'time': row[1][:19] if row[1] else ''
        })
    
    conn.close()
    return jsonify({'activity': activities})

@app.route('/api/files')
def api_files():
    """Get uploaded files"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({'files': []})
    
    conn = sqlite3.connect('dashboard.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, filename, file_type, file_size, uploaded_at
        FROM files 
        WHERE user_id = ?
        ORDER BY uploaded_at DESC
    ''', (user_id,))
    
    files = []
    for row in cursor.fetchall():
        files.append({
            'id': row[0],
            'filename': row[1],
            'file_type': row[2],
            'file_size': row[3],
            'uploaded_at': row[4]
        })
    
    conn.close()
    return jsonify({'files': files})

@app.route('/api/stats')
def api_stats():
    """Get user stats"""
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return jsonify({})
    
    conn = sqlite3.connect('dashboard.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM messages WHERE user_id = ?', (user_id,))
    total_messages = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM files WHERE user_id = ?', (user_id,))
    total_files = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM templates WHERE user_id = ?', (user_id,))
    total_templates = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM messages WHERE user_id = ? AND is_scheduled = 1 AND status = "scheduled"', (user_id,))
    total_scheduled = cursor.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'total_messages': total_messages,
        'total_files': total_files,
        'total_templates': total_templates,
        'total_scheduled': total_scheduled
    })

@app.route('/api/public_stats')
def api_public_stats():
    """Public statistics"""
    total_members = sum(g.member_count for g in bot.guilds)
    
    return jsonify({
        'servers': len(bot.guilds),
        'total_users': total_members,
        'status': 'online'
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'bot': 'online',
        'timestamp': time.time(),
        'servers': len(bot.guilds)
    })

# ========== BOT EVENTS ==========
@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    print(f"📊 Serving {len(bot.guilds)} servers")
    print(f"👥 Total members: {sum(g.member_count for g in bot.guilds)}")
    print(f"🌐 Dashboard available at: https://dashboard.digamber.in")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Message Dashboard Pro"
        )
    )
    
    # Initialize data
    for guild in bot.guilds:
        bot_guilds.append(str(guild.id))
        available_emojis[str(guild.id)] = [str(emoji) for emoji in guild.emojis]

# ========== RUN APPLICATION ==========
def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def main():
    print("🚀 Starting Discord Dashboard Pro...")
    print(f"🔧 Client ID: {DISCORD_CLIENT_ID}")
    print(f"🔧 Redirect URI: {DISCORD_REDIRECT_URI}")
    print("📋 Features: File Upload | Scheduling | Templates | Analytics | Bulk Send")
    
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flask server started on port 8080")
    
    # Run Discord bot
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
