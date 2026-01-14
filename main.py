# ========== ULTIMATE FIX FOR AUDIOOP ERROR ==========
import sys
import types

# Block audioop import completely
class AudioopBlocker:
    def find_spec(self, fullname, path, target=None):
        if fullname in ['audioop', '_audioop']:
            # Return a spec with a dummy loader
            return types.SimpleNamespace(
                loader=None,
                origin='dummy',
                submodule_search_locations=[]
            )
        return None

# Insert our blocker first
sys.meta_path.insert(0, AudioopBlocker())

# Create dummy audioop module before discord tries to import it
audioop_module = types.ModuleType('audioop')
audioop_module.ulaw2lin = lambda x, y: x
audioop_module.lin2ulaw = lambda x, y: x
audioop_module.lin2adpcm = lambda x, y, z: (x, None)
audioop_module.adpcm2lin = lambda x, y, z: x
sys.modules['audioop'] = audioop_module

# Create dummy _audioop module
sys.modules['_audioop'] = types.ModuleType('_audioop')

# NOW import discord
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
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ========== STORAGE ==========
user_sessions = {}
message_history = []
bot_guilds = []
available_emojis = {}

# ========== HTML TEMPLATE ==========
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🤖 Discord Message Dashboard</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --primary: #7289da;
            --secondary: #43b581;
            --danger: #f04747;
            --dark: #2c2f33;
            --darker: #23272a;
            --light: #99aab5;
            --lighter: #ffffff;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: var(--lighter);
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        /* Header */
        .header {
            text-align: center;
            padding: 50px 30px;
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border-radius: 25px;
            margin-bottom: 40px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }
        
        .title {
            font-size: 3.5rem;
            background: linear-gradient(90deg, #00dbde, #fc00ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 15px;
            font-weight: 800;
        }
        
        .subtitle {
            font-size: 1.3rem;
            color: var(--light);
            margin-bottom: 30px;
        }
        
        /* Login Button */
        .login-btn {
            display: inline-flex;
            align-items: center;
            gap: 15px;
            background: var(--primary);
            color: white;
            padding: 18px 40px;
            border: none;
            border-radius: 12px;
            font-size: 1.3rem;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.3s;
            box-shadow: 0 5px 15px rgba(114, 137, 218, 0.4);
        }
        
        .login-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(114, 137, 218, 0.6);
            background: #5b6eae;
        }
        
        /* Dashboard Layout */
        .dashboard {
            display: grid;
            grid-template-columns: 320px 1fr;
            gap: 30px;
            margin-top: 30px;
        }
        
        /* Sidebar */
        .sidebar {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.2);
        }
        
        .sidebar-title {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 1.4rem;
            margin-bottom: 25px;
            color: var(--lighter);
            padding-bottom: 15px;
            border-bottom: 2px solid rgba(255, 255, 255, 0.1);
        }
        
        /* Guild List */
        .guild-list {
            max-height: 600px;
            overflow-y: auto;
            padding-right: 10px;
        }
        
        .guild-list::-webkit-scrollbar {
            width: 8px;
        }
        
        .guild-list::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
        }
        
        .guild-list::-webkit-scrollbar-thumb {
            background: linear-gradient(180deg, #00dbde, #fc00ff);
            border-radius: 10px;
        }
        
        .guild-item {
            display: flex;
            align-items: center;
            gap: 15px;
            padding: 18px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            margin-bottom: 12px;
            cursor: pointer;
            transition: all 0.3s;
            border: 2px solid transparent;
        }
        
        .guild-item:hover {
            background: rgba(255, 255, 255, 0.12);
            transform: translateX(8px);
            border-color: var(--primary);
        }
        
        .guild-item.active {
            background: linear-gradient(135deg, rgba(114, 137, 218, 0.2), rgba(67, 181, 129, 0.2));
            border-color: var(--secondary);
        }
        
        .guild-icon {
            width: 55px;
            height: 55px;
            border-radius: 50%;
            background: var(--dark);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            overflow: hidden;
        }
        
        .guild-icon img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .guild-info {
            flex: 1;
        }
        
        .guild-name {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 5px;
        }
        
        .guild-stats {
            font-size: 0.85rem;
            color: var(--light);
            display: flex;
            gap: 10px;
        }
        
        /* Main Content */
        .main-content {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.2);
        }
        
        /* Status Bar */
        .status-bar {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .status-card {
            background: rgba(255, 255, 255, 0.08);
            padding: 25px;
            border-radius: 15px;
            text-align: center;
            border: 1px solid rgba(255, 255, 255, 0.1);
            transition: transform 0.3s;
        }
        
        .status-card:hover {
            transform: translateY(-5px);
        }
        
        .status-icon {
            font-size: 2.5rem;
            margin-bottom: 15px;
        }
        
        .status-value {
            font-size: 2rem;
            font-weight: 700;
            color: var(--secondary);
            margin-bottom: 5px;
        }
        
        .status-label {
            font-size: 0.9rem;
            color: var(--light);
        }
        
        /* Selected Guild Info */
        .selected-info {
            background: rgba(255, 255, 255, 0.08);
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 30px;
            border-left: 5px solid var(--secondary);
        }
        
        /* Channel Selector */
        .channel-selector {
            margin-bottom: 30px;
        }
        
        .channel-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }
        
        .channel-card {
            background: rgba(255, 255, 255, 0.08);
            padding: 20px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s;
            text-align: center;
            border: 2px solid transparent;
        }
        
        .channel-card:hover {
            background: rgba(255, 255, 255, 0.12);
            transform: translateY(-3px);
        }
        
        .channel-card.active {
            background: rgba(67, 181, 129, 0.2);
            border-color: var(--secondary);
        }
        
        .channel-icon {
            font-size: 2rem;
            margin-bottom: 10px;
        }
        
        /* Message Form */
        .message-form {
            background: rgba(255, 255, 255, 0.08);
            padding: 30px;
            border-radius: 20px;
            margin-bottom: 30px;
        }
        
        .form-group {
            margin-bottom: 25px;
        }
        
        .form-label {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 12px;
            color: var(--lighter);
        }
        
        .form-control {
            width: 100%;
            padding: 18px;
            background: rgba(255, 255, 255, 0.1);
            border: 2px solid rgba(255, 255, 255, 0.2);
            border-radius: 12px;
            color: var(--lighter);
            font-size: 1rem;
            transition: all 0.3s;
        }
        
        .form-control:focus {
            outline: none;
            border-color: var(--secondary);
            box-shadow: 0 0 0 3px rgba(67, 181, 129, 0.2);
        }
        
        textarea.form-control {
            min-height: 150px;
            resize: vertical;
            font-family: 'Courier New', monospace;
        }
        
        /* Emoji Picker */
        .emoji-section {
            margin-bottom: 30px;
        }
        
        .emoji-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(45px, 1fr));
            gap: 12px;
            margin-top: 15px;
            max-height: 200px;
            overflow-y: auto;
            padding: 15px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 12px;
        }
        
        .emoji-item {
            font-size: 28px;
            text-align: center;
            padding: 10px;
            cursor: pointer;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.05);
            transition: all 0.2s;
        }
        
        .emoji-item:hover {
            background: rgba(255, 255, 255, 0.15);
            transform: scale(1.15);
        }
        
        /* Preview Section */
        .preview-section {
            background: rgba(0, 0, 0, 0.3);
            padding: 25px;
            border-radius: 15px;
            margin: 30px 0;
            border-left: 5px solid var(--primary);
        }
        
        .preview-title {
            font-size: 1.2rem;
            font-weight: 600;
            margin-bottom: 15px;
            color: var(--secondary);
        }
        
        .preview-content {
            font-size: 1rem;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-word;
            padding: 20px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        /* Button Group */
        .button-group {
            display: flex;
            gap: 20px;
            margin-top: 30px;
            flex-wrap: wrap;
        }
        
        .btn {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            padding: 18px 35px;
            border: none;
            border-radius: 12px;
            font-size: 1.1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            text-decoration: none;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--primary), #5b6eae);
            color: white;
            box-shadow: 0 5px 20px rgba(114, 137, 218, 0.4);
        }
        
        .btn-primary:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(114, 137, 218, 0.6);
        }
        
        .btn-success {
            background: linear-gradient(135deg, var(--secondary), #3a9d6e);
            color: white;
            box-shadow: 0 5px 20px rgba(67, 181, 129, 0.4);
        }
        
        .btn-success:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(67, 181, 129, 0.6);
        }
        
        .btn-secondary {
            background: rgba(255, 255, 255, 0.1);
            color: white;
            border: 2px solid rgba(255, 255, 255, 0.2);
        }
        
        .btn-secondary:hover {
            background: rgba(255, 255, 255, 0.2);
            transform: translateY(-3px);
        }
        
        .btn-danger {
            background: linear-gradient(135deg, var(--danger), #d63c3c);
            color: white;
        }
        
        /* Message Logs */
        .message-logs {
            margin-top: 40px;
        }
        
        .logs-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .logs-container {
            max-height: 400px;
            overflow-y: auto;
            padding: 20px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 15px;
        }
        
        .log-item {
            background: rgba(255, 255, 255, 0.08);
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 15px;
            border-left: 4px solid var(--secondary);
            animation: slideIn 0.3s ease-out;
        }
        
        @keyframes slideIn {
            from { opacity: 0; transform: translateX(-20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        
        .log-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        
        .log-time {
            color: var(--secondary);
            font-size: 0.9rem;
        }
        
        .log-type {
            background: var(--primary);
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
        }
        
        .log-content {
            font-size: 0.95rem;
            line-height: 1.5;
            color: var(--lighter);
        }
        
        /* Footer */
        .footer {
            text-align: center;
            padding: 30px;
            margin-top: 50px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 20px;
            border-top: 2px solid rgba(255, 255, 255, 0.1);
        }
        
        .footer-links {
            display: flex;
            justify-content: center;
            gap: 30px;
            margin: 20px 0;
        }
        
        .footer-link {
            color: var(--secondary);
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 10px;
            transition: color 0.3s;
        }
        
        .footer-link:hover {
            color: var(--primary);
        }
        
        /* Responsive */
        @media (max-width: 1200px) {
            .dashboard {
                grid-template-columns: 1fr;
            }
            
            .sidebar {
                order: 2;
            }
            
            .status-bar {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        
        @media (max-width: 768px) {
            .header {
                padding: 30px 20px;
            }
            
            .title {
                font-size: 2.5rem;
            }
            
            .status-bar {
                grid-template-columns: 1fr;
            }
            
            .button-group {
                flex-direction: column;
            }
            
            .btn {
                width: 100%;
                justify-content: center;
            }
            
            .channel-grid {
                grid-template-columns: 1fr;
            }
        }
        
        /* Loading Animation */
        .loading {
            display: inline-block;
            width: 50px;
            height: 50px;
            border: 5px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: var(--secondary);
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        /* Toast Notifications */
        .toast {
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: var(--secondary);
            color: white;
            padding: 15px 25px;
            border-radius: 10px;
            font-weight: 600;
            z-index: 1000;
            animation: slideInRight 0.3s, fadeOut 0.3s 2.7s;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.3);
        }
        
        @keyframes slideInRight {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        @keyframes fadeOut {
            from { opacity: 1; }
            to { opacity: 0; }
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
        
        .mb-3 {
            margin-bottom: 30px;
        }
        
        .mt-3 {
            margin-top: 30px;
        }
        
        .p-3 {
            padding: 30px;
        }
    </style>
</head>
<body>
    <div class="container">
        {% if not user %}
        <!-- Login Page -->
        <div class="header">
            <h1 class="title">🤖 Discord Message Dashboard</h1>
            <p class="subtitle">Professional Dashboard to Send Messages Across All Your Discord Servers</p>
            <a href="/login" class="login-btn">
                <i class="fab fa-discord"></i> Login with Discord
            </a>
            <div class="mt-3">
                <p style="color: var(--light); font-size: 0.95rem;">
                    <i class="fas fa-shield-alt"></i> Secure OAuth2 Authentication | 
                    <i class="fas fa-server"></i> Multi-Server Support | 
                    <i class="fas fa-bolt"></i> Real-time Messaging
                </p>
            </div>
        </div>
        
        <div class="status-bar">
            <div class="status-card">
                <div class="status-icon">🤖</div>
                <div class="status-value" id="serverCount">0</div>
                <div class="status-label">Servers Online</div>
            </div>
            <div class="status-card">
                <div class="status-icon">📊</div>
                <div class="status-value" id="totalUsers">0</div>
                <div class="status-label">Total Users</div>
            </div>
            <div class="status-card">
                <div class="status-icon">⚡</div>
                <div class="status-value">24/7</div>
                <div class="status-label">Uptime</div>
            </div>
            <div class="status-card">
                <div class="status-icon">🔒</div>
                <div class="status-value">100%</div>
                <div class="status-label">Secure</div>
            </div>
        </div>
        
        <div class="p-3" style="background: rgba(255,255,255,0.05); border-radius: 20px; margin-top: 40px;">
            <h3 style="margin-bottom: 20px; color: var(--secondary);">
                <i class="fas fa-star"></i> Features
            </h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px;">
                <div style="background: rgba(255,255,255,0.08); padding: 20px; border-radius: 12px;">
                    <h4><i class="fas fa-paper-plane"></i> Send Messages</h4>
                    <p style="color: var(--light); margin-top: 10px;">Send messages to any channel in any server where the bot is present.</p>
                </div>
                <div style="background: rgba(255,255,255,0.08); padding: 20px; border-radius: 12px;">
                    <h4><i class="fas fa-edit"></i> Edit Messages</h4>
                    <p style="color: var(--light); margin-top: 10px;">Edit existing messages sent by the bot.</p>
                </div>
                <div style="background: rgba(255,255,255,0.08); padding: 20px; border-radius: 12px;">
                    <h4><i class="fas fa-image"></i> Embed Support</h4>
                    <p style="color: var(--light); margin-top: 10px;">Create beautiful embeds with colors, images, and fields.</p>
                </div>
                <div style="background: rgba(255,255,255,0.08); padding: 20px; border-radius: 12px;">
                    <h4><i class="fas fa-smile"></i> Emoji Picker</h4>
                    <p style="color: var(--light); margin-top: 10px;">Access all emojis from all servers the bot is in.</p>
                </div>
            </div>
        </div>
        
        {% else %}
        <!-- Dashboard -->
        <div class="header">
            <h1 class="title">Welcome, {{ user.username }}!</h1>
            <p class="subtitle">Select a server and channel to start sending messages</p>
            <div style="margin-top: 20px;">
                <a href="/logout" style="color: var(--danger); text-decoration: none;">
                    <i class="fas fa-sign-out-alt"></i> Logout
                </a>
            </div>
        </div>
        
        <div class="dashboard">
            <!-- Sidebar: Servers -->
            <div class="sidebar">
                <div class="sidebar-title">
                    <i class="fas fa-server"></i> Your Servers
                    <span style="margin-left: auto; font-size: 0.9rem; color: var(--secondary);" id="guildCount">0</span>
                </div>
                
                <div class="guild-list" id="guildList">
                    <div class="text-center p-3">
                        <div class="loading"></div>
                        <p style="margin-top: 15px; color: var(--light);">Loading servers...</p>
                    </div>
                </div>
            </div>
            
            <!-- Main Content -->
            <div class="main-content">
                <!-- Status Bar -->
                <div class="status-bar">
                    <div class="status-card">
                        <div class="status-icon"><i class="fas fa-hashtag"></i></div>
                        <div class="status-value" id="channelCount">0</div>
                        <div class="status-label">Channels</div>
                    </div>
                    <div class="status-card">
                        <div class="status-icon"><i class="fas fa-smile"></i></div>
                        <div class="status-value" id="emojiCount">0</div>
                        <div class="status-label">Emojis</div>
                    </div>
                    <div class="status-card">
                        <div class="status-icon"><i class="fas fa-users"></i></div>
                        <div class="status-value" id="memberCount">0</div>
                        <div class="status-label">Members</div>
                    </div>
                    <div class="status-card">
                        <div class="status-icon"><i class="fas fa-bolt"></i></div>
                        <div class="status-value">{{ ping }}ms</div>
                        <div class="status-label">Ping</div>
                    </div>
                </div>
                
                <!-- Selected Guild Info -->
                <div class="selected-info hidden" id="selectedGuildInfo">
                    <h3 id="selectedGuildName"></h3>
                    <div style="display: flex; gap: 20px; margin-top: 10px; color: var(--light);">
                        <span id="selectedGuildStats"></span>
                    </div>
                </div>
                
                <!-- Channel Selector -->
                <div class="channel-selector hidden" id="channelSection">
                    <h3 class="form-label">
                        <i class="fas fa-hashtag"></i> Select Channel
                    </h3>
                    <div class="channel-grid" id="channelList">
                        <!-- Channels will be loaded here -->
                    </div>
                </div>
                
                <!-- Message Form -->
                <div class="message-form hidden" id="messageSection">
                    <h3 class="form-label">
                        <i class="fas fa-edit"></i> Compose Message
                    </h3>
                    
                    <div class="form-group">
                        <label class="form-label">
                            <i class="fas fa-heading"></i> Message Title (Optional)
                        </label>
                        <input type="text" class="form-control" id="messageTitle" 
                               placeholder="Enter a title for your message...">
                    </div>
                    
                    <div class="form-group">
                        <label class="form-label">
                            <i class="fas fa-comment"></i> Message Content
                        </label>
                        <textarea class="form-control" id="messageContent" rows="6"
                                  placeholder="Type your message here... You can use markdown formatting!"></textarea>
                    </div>
                    
                    <!-- Emoji Picker -->
                    <div class="emoji-section">
                        <label class="form-label">
                            <i class="fas fa-smile"></i> Emoji Picker
                        </label>
                        <div class="emoji-grid" id="emojiPicker">
                            <!-- Emojis will be loaded here -->
                        </div>
                    </div>
                    
                    <!-- Preview -->
                    <div class="preview-section">
                        <div class="preview-title">
                            <i class="fas fa-eye"></i> Live Preview
                        </div>
                        <div class="preview-content" id="messagePreview">
                            Preview will appear here...
                        </div>
                    </div>
                    
                    <!-- Action Buttons -->
                    <div class="button-group">
                        <button class="btn btn-primary" onclick="sendMessage(false)">
                            <i class="fas fa-paper-plane"></i> Send Message
                        </button>
                        <button class="btn btn-success" onclick="sendMessage(true)">
                            <i class="fas fa-paint-brush"></i> Send as Embed
                        </button>
                        <button class="btn btn-secondary" onclick="clearForm()">
                            <i class="fas fa-trash"></i> Clear Form
                        </button>
                        <button class="btn btn-secondary" onclick="updatePreview()">
                            <i class="fas fa-sync"></i> Update Preview
                        </button>
                    </div>
                </div>
                
                <!-- Message Logs -->
                <div class="message-logs">
                    <div class="logs-header">
                        <h3 class="form-label">
                            <i class="fas fa-history"></i> Recent Messages
                        </h3>
                        <button class="btn btn-secondary" onclick="refreshLogs()">
                            <i class="fas fa-sync"></i> Refresh
                        </button>
                    </div>
                    <div class="logs-container" id="messageLogs">
                        <!-- Logs will be loaded here -->
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Footer -->
        <div class="footer">
            <div class="footer-links">
                <a href="/api/stats" class="footer-link" target="_blank">
                    <i class="fas fa-chart-bar"></i> Statistics
                </a>
                <a href="/health" class="footer-link" target="_blank">
                    <i class="fas fa-heartbeat"></i> Health
                </a>
                <a href="https://discord.com" class="footer-link" target="_blank">
                    <i class="fab fa-discord"></i> Discord
                </a>
            </div>
            <p style="color: var(--light); margin-top: 20px;">
                🤖 Discord Message Dashboard v2.0 | Powered by Discord.py & Flask
            </p>
            <p class="update-time" style="color: var(--light); font-size: 0.9rem; margin-top: 10px;">
                Last Updated: <span id="currentTime">{{ current_time }}</span>
            </p>
        </div>
        {% endif %}
    </div>
    
    <script>
        // Global variables
        let selectedGuild = null;
        let selectedChannel = null;
        let currentEmojis = [];
        
        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            {% if user %}
            loadGuilds();
            loadMessageLogs();
            updateCurrentTime();
            
            // Auto-update time every minute
            setInterval(updateCurrentTime, 60000);
            
            // Auto-refresh data every 30 seconds
            setInterval(refreshData, 30000);
            {% else %}
            // Update stats for login page
            updatePublicStats();
            {% endif %}
        });
        
        // Update current time
        function updateCurrentTime() {
            const now = new Date();
            document.getElementById('currentTime').textContent = 
                now.toLocaleString('en-US', { 
                    year: 'numeric', 
                    month: 'short', 
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit'
                }) + ' UTC';
        }
        
        // Update public stats
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
        
        // Load guilds
        async function loadGuilds() {
            try {
                const response = await fetch('/api/guilds');
                const data = await response.json();
                
                const guildList = document.getElementById('guildList');
                const guildCount = document.getElementById('guildCount');
                
                if (data.guilds && data.guilds.length > 0) {
                    guildList.innerHTML = '';
                    guildCount.textContent = data.guilds.length;
                    
                    data.guilds.forEach(guild => {
                        const guildItem = document.createElement('div');
                        guildItem.className = 'guild-item';
                        guildItem.innerHTML = `
                            <div class="guild-icon">
                                ${guild.icon ? `<img src="${guild.icon}" alt="${guild.name}">` : '🤖'}
                            </div>
                            <div class="guild-info">
                                <div class="guild-name">${guild.name}</div>
                                <div class="guild-stats">
                                    <span><i class="fas fa-hashtag"></i> ${guild.channel_count}</span>
                                    <span><i class="fas fa-smile"></i> ${guild.emoji_count}</span>
                                    <span><i class="fas fa-users"></i> ${guild.member_count}</span>
                                </div>
                            </div>
                        `;
                        
                        guildItem.onclick = () => selectGuild(guild);
                        guildList.appendChild(guildItem);
                    });
                } else {
                    guildList.innerHTML = `
                        <div class="text-center p-3">
                            <i class="fas fa-exclamation-triangle" style="font-size: 3rem; color: var(--danger);"></i>
                            <p style="margin-top: 15px; color: var(--light);">
                                No mutual servers found. Make sure the bot is in your servers.
                            </p>
                        </div>
                    `;
                }
                
            } catch (error) {
                console.error('Error loading guilds:', error);
                showToast('Error loading servers', 'danger');
            }
        }
        
        // Select guild
        async function selectGuild(guild) {
            selectedGuild = guild;
            selectedChannel = null;
            
            // Update UI
            document.querySelectorAll('.guild-item').forEach(item => {
                item.classList.remove('active');
            });
            event.currentTarget.classList.add('active');
            
            // Show selected guild info
            const infoDiv = document.getElementById('selectedGuildInfo');
            const nameSpan = document.getElementById('selectedGuildName');
            const statsSpan = document.getElementById('selectedGuildStats');
            
            nameSpan.innerHTML = `<i class="fas fa-server"></i> ${guild.name}`;
            statsSpan.innerHTML = `
                <span><i class="fas fa-hashtag"></i> ${guild.channel_count} channels</span>
                <span><i class="fas fa-smile"></i> ${guild.emoji_count} emojis</span>
                <span><i class="fas fa-users"></i> ${guild.member_count} members</span>
            `;
            infoDiv.classList.remove('hidden');
            
            // Load channels
            await loadChannels(guild.id);
            
            // Load emojis
            await loadEmojis(guild.id);
            
            // Update stats
            document.getElementById('channelCount').textContent = guild.channel_count;
            document.getElementById('emojiCount').textContent = guild.emoji_count;
            document.getElementById('memberCount').textContent = guild.member_count;
        }
        
        // Load channels
        async function loadChannels(guildId) {
            try {
                const response = await fetch(`/api/channels?guild_id=${guildId}`);
                const data = await response.json();
                
                const channelSection = document.getElementById('channelSection');
                const channelList = document.getElementById('channelList');
                
                if (data.channels && data.channels.length > 0) {
                    channelList.innerHTML = '';
                    
                    data.channels.forEach(channel => {
                        const channelCard = document.createElement('div');
                        channelCard.className = 'channel-card';
                        channelCard.innerHTML = `
                            <div class="channel-icon">
                                <i class="fas fa-hashtag"></i>
                            </div>
                            <div>${channel.name}</div>
                        `;
                        
                        channelCard.onclick = () => selectChannel(channel);
                        channelList.appendChild(channelCard);
                    });
                    
                    channelSection.classList.remove('hidden');
                }
                
            } catch (error) {
                console.error('Error loading channels:', error);
            }
        }
        
        // Load emojis
        async function loadEmojis(guildId) {
            try {
                const response = await fetch(`/api/emojis?guild_id=${guildId}`);
                const data = await response.json();
                
                currentEmojis = data.emojis || [];
                updateEmojiPicker();
                
            } catch (error) {
                console.error('Error loading emojis:', error);
            }
        }
        
        // Update emoji picker
        function updateEmojiPicker() {
            const emojiPicker = document.getElementById('emojiPicker');
            emojiPicker.innerHTML = '';
            
            // Add default emojis
            const defaultEmojis = ['😀', '😂', '❤️', '🔥', '👍', '🎉', '✨', '🌟', '🚀', '💯', '😎', '🤔', '😍', '🥳', '🙏'];
            
            defaultEmojis.forEach(emoji => {
                addEmojiToPicker(emoji, emoji);
            });
            
            // Add custom emojis
            currentEmojis.forEach(emoji => {
                addEmojiToPicker(emoji, emoji);
            });
        }
        
        function addEmojiToPicker(emoji, display) {
            const emojiPicker = document.getElementById('emojiPicker');
            const emojiItem = document.createElement('div');
            emojiItem.className = 'emoji-item';
            emojiItem.innerHTML = display;
            emojiItem.title = emoji;
            emojiItem.onclick = () => insertEmoji(emoji);
            emojiPicker.appendChild(emojiItem);
        }
        
        // Select channel
        function selectChannel(channel) {
            selectedChannel = channel;
            
            // Update UI
            document.querySelectorAll('.channel-card').forEach(card => {
                card.classList.remove('active');
            });
            event.currentTarget.classList.add('active');
            
            // Show message form
            document.getElementById('messageSection').classList.remove('hidden');
            
            updatePreview();
        }
        
        // Insert emoji
        function insertEmoji(emoji) {
            const textarea = document.getElementById('messageContent');
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const text = textarea.value;
            
            textarea.value = text.substring(0, start) + emoji + text.substring(end);
            textarea.focus();
            textarea.selectionStart = textarea.selectionEnd = start + emoji.length;
            
            updatePreview();
        }
        
        // Update preview
        function updatePreview() {
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            const preview = document.getElementById('messagePreview');
            
            let previewHTML = '';
            
            if (title) {
                previewHTML += `<strong style="color: var(--secondary);">${title}</strong><br><br>`;
            }
            
            previewHTML += content || '<em style="color: var(--light);">No content entered yet...</em>';
            
            preview.innerHTML = previewHTML;
        }
        
        // Send message
        async function sendMessage(isEmbed) {
            if (!selectedGuild || !selectedChannel) {
                showToast('Please select a server and channel first!', 'danger');
                return;
            }
            
            const title = document.getElementById('messageTitle').value;
            const content = document.getElementById('messageContent').value;
            
            if (!content.trim()) {
                showToast('Please enter a message!', 'danger');
                return;
            }
            
            try {
                const response = await fetch('/api/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        guild_id: selectedGuild.id,
                        channel_id: selectedChannel.id,
                        title: title,
                        content: content,
                        embed: isEmbed
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showToast(isEmbed ? '✅ Embed sent successfully!' : '✅ Message sent successfully!', 'success');
                    addToMessageLog(title, content, selectedGuild.name, isEmbed);
                    clearForm();
                } else {
                    showToast('❌ Error: ' + (result.error || 'Failed to send message'), 'danger');
                }
                
            } catch (error) {
                showToast('❌ Network error: ' + error.message, 'danger');
            }
        }
        
        // Add to message log
        function addToMessageLog(title, content, guildName, isEmbed) {
            const logs = document.getElementById('messageLogs');
            const now = new Date();
            const timeStr = now.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            
            const logItem = document.createElement('div');
            logItem.className = 'log-item';
            logItem.innerHTML = `
                <div class="log-header">
                    <div class="log-time">${timeStr} • ${guildName}</div>
                    <div class="log-type">${isEmbed ? '🎨 Embed' : '📨 Message'}</div>
                </div>
                <div class="log-content">
                    ${title ? `<strong>${title}</strong><br>` : ''}
                    ${content.substring(0, 100)}${content.length > 100 ? '...' : ''}
                </div>
            `;
            
            logs.insertBefore(logItem, logs.firstChild);
            
            // Keep only last 20 logs
            const items = logs.getElementsByClassName('log-item');
            if (items.length > 20) {
                logs.removeChild(items[items.length - 1]);
            }
        }
        
        // Load message logs
        async function loadMessageLogs() {
            try {
                const response = await fetch('/api/logs');
                const data = await response.json();
                
                const logs = document.getElementById('messageLogs');
                logs.innerHTML = '';
                
                if (data.messages && data.messages.length > 0) {
                    data.messages.forEach(msg => {
                        const time = new Date(msg.timestamp * 1000);
                        const timeStr = time.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                        
                        const logItem = document.createElement('div');
                        logItem.className = 'log-item';
                        logItem.innerHTML = `
                            <div class="log-header">
                                <div class="log-time">${timeStr} • ${msg.guild}</div>
                                <div class="log-type">${msg.type}</div>
                            </div>
                            <div class="log-content">
                                ${msg.title ? `<strong>${msg.title}</strong><br>` : ''}
                                ${msg.content}
                            </div>
                        `;
                        logs.appendChild(logItem);
                    });
                } else {
                    logs.innerHTML = `
                        <div class="text-center p-3">
                            <i class="fas fa-comment-slash" style="font-size: 3rem; color: var(--light);"></i>
                            <p style="margin-top: 15px; color: var(--light);">
                                No messages sent yet. Send your first message!
                            </p>
                        </div>
                    `;
                }
                
            } catch (error) {
                console.error('Error loading logs:', error);
            }
        }
        
        // Refresh logs
        function refreshLogs() {
            loadMessageLogs();
            showToast('Logs refreshed!', 'success');
        }
        
        // Refresh all data
        function refreshData() {
            if (selectedGuild) {
                selectGuild(selectedGuild);
            } else {
                loadGuilds();
            }
            loadMessageLogs();
        }
        
        // Clear form
        function clearForm() {
            document.getElementById('messageTitle').value = '';
            document.getElementById('messageContent').value = '';
            updatePreview();
            showToast('Form cleared!', 'success');
        }
        
        // Show toast notification
        function showToast(message, type = 'success') {
            // Remove existing toast
            const existingToast = document.querySelector('.toast');
            if (existingToast) existingToast.remove();
            
            // Create new toast
            const toast = document.createElement('div');
            toast.className = 'toast';
            
            const bgColor = type === 'success' ? 'var(--secondary)' : 
                           type === 'danger' ? 'var(--danger)' : 'var(--primary)';
            
            toast.innerHTML = `
                <style>
                    .toast {
                        background: ${bgColor};
                    }
                </style>
                ${message}
            `;
            
            document.body.appendChild(toast);
            
            // Auto-remove after 3 seconds
            setTimeout(() => {
                if (toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
            }, 3000);
        }
        
        // Auto-update preview
        document.getElementById('messageTitle').addEventListener('input', updatePreview);
        document.getElementById('messageContent').addEventListener('input', updatePreview);
        
        // Keyboard shortcuts
        document.addEventListener('keydown', function(e) {
            // Ctrl+Enter to send message
            if (e.ctrlKey && e.key === 'Enter') {
                sendMessage(false);
            }
            // Ctrl+Shift+Enter to send embed
            if (e.ctrlKey && e.shiftKey && e.key === 'Enter') {
                sendMessage(true);
            }
            // Escape to clear form
            if (e.key === 'Escape') {
                clearForm();
            }
        });
    </script>
</body>
</html>
'''

# ========== FLASK ROUTES ==========
@app.route('/')
def index():
    user = session.get('user')
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    
    # Calculate bot ping (placeholder)
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
    
    try:
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
        
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route('/logout')
def logout():
    user_id = session.get('user', {}).get('id')
    if user_id:
        user_sessions.pop(str(user_id), None)
    session.clear()
    return redirect('/')

@app.route('/api/guilds')
def api_guilds():
    """Get mutual guilds (where both user and bot are present)"""
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
    """Get channels for a specific guild"""
    guild_id = request.args.get('guild_id')
    
    if not guild_id:
        return jsonify({'channels': []})
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'channels': []})
    
    # Get text channels where bot can send messages
    channels = []
    for channel in guild.channels:
        if isinstance(channel, discord.TextChannel) and channel.permissions_for(guild.me).send_messages:
            channels.append({
                'id': str(channel.id),
                'name': channel.name,
                'topic': channel.topic or '',
                'position': channel.position
            })
    
    # Sort by position
    channels.sort(key=lambda x: x['position'])
    
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
    
    emojis = [str(emoji) for emoji in guild.emojis]
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
                title=title if title else None,
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
        
        # Log the message
        message_history.append({
            'timestamp': time.time(),
            'guild': guild.name,
            'channel': channel.name,
            'title': title,
            'content': content[:80] + '...' if len(content) > 80 else content,
            'type': '🎨 Embed' if embed else '📨 Message'
        })
        
        # Keep only last 50 messages
        if len(message_history) > 50:
            message_history.pop(0)
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/logs')
def api_logs():
    """Get recent messages"""
    # Return last 20 messages
    return jsonify({'messages': message_history[-20:]})

@app.route('/api/public_stats')
def api_public_stats():
    """Public statistics"""
    total_members = sum(g.member_count for g in bot.guilds)
    
    return jsonify({
        'servers': len(bot.guilds),
        'total_users': total_members,
        'status': 'online'
    })

@app.route('/api/stats')
def api_stats():
    """Detailed statistics"""
    total_members = sum(g.member_count for g in bot.guilds)
    total_channels = sum(len([c for c in g.channels if isinstance(c, discord.TextChannel)]) for g in bot.guilds)
    total_emojis = sum(len(g.emojis) for g in bot.guilds)
    
    return jsonify({
        'servers': len(bot.guilds),
        'total_members': total_members,
        'total_channels': total_channels,
        'total_emojis': total_emojis,
        'uptime': time.time() - bot_start_time,
        'ping': round(bot.latency * 1000) if hasattr(bot, 'latency') else 0
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
            name="Message Dashboard"
        )
    )
    
    # Store bot guilds
    for guild in bot.guilds:
        bot_guilds.append(str(guild.id))
        
        # Store emojis
        emoji_list = [str(emoji) for emoji in guild.emojis]
        available_emojis[str(guild.id)] = emoji_list

bot_start_time = time.time()

# ========== RUN APPLICATION ==========
def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def main():
    print("🚀 Starting Discord Message Dashboard...")
    print(f"🔧 Client ID: {DISCORD_CLIENT_ID}")
    print(f"🔧 Redirect URI: {DISCORD_REDIRECT_URI}")
    print("📋 Features: OAuth2 Login | Multi-Server Support | Emoji Picker | Message Logs")
    
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flask server started on port 8080")
    
    # Run Discord bot
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
