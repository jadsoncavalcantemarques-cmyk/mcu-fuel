"""
MCU — WSGI Entry Point (Produção)
Use com gunicorn: gunicorn --workers 2 --bind 0.0.0.0:5000 wsgi:app
"""
from app import app, init_db

init_db()

# Para cPanel/Passenger usar 'application'
application = app

if __name__ == '__main__':
    app.run()
