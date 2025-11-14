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

def serialize_doc(doc):
    if doc is None:
        return None
    doc['_id'] = str(doc['_id'])
    if 'user_id' in doc:
        doc['user_id'] = str(doc['user_id'])
    if 'created_at' in doc and isinstance(doc['created_at'], datetime):
        doc['created_at'] = doc['created_at'].isoformat()
    if 'updated_at' in doc and isinstance(doc['updated_at'], datetime):
        doc['updated_at'] = doc['updated_at'].isoformat()
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
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Invalid token'}), 401
        except Exception as e:
            print(f"Token error: {str(e)}")
            return jsonify({'message': 'Token validation failed'}), 401
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
    return jsonify({
        'status': 'healthy',
        'database': 'connected' if mongo else 'disconnected',
        'timestamp': datetime.utcnow().isoformat()
    }), 200

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        if not mongo:
            return jsonify({'message': 'Database not connected'}), 500
        
        data = request.get_json()
        
        if not data or not data.get('fullName') or not data.get('email') or not data.get('password'):
            return jsonify({'message': 'Missing required fields'}), 400
        
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
        
        if not data or not data.get('email') or not data.get('password'):
            return jsonify({'message': 'Email and password required'}), 400
        
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
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        skip = (page - 1) * limit
        
        recipes = list(mongo.db.recipes.find().skip(skip).limit(limit).sort('rating', -1))
        total = mongo.db.recipes.count_documents({})
        
        print(f"📚 Found {len(recipes)} recipes (total: {total})")
        
        return jsonify({
            'recipes': [serialize_doc(r) for r in recipes],
            'total': total,
            'page': page
        }), 200
        
    except Exception as e:
        print(f"❌ Get recipes error: {str(e)}")
        return jsonify({'recipes': [], 'total': 0}), 200

@app.route('/api/recipes/<recipe_id>', methods=['GET'])
@token_required
def get_recipe(current_user, recipe_id):
    try:
        recipe = mongo.db.recipes.find_one({'_id': ObjectId(recipe_id)})
        if not recipe:
            return jsonify({'message': 'Recipe not found'}), 404
        return jsonify({'recipe': serialize_doc(recipe)}), 200
    except Exception as e:
        return jsonify({'message': 'Failed to fetch recipe', 'error': str(e)}), 500

@app.route('/api/recipes/<recipe_id>/save', methods=['POST'])
@token_required
def save_recipe(current_user, recipe_id):
    try:
        recipe = mongo.db.recipes.find_one({'_id': ObjectId(recipe_id)})
        if not recipe:
            return jsonify({'message': 'Recipe not found'}), 404
        
        result = mongo.db.users.update_one(
            {'_id': current_user['_id']},
            {'$addToSet': {'savedRecipes': ObjectId(recipe_id)}}
        )
        
        print(f"💾 Recipe saved: {recipe_id} (modified: {result.modified_count})")
        return jsonify({'message': 'Recipe saved successfully'}), 200
        
    except Exception as e:
        print(f"❌ Save error: {str(e)}")
        return jsonify({'message': 'Failed to save recipe', 'error': str(e)}), 500

@app.route('/api/recipes/saved', methods=['GET'])
@token_required
def get_saved_recipes(current_user):
    try:
        saved_ids = current_user.get('savedRecipes', [])
        recipes = list(mongo.db.recipes.find({'_id': {'$in': saved_ids}}))
        
        return jsonify({
            'recipes': [serialize_doc(r) for r in recipes],
            'total': len(recipes)
        }), 200
    except Exception as e:
        return jsonify({'message': 'Failed to fetch saved recipes', 'error': str(e)}), 500

@app.route('/api/recipes/<recipe_id>/mark-tried', methods=['POST'])
@token_required
def mark_recipe_tried(current_user, recipe_id):
    try:
        if ObjectId(recipe_id) in current_user.get('triedRecipes', []):
            return jsonify({'message': 'Recipe already marked as tried'}), 400
        
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
        return jsonify({'message': 'Failed to mark recipe', 'error': str(e)}), 500

