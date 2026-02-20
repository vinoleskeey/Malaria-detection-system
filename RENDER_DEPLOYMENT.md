# Deployment Guide for Malaria Detection System on Render

## Prerequisites
- A GitHub account
- A Render.com account (free tier works)

## Step 1: Prepare Your Project for Deployment

### 1.1 Create a .gitignore file
Create a `.gitignore` file to exclude unnecessary files:
```
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.so
*.egg
*.egg-info/
dist/
build/
.venv/
venv/
env/
.env
*.sqlite3
database.db
uploads/
.pytest/
.coverage
htmlcov/
```

### 1.2 Create a Render-compatible requirements.txt
Update requirements.txt with:
```
flask==3.1.3
werkzeug==3.1.5
tensorflow==2.20.0
numpy==2.4.2
pillow==12.1.1
gunicorn==23.0.0
```

### 1.3 Create runtime.txt (optional but recommended)
```
python-3.13.7
```

### 1.4 Create a Procfile for Render
Create a file named `Procfile` (no extension):
```
web: gunicorn app:app --workers 1 --timeout 120
```

### 1.5 Create build.sh for model setup
Create `build.sh` to download/set up the model:
```
bash
#!/bin/bash
echo "Setting up Malaria Model..."
# If model needs to be downloaded from somewhere, add commands here
echo "Setup complete!"
```

## Step 2: Push to GitHub

### 2.1 Initialize Git (if not already done)
```
bash
git init
git add .
git commit -m "Initial commit for Render deployment"
```

### 2.2 Create a new GitHub repository
1. Go to github.com and create a new repository
2. Push your code:
```
bash
git remote add origin https://github.com/YOUR_USERNAME/malaria-detection.git
git branch -M main
git push -u origin main
```

## Step 3: Deploy to Render

### 3.1 Create a Web Service on Render
1. Log in to render.com
2. Click "New +" and select "Web Service"
3. Connect your GitHub repository
4. Configure the service:
   - **Name**: malaria-detection
   - **Environment**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --workers 1 --timeout 120`
   - **Plan**: Free

### 3.2 Add Environment Variables
In the Render dashboard, add these environment variables:
- `SECRET_KEY`: Use a strong secret key (generate one with `python -c "import secrets; print(secrets.token_hex(32))"`)
- `PYTHON_VERSION`: `3.13.7`

### 3.3 Deploy
Click "Create Web Service" and wait for deployment to complete.

## Step 4: Verify Deployment

Once deployed, your app will be available at `https://malaria-detection.onrender.com`

Test these endpoints:
- `https://malaria-detection.onrender.com/` - Should redirect to login
- `https://malaria-detection.onrender.com/register` - Registration page

## Important Notes for Production

1. **Database**: SQLite works on Render but may have concurrency issues. Consider using PostgreSQL for production.

2. **File Uploads**: The `uploads/` folder is ephemeral on Render's free tier. Files are deleted on each deployment. For production, integrate with cloud storage like AWS S3.

3. **Model Path**: Update the model path in app.py to work on Render:
```
python
# For Render, use relative path from app.py location
model_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "malaria_model")
```

4. **Security**: Update password hashing to use bcrypt:
```
bash
pip install bcrypt
```
Then update the password hashing in app.py.

## Troubleshooting

- **Build fails**: Check that all dependencies are in requirements.txt
- **Model not found**: Ensure malaria_model folder is in your repo
- **500 errors**: Check Render logs for error details
