from flask import Flask
from os import environ
from waitress import serve
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def run():
    serve(app, host='0.0.0.0', port=int(environ.get('PORT')))
    # app.run(host='0.0.0.0',port=int(environ.get('PORT')))

def keep_alive():
    t = Thread(target=run)
    t.start()