@app.route('/api/seed', methods=['POST'])
def seed_data():
    try:
        if not mongo:
            return jsonify({'message': 'Database not connected'}), 500
        
        existing_count = mongo.db.recipes.count_documents({})
        if existing_count > 0:
            return jsonify({
                'message': f'Database already has {existing_count} recipes',
                'existing': existing_count
            }), 400
        
        sample_recipes = [
            {
                'name': 'Avocado Toast', 'cuisine': 'Breakfast', 'prepTime': 5, 'cookTime': 5,
                'servings': 2, 'calories': 250, 'difficulty': 'Easy', 'diet': ['vegetarian'],
                'rating': 4.8, 'reviewCount': 0,
                'ingredients': ['2 slices bread', '1 avocado', 'salt', 'pepper', 'lemon juice'],
                'instructions': ['Toast bread until golden', 'Mash avocado with lemon juice', 'Spread on toast', 'Season with salt and pepper'],
                'icon': '🥑', 'created_at': datetime.utcnow()
            },
            {
                'name': 'Greek Salad', 'cuisine': 'Mediterranean', 'prepTime': 10, 'cookTime': 0,
                'servings': 4, 'calories': 180, 'difficulty': 'Easy', 'diet': ['vegetarian', 'gluten-free'],
                'rating': 4.7, 'reviewCount': 0,
                'ingredients': ['tomatoes', 'cucumber', 'feta cheese', 'olives', 'olive oil', 'oregano'],
                'instructions': ['Chop vegetables', 'Add feta and olives', 'Drizzle with olive oil', 'Season with oregano'],
                'icon': '🥗', 'created_at': datetime.utcnow()
            },
            {
                'name': 'Chicken Stir Fry', 'cuisine': 'Asian', 'prepTime': 15, 'cookTime': 20,
                'servings': 4, 'calories': 420, 'difficulty': 'Medium', 'diet': [],
                'rating': 4.9, 'reviewCount': 0,
                'ingredients': ['chicken breast', 'mixed vegetables', 'soy sauce', 'garlic', 'ginger', 'sesame oil'],
                'instructions': ['Cut chicken into strips', 'Heat oil and cook chicken', 'Add vegetables', 'Stir fry with sauce'],
                'icon': '🍜', 'created_at': datetime.utcnow()
            },
            {
                'name': 'Margherita Pizza', 'cuisine': 'Italian', 'prepTime': 20, 'cookTime': 15,
                'servings': 4, 'calories': 520, 'difficulty': 'Medium', 'diet': ['vegetarian'],
                'rating': 4.6, 'reviewCount': 0,
                'ingredients': ['pizza dough', 'tomato sauce', 'mozzarella', 'fresh basil', 'olive oil'],
                'instructions': ['Roll out dough', 'Spread tomato sauce', 'Add mozzarella', 'Bake at 450°F for 12-15 minutes'],
                'icon': '🍕', 'created_at': datetime.utcnow()
            },
            {
                'name': 'Smoothie Bowl', 'cuisine': 'Healthy', 'prepTime': 10, 'cookTime': 0,
                'servings': 2, 'calories': 280, 'difficulty': 'Easy', 'diet': ['vegan', 'gluten-free'],
                'rating': 4.9, 'reviewCount': 0,
                'ingredients': ['frozen berries', 'banana', 'almond milk', 'granola', 'honey', 'chia seeds'],
                'instructions': ['Blend frozen fruit with milk', 'Pour into bowl', 'Top with granola and seeds', 'Drizzle with honey'],
                'icon': '🍇', 'created_at': datetime.utcnow()
            }
        ]
        
        result = mongo.db.recipes.insert_many(sample_recipes)
        print(f"✅ Seeded {len(result.inserted_ids)} recipes")
        
        return jsonify({
            'message': f'{len(result.inserted_ids)} recipes seeded successfully',
            'count': len(result.inserted_ids)
        }), 201
        
    except Exception as e:
        print(f"❌ Seed error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'message': 'Seeding failed', 'error': str(e)}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'message': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    print(f"500 Error: {str(error)}")
    return jsonify({'message': 'Internal server error'}), 500

# Production: Gunicorn handles app execution
# For local development only, uncomment below:
# if __name__ == '__main__':
#     app.run(debug=True, host='0.0.0.0', port=5000)
