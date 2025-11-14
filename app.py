from flask import Flask, request, jsonify, send_file
from flask_pymongo import PyMongo
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta
from bson import ObjectId
import os
import jwt
from functools import wraps
from dotenv import load_dotenv
import traceback

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'fallback-secret-key-12345')
app.config['MONGO_URI'] = os.getenv('MONGO_URI')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

try:
    mongo = PyMongo(app)
    mongo.db.command('ping')
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Failed: {str(e)}")
    mongo = None

CORS(app, resources={r"/api/*": {"origins": "*"}})

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

JWT_EXPIRATION_DAYS = 30
JWT_ALGORITHM = 'HS256'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

print("\n" + "="*70)
print("🍽️  TasteTrail Backend Server")
print("="*70)
print(f"✅ Server: http://localhost:{os.getenv('PORT', 5000)}")
print("="*70 + "\n")

def serialize_doc(doc):
    if doc is None:
        return None
    doc['_id'] = str(doc['_id'])
    if 'user_id' in doc:
        doc['user_id'] = str(doc['user_id'])
    if 'created_at' in doc and isinstance(doc['created_at'], datetime):
        doc['created_at'] = doc['created_at'].isoformat()
    return doc

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': 'Token missing'}), 401
        try:
            if token.startswith('Bearer '):
                token = token.split(' ')[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=[JWT_ALGORITHM])
            current_user = mongo.db.users.find_one({'_id': ObjectId(data['user_id'])})
            if not current_user:
                return jsonify({'message': 'User not found'}), 401
        except:
            return jsonify({'message': 'Invalid token'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

@app.route('/')
def index():
    try:
        if os.path.exists('index.html'):
            return send_file('index.html')
        return jsonify({'message': 'index.html not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'database': 'connected' if mongo else 'disconnected'}), 200

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        if not mongo:
            return jsonify({'message': 'Database not connected'}), 500
        
        data = request.get_json()
        
        if mongo.db.users.find_one({'email': data['email'].lower()}):
            return jsonify({'message': 'Email already registered'}), 409
        
        user_doc = {
            'fullName': data['fullName'],
            'email': data['email'].lower(),
            'password': generate_password_hash(data['password']),
            'dietaryPreferences': data.get('dietaryPreferences', []),
            'savedRecipes': [],
            'triedRecipes': [],
            'recipesTried': 0,
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }
        
        result = mongo.db.users.insert_one(user_doc)
        token = jwt.encode({
            'user_id': str(result.inserted_id),
            'exp': datetime.utcnow() + timedelta(days=JWT_EXPIRATION_DAYS)
        }, app.config['SECRET_KEY'], algorithm=JWT_ALGORITHM)
        
        print(f"✅ User registered: {data['email']}")
        
        return jsonify({
            'message': 'Registration successful',
            'token': token,
            'user': {
                'id': str(result.inserted_id),
                'fullName': data['fullName'],
                'email': data['email']
            }
        }), 201
        
    except Exception as e:
        print(f"❌ Registration error: {str(e)}")
        return jsonify({'message': 'Registration failed', 'error': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        if not mongo:
            return jsonify({'message': 'Database not connected'}), 500
        
        data = request.get_json()
        user = mongo.db.users.find_one({'email': data['email'].lower()})
        
        if not user or not check_password_hash(user['password'], data['password']):
            return jsonify({'message': 'Invalid credentials'}), 401
        
        token = jwt.encode({
            'user_id': str(user['_id']),
            'exp': datetime.utcnow() + timedelta(days=JWT_EXPIRATION_DAYS)
        }, app.config['SECRET_KEY'], algorithm=JWT_ALGORITHM)
        
        print(f"✅ User logged in: {data['email']}")
        
        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user': {
                'id': str(user['_id']),
                'fullName': user['fullName'],
                'email': user['email']
            }
        }), 200
        
    except Exception as e:
        print(f"❌ Login error: {str(e)}")
        return jsonify({'message': 'Login failed', 'error': str(e)}), 500

@app.route('/api/dashboard/stats', methods=['GET'])
@token_required
def get_dashboard_stats(current_user):
    try:
        stats = {
            'savedRecipes': len(current_user.get('savedRecipes', [])),
            'mealsPlanned': mongo.db.meal_plans.count_documents({'user_id': current_user['_id']}),
            'recipesTried': current_user.get('recipesTried', 0),
            'collections': mongo.db.collections.count_documents({'user_id': current_user['_id']})
        }
        
        print(f"📊 Stats for {current_user['email']}: {stats}")
        
        return jsonify({'stats': stats}), 200
        
    except Exception as e:
        print(f"❌ Stats error: {str(e)}")
        return jsonify({
            'stats': {'savedRecipes': 0, 'mealsPlanned': 0, 'recipesTried': 0, 'collections': 0}
        }), 200

@app.route('/api/recipes', methods=['GET'])
@token_required
def get_recipes(current_user):
    try:
        recipes = list(mongo.db.recipes.find().limit(50))
        print(f"📚 Found {len(recipes)} recipes")
        return jsonify({'recipes': [serialize_doc(r) for r in recipes]}), 200
    except Exception as e:
        print(f"❌ Get recipes error: {str(e)}")
        return jsonify({'recipes': []}), 200

@app.route('/api/recipes/<recipe_id>/save', methods=['POST'])
@token_required
def save_recipe(current_user, recipe_id):
    try:
        mongo.db.users.update_one(
            {'_id': current_user['_id']},
            {'$addToSet': {'savedRecipes': ObjectId(recipe_id)}}
        )
        print(f"💾 Recipe saved: {recipe_id}")
        return jsonify({'message': 'Recipe saved'}), 200
    except Exception as e:
        print(f"❌ Save error: {str(e)}")
        return jsonify({'message': 'Failed to save', 'error': str(e)}), 500

@app.route('/api/recipes/<recipe_id>/mark-tried', methods=['POST'])
@token_required
def mark_recipe_tried(current_user, recipe_id):
    try:
        mongo.db.users.update_one(
            {'_id': current_user['_id']},
            {
                '$addToSet': {'triedRecipes': ObjectId(recipe_id)},
                '$inc': {'recipesTried': 1}
            }
        )
        print(f"✅ Recipe marked tried: {recipe_id}")
        return jsonify({'message': 'Recipe marked as tried'}), 200
    except Exception as e:
        return jsonify({'message': 'Failed', 'error': str(e)}), 500

@app.route('/api/seed', methods=['POST'])
def seed_data():
    try:
        if not mongo:
            return jsonify({'message': 'Database not connected'}), 500
        
        if mongo.db.recipes.count_documents({}) > 0:
            return jsonify({'message': 'Database already has recipes'}), 400
        
        sample_recipes = [
            {
                'name': 'Avocado Toast', 'cuisine': 'Breakfast', 'prepTime': 5, 'cookTime': 5,
                'servings': 2, 'calories': 250, 'difficulty': 'Easy', 'diet': ['vegetarian'],
                'rating': 4.8, 'reviewCount': 0,
                'ingredients': ['2 slices bread', '1 avocado', 'salt', 'pepper'],
                'instructions': ['Toast bread', 'Mash avocado', 'Spread and season'],
                'icon': '🥑', 'created_at': datetime.utcnow()
            },
            {
                'name': 'Greek Salad', 'cuisine': 'Mediterranean', 'prepTime': 10, 'cookTime': 0,
                'servings': 4, 'calories': 180, 'difficulty': 'Easy', 'diet': ['vegetarian'],
                'rating': 4.7, 'reviewCount': 0,
                'ingredients': ['tomatoes', 'cucumber', 'feta', 'olives'],
                'instructions': ['Chop vegetables', 'Add feta', 'Drizzle olive oil'],
                'icon': '🥗', 'created_at': datetime.utcnow()
            },
            {
                'name': 'Chicken Stir Fry', 'cuisine': 'Asian', 'prepTime': 15, 'cookTime': 20,
                'servings': 4, 'calories': 420, 'difficulty': 'Medium', 'diet': [],
                'rating': 4.9, 'reviewCount': 0,
                'ingredients': ['chicken', 'vegetables', 'soy sauce', 'garlic'],
                'instructions': ['Cut chicken', 'Stir fry', 'Add sauce'],
                'icon': '🍜', 'created_at': datetime.utcnow()
            }
        ]
        
        mongo.db.recipes.insert_many(sample_recipes)
        print(f"✅ Seeded {len(sample_recipes)} recipes")
        
        return jsonify({'message': f'{len(sample_recipes)} recipes seeded'}), 201
        
    except Exception as e:
        print(f"❌ Seed error: {str(e)}")
        return jsonify({'message': 'Seeding failed', 'error': str(e)}), 500
if __name__ == '__main__':
    # Get port from environment variable (Render provides PORT=10000)
    port = int(os.environ.get('PORT', 5000))
    
    # Don't print hardcoded port in production
    if os.environ.get('RENDER'):
        print(f"🌐 Server starting on port {port}")
    else:
        print(f"\n🌐 Visit: http://localhost:{port}\n")
    
    # Run app
    app.run(host='0.0.0.0', port=port, debug=False)
