from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import requests
import os
from datetime import datetime
import hashlib

app = Flask(__name__)
CORS(app)

# MongoDB connection
MONGO_URI = "mongodb+srv://dsadeepa02_db_user:zero8907@cluster0.nfiluqd.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['telegram_bots']
bots_collection = db['bots']
messages_collection = db['messages']

# Store for active bot scripts
bot_scripts = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'status': 'active',
        'service': 'Telegram Bot Hosting API',
        'endpoints': {
            'POST /api/bot/create': 'Create a new bot',
            'POST /api/bot/update': 'Update bot script',
            'GET /api/bot/<bot_id>': 'Get bot details',
            'POST /api/webhook/<bot_token>': 'Telegram webhook endpoint',
            'GET /api/bots': 'List all bots',
            'DELETE /api/bot/<bot_id>': 'Delete a bot'
        }
    })

@app.route('/api/bot/create', methods=['POST'])
def create_bot():
    try:
        data = request.json
        bot_token = data.get('bot_token')
        bot_script = data.get('script')
        bot_name = data.get('name', 'Unnamed Bot')
        
        if not bot_token or not bot_script:
            return jsonify({'error': 'bot_token and script are required'}), 400
        
        # Verify bot token with Telegram
        verify_url = f"https://api.telegram.org/bot{bot_token}/getMe"
        response = requests.get(verify_url)
        
        if not response.json().get('ok'):
            return jsonify({'error': 'Invalid bot token'}), 400
        
        bot_info = response.json()['result']
        bot_id = hashlib.md5(bot_token.encode()).hexdigest()[:12]
        
        # Store bot in database
        bot_data = {
            'bot_id': bot_id,
            'bot_token': bot_token,
            'bot_username': bot_info.get('username'),
            'bot_name': bot_name,
            'script': bot_script,
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
            'active': True
        }
        
        bots_collection.update_one(
            {'bot_id': bot_id},
            {'$set': bot_data},
            upsert=True
        )
        
        # Load script into memory
        bot_scripts[bot_id] = bot_script
        
        # Set webhook
        webhook_url = f"{request.host_url}api/webhook/{bot_token}"
        set_webhook_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
        webhook_response = requests.post(set_webhook_url, json={'url': webhook_url})
        
        return jsonify({
            'success': True,
            'bot_id': bot_id,
            'bot_username': bot_info.get('username'),
            'webhook_url': webhook_url,
            'webhook_set': webhook_response.json().get('ok', False)
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bot/update', methods=['POST'])
def update_bot():
    try:
        data = request.json
        bot_id = data.get('bot_id')
        bot_script = data.get('script')
        
        if not bot_id or not bot_script:
            return jsonify({'error': 'bot_id and script are required'}), 400
        
        # Update in database
        result = bots_collection.update_one(
            {'bot_id': bot_id},
            {'$set': {
                'script': bot_script,
                'updated_at': datetime.utcnow()
            }}
        )
        
        if result.matched_count == 0:
            return jsonify({'error': 'Bot not found'}), 404
        
        # Update in memory
        bot_scripts[bot_id] = bot_script
        
        return jsonify({'success': True, 'message': 'Bot script updated'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bot/<bot_id>', methods=['GET'])
def get_bot(bot_id):
    try:
        bot = bots_collection.find_one({'bot_id': bot_id}, {'_id': 0, 'bot_token': 0})
        
        if not bot:
            return jsonify({'error': 'Bot not found'}), 404
        
        return jsonify(bot)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bots', methods=['GET'])
def list_bots():
    try:
        bots = list(bots_collection.find({}, {'_id': 0, 'bot_token': 0, 'script': 0}))
        return jsonify({'bots': bots, 'count': len(bots)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bot/<bot_id>', methods=['DELETE'])
def delete_bot(bot_id):
    try:
        bot = bots_collection.find_one({'bot_id': bot_id})
        
        if not bot:
            return jsonify({'error': 'Bot not found'}), 404
        
        # Remove webhook
        bot_token = bot['bot_token']
        delete_webhook_url = f"https://api.telegram.org/bot{bot_token}/deleteWebhook"
        requests.post(delete_webhook_url)
        
        # Delete from database
        bots_collection.delete_one({'bot_id': bot_id})
        
        # Remove from memory
        if bot_id in bot_scripts:
            del bot_scripts[bot_id]
        
        return jsonify({'success': True, 'message': 'Bot deleted'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/webhook/<bot_token>', methods=['POST'])
def webhook(bot_token):
    try:
        update = request.json
        
        # Find bot by token
        bot = bots_collection.find_one({'bot_token': bot_token})
        
        if not bot:
            return jsonify({'error': 'Bot not found'}), 404
        
        bot_id = bot['bot_id']
        script = bot_scripts.get(bot_id, bot['script'])
        
        # Store message
        messages_collection.insert_one({
            'bot_id': bot_id,
            'update': update,
            'timestamp': datetime.utcnow()
        })
        
        # Process message with user's script
        if 'message' in update:
            execute_bot_script(script, update['message'], bot_token)
        
        return jsonify({'ok': True})
        
    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return jsonify({'error': str(e)}), 500

class ReturnCommand(Exception):
    """Exception to stop script execution"""
    pass

class MessageObject:
    """Mock message object for bot scripts"""
    def __init__(self, message_data):
        self.text = message_data.get('text', '')
        self.chat = type('Chat', (), {
            'id': message_data['chat']['id'],
            'type': message_data['chat'].get('type', 'private')
        })()
        self.from_user = message_data.get('from', {})
        self.message_id = message_data.get('message_id')
        self.date = message_data.get('date')
        self._raw_data = message_data

class BotAPI:
    """Mock bot API for sending messages"""
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
    def sendMessage(self, chat_id, text, parse_mode=None):
        """Send a message to a chat"""
        url = f"{self.base_url}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': text
        }
        if parse_mode:
            data['parse_mode'] = parse_mode
        
        try:
            response = requests.post(url, json=data)
            return response.json()
        except Exception as e:
            print(f"Error sending message: {str(e)}")
            return None

class HTTPClient:
    """Mock HTTP client for making requests"""
    @staticmethod
    def get(url, **kwargs):
        """Make GET request"""
        return requests.get(url, **kwargs)
    
    @staticmethod
    def post(url, **kwargs):
        """Make POST request"""
        return requests.post(url, **kwargs)

def execute_bot_script(script, message_data, bot_token):
    """
    Execute user-defined bot script
    Script should define on_message(message) function
    """
    try:
        # Create message object
        message = MessageObject(message_data)
        
        # Create bot API instance
        bot = BotAPI(bot_token)
        
        # Create HTTP client
        HTTP = HTTPClient()
        
        # Create safe execution environment
        safe_globals = {
            '__builtins__': {
                'len': len,
                'str': str,
                'int': int,
                'float': float,
                'bool': bool,
                'list': list,
                'dict': dict,
                'tuple': tuple,
                'range': range,
                'enumerate': enumerate,
                'zip': zip,
                'print': print,
                'Exception': Exception,
            },
            'bot': bot,
            'HTTP': HTTP,
            'message': message,
            'ReturnCommand': ReturnCommand,
        }
        
        # Execute the script
        exec(script, safe_globals)
        
        # Call on_message if it exists
        if 'on_message' in safe_globals:
            safe_globals['on_message'](message)
        else:
            # Fallback: try to find and call handle_message
            if 'handle_message' in safe_globals:
                safe_globals['handle_message'](message.text, message)
            else:
                bot.sendMessage(chat_id=message.chat.id, text=f"Echo: {message.text}")
            
    except ReturnCommand:
        # Script requested to stop execution
        pass
    except Exception as e:
        print(f"Script execution error: {str(e)}")
        try:
            bot = BotAPI(bot_token)
            bot.sendMessage(
                chat_id=message_data['chat']['id'],
                text=f"❌ Bot script error: {str(e)}"
            )
        except:
            pass

# For Vercel serverless deployment
if __name__ == '__main__':
    app.run(debug=True)